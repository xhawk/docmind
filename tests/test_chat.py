"""The conversation itself: turns, history, errors, session isolation.

Every conversation is about a document, so these all run against
/chat/<name>. What the attachment looks like on the wire is
test_document_chat.py's job; this file is about the transcript.
"""

import anthropic
import httpx2 as httpx

from docmind import ai, chat


def send(client, document, message):
    """POST a question and read the streamed reply to the end.

    Reading matters. The reply is generated *while* the body is being read, so
    the assistant turn isn't recorded until someone consumes it -- a browser
    always does, but the test client only does so on request.
    """
    response = client.post(f"/chat/{document}", data={"message": message})
    response.get_data()
    return response


def test_empty_chat_page(client, document):
    response = client.get(f"/chat/{document}")
    assert response.status_code == 200
    assert b'name="message"' in response.data
    # Nothing said yet, so the transcript renders but holds no turns.
    assert b'class="transcript"' in response.data
    assert b'class="turn' not in response.data


def test_send_message_streams_the_answer_back(client, document, recorded_asks):
    """The POST no longer redirects -- its body *is* the reply."""
    response = send(client, document, "What is in my invoice?")

    assert response.status_code == 200
    assert response.mimetype == "text/plain"
    assert response.data.decode() == "answer 1"


def test_the_answer_arrives_in_pieces(client, document, recorded_asks):
    """Streamed, not buffered: the body is assembled from several chunks.

    `iter_encoded()` hands back what the generator yielded, one item at a
    time, so this fails if the route ever goes back to producing the whole
    reply before responding.
    """
    # Not send(): that reads the body to the end, which is what we're
    # inspecting here.
    response = client.post(f"/chat/{document}", data={"message": "What is in my invoice?"})

    chunks = [chunk.decode() for chunk in response.iter_encoded()]
    assert len(chunks) > 1
    assert "".join(chunks) == "answer 1"


def test_the_turn_is_saved_and_shown_on_the_page(client, document, recorded_asks):
    send(client, document, "What is in my invoice?")

    page = client.get(f"/chat/{document}").data.decode()
    assert "What is in my invoice?" in page
    assert "answer 1" in page


def test_history_is_resent_with_assistant_blocks(client, document, recorded_asks):
    send(client, document, "first")
    send(client, document, "second")

    # The opening turn carries the attachment (see test_document_chat.py);
    # what matters here is what follows it.
    assert recorded_asks[1][1:] == [
        # The assistant turn goes back as raw blocks, not flattened text.
        {"role": "assistant", "content": [{"type": "text", "text": "answer 1"}]},
        {"role": "user", "content": "second"},
    ]


def test_blank_message_does_not_call_the_api(client, document, recorded_asks):
    assert send(client, document, "   ").status_code == 302
    assert recorded_asks == []


def test_api_error_is_streamed_and_the_question_is_not_kept(client, document, failing_ask):
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    failing_ask(
        anthropic.AuthenticationError(
            "nope", response=httpx.Response(401, request=request), body=None
        )
    )

    # Headers are long gone by the time Claude fails, so the explanation can
    # only travel in the body -- not as a status code, and not as a flash.
    response = send(client, document, "hello?")
    assert response.status_code == 200
    assert "ant auth login" in response.data.decode()

    # The unanswered question is gone, so resending isn't a duplicate turn.
    page = client.get(f"/chat/{document}").data.decode()
    assert "hello?" not in page
    assert 'class="turn' not in page


def test_rate_limit_gets_its_own_message(client, document, failing_ask):
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    failing_ask(
        anthropic.RateLimitError(
            "slow down", response=httpx.Response(429, request=request), body=None
        )
    )

    assert "Rate limited" in send(client, document, "hello?").data.decode()


def test_new_conversation_clears_the_transcript(client, document, recorded_asks):
    send(client, document, "remember this")
    assert "remember this" in client.get(f"/chat/{document}").data.decode()

    assert client.post("/chat/new", data={"document": document}).status_code == 302
    page = client.get(f"/chat/{document}").data.decode()
    assert "remember this" not in page
    assert 'class="turn' not in page


def test_new_conversation_without_a_document_is_400(client):
    assert client.post("/chat/new", data={}).status_code == 400


def test_conversations_are_per_session(app, document, recorded_asks):
    first, second = app.test_client(), app.test_client()
    send(first, document, "mine")
    second.get(f"/chat/{document}")

    assert "mine" in first.get(f"/chat/{document}").data.decode()
    assert "mine" not in second.get(f"/chat/{document}").data.decode()


def test_refusal_still_renders_something(client, document, monkeypatch):
    def refusing_ask(messages, **kwargs):
        # A refusal streams no text at all -- straight to Done.
        yield ai.Done(ai.Reply(text="", blocks=[], stop_reason="refusal"))

    monkeypatch.setattr(ai, "ask", refusing_ask)

    assert send(client, document, "something disallowed").status_code == 200
    # The route stores whatever ask() produced; ask() substitutes the
    # placeholder itself, so an empty refusal must not crash the page.
    assert client.get(f"/chat/{document}").status_code == 200


def test_conversation_as_messages_prefers_blocks():
    conversation = chat.Conversation(id="x", document="report.pdf")
    conversation.add_user("q")
    conversation.add_assistant("a", [{"type": "thinking", "thinking": ""}, {"type": "text", "text": "a"}])

    assert conversation.as_messages() == [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": [{"type": "thinking", "thinking": ""}, {"type": "text", "text": "a"}]},
    ]


def test_drop_last_user_turn_only_drops_user_turns():
    conversation = chat.Conversation(id="x", document="report.pdf")
    conversation.add_user("q")
    conversation.add_assistant("a", [])
    conversation.drop_last_user_turn()
    assert [turn.role for turn in conversation.turns] == ["user", "assistant"]

    conversation.add_user("unanswered")
    conversation.drop_last_user_turn()
    assert [turn.role for turn in conversation.turns] == ["user", "assistant"]
