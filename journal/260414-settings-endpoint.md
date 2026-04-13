# Settings API endpoint

## What changed

Added `GET /api/settings` endpoint that returns the server's current
configuration with all secrets redacted. Returns grouped sections:
ocr (provider, model), transcription, embedding, chunking, and features.

This powers the new Settings & Health view in the webapp — the user can
see at a glance which OCR provider is active, what chunking strategy is
running, and whether mood scoring is enabled, without SSHing into the
server or reading docker-compose env files.

## Testing

2 new tests: response shape validation and a secrets-leak check that
asserts no API keys, bearer tokens, or secret prefixes appear in the
serialized response. Full suite: 765 passed.
