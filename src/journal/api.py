"""REST API endpoints for the journal webapp."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from typing import TYPE_CHECKING, Any

import tiktoken
from starlette.responses import JSONResponse

# Cache the encoding at module load — tiktoken.get_encoding is not free
# and the tokens endpoint may be called repeatedly as the user switches
# overlays. cl100k_base matches text-embedding-3-large, which is the
# embedding model the chunker's token counts are computed against.
_TOKEN_ENCODING_NAME = "cl100k_base"
_TOKEN_MODEL_HINT = "text-embedding-3-large"
_token_encoder = tiktoken.get_encoding(_TOKEN_ENCODING_NAME)

if TYPE_CHECKING:
    from collections.abc import Callable

    from mcp.server.fastmcp import FastMCP
    from starlette.requests import Request

    from journal.services.ingestion import IngestionService
    from journal.services.query import QueryService

log = logging.getLogger(__name__)


def _entry_to_dict(entry: Any, page_count: int = 0) -> dict[str, Any]:
    """Convert an Entry to a JSON-serializable dict."""
    return {
        "id": entry.id,
        "entry_date": entry.entry_date,
        "source_type": entry.source_type,
        "raw_text": entry.raw_text,
        "final_text": entry.final_text,
        "word_count": entry.word_count,
        "chunk_count": entry.chunk_count,
        "page_count": page_count,
        "language": entry.language,
        "created_at": entry.created_at,
        "updated_at": entry.updated_at,
    }


def _entry_summary(entry: Any, page_count: int = 0) -> dict[str, Any]:
    """Convert an Entry to a summary dict (no text fields)."""
    return {
        "id": entry.id,
        "entry_date": entry.entry_date,
        "source_type": entry.source_type,
        "word_count": entry.word_count,
        "chunk_count": entry.chunk_count,
        "page_count": page_count,
        "created_at": entry.created_at,
    }


def register_api_routes(
    mcp: FastMCP,
    services_getter: Callable[[], dict | None],
) -> None:
    """Register REST API routes on the MCP server.

    Args:
        mcp: The FastMCP instance.
        services_getter: A callable that returns the services dict
            (with 'query' and 'ingestion' keys).
    """

    @mcp.custom_route("/api/entries", methods=["GET"], name="api_list_entries")
    async def list_entries(request: Request) -> JSONResponse:
        """List journal entries with pagination and optional date filtering."""
        services = services_getter()
        if services is None:
            log.error("GET /api/entries — services not initialized")
            return JSONResponse(
                {"error": "Server not initialized"}, status_code=503
            )

        query_svc: QueryService = services["query"]

        # Parse query params
        start_date = request.query_params.get("start_date")
        end_date = request.query_params.get("end_date")
        try:
            limit = min(int(request.query_params.get("limit", "20")), 100)
        except ValueError:
            limit = 20
        try:
            offset = max(int(request.query_params.get("offset", "0")), 0)
        except ValueError:
            offset = 0

        entries = query_svc.list_entries(start_date, end_date, limit, offset)
        total = query_svc._repo.count_entries(start_date, end_date)

        items = []
        for entry in entries:
            page_count = query_svc._repo.get_page_count(entry.id)
            items.append(_entry_summary(entry, page_count))

        log.info("GET /api/entries — returned %d/%d entries (offset=%d)", len(items), total, offset)
        return JSONResponse({
            "items": items,
            "total": total,
            "limit": limit,
            "offset": offset,
        })

    @mcp.custom_route(
        "/api/entries/{entry_id:int}",
        methods=["GET", "PATCH", "DELETE"],
        name="api_entry_detail",
    )
    async def entry_detail(request: Request) -> JSONResponse:
        """Get, update, or delete a single journal entry."""
        services = services_getter()
        if services is None:
            return JSONResponse(
                {"error": "Server not initialized"}, status_code=503
            )

        entry_id = int(request.path_params["entry_id"])

        if request.method == "GET":
            return await _get_entry(services, entry_id)
        elif request.method == "PATCH":
            return await _patch_entry(request, services, entry_id)
        elif request.method == "DELETE":
            return await _delete_entry(services, entry_id)
        else:
            return JSONResponse(
                {"error": "Method not allowed"}, status_code=405
            )

    async def _get_entry(services: dict, entry_id: int) -> JSONResponse:
        query_svc: QueryService = services["query"]
        entry = query_svc._repo.get_entry(entry_id)
        if entry is None:
            log.warning("GET /api/entries/%d — not found", entry_id)
            return JSONResponse(
                {"error": f"Entry {entry_id} not found"}, status_code=404
            )
        page_count = query_svc._repo.get_page_count(entry_id)
        log.info("GET /api/entries/%d — %s, %d words", entry_id, entry.entry_date, entry.word_count)
        return JSONResponse(_entry_to_dict(entry, page_count))

    async def _patch_entry(
        request: Request, services: dict, entry_id: int
    ) -> JSONResponse:
        query_svc: QueryService = services["query"]
        ingestion_svc: IngestionService = services["ingestion"]

        # Verify entry exists
        entry = query_svc._repo.get_entry(entry_id)
        if entry is None:
            return JSONResponse(
                {"error": f"Entry {entry_id} not found"}, status_code=404
            )

        # Parse request body
        try:
            body = await request.json()
        except (json.JSONDecodeError, ValueError):
            return JSONResponse(
                {"error": "Invalid JSON body"}, status_code=400
            )

        final_text = body.get("final_text")
        if final_text is None or not isinstance(final_text, str):
            return JSONResponse(
                {"error": "'final_text' is required and must be a string"},
                status_code=400,
            )

        if not final_text.strip():
            return JSONResponse(
                {"error": "'final_text' must not be empty"},
                status_code=400,
            )

        try:
            updated = ingestion_svc.update_entry_text(entry_id, final_text)
        except ValueError as e:
            log.warning("PATCH /api/entries/%d — error: %s", entry_id, e)
            return JSONResponse({"error": str(e)}, status_code=400)

        page_count = query_svc._repo.get_page_count(entry_id)
        log.info("PATCH /api/entries/%d — updated, %d words", entry_id, updated.word_count)
        return JSONResponse(_entry_to_dict(updated, page_count))

    async def _delete_entry(services: dict, entry_id: int) -> JSONResponse:
        ingestion_svc: IngestionService = services["ingestion"]
        deleted = ingestion_svc.delete_entry(entry_id)
        if not deleted:
            log.warning("DELETE /api/entries/%d — not found", entry_id)
            return JSONResponse(
                {"error": f"Entry {entry_id} not found"}, status_code=404
            )
        log.info("DELETE /api/entries/%d — deleted", entry_id)
        return JSONResponse({"deleted": True, "id": entry_id})

    @mcp.custom_route(
        "/api/entries/{entry_id:int}/chunks",
        methods=["GET"],
        name="api_entry_chunks",
    )
    async def entry_chunks(request: Request) -> JSONResponse:
        """Return the persisted chunks for an entry, with source offsets.

        Used by the webapp overlay to draw chunk boundaries on top of
        the entry text. The 404 `chunks_not_backfilled` response is
        distinguished from `entry_not_found` so the webapp can surface
        a clear message telling the user to re-ingest or run backfill.
        """
        services = services_getter()
        if services is None:
            return JSONResponse(
                {"error": "Server not initialized"}, status_code=503
            )

        query_svc: QueryService = services["query"]
        entry_id = int(request.path_params["entry_id"])

        entry = query_svc._repo.get_entry(entry_id)
        if entry is None:
            log.warning("GET /api/entries/%d/chunks — entry not found", entry_id)
            return JSONResponse(
                {
                    "error": "entry_not_found",
                    "message": f"Entry {entry_id} not found",
                },
                status_code=404,
            )

        chunks = query_svc._repo.get_chunks(entry_id)
        if not chunks:
            log.info(
                "GET /api/entries/%d/chunks — no chunks persisted (pre-backfill entry)",
                entry_id,
            )
            return JSONResponse(
                {
                    "error": "chunks_not_backfilled",
                    "message": (
                        "This entry was ingested before chunk persistence was "
                        "available. Re-ingest the entry or run the backfill "
                        "service to populate chunks."
                    ),
                },
                status_code=404,
            )

        payload = {
            "entry_id": entry_id,
            "chunks": [
                {
                    "index": i,
                    "text": c.text,
                    "char_start": c.char_start,
                    "char_end": c.char_end,
                    "token_count": c.token_count,
                }
                for i, c in enumerate(chunks)
            ],
        }
        log.info("GET /api/entries/%d/chunks — %d chunks", entry_id, len(chunks))
        return JSONResponse(payload)

    @mcp.custom_route(
        "/api/entries/{entry_id:int}/tokens",
        methods=["GET"],
        name="api_entry_tokens",
    )
    async def entry_tokens(request: Request) -> JSONResponse:
        """Tokenise an entry's text on demand using tiktoken `cl100k_base`.

        Returns per-token `{index, token_id, text, char_start, char_end}`
        where the character offsets are positions in `final_text` (or
        `raw_text` as fallback). Valid UTF-8 text round-trips through
        tiktoken exactly, so the offsets slice the original text without
        any loss. Computed per request — the call is cheap (< 10 ms for
        journal-scale text) and avoids any cache invalidation logic
        when `final_text` is edited.
        """
        services = services_getter()
        if services is None:
            return JSONResponse(
                {"error": "Server not initialized"}, status_code=503
            )

        query_svc: QueryService = services["query"]
        entry_id = int(request.path_params["entry_id"])

        entry = query_svc._repo.get_entry(entry_id)
        if entry is None:
            log.warning("GET /api/entries/%d/tokens — entry not found", entry_id)
            return JSONResponse(
                {
                    "error": "entry_not_found",
                    "message": f"Entry {entry_id} not found",
                },
                status_code=404,
            )

        text = entry.final_text or entry.raw_text or ""
        token_ids = _token_encoder.encode(text)
        # `decode_with_offsets` returns (decoded_str, offsets) where each
        # offset is the character index in the decoded string where the
        # corresponding token begins. For valid UTF-8 input the decoded
        # string equals the input, so these offsets are positions in the
        # original text the webapp will render.
        decoded, starts = _token_encoder.decode_with_offsets(token_ids)
        tokens: list[dict[str, Any]] = []
        for i, (tid, start) in enumerate(zip(token_ids, starts, strict=True)):
            end = starts[i + 1] if i + 1 < len(starts) else len(decoded)
            tokens.append(
                {
                    "index": i,
                    "token_id": int(tid),
                    "text": decoded[start:end],
                    "char_start": int(start),
                    "char_end": int(end),
                }
            )

        log.info(
            "GET /api/entries/%d/tokens — %d tokens", entry_id, len(tokens)
        )
        return JSONResponse(
            {
                "entry_id": entry_id,
                "encoding": _TOKEN_ENCODING_NAME,
                "model_hint": _TOKEN_MODEL_HINT,
                "token_count": len(tokens),
                "tokens": tokens,
            }
        )

    @mcp.custom_route("/api/stats", methods=["GET"], name="api_stats")
    async def get_stats(request: Request) -> JSONResponse:
        """Get journal statistics."""
        services = services_getter()
        if services is None:
            return JSONResponse(
                {"error": "Server not initialized"}, status_code=503
            )

        query_svc: QueryService = services["query"]

        start_date = request.query_params.get("start_date")
        end_date = request.query_params.get("end_date")

        stats = query_svc.get_statistics(start_date, end_date)
        log.info("GET /api/stats — %d entries, %d words", stats.total_entries, stats.total_words)
        return JSONResponse(asdict(stats))
