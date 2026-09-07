"""Claude API access for Docmind.

The client is constructed with no arguments on purpose: the SDK resolves
credentials from `ANTHROPIC_API_KEY`, then `ANTHROPIC_AUTH_TOKEN`, then the
active `ant auth login` profile under ~/.config/anthropic. So `ant auth login`
alone is enough -- no need to export a key before starting the app.
"""

import base64
import threading
import time
from dataclasses import dataclass, asdict
from collections.abc import Iterator

import anthropic
from anthropic.types import ContentBlock

MODEL = "claude-sonnet-5"

SYSTEM_PROMPT = (
    "You are Docmind, an assistant that helps people understand their own documents "
    "(PDFs, images, screenshots). Answer from the documents in the conversation when "
    "they are present, and say so plainly when the answer isn't in them."
)

# default value for reply tokens
MAX_REPLY_TOKENS = 64000

# What we know how to put in front of Claude. A PDF becomes a `document`
# block; the picture formats become an `image` block.
ATTACHABLE_TYPES = {
    "application/pdf": "document",
    "image/png": "image",
    "image/jpeg": "image",
    "image/gif": "image",
    "image/webp": "image",
}

# The API caps a whole request at 32 MB and base64 inflates bytes by about a
# third, so a file near the 25 MB upload limit would produce a request Claude
# rejects outright. 20 MB raw (~27 MB encoded) leaves room for the transcript.
MAX_ATTACH_BYTES = 20 * 1024 * 1024

# A health check must answer fast and must not sit in the SDK's retry
# backoff, so it gets a short timeout and no retries of its own.
PROBE_TIMEOUT_SECONDS = 5.0
# /healthz can be polled hard; reuse a recent verdict instead of hitting the
# API on every request.
PROBE_CACHE_SECONDS = 30.0

_cache_lock = threading.Lock()
_cached: tuple[float, "ApiStatus"] | None = None


@dataclass(frozen=True)
class ApiError:
    """One Claude API failure, described for every surface that reports it."""

    code: str  # machine-readable: "auth", "rate_limited", "http_500", ...
    message: str  # a sentence worth showing a person
    hint: str | None  # what to do about it
    http_status: int  # what a REST caller should be told


@dataclass(frozen=True)
class ApiStatus:
    """Whether the Claude API is reachable and usable with our credentials."""

    ok: bool
    model: str
    latency_ms: int | None = None
    error: str | None = None
    detail: str | None = None
    hint: str | None = None

    def as_dict(self) -> dict:
        return {k: v for k, v in asdict(self).items() if v is not None}


def client() -> anthropic.Anthropic:
    return anthropic.Anthropic()


def classify(exc: Exception) -> ApiError:
    """Map an SDK exception onto one description every surface can use.

    /healthz reports the `code` and `hint`, the chat page flashes the
    `message`, and the REST API answers with `http_status`. Keeping the
    mapping here means a new case is added in one place.

    Order matters: RateLimitError, NotFoundError and AuthenticationError are
    all APIStatusError subclasses, and APITimeoutError is an
    APIConnectionError subclass, so the specific checks come first.
    """
    if isinstance(exc, (anthropic.AuthenticationError, anthropic.PermissionDeniedError)):
        return ApiError(
            code="auth",
            message="Claude rejected our credentials. Run `ant auth login` and try again.",
            hint="credentials rejected; run `ant auth login`",
            # Our misconfiguration toward the upstream API, not the caller's fault.
            http_status=502,
        )
    if isinstance(exc, anthropic.NotFoundError):
        return ApiError(
            code="unknown_model",
            message=f"{MODEL} is not available to this organization.",
            hint=f"{MODEL} is not available to this org",
            http_status=502,
        )
    if isinstance(exc, anthropic.RateLimitError):
        return ApiError(
            code="rate_limited",
            message="Rate limited by the Claude API. Wait a moment and resend.",
            hint="credentials work but the org is throttled",
            # Worth passing straight through: the caller should retry later.
            http_status=429,
        )
    if isinstance(exc, anthropic.APITimeoutError):
        return ApiError(
            code="timeout",
            message="Claude took too long to answer. Resend, or try a shorter question.",
            hint="the API did not respond in time",
            http_status=504,
        )
    if isinstance(exc, anthropic.APIConnectionError):
        return ApiError(
            code="network",
            message="Can't reach the Claude API. Check your connection.",
            hint="cannot reach api.anthropic.com",
            http_status=502,
        )
    if isinstance(exc, anthropic.APIStatusError):
        return ApiError(
            code=f"http_{exc.status_code}",
            message=f"Claude API error: {exc}",
            hint=None,
            http_status=502,
        )
    # Anything else from the SDK: credential resolution failing outright (no
    # key, no profile) lands here.
    return ApiError(
        code="no_credentials",
        message="No Claude credentials found. Run `ant auth login` or set ANTHROPIC_API_KEY.",
        hint="run `ant auth login` or set ANTHROPIC_API_KEY",
        http_status=503,
    )


def probe() -> ApiStatus:
    """Check the Claude API without spending tokens.

    `models.retrieve` is a plain GET: it exercises credentials, network and
    model availability -- everything a message send needs -- but is not billed
    and cannot be slowed down by generation.
    """
    started = time.monotonic()
    try:
        api = client().with_options(timeout=PROBE_TIMEOUT_SECONDS, max_retries=0)
        model = api.models.retrieve(MODEL)
    except anthropic.AnthropicError as exc:
        error = classify(exc)
        return ApiStatus(
            ok=False,
            model=MODEL,
            error=error.code,
            detail=str(exc) or type(exc).__name__,
            hint=error.hint,
        )

    return ApiStatus(
        ok=True,
        model=model.id,
        latency_ms=round((time.monotonic() - started) * 1000),
    )


def cached_probe(max_age_seconds: float = PROBE_CACHE_SECONDS) -> ApiStatus:
    """`probe()`, but reusing a verdict from the last `max_age_seconds`."""
    global _cached

    now = time.monotonic()
    with _cache_lock:
        if _cached is not None and now - _cached[0] < max_age_seconds:
            return _cached[1]

    status = probe()
    with _cache_lock:
        _cached = (time.monotonic(), status)
    return status


def reset_cache() -> None:
    """Forget the cached verdict. Used by tests and after a re-login."""
    global _cached
    with _cache_lock:
        _cached = None


@dataclass(frozen=True)
class Reply:
    """One assistant turn: what to show, and what to send back next time."""

    text: str
    # The raw content blocks, including thinking blocks. These go back into
    # `messages` verbatim on the next turn -- Claude needs its own thinking
    # replayed unchanged to continue the same reasoning.
    blocks: list[ContentBlock]
    stop_reason: str | None
    # The response's `usage`, kept so callers can see what the attached
    # document cost and whether the next turn read it from cache.
    usage: object | None = None

    @property
    def refused(self) -> bool:
        return self.stop_reason == "refusal"


@dataclass(frozen=True)
class Delta:
    """Some kind of difference"""
    text: str


@dataclass(frozen=True)
class Done:
    """Class that we can observe and decide if we are done."""
    reply: Reply


def is_attachable(media_type: str | None) -> bool:
    """Whether a file of this type can be put in front of Claude at all."""
    return (media_type or "") in ATTACHABLE_TYPES


def attachment_block(name: str, data: bytes, media_type: str | None) -> dict:
    """A document or image content block for one uploaded file.

    Raises ValueError if we can't send this file.
    """
    kind = ATTACHABLE_TYPES.get(media_type or "")
    if kind is None:
        raise ValueError(
            f"{name} is a {media_type or 'unrecognised'} file; "
            "Docmind can only read PDFs and images so far."
        )
    if len(data) > MAX_ATTACH_BYTES:
        limit_mb = MAX_ATTACH_BYTES // (1024 * 1024)
        raise ValueError(f"{name} is larger than {limit_mb} MB, too big to send to Claude.")

    return {
        "type": kind,
        "source": {
            "type": "base64",
            "media_type": media_type,
            "data": base64.standard_b64encode(data).decode("ascii"),
        },
        # The document is the biggest and most stable part of the prefix and
        # the stateless API makes us resend it every turn, so cache it. Watch
        # `usage.cache_read_input_tokens` on the second turn.
        "cache_control": {"type": "ephemeral"},
    }


def user_content(message: str, attachment: dict) -> list[dict]:
    """What to put in a user message's `content`.

    Plain text when there's nothing attached. With an attachment, the file
    comes *before* the text -- the API docs are explicit that documents and
    images should precede the question about them.
    """
    return [attachment, {"type": "text", "text": message}]


def ask(messages: list[dict], *, system: str = SYSTEM_PROMPT) -> Iterator[Delta | Done]:
    """Send a conversation and stream the response.
    """
    with client().messages.stream(
        model=MODEL,
        max_tokens=MAX_REPLY_TOKENS,
        system=system,
        messages=messages,
        thinking={"type": "adaptive"},
    ) as stream:
        for text in stream.text_stream:
            yield Delta(text)

        message = stream.get_final_message()

        parts = []
        for block in message.content:
            if block.type == "text":
                parts.append(block.text)
        text = "".join(parts)

        if message.stop_reason == "refusal" and not text:
            text = "Sorry, I'm unable to answer to that. Please rephrase your question."

        yield Done(Reply(text=text, blocks=list(message.content), usage=message.usage, stop_reason=message.stop_reason))
