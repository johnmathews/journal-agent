-- Migration 0012: Clear plaintext session tokens
--
-- Session tokens were previously stored as plaintext in user_sessions.id.
-- The AuthService now stores SHA-256 hashes instead (mirroring the API key
-- pattern). Existing plaintext sessions are invalidated — users must log
-- in again. With only one active user pre-launch, this is acceptable.

DELETE FROM user_sessions;
