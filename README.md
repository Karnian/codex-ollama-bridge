# codex2ollama bridge

This project exposes Ollama-like HTTP endpoints and routes requests to local `codex`.

## Endpoints

- `POST /api/chat`
- `POST /api/generate`
- `GET /api/tags`
- `GET /healthz`

Default port is `11435` (customizable with `BRIDGE_PORT`).

By default the bridge uses your Codex CLI default model/profile. If needed, set
`CODEX_MODEL` to force a specific model.

Optional detail controls:

- `CODEX_MODEL_VERBOSITY=low|medium|high` (default: `high`)
- `DETAIL_MODE=off|high` (default: `high`)
- `DETAIL_SYSTEM_INSTRUCTION="..."` to customize internal guidance (default favors natural conversational style, without forced numbering)

## Requirements

- Python 3.10+
- `codex` CLI installed and logged in

## Run

```bash
python3 bridge_server.py
```

or with custom port:

```bash
BRIDGE_PORT=18080 python3 bridge_server.py
```

## Example calls

### Chat

```bash
curl -s http://localhost:11435/api/chat \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "codex",
    "messages": [
      {"role": "user", "content": "한 문장으로 인사해줘"}
    ],
    "stream": false
  }'
```

### Generate

```bash
curl -s http://localhost:11435/api/generate \
  -H 'Content-Type: application/json' \
  -d '{
    "model": "codex",
    "prompt": "hello in one short sentence",
    "stream": false
  }'
```

### Tags

```bash
curl -s http://localhost:11435/api/tags
```

## Notes

- `stream: true` is supported as Ollama-style NDJSON framing.
- Current streaming is simulated from final response text produced by `codex exec`.
- Unsupported Ollama options are ignored by design.
