"""Bearer token authentication middleware for the REST API and MCP endpoint.

The middleware is applied once in `mcp_server.main()` and covers every
route on the Starlette app built by FastMCP — both `/api/*` custom routes
and the MCP streamable-HTTP transport at `/mcp`. Comparison is
constant-time to avoid leaking the token via timing. OPTIONS requests
are allowed through unauthenticated so CORS preflight works for the
webapp; every other method must present the bearer token.
"""

from __future__ import annotations

import hmac
import logging
from typing import TYPE_CHECKING

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from starlette.requests import Request
    from starlette.responses import Response

log = logging.getLogger(__name__)


class BearerTokenMiddleware(BaseHTTPMiddleware):
    """Reject any request whose Authorization header does not match the
    configured bearer token.

    Note: this middleware is intentionally NOT applied during unit tests
    that build their own Starlette test app directly from
    `FastMCP(...).streamable_http_app()` — the middleware is wired in
    `mcp_server.main()`, which the tests do not invoke. Tests that need
    to exercise the auth path install the middleware explicitly on a
    fresh test app (see `tests/test_auth.py`).

    An optional `exempt_paths` set lets specific paths bypass auth
    entirely — used for `/health` so a loopback operator can poll
    the endpoint without sharing the bearer token with whatever
    external process (docker healthcheck, shell cron, etc.) is
    doing the polling. The server binds to loopback only (see
    `docs/security.md`), so any caller that can reach `/health`
    already has a shell on the box. Exemption is path-matched
    exactly, not by prefix, to avoid accidentally exempting
    `/health/private` or similar.
    """

    def __init__(
        self,
        app: object,
        token: str,
        exempt_paths: set[str] | None = None,
    ) -> None:
        super().__init__(app)  # type: ignore[arg-type]
        if not token:
            raise ValueError(
                "BearerTokenMiddleware requires a non-empty token"
            )
        self._token = token
        self._exempt_paths: frozenset[str] = frozenset(exempt_paths or ())

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        # Allow CORS preflight requests through. The CORS middleware
        # (added after this one in the stack) handles OPTIONS itself and
        # does not need a token; forcing auth on preflight would break
        # the webapp before it ever gets to the real request.
        if request.method == "OPTIONS":
            return await call_next(request)

        # Exact-path exemptions (e.g. `/health`). Note: we match on
        # `request.url.path` so query strings do not affect the check.
        if request.url.path in self._exempt_paths:
            return await call_next(request)

        header = request.headers.get("authorization", "")
        if not header.startswith("Bearer "):
            log.warning(
                "Unauthorized %s %s — missing bearer token",
                request.method,
                request.url.path,
            )
            return JSONResponse(
                {
                    "error": "unauthorized",
                    "message": "Missing bearer token",
                },
                status_code=401,
            )

        provided = header.removeprefix("Bearer ").strip()
        if not hmac.compare_digest(provided, self._token):
            log.warning(
                "Unauthorized %s %s — invalid bearer token",
                request.method,
                request.url.path,
            )
            return JSONResponse(
                {
                    "error": "unauthorized",
                    "message": "Invalid bearer token",
                },
                status_code=401,
            )

        return await call_next(request)
