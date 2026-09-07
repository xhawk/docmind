import io

import pytest

from docmind import ai, chat, create_app


@pytest.fixture
def app(tmp_path):
    app = create_app(upload_dir=tmp_path / "uploads")
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(app):
    return app.test_client()


@pytest.fixture(autouse=True)
def no_stale_state():
    """Keep one test's API verdict and transcripts from leaking into the next."""
    ai.reset_cache()
    chat.reset_all()
    yield
    ai.reset_cache()
    chat.reset_all()


PDF_BYTES = b"%PDF-1.4\nfake but plausible\n"


@pytest.fixture
def upload(client):
    """Upload a file and return the name it was stored under."""

    def _upload(filename, data=PDF_BYTES):
        client.post(
            "/upload",
            data={"file": (io.BytesIO(data), filename)},
            content_type="multipart/form-data",
        )
        names = [n for n in client.get("/").data.decode().split('"') if n.endswith(filename)]
        # The listing renders /chat/<stored-name> and /files/<stored-name>.
        return names[0].rsplit("/", 1)[-1]

    return _upload


@pytest.fixture
def document(upload):
    """A stored PDF to chat about. Every conversation is about one."""
    return upload("report.pdf")


@pytest.fixture
def recorded_asks(monkeypatch):
    """Stub ai.ask, recording the conversation it was handed each time.

    The real `ask` is a generator: it yields a `Delta` per chunk of text as
    Claude produces it, then one `Done` carrying the finished `Reply`. The
    stub has the same shape -- in two chunks, so a test can tell streamed
    output from a single blob -- which keeps the route's streaming path under
    test instead of bypassing it.

    Recording happens when `ask` is *called*, not when the generator is first
    pulled, so `recorded_asks == []` still means "the API was never reached".
    """
    calls = []

    def fake_ask(messages, **kwargs):
        calls.append(messages)
        answer = f"answer {len(calls)}"

        def stream():
            yield ai.Delta(answer[: len(answer) // 2])
            yield ai.Delta(answer[len(answer) // 2 :])
            yield ai.Done(
                ai.Reply(
                    text=answer,
                    blocks=[{"type": "text", "text": answer}],
                    stop_reason="end_turn",
                )
            )

        return stream()

    monkeypatch.setattr(ai, "ask", fake_ask)
    return calls


@pytest.fixture
def failing_ask(monkeypatch):
    """Stub `ai.ask` so that iterating it raises the given error.

    The error has to surface while the response body is being generated, not
    when `ask` is called -- that is where it happens for real, and it is the
    only place `web.py` can still catch it.
    """

    def _install(error: Exception):
        def fake_ask(messages, **kwargs):
            def stream():
                raise error
                yield  # pragma: no cover - makes this a generator function

            return stream()

        monkeypatch.setattr(ai, "ask", fake_ask)

    return _install
