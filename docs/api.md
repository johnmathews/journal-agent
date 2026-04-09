# MCP Tool Reference

The journal MCP server exposes 8 tools via streamable HTTP transport.

## Query Tools

### journal_search_entries

Semantic similarity search across journal entries.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `query` | string | yes | | Natural language query |
| `start_date` | string | no | | Filter from date (ISO 8601) |
| `end_date` | string | no | | Filter until date (ISO 8601) |
| `limit` | int | no | 10 | Max results (1-50) |
| `offset` | int | no | 0 | Pagination offset |

### journal_get_entries_by_date

Get all entries for a specific date.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `date` | string | yes | Date in ISO 8601 format |

### journal_list_entries

List entries in reverse chronological order.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `start_date` | string | no | | Filter from date |
| `end_date` | string | no | | Filter until date |
| `limit` | int | no | 20 | Max results (1-50) |
| `offset` | int | no | 0 | Pagination offset |

## Statistics Tools

### journal_get_statistics

Get journal statistics: entry count, frequency, word counts, date range.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `start_date` | string | no | all time | Start of period |
| `end_date` | string | no | today | End of period |

### journal_get_mood_trends

Analyze mood trends over time.

| Parameter | Type | Required | Default | Description |
|-----------|------|----------|---------|-------------|
| `start_date` | string | no | | Start of period |
| `end_date` | string | no | | End of period |
| `granularity` | string | no | "week" | "day", "week", or "month" |

### journal_get_topic_frequency

Count how often a topic, person, or place appears.

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `topic` | string | yes | Topic to search for |
| `start_date` | string | no | Start of period |
| `end_date` | string | no | End of period |

## Ingestion Tools

### journal_ingest_from_url

Ingest a journal entry by downloading an image or voice note from a URL. This is the
preferred ingestion method for MCP clients like Nanoclaw, since it avoids base64-encoding
large files as tool parameters.

| Parameter     | Type   | Required | Default | Description                                       |
|---------------|--------|----------|---------|---------------------------------------------------|
| `source_type` | string | yes      |         | "image" or "voice"                                |
| `url`         | string | yes      |         | URL to download the file from                     |
| `media_type`  | string | no       |         | MIME type override (inferred from response header) |
| `date`        | string | no       | today   | Entry date (ISO 8601)                             |
| `language`    | string | no       | "en"    | Language for voice transcription                  |

**Slack file URLs** (`files.slack.com`) are automatically authenticated using the
`SLACK_BOT_TOKEN` environment variable. No auth headers needed in the tool call — just
pass the raw `url_private` or `url_private_download` URL from Slack.

For other URLs, the server makes a plain HTTP GET with no authentication. The URL must be
accessible from the journal server's network.

### journal_ingest_entry

Ingest a journal entry from base64-encoded data. Use `journal_ingest_from_url` instead when
the file is available at a URL — this avoids MCP tool parameter size limits.

| Parameter     | Type   | Required | Default | Description                          |
|---------------|--------|----------|---------|--------------------------------------|
| `source_type` | string | yes      |         | "image" or "voice"                   |
| `data_base64` | string | yes      |         | Base64-encoded file data             |
| `media_type`  | string | yes      |         | MIME type (e.g. "image/jpeg")        |
| `date`        | string | no       | today   | Entry date (ISO 8601)                |
| `language`    | string | no       | "en"    | Language for voice transcription     |

## Transport

- **Protocol**: Streamable HTTP (MCP spec 2025-03-26)
- **Default endpoint**: `http://localhost:8400/mcp`
- **Docker Compose**: `http://journal:8400/mcp` (internal service name)

### Direct HTTP Calls

MCP clients normally handle the session protocol automatically. If calling directly (e.g.,
via curl), the streamable HTTP transport requires a session handshake:

1. **Initialize** — `POST /mcp` with the MCP `initialize` request. The response includes an
   `mcp-session-id` header.
2. **Call tools** — `POST /mcp` with headers:
   - `Content-Type: application/json`
   - `Accept: application/json, text/event-stream`
   - `Mcp-Session-Id: <id from step 1>`
