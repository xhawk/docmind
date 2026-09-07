"""The /api/v1 surface has no resources yet, but it does have conventions."""

import anthropic
import httpx2 as httpx
import pytest

from docmind import api

# Registered at import time, before any app is built, so it lands in the
# blueprint every test app gets. Test-only: it exists to prove that an
# exception raised inside an API route comes back as JSON.
UPSTREAM_ERRORS = {
    "auth": anthropic.AuthenticationError,
    "rate_limited": anthropic.RateLimitError,
}


@api.bp.get("/boom/<kind>")
def _boom(kind: str):
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    status = {"auth": 401, "rate_limited": 429}[kind]
    raise UPSTREAM_ERRORS[kind](
        "upstream said no", response=httpx.Response(status, request=request), body=None
    )


def test_unknown_api_path_answers_json_not_html(client):
    response = client.get("/api/v1/nope")

    assert response.status_code == 404
    assert response.mimetype == "application/json"
    assert response.get_json() == {
        "error": {"code": "not_found", "message": response.get_json()["error"]["message"]}
    }
    assert b"<!doctype" not in response.data.lower()


def test_unknown_page_outside_the_api_still_answers_html(client):
    """The JSON handler is blueprint-scoped, so the HTML surface is untouched."""
    response = client.get("/nope")

    assert response.status_code == 404
    assert response.mimetype == "text/html"


@pytest.mark.parametrize(
    ("kind", "expected_status"),
    [("auth", 502), ("rate_limited", 429)],
)
def test_upstream_claude_failure_becomes_json(client, kind, expected_status):
    response = client.get(f"/api/v1/boom/{kind}")

    assert response.status_code == expected_status
    assert response.mimetype == "application/json"
    body = response.get_json()
    assert body["error"]["code"] == kind
    assert body["error"]["message"]


def test_api_prefix_is_versioned():
    from docmind.app import API_PREFIX

    assert API_PREFIX == "/api/v1"


def test_web_and_api_routes_do_not_overlap(app):
    web_rules = {r.rule for r in app.url_map.iter_rules() if r.endpoint.startswith("web.")}
    api_rules = {r.rule for r in app.url_map.iter_rules() if r.endpoint.startswith("api.")}

    assert web_rules == {
        "/",
        "/upload",
        "/files/<name>",
        "/chat/<name>",
        "/chat/new",
    }
    assert all(rule.startswith("/api/v1") for rule in api_rules)
    # /healthz belongs to neither surface.
    assert "/healthz" not in web_rules | api_rules
