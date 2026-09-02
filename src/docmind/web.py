"""The HTML surface: server-rendered pages for a person in a browser.

Conventions here, which are deliberately not the API's (see api.py):
identity comes from the signed session cookie, problems are reported with
`flash()`, and a successful POST answers with a redirect so the browser
doesn't resubmit on refresh.

No logic lives in this module -- it reads forms, calls ai/chat/storage, and
renders. Anything worth testing without a request belongs in those modules.
"""

import anthropic
from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_from_directory,
    session,
    url_for,
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
            flash(str(exc))
            return redirect(url_for("web.chat_page", name=name))
        except FileNotFoundError:
            flash(f"{conversation.document} is no longer in your uploads.")
            return redirect(url_for("web.chat_page", name=name))

    blocks = ai.user_content(message, attachment) if attachment else None
    conversation.add_user(message, blocks=blocks)

    # Synchronous on purpose: the request holds open until Claude has
    # finished the whole reply, then the page renders it.
    try:
        reply = ai.ask(conversation.as_messages())
    except anthropic.AnthropicError as exc:
        # Drop the unanswered question so resending isn't a duplicate turn.
        conversation.drop_last_user_turn()
        flash(ai.classify(exc).message)
    else:
        conversation.add_assistant(reply.text, reply.blocks)
        _log_usage(conversation, reply)

    return redirect(url_for("web.chat_page", name=name))


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
