# Authentication & Multi-User Architecture

The journal server supports multiple users with per-user data isolation. Two authentication
mechanisms are supported:

1. **Cookie sessions** — used by the web frontend (httpOnly, Secure, SameSite=Lax)
2. **API keys (bearer tokens)** — used by MCP clients and external API consumers

## User Model

Each user has:

- `email` — unique identifier (case-insensitive), used for login
- `display_name` — human-friendly name shown in the UI
- `password_hash` — Argon2id hash (NULL for social auth users, future)
- `is_admin` — admin flag (can manage other users)
- `is_active` — account enabled/disabled by admin
- `email_verified` — must be true before the user can access the app

## Registration Flow

1. User submits email, password, display_name to `POST /api/auth/register`
2. Server creates user with `email_verified=false`
3. Server sends verification email with a signed token (24h expiry)
4. User clicks link → `GET /api/auth/verify-email?token=...`
5. Server sets `email_verified=true`, user can now access the app

Registration is controlled by the `REGISTRATION_ENABLED` environment variable (default: `false`).
The frontend checks `GET /api/auth/config` to show/hide the registration link.

## Login Flow (Web)

1. User submits email + password to `POST /api/auth/login`
2. Server verifies via Argon2id, creates a session row in `user_sessions`
3. Response includes `Set-Cookie: session_id=<token>; HttpOnly; Secure; SameSite=Lax`
4. All subsequent requests automatically include the cookie
5. Sessions expire after 7 days (configurable via `SESSION_EXPIRY_DAYS`)

## Login Flow (MCP / API)

1. User generates an API key via the web UI (`POST /api/auth/api-keys`)
2. Key is shown once (format: `jnl_<random>`)
3. User adds the key to their MCP client config:
   ```json
   {
     "mcpServers": {
       "journal": {
         "url": "https://journal.example.com/mcp",
         "headers": {
           "Authorization": "Bearer jnl_..."
         }
       }
     }
   }
   ```
4. Server validates the key by SHA-256 hashing and looking up in `api_keys`

## Account Lockout

After 5 consecutive failed login attempts, the account is locked for 15 minutes. The counter
resets on successful login.

## Password Reset

1. User submits email to `POST /api/auth/forgot-password` (always returns 200)
2. If email exists, server sends a reset email with a signed token (30 min expiry)
3. User clicks link → `/reset-password?token=...`
4. User submits new password to `POST /api/auth/reset-password`

## Data Isolation

All user-generated data is scoped by `user_id`:

- **entries** — `user_id` column, all queries filtered
- **entities** — `user_id` column, UNIQUE constraint includes user_id
- **jobs** — `user_id` column, admins can see all
- **ChromaDB vectors** — `user_id` in metadata, filtered via `where` clause
- **FTS5 search** — scoped via JOIN with entries table

A user can never see another user's data through any API endpoint or MCP tool.

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `JOURNAL_SECRET_KEY` | (required) | Secret for signing session tokens and reset URLs |
| `REGISTRATION_ENABLED` | `false` | Allow self-service registration |
| `SESSION_EXPIRY_DAYS` | `7` | Session lifetime in days |
| `SMTP_HOST` | `smtp.gmail.com` | SMTP server for sending emails |
| `SMTP_PORT` | `465` | SMTP port (SSL) |
| `SMTP_USERNAME` | (empty) | SMTP username |
| `SMTP_PASSWORD` | (empty) | SMTP password (Gmail App Password) |
| `SMTP_FROM_EMAIL` | (empty) | From address for system emails |
| `APP_BASE_URL` | `http://localhost:5173` | Base URL for email links |

## Admin Panel

Admin users (`is_admin=true`) can:

- View all users with stats (entry count, word count, job count)
- Enable/disable user accounts
- Promote/demote admin status

Access the admin panel via `/admin` in the web UI (link visible only to admins).
