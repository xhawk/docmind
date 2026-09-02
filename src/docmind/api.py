"""The REST surface: JSON for programmatic callers.

Nothing is mounted here yet -- this module exists to hold the conventions, so
the first endpoint has something to follow:

- **No session cookie.** Identity comes from the URL, not from who the browser
  says it is. A conversation is addressed as
  `/api/v1/conversations/<id>/messages`; `chat.get_or_create(id)` and
  `chat.drop(id)` already take an id, so no new state is needed.
- **No redirects.** Return a representation and a status code: 201 with the
  new resource on create, 200 with the reply on send, 404 for an unknown
  conversation.
- **Errors are JSON**, shaped `{"error": {"code": ..., "message": ...}}`. Both
  handlers below produce that, so a route can simply `abort(404)` or let an
  `anthropic.AnthropicError` propagate.
- **No logic.** Same rule as web.py: call ai/chat/storage and serialise.
"""

import anthropic
from flask import Blueprint, jsonify
from werkzeug.exceptions import HTTPException

from docmind import ai

bp = Blueprint("api", __name__)


def error_response(code: str, message: str, http_status: int):
    return jsonify({"error": {"code": code, "message": message}}), http_status


def handle_http_exception(exc: HTTPException):
    """Answer JSON, not Flask's HTML error page, for anything under /api/v1.

    Not a blueprint errorhandler: Flask raises a routing 404 before it knows
    which blueprint the path was meant for, so a blueprint-scoped handler
    never sees an unknown /api/v1 URL. app.py dispatches here by prefix
    instead.
    """
    return error_response(
        code=exc.name.lower().replace(" ", "_"),
        message=exc.description or exc.name,
        http_status=exc.code or 500,
    )


@bp.errorhandler(anthropic.AnthropicError)
def handle_anthropic_error(exc: anthropic.AnthropicError):
    """Upstream Claude failures, mapped once in ai.classify."""
    error = ai.classify(exc)
    return error_response(error.code, error.message, error.http_status)
