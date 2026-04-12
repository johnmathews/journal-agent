# Entity quotes in entry-entities API response

## Problem

The `GET /api/entries/{id}/entities` endpoint returned only the `canonical_name`
for each entity, but the frontend needed the verbatim `quote` text from
`entity_mentions` to accurately highlight where entities appear in the entry
text. The canonical name often differs from the actual text (e.g., canonical
"prayer" when the entry says "quiet reflection and contemplation").

## Changes

- Extended `_entity_summary()` to accept an optional `quotes: list[str]` param.
- Modified the `entry_entities` endpoint to build a deduplicated `quotes` list
  per entity from the existing `entity_mentions` rows (which were already being
  fetched for counting) and pass them through.
- Added `entity_store` (SQLiteEntityStore) to the test API services fixture so
  entity endpoint tests can seed real entity/mention data.
- Added 2 tests: `test_entry_entities_includes_quotes` and
  `test_entry_entities_deduplicates_quotes`.
- Updated `docs/api.md` to document the new `quotes` field in the response.

## Response shape change

```json
{
  "id": 5,
  "canonical_name": "prayer",
  "entity_type": "topic",
  "quotes": ["quiet reflection and contemplation", "prayer"],
  "mention_count": 2,
  ...
}
```

The `quotes` array is only present in the entry-scoped endpoint, not in the
global entity list endpoint.
