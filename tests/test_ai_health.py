import anthropic
import httpx2 as httpx
import pytest

from docmind import ai


def fake_client(raises=None, model_id=ai.MODEL):
    """A stand-in for anthropic.Anthropic() that never touches the network."""

    class Models:
        def retrieve(self, model):
            if raises is not None:
                raise raises
            return type("Model", (), {"id": model_id})()

    class Client:
        models = Models()

        def with_options(self, **_kwargs):
            return self

    return lambda: Client()


def status_error(cls, code, message="boom"):
    request = httpx.Request("GET", "https://api.anthropic.com/v1/models/x")
    return cls(message, response=httpx.Response(code, request=request), body=None)


def test_probe_ok(monkeypatch):
    monkeypatch.setattr(ai, "client", fake_client())

    status = ai.probe()
    assert status.ok
    assert status.model == ai.MODEL
    assert status.latency_ms is not None
    assert "error" not in status.as_dict()


@pytest.mark.parametrize(
    ("exc", "expected_error"),
    [
        (status_error(anthropic.AuthenticationError, 401), "auth"),
        (status_error(anthropic.PermissionDeniedError, 403), "auth"),
        (status_error(anthropic.NotFoundError, 404), "unknown_model"),
        (status_error(anthropic.RateLimitError, 429), "rate_limited"),
        (status_error(anthropic.InternalServerError, 500), "http_500"),
        (anthropic.APITimeoutError(httpx.Request("GET", "https://api.anthropic.com")), "timeout"),
        (anthropic.APIConnectionError(request=httpx.Request("GET", "https://api.anthropic.com")), "network"),
        (anthropic.AnthropicError("The api_key client option must be set"), "no_credentials"),
    ],
)
def test_probe_classifies_failures(monkeypatch, exc, expected_error):
    monkeypatch.setattr(ai, "client", fake_client(raises=exc))

    status = ai.probe()
    assert not status.ok
    assert status.error == expected_error
    assert status.detail  # never an empty explanation


def test_cached_probe_hits_the_api_once(monkeypatch):
    calls = []

    def counting_probe():
        calls.append(1)
        return ai.ApiStatus(ok=True, model=ai.MODEL, latency_ms=1)

    monkeypatch.setattr(ai, "probe", counting_probe)

    ai.cached_probe()
    ai.cached_probe()
    assert len(calls) == 1

    # A zero-length window forces a fresh check.
    ai.cached_probe(max_age_seconds=0)
    assert len(calls) == 2


def test_healthz_ok(monkeypatch, client):
    monkeypatch.setattr(ai, "client", fake_client())

    response = client.get("/healthz")
    assert response.status_code == 200
    body = response.get_json()
    assert body["status"] == "ok"
    assert body["ai"]["ok"] is True
    assert body["ai"]["model"] == ai.MODEL


def test_healthz_reports_degraded_when_api_is_down(monkeypatch, client):
    monkeypatch.setattr(
        ai, "client", fake_client(raises=status_error(anthropic.AuthenticationError, 401))
    )

    response = client.get("/healthz")
    assert response.status_code == 503
    body = response.get_json()
    assert body["status"] == "degraded"
    assert body["ai"] == {
        "ok": False,
        "model": ai.MODEL,
        "error": "auth",
        "detail": body["ai"]["detail"],
        "hint": "credentials rejected; run `ant auth login`",
    }


@pytest.mark.parametrize(
    ("exc", "expected_code", "expected_status"),
    [
        (status_error(anthropic.AuthenticationError, 401), "auth", 502),
        (status_error(anthropic.PermissionDeniedError, 403), "auth", 502),
        (status_error(anthropic.NotFoundError, 404), "unknown_model", 502),
        # RateLimitError is an APIStatusError subclass; it must not fall through
        # to the generic http_429 case.
        (status_error(anthropic.RateLimitError, 429), "rate_limited", 429),
        (status_error(anthropic.InternalServerError, 500), "http_500", 502),
        # APITimeoutError is an APIConnectionError subclass, same trap.
        (anthropic.APITimeoutError(httpx.Request("GET", "https://api.anthropic.com")), "timeout", 504),
        (anthropic.APIConnectionError(request=httpx.Request("GET", "https://api.anthropic.com")), "network", 502),
        (anthropic.AnthropicError("The api_key client option must be set"), "no_credentials", 503),
    ],
)
def test_classify_is_shared_by_every_surface(exc, expected_code, expected_status):
    error = ai.classify(exc)

    assert error.code == expected_code
    assert error.http_status == expected_status
    assert error.message  # the chat page always has something to flash


def test_probe_and_classify_agree_on_the_code(monkeypatch):
    """/healthz codes come from the same classifier the other surfaces use."""
    exc = status_error(anthropic.AuthenticationError, 401)
    monkeypatch.setattr(ai, "client", fake_client(raises=exc))

    assert ai.probe().error == ai.classify(exc).code
