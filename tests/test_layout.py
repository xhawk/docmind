"""Both HTML pages share one shell and one stylesheet.

These are the guards against the two pages drifting apart again: styles live
in static/app.css, never in a template.
"""

import pytest


@pytest.fixture(params=["documents", "chat"])
def page(request, client, document):
    """Each of the app's two pages, rendered."""
    path = "/" if request.param == "documents" else f"/chat/{document}"
    return client.get(path).data.decode()


def test_page_links_the_shared_stylesheet(page):
    assert page.count('href="/static/app.css"') == 1
    # Styling belongs in the stylesheet, so neither page carries its own.
    assert "<style" not in page


def test_page_renders_the_shared_masthead(page):
    assert '<header class="masthead">' in page
    assert ">Documents</a>" in page
    # base.html supplies the document skeleton, not the individual pages.
    assert page.lstrip().startswith("<!doctype html>")
    assert '<meta name="viewport"' in page


def test_nav_marks_the_document_list_only_when_you_are_on_it(client, document):
    assert 'aria-current="page">Documents</a>' in client.get("/").data.decode()
    assert 'aria-current="page"' not in client.get(f"/chat/{document}").data.decode()


def test_stylesheet_is_served(client):
    response = client.get("/static/app.css")

    assert response.status_code == 200
    assert response.mimetype == "text/css"
    # The reset and the shared tokens are both in there.
    body = response.data.decode()
    assert "box-sizing: border-box" in body
    assert "--measure" in body
