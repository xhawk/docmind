"""Chatting about an uploaded document: how the file reaches Claude."""

import base64

from docmind import ai

PNG_BYTES = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def test_document_chat_page_names_the_document(client, upload):
    name = upload("quarterly-report.pdf")

    response = client.get(f"/chat/{name}")

    assert response.status_code == 200
    page = response.data.decode()
    assert name in page
    assert 'name="message"' in page


def test_unknown_document_is_404(client):
    assert client.get("/chat/nope.pdf").status_code == 404


def test_traversal_in_the_url_is_404(client):
    assert client.get("/chat/..%2f..%2fREADME.md").status_code == 404


def test_index_links_each_document_to_its_chat(client, upload):
    name = upload("report.pdf")

    page = client.get("/").data.decode()
    assert f'href="/chat/{name}"' in page
    # The download link is still reachable.
    assert f'href="/files/{name}"' in page


def test_unchattable_document_is_listed_but_not_linked_to_chat(client, upload):
    name = upload("notes.txt", data=b"just text")

    page = client.get("/").data.decode()
    assert name in page  # still listed, still downloadable
    assert f'href="/files/{name}"' in page
    # ...but no link into a chat that could only refuse it.
    assert f'href="/chat/{name}"' not in page
    # Literal template text, so no escaping -- unlike a rendered variable.
    assert "can't chat about this type yet" in page


def test_first_message_attaches_the_document_before_the_text(client, upload, recorded_asks):
    pdf = b"%PDF-1.4\nthe exact bytes we uploaded\n"
    name = upload("report.pdf", data=pdf)

    client.post(f"/chat/{name}", data={"message": "What is the headline number?"})

    (content,) = [m["content"] for m in recorded_asks[0]]
    attachment, text = content
    assert attachment["type"] == "document"
    assert attachment["source"]["media_type"] == "application/pdf"
    assert base64.standard_b64decode(attachment["source"]["data"]) == pdf
    # Resent on every turn, so it has to be cached.
    assert attachment["cache_control"] == {"type": "ephemeral"}
    assert text == {"type": "text", "text": "What is the headline number?"}


def test_an_image_is_attached_as_an_image_block(client, upload, recorded_asks):
    name = upload("screenshot.png", data=PNG_BYTES)

    client.post(f"/chat/{name}", data={"message": "What does this show?"})

    (content,) = [m["content"] for m in recorded_asks[0]]
    assert content[0]["type"] == "image"
    assert content[0]["source"]["media_type"] == "image/png"


def test_the_document_is_not_attached_twice(client, upload, recorded_asks):
    name = upload("report.pdf")

    # get_data() reads each streamed reply to the end; without that the
    # assistant turn is never recorded and the second request has no history.
    client.post(f"/chat/{name}", data={"message": "first"}).get_data()
    client.post(f"/chat/{name}", data={"message": "second"}).get_data()

    second_request = recorded_asks[1]
    assert len(second_request) == 3  # user+attachment, assistant, user
    # The history still carries the one attachment...
    assert second_request[0]["content"][0]["type"] == "document"
    # ...and the new question is plain text.
    assert second_request[2] == {"role": "user", "content": "second"}
    attachments = [
        block
        for msg in second_request
        if isinstance(msg["content"], list)
        for block in msg["content"]
        if isinstance(block, dict) and block.get("type") in {"document", "image"}
    ]
    assert len(attachments) == 1


def test_each_document_gets_its_own_thread(client, upload, recorded_asks):
    first = upload("first.pdf")
    second = upload("second.pdf")

    client.post(f"/chat/{first}", data={"message": "about the first"})
    client.post(f"/chat/{second}", data={"message": "about the second"})

    first_page = client.get(f"/chat/{first}").data.decode()
    second_page = client.get(f"/chat/{second}").data.decode()

    assert "about the first" in first_page
    assert "about the first" not in second_page
    assert "about the second" in second_page


def test_returning_to_a_document_resumes_its_thread(client, upload, recorded_asks):
    name = upload("report.pdf")
    client.post(f"/chat/{name}", data={"message": "remember this"})

    client.get("/")  # wander off to the document list
    assert "remember this" in client.get(f"/chat/{name}").data.decode()


def test_unsupported_file_type_is_refused_without_calling_the_api(client, upload, recorded_asks):
    name = upload("notes.txt", data=b"just text")

    response = client.post(f"/chat/{name}", data={"message": "what does it say?"})

    # Refused before streaming starts, so the reason comes back as the body.
    assert "only read PDFs and images" in response.data.decode()
    assert recorded_asks == []
    # No half-finished turn left behind.
    assert "what does it say?" not in client.get(f"/chat/{name}").data.decode()


def test_oversized_document_is_refused(client, upload, recorded_asks, monkeypatch):
    monkeypatch.setattr(ai, "MAX_ATTACH_BYTES", 4)
    name = upload("big.pdf")

    response = client.post(f"/chat/{name}", data={"message": "summarise"})

    assert "too big to send" in response.data.decode()
    assert recorded_asks == []


def test_new_conversation_resets_only_that_thread(client, upload, recorded_asks):
    """The static /chat/new rule must also win over /chat/<name>."""
    first = upload("first.pdf")
    second = upload("second.pdf")
    client.post(f"/chat/{first}", data={"message": "about the first"})
    client.post(f"/chat/{second}", data={"message": "about the second"})

    response = client.post("/chat/new", data={"document": first})
    assert response.headers["Location"].endswith(f"/chat/{first}")

    assert "about the first" not in client.get(f"/chat/{first}").data.decode()
    assert "about the second" in client.get(f"/chat/{second}").data.decode()


# ------------------------------------------------------- the preview pane --
# The document chat is split: the file on the left, the conversation on the
# right. The browser does the rendering, so the page just has to point the
# right element at /files/<name>.


def test_pdf_chat_previews_the_document(client, upload):
    name = upload("quarterly-report.pdf")

    page = client.get(f"/chat/{name}").data.decode()

    assert "chat-layout" in page
    assert '<iframe class="preview-frame"' in page
    assert f'src="/files/{name}"' in page
    assert "preview-empty" not in page


def test_image_chat_previews_the_image(client, upload):
    name = upload("scan.png", data=PNG_BYTES)

    page = client.get(f"/chat/{name}").data.decode()

    assert '<img class="preview-frame"' in page
    assert f'src="/files/{name}"' in page


def test_unpreviewable_document_keeps_the_pane(client, upload):
    """A type the browser can't show still gets a pane, so nothing jumps."""
    name = upload("notes.txt", data=b"plain text")

    page = client.get(f"/chat/{name}").data.decode()

    assert "preview-empty" in page
    assert "No inline preview" in page
    # Still a working chat page underneath the placeholder.
    assert f'action="/chat/{name}"' in page


def test_chat_page_is_wider_than_the_document_list(client, upload):
    name = upload("quarterly-report.pdf")

    assert 'class="page-chat"' in client.get(f"/chat/{name}").data.decode()
    assert 'class="page-documents"' in client.get("/").data.decode()
