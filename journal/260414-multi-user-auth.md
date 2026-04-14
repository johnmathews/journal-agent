# Multi-User Authentication & Authorization

**Date:** 2026-04-14

## Summary

Converted the journal application from single-user (bearer token) to multi-user with
per-user data isolation, session-based web auth, and per-user API keys for MCP clients.

## Key Decisions

- **Server-side sessions** over JWT — simpler, instant revocation, negligible overhead for ~10 users
- **Per-user API keys** for MCP (like GitHub PATs) — all MCP clients support bearer tokens
- **Argon2id** for password hashing — current gold standard, via `argon2-cffi`
- **itsdangerous** for password reset / email verification tokens — signed, time-limited, no DB table
- **Simple schema** — password_hash on users table (no separate auth_credentials table)
- **Email as identifier** — no username field; display_name for human-friendly name
- **Registration toggle** — `REGISTRATION_ENABLED` env var controls self-service signup
- **Email verification required** before users can access the app

## Architecture

### Auth Middleware Stack

```
client → CORS → AuthenticationMiddleware → RequireAuthMiddleware → route
```

- `AuthenticationMiddleware` (Starlette built-in) populates `request.user` via `SessionOrKeyBackend`
- `RequireAuthMiddleware` (custom ASGI) enforces auth rules (401/403) on non-public paths
- `SessionOrKeyBackend` tries cookie first, then bearer token

### Data Scoping

- `user_id` added to `entries`, `entities`, `jobs` tables (migration 0011)
- Entity UNIQUE constraint: `(user_id, entity_type, canonical_name)`
- FTS5 scoped via JOIN: `AND e.user_id = ?`
- ChromaDB scoped via metadata filter: `where: {user_id: N}`

### New Tables

- `users` — user accounts
- `user_sessions` — server-side sessions (token as PK)
- `api_keys` — per-user API keys (SHA-256 hash stored, prefix for UI display)

## Files Changed

### New files (backend):
- `src/journal/db/migrations/0011_multi_tenant.sql`
- `src/journal/db/user_repository.py`
- `src/journal/db/chromadb_migration.py`
- `src/journal/services/auth.py`
- `src/journal/services/email.py`
- `src/journal/auth_api.py`

### Modified files (backend):
- `src/journal/auth.py` — complete rewrite (BearerTokenMiddleware → session/key auth)
- `src/journal/models.py` — added User, ApiKeyInfo
- `src/journal/config.py` — added auth/SMTP config fields
- `src/journal/db/repository.py` — user_id on create_entry
- `src/journal/entitystore/store.py` — user_id on create_entity
- `src/journal/mcp_server.py` — auth service init, middleware wiring, route registration
- `src/journal/cli.py` — migrate-chromadb command
- `pyproject.toml` — argon2-cffi, itsdangerous deps

### New files (frontend):
- `src/stores/auth.ts`
- `src/types/router.d.ts`
- `src/views/LoginView.vue`, `RegisterView.vue`, `ForgotPasswordView.vue`,
  `ResetPasswordView.vue`, `VerifyEmailView.vue`
- `src/views/ApiKeysView.vue`
- `src/views/admin/AdminLayout.vue`, `AdminDashboard.vue`

### Modified files (frontend):
- `src/api/client.ts` — cookie auth, 401 handling
- `src/router/index.ts` — auth routes, guards
- `src/App.vue` — conditional layout
- `src/components/layout/AppHeader.vue` — admin link, sign out
- `src/components/layout/AppSidebar.vue` — API keys, admin nav
- `src/main.ts` — unauthorized handler

## Test Coverage

- Backend: 969 tests (158 new), ruff clean
- Frontend: 787 tests (115 new), ESLint clean, all coverage thresholds met

## Post-Deployment Fixes

- **h11 LocalProtocolError on logout**: `JSONResponse(None, 204)` serialized "null" body conflicting
  with HTTP 204 zero-content-length. Changed logout/revoke to return 200 with `{"ok": true}`.
- **Auth store response envelope**: Backend returns `{"user": {...}}` but store assigned the whole
  response. Fixed to extract `resp.user`.
- **Admin dashboard field mismatch**: Frontend expected `entries_count`/`cost_estimate`/`last_activity`
  but backend returned `entry_count`/`job_count`/`last_entry_at`. Aligned frontend to backend.
- **Cross-join word count inflation**: Double LEFT JOIN on entries + jobs caused word_count to be
  multiplied by job count. Fixed with subquery aggregation.
- **Number formatting locale**: `toLocaleString()` used European locale (periods). Switched to
  explicit `en-US` (commas).
- **Cost estimation**: Added `cost_estimate` (all-time) and `cost_this_week` (7-day) columns to
  admin dashboard, computed from per-job-type approximate costs.
- **Bearer token cleanup**: Removed `40-journal-config.sh` token injection, `config.js`, and
  `JOURNAL_API_TOKEN` references from webapp docker-compose.
