# 2026-04-27 — canonical_name validator + explicit edit-pipeline pushover lines

## Two changes shipped together

### 1. Edit-pipeline pushover wording

The previous edit-pipeline summary read `"2 entities, 5 mentions"`. On a typical edit
this surfaced as `"0 entities, 19 mentions"` because re-extraction over already-known
text usually creates zero NEW entity rows — every name matches an existing entity.
The user reasonably read `"0 entities"` as "extraction found nothing", which is the
opposite of what it means.

Each line in the save-entry pipeline summary now carries an explicit label:

```
Entry 76 updated
Reprocessed: 10 chunks
Entities created: 0
Entities deleted: 1
Total entities: 7
Mentions: 19
Mood scores: 7
```

To support `Entities deleted`, the orphan-cleanup count from
`EntityStore.delete_orphaned_entities` is now plumbed through `ExtractionResult`
(new `entities_deleted` field) and rolled up in the entity-extraction job summary.

`Total entities` is `entities_created + entities_matched` — i.e. the count attached
to the entry AFTER this extraction. Deleted entities are orphans that lost all
their mentions; they are not in the post-extraction set, so they are not subtracted
from the total.

The same explicit labels are used in the partial-failure breakdown body so users
read both formats consistently.

### 2. Post-LLM canonical_name validator

Background: in production we found entity #671 with `canonical_name="Nautilin"` whose
only mention quote contains `"Nautiline, the iOS app..."`. There is no truncation in
the Python code path — Claude returned the clipped string in its tool-call output,
both on initial extraction AND on the re-extraction that ran after a later edit.
Two extraction runs both produced the same clipped form, so orphan cleanup correctly
left the entity in place.

The naive substring check `canonical_name in quote` is *not* enough — `"Nautilin"` IS
a substring of `"Nautiline"`. The validator (`_repair_canonical_name` in
`src/journal/providers/extraction.py`) operates at the token level instead:

1. If any whitespace-separated token in the quote (after stripping surrounding
   punctuation) equals the canonical_name case-insensitively → trust as-is.
   Protects deliberately-shorter canonicals like `"Bob"` for a quote
   `"Robert 'Bob' Smith"`.
2. Else if canonical_name is a strict prefix of some longer token → repair to that
   token. Catches the Nautilin/Nautiline class of bug.
3. Else → keep, log a WARNING so the rate is visible.

Wired into `_parse_tool_response` so it applies to every extraction call. A WARNING
log is emitted on every repair so the failure rate is observable in production logs.

### `journal repair-entity-names` CLI

For cleaning up existing rows created before the validator shipped. Iterates every
entity, runs the same repair logic against each entity's mention quotes, proposes
updates. Dry-run by default; `--apply` to actually update. Skips proposed repairs
that would collide with another entity's canonical_name (for the same user) so
we never produce a duplicate row.

Plan: ship → run on prod via `ssh media docker exec journal-server uv run journal
repair-entity-names` → eyeball dry-run output → apply.

## Out of scope

1. **Re-prompting Claude on detected mismatches.** Doubles cost/latency for a rare
   failure mode the deterministic fix already handles.
2. **Tightening the extraction prompt.** Could be done eventually but: (a) hard to
   verify the change actually reduces the rate without telemetry; (b) the post-LLM
   validator is defense-in-depth that we want regardless.
3. **Auto-merge of near-duplicate entities** (e.g. a "Nautilin" row alongside a
   "Nautiline" row). The repair CLI logs and skips collisions in v1 — manual
   merge is a follow-up if it ever happens.

## Tests

- 8 new unit tests for `_repair_canonical_name` covering the substring-not-token-match
  case, repair, no-match, punctuation stripping, case-insensitivity, multi-token
  selection, empty inputs, and equal-length non-strict-prefix.
- 2 new integration tests for `_parse_tool_response` asserting that clipped names
  get repaired with a WARNING log, and unrepairable mismatches keep the LLM output
  but log a different WARNING.
- 3 new CLI integration tests for `repair-entity-names` covering dry-run output,
  apply path, and collision-skipping.
- All 1340 existing server tests still pass; lint clean.
