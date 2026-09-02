"""Flask application factory for Docmind.

Two surfaces, one app: `web` renders HTML for a browser, `api` speaks JSON
under /api/v1. Neither holds any logic -- both call into ai.py, chat.py and
storage.py, which know nothing about Flask.
"""

import os
from pathlib import Path

from flask import Flask, request
from werkzeug.exceptions import HTTPException

from docmind import ai, api, storage, web

MAX_UPLOAD_BYTES = 25 * 1024 * 1024
API_PREFIX = "/api/v1"


def create_app(upload_dir: Path | None = None) -> Flask:
    app = Flask(__name__)
    app.config["UPLOAD_DIR"] = storage.ensure_dir(upload_dir or storage.UPLOAD_DIR)
    # Flask answers anything larger with a 413 instead of filling the disk.
    app.config["MAX_CONTENT_LENGTH"] = MAX_UPLOAD_BYTES
    # Signs the session cookie that holds the conversation id. Without
    # DOCMIND_SECRET_KEY set, a fresh key each start means chats don't survive
    # a restart -- which matches the in-memory transcript store anyway.
    app.secret_key = os.environ.get("DOCMIND_SECRET_KEY") or os.urandom(32)

    app.register_blueprint(web.bp)
    app.register_blueprint(api.bp, url_prefix=API_PREFIX)

    @app.errorhandler(HTTPException)
    def handle_http_exception(exc: HTTPException):
        # Routing errors (a typo'd URL) are raised before Flask knows which
        # blueprint was meant, so api.py cannot catch its own 404s. Decide by
        # prefix here; returning the exception gives the default HTML page.
        if request.path.startswith(API_PREFIX):
            return api.handle_http_exception(exc)
        return exc

    @app.get("/healthz")
    def healthz():
        # Deliberately on the app rather than either blueprint: this is a probe
        # for the process, not a resource of the web or the API surface, and
        # probes are expected at the root.
        #
        # The app is useless without the Claude API, so its reachability is
        # part of our health: 503 when we can't talk to it.
        api_status = ai.cached_probe()
        body = {"status": "ok" if api_status.ok else "degraded", "ai": api_status.as_dict()}
        return body, (200 if api_status.ok else 503)

    return app
