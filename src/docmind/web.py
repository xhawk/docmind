"""The HTML surface: server-rendered pages for a person in a browser.

No logic lives in this module -- it reads forms, calls ai/chat/storage, and
renders. Anything worth testing without a request belongs in those modules.
"""
from collections.abc import Iterator

import anthropic
from flask import (
    Blueprint,
    abort,
    current_app,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for, Response, stream_with_context,
)

from docmind import ai, chat, storage

bp = Blueprint("web", __name__)


@bp.get("/")
def index():
    upload_dir = current_app.config["UPLOAD_DIR"]
    documents = [
        {"name": name, "attachable": ai.is_attachable(storage.media_type(name))}
        for name in storage.list_files(upload_dir)
    ]
    return render_template("index.html", documents=documents)


@bp.post("/upload")
def upload():
    file_storage = request.files.get("file")
    if file_storage is None:
        abort(400, "no file field in the request")
    try:
        storage.save(file_storage, current_app.config["UPLOAD_DIR"])
    except ValueError as exc:
        abort(400, str(exc))
    return redirect(url_for("web.index"))


@bp.get("/files/<name>")
def download(name: str):
    # send_from_directory does its own traversal check; never join by hand.
    return send_from_directory(current_app.config["UPLOAD_DIR"], name)


@bp.get("/chat/<name>")
def chat_page(name: str):
    conversation = _conversation_for(name)
    # How the browser can show the document beside the transcript, if at all.
    preview = storage.preview_kind(name)
    return render_template(
        "chat.html", conversation=conversation, turns=conversation.turns, preview=preview
    )


@bp.post("/chat/<name>")
def send_message(name: str):
    message = (request.form.get("message") or "").strip()
    if not message:
        return redirect(url_for("web.chat_page", name=name))

    conversation = _conversation_for(name)

    # The document rides in the first user message and the stateless API
    # resends the history after that, so it crosses the wire exactly once.
    attachment = None
    if conversation.needs_attachment:
        try:
            attachment = _attachment_for(conversation.document)
        except ValueError as exc:
            # Unsupported type or too big -- nothing sent, no turn recorded.
            return Response(str(exc), mimetype="text/plain")
        except FileNotFoundError as exc:
            return Response("File not found: " + str(exc), mimetype="text/plain")

    blocks = ai.user_content(message, attachment) if attachment else None
    conversation.add_user(message, blocks=blocks)

    delta_or_dones = ai.ask(conversation.as_messages())
    generator_fun = _conversation_iterator(conversation, delta_or_dones)
    return Response(stream_with_context(generator_fun), mimetype="text/plain")


@bp.post("/chat/new")
def new_chat():
    """Start a document's thread over. Static rule, so it wins over /chat/<name>."""
    name = request.form.get("document")
    if not name:
        abort(400, "no document field in the request")
    conversations = dict(session.get("conversations") or {})
    chat.drop(conversations.pop(name, None))
    session["conversations"] = conversations
    return redirect(url_for("web.chat_page", name=name))


def _conversation_iterator(conversation: chat.Conversation, delta_or_dones: Iterator[ai.Delta | ai.Done]) -> Iterator[str]:
    try:
        for delta_or_done in delta_or_dones:
            if isinstance(delta_or_done, ai.Delta):
                yield delta_or_done.text
            elif isinstance(delta_or_done, ai.Done):
                conversation.add_assistant(delta_or_done.reply.text, delta_or_done.reply.blocks)
                _log_usage(conversation, delta_or_done.reply)
    except anthropic.AnthropicError as exc:
        conversation.drop_last_user_turn()
        yield ai.classify(exc).message

def _conversation_for(name: str) -> chat.Conversation:
    """This session's thread for a document.

    One thread per document, so clicking the same file always comes back to
    the same transcript. The session cookie holds only the ids.
    """
    if storage.resolve(name, current_app.config["UPLOAD_DIR"]) is None:
        abort(404, f"no uploaded document named {name}")

    conversations = dict(session.get("conversations") or {})
    conversation = chat.get_or_create(conversations.get(name), document=name)
    conversations[name] = conversation.id
    session["conversations"] = conversations
    return conversation


def _attachment_for(name: str) -> dict:
    """The content block for a stored document. Raises ValueError if unsendable."""
    upload_dir = current_app.config["UPLOAD_DIR"]
    return ai.attachment_block(
        name=name,
        data=storage.read_bytes(name, upload_dir),
        media_type=storage.media_type(name),
    )


def _log_usage(conversation: chat.Conversation, reply: ai.Reply) -> None:
    """Print what the turn cost. The point is watching the cache land."""
    usage = reply.usage
    if usage is None:
        return
    current_app.logger.info(
        "chat turn document=%s input=%s output=%s cache_write=%s cache_read=%s",
        conversation.document,
        getattr(usage, "input_tokens", "?"),
        getattr(usage, "output_tokens", "?"),
        getattr(usage, "cache_creation_input_tokens", "?"),
        getattr(usage, "cache_read_input_tokens", "?"),
    )
