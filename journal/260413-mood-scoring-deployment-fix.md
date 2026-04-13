# Mood scoring deployment fix

**Date:** 2026-04-13

## What happened

Investigated the mood/emotion tracking feature status. Found that:

1. The entire mood scoring backend (T1.3b.i–vi, viii) was already shipped.
2. The frontend mood chart (T1.3b.vii) was also already fully implemented in
   `DashboardView.vue` — the roadmap just hadn't been updated to mark it done.
3. The journal stack runs on the **media VM** (not infra).
4. `JOURNAL_ENABLE_MOOD_SCORING` was not set, so scoring was disabled.

## Deployment fix

Enabled mood scoring on the media VM and hit a crash loop:
`config/mood-dimensions.toml` was missing from the Docker image. The Dockerfile
only copied `src/` but not `config/`, so the server started, saw mood scoring
enabled, tried to load the TOML config, and crashed with `FileNotFoundError`.

**Fix:** Added `COPY config/ config/` to the Dockerfile so future image builds
include the mood dimensions config. Also added a volume mount in the Ansible
compose template so the host-managed TOML file overrides the image's built-in
copy (allows editing dimensions without rebuilding the image).

## Backfill

Ran `journal backfill-mood` inside the container on the media VM. First run hit
3x Anthropic 529 (overloaded) errors; second run picked up the remaining 3
entries. Final result: 42 mood scores (6 entries × 7 dimensions).

Dimension averages across the corpus:
- agency: 0.32
- anxiety_eagerness: -0.33
- comfort_discomfort: -0.40
- energy_fatigue: -0.32
- fulfillment: 0.22
- joy_sadness: -0.32
- proactive_reactive: -0.13

## Files changed

- `Dockerfile` — added `COPY config/ config/`
- (Ansible) `docker-compose.yml.j2` — added mood-dimensions.toml volume mount
- (Ansible) `mood-dimensions.toml` — created in Ansible templates directory
