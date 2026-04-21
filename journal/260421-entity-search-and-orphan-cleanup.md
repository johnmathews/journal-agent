# Entity search fix and orphan cleanup on entry deletion

**Date:** 2026-04-21

## Entity search across all pages

The `GET /api/entities?search=...` endpoint was applying the search filter in-memory after
`LIMIT`/`OFFSET`, so it only searched the current page of results (50 entities). Moved the
filter into SQL using `LIKE` on `canonical_name` and an `EXISTS` subquery on `entity_aliases`,
so search now runs before pagination. The `total` count also reflects filtered results, fixing
pagination controls.

## Orphan entity cleanup on entry deletion

When an entry was deleted, `ON DELETE CASCADE` removed `entity_mentions` rows but left the
parent `entities` records behind with zero mentions. The delete handler now snapshots entity IDs
before deletion, then calls `delete_orphaned_entities()` to prune any that lost all mentions.

The re-extraction path (edit → entity extraction job) already handled this via
`extract_from_entry()`. Only the delete path was missing cleanup.

Found and removed 8 orphaned entities in production, including hallucinated places like
"Het Groenschap" that the extraction LLM invented without any corresponding text in entries.

## Files changed

- `src/journal/api.py` — entity search params passed to store; orphan cleanup in delete handler
- `src/journal/entitystore/store.py` — added `search` param to `list_entities_with_mention_counts`
  and `count_entities` (Protocol + SQLite impl)
- `tests/test_api.py` — two new tests: orphan pruned on delete, entity preserved when mentioned
  elsewhere
- `docs/entity-tracking.md` — new "Entity lifecycle and orphan cleanup" section
