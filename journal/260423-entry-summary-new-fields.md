# Entry Summary New Fields

## What changed

Extended the `GET /api/entries` list endpoint to return three new fields
in each entry summary:

- `language` — entry language (from Entry model, default "en")
- `updated_at` — last modification timestamp
- `entity_mention_count` — count of entity mentions for the entry

## Implementation

Added `get_entity_mention_count(entry_id)` to the repository Protocol and
SQLite implementation. The query is a simple `COUNT(*) FROM
entity_mentions WHERE entry_id = ?`, following the same per-entry pattern
as `get_page_count` and `get_uncertain_span_count`.

The `_entry_summary` helper was updated to include `language` and
`updated_at` (read directly from the Entry model) and the new
`entity_mention_count` (computed per-entry in the list endpoint loop).

## Motivation

The journal webapp's entries table needed these fields for new columns:
Language (uppercase ISO code), Modified (when the entry was last edited),
and Entities (how many entities were extracted). All three columns are
hidden by default but can be toggled on by the user.
