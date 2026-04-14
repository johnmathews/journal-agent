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

- Backend: 968 tests (157 new), ruff clean
- Frontend: 672+ tests, ESLint clean, builds successfully

## Remaining Work

- Deploy with `JOURNAL_SECRET_KEY` and SMTP credentials
- Set admin user email/password via first login or CLI
- Run `uv run journal migrate-chromadb` on existing deployments
- Full user-scoping of all query methods (currently create_entry and create_entity have user_id;
  remaining query methods pass through with default user_id=1 until auth is enforced in production)
