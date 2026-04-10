"""Tests for the bearer-token authentication middleware."""

from __future__ import annotations

import pytest
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from journal.auth import BearerTokenMiddleware


def _build_app(token: str) -> TestClient:
    async def ok(_request: object) -> JSONResponse:
        return JSONResponse({"ok": True})

    async def patch_ok(_request: object) -> JSONResponse:
        return JSONResponse({"patched": True})

    app = Starlette(
        routes=[
            Route("/api/entries", ok, methods=["GET", "OPTIONS"]),
            Route("/api/entries/1", patch_ok, methods=["PATCH"]),
            Route("/mcp", ok, methods=["POST"]),
        ]
    )
    app.add_middleware(BearerTokenMiddleware, token=token)
    return TestClient(app, raise_server_exceptions=False)


class TestBearerTokenMiddleware:
    def test_rejects_missing_token(self) -> None:
        client = _build_app("secret-abc")
        resp = client.get("/api/entries")
        assert resp.status_code == 401
        body = resp.json()
        assert body["error"] == "unauthorized"
        assert "Missing" in body["message"]

    def test_rejects_wrong_token(self) -> None:
        client = _build_app("secret-abc")
        resp = client.get(
            "/api/entries",
            headers={"Authorization": "Bearer wrong-token"},
        )
        assert resp.status_code == 401
        body = resp.json()
        assert body["error"] == "unauthorized"
        assert "Invalid" in body["message"]

    def test_rejects_non_bearer_scheme(self) -> None:
        client = _build_app("secret-abc")
        # Basic auth must not be accepted.
        resp = client.get(
            "/api/entries",
            headers={"Authorization": "Basic c2VjcmV0LWFiYw=="},
        )
        assert resp.status_code == 401

    def test_accepts_correct_token(self) -> None:
        client = _build_app("secret-abc")
        resp = client.get(
            "/api/entries",
            headers={"Authorization": "Bearer secret-abc"},
        )
        assert resp.status_code == 200
        assert resp.json() == {"ok": True}

    def test_allows_options_without_token(self) -> None:
        # CORS preflight must succeed without auth so the webapp can
        # negotiate the real call.
        client = _build_app("secret-abc")
        resp = client.options("/api/entries")
        assert resp.status_code in (200, 204)

    def test_protects_mcp_path(self) -> None:
        client = _build_app("secret-abc")
        resp = client.post("/mcp")
        assert resp.status_code == 401

    def test_protects_patch(self) -> None:
        client = _build_app("secret-abc")
        resp = client.patch("/api/entries/1", json={"final_text": "new"})
        assert resp.status_code == 401

    def test_token_with_trailing_whitespace(self) -> None:
        # Clients sometimes wrap headers with accidental whitespace —
        # the middleware strips the Bearer prefix and trailing spaces.
        client = _build_app("secret-abc")
        resp = client.get(
            "/api/entries",
            headers={"Authorization": "Bearer secret-abc  "},
        )
        assert resp.status_code == 200

    def test_refuses_empty_token_at_init(self) -> None:
        from starlette.applications import Starlette

        with pytest.raises(ValueError, match="non-empty token"):
            BearerTokenMiddleware(Starlette(), token="")
