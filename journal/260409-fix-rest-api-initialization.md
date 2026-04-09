# Fix REST API initialization and add deployment debugging logs

## Problem

After deploying journal-server, journal-webapp, and journal-chromadb, the webapp
showed "HTTP 404" when hitting `GET /api/entries`. Two root causes:

1. **Likely stale Docker image** — the Ansible-deployed image version may predate
   the commit that added `api.py` (REST API routes). The `journal_agent_version`
   Ansible variable needs updating to a SHA after `51eb612`.

2. **Services not initialized for REST API** — even with the correct image, the
   REST API would return 503 "Server not initialized" on startup. Services were
   only initialized inside the MCP session lifespan (triggered by the first MCP
   client connection). REST API requests arriving before any MCP session would
   get a 503.

## Fix

Extracted service initialization from the MCP lifespan into `_init_services()`,
a standalone idempotent function. This is called eagerly in `main()` at server
startup, before the HTTP server starts accepting requests. The MCP lifespan
still calls the same function but it's a no-op on subsequent calls.

## Debugging logs

Added structured logging at startup and per-request:

- **Startup**: DB path, ChromaDB host/port, provider models, entry count,
  all registered HTTP routes (path + methods)
- **REST API requests**: entry counts returned, specific entry details, errors

These make it immediately obvious in `docker logs` if routes are missing,
services failed to connect, or requests are being handled incorrectly.

## Verified

- 158 tests pass, 73% coverage
- Lint clean (ruff)
- Local route registration confirmed: `/api/entries`, `/api/entries/{id}`,
  `/api/stats` all appear in the Starlette app routes
