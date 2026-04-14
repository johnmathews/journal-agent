"""Backfill user_id into ChromaDB vector metadata for multi-tenant migration.

This script adds ``user_id: 1`` (the admin user) to all existing ChromaDB
documents that don't already have a ``user_id`` in their metadata. It is
idempotent — safe to run multiple times. Documents that already have
``user_id`` are skipped.

Run via CLI::

    uv run journal migrate-chromadb [--host localhost] [--port 8000] [--collection journal_entries]

Or programmatically::

    from journal.db.chromadb_migration import backfill_user_id
    backfill_user_id("localhost", 8000, "journal_entries", admin_user_id=1)
"""

from __future__ import annotations

import logging

import chromadb

log = logging.getLogger(__name__)


def backfill_user_id(
    host: str,
    port: int,
    collection_name: str,
    admin_user_id: int = 1,
    batch_size: int = 100,
) -> int:
    """Add ``user_id`` to all ChromaDB documents missing it.

    Returns the number of documents updated.
    """
    client = chromadb.HttpClient(host=host, port=port)
    collection = client.get_or_create_collection(
        name=collection_name,
        metadata={"hnsw:space": "cosine"},
    )

    total_count = collection.count()
    if total_count == 0:
        log.info("Collection '%s' is empty — nothing to migrate", collection_name)
        return 0

    log.info(
        "Scanning %d documents in '%s' for missing user_id...",
        total_count,
        collection_name,
    )

    updated = 0
    offset = 0

    while offset < total_count:
        results = collection.get(
            include=["metadatas"],
            limit=batch_size,
            offset=offset,
        )

        if not results["ids"]:
            break

        ids_to_update: list[str] = []
        metadatas_to_update: list[dict] = []

        for doc_id, metadata in zip(results["ids"], results["metadatas"], strict=True):
            if metadata is None:
                metadata = {}
            if "user_id" not in metadata:
                ids_to_update.append(doc_id)
                metadatas_to_update.append({**metadata, "user_id": admin_user_id})

        if ids_to_update:
            collection.update(ids=ids_to_update, metadatas=metadatas_to_update)
            updated += len(ids_to_update)
            log.info(
                "  Updated %d documents (batch at offset %d)",
                len(ids_to_update),
                offset,
            )

        offset += len(results["ids"])

    log.info(
        "ChromaDB migration complete: %d of %d documents updated",
        updated,
        total_count,
    )
    return updated
