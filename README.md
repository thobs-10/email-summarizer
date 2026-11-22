# Email Summarizer

[![CI](https://github.com/thobs-10/email-summarizer/actions/workflows/ci.yaml/badge.svg)](https://github.com/thobs-10/email-summarizer/actions/workflows/ci.yaml)
![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue)
![uv](https://img.shields.io/badge/packaging-uv-purple)

An AI agent that reads your newsletter emails, summarizes each one once, and answers
follow-up questions about them.

It serves two kinds of request:

| You ask | How it's answered |
|---|---|
| "What came in this week?" | A digest built from summaries computed at ingest time (no LLM call per query) |
| "What did X say about Y?" | An agent that searches the stored newsletters and answers from what it finds |

Gmail access is **read-only** (`gmail.readonly` scope), and only senders on your allow-list are
ever fetched.

## Status

The project is built in phases. The full plan is in [docs/PHASES.md](docs/PHASES.md).

| Phase | What it covers | Status |
|---|---|---|
| 0. Foundations | uv, ruff, bandit, pre-commit, settings, logging, CI | Mostly done |
| 1. Gmail integration | OAuth, incremental sync, read-only MCP server | **Done** |
| 2 to 5. Core | SQL storage, summarization, embeddings and retrieval, agent | Planned |
| 6 and 7. App | FastAPI with streaming, Streamlit UI | Planned |
| 8 to 13. Production | Caching, guardrails, orchestration, observability, evals, deployment | Planned |

**Available now:** sign in to Gmail, sync allow-listed newsletters to disk incrementally, and
browse or read them from any MCP client (Claude Desktop, MCP Inspector, and others).

## How it works today

```
Gmail API (read-only)
      │
      ▼
gmail/auth.py ─► gmail/client.py ─► gmail/service.py ◄── the only code that knows about Gmail
                                          │
                        ┌─────────────────┴─────────────────┐
                        ▼                                   ▼
               cli/sync.py (make sync)            mcp_server/ (make mcp)
               writes new mail to data/raw/       4 read-only tools over stdio
               and records sync state
```

- **Incremental sync.** Each run fetches only mail that arrived since the last sync, using
  Gmail's history ID. If the ID has expired, it falls back to a date-bounded search.
- **Thin MCP server.** Each tool calls the same service layer the sync uses. There are no tools
  that write: syncing happens outside the server, so a retried tool call can't change state.
- **Allow-list on every read.** The server checks the sender allow-list on each request, not
  just when it builds the query.

### MCP tools

| Tool | What it does |
|---|---|
| `list_newsletter_senders` | Lists the allow-listed sender addresses |
| `find_newsletters` | Lists recent newsletters (metadata only, newest first), with optional sender and day filters |
| `get_newsletter` | Returns one newsletter's cleaned body, truncated to a maximum length |
| `sync_status` | Reports when the last sync ran (reads saved state, never syncs) |

## Getting started

### Prerequisites

- Python 3.12+ and [uv](https://docs.astral.sh/uv/)
- A Google Cloud project with the **Gmail API** enabled and an **OAuth client ID** of type
  *Desktop app*. Download its JSON file to `secrets/credentials.json`.

### Install and configure

```bash
make install                 # uv sync, including dev tools
cp .env.example .env         # then edit .env
```

In `.env`, set `GMAIL__NEWSLETTER_SENDERS` to the exact sender addresses you want read, either
comma-separated or as a JSON list. Display names and wildcards aren't accepted. See
[.env.example](.env.example) for every setting and its default.

### Run

```bash
make auth    # one-off browser sign-in; saves the read-only token to secrets/token.json
make sync    # fetch newly arrived allow-listed newsletters into data/raw/
make mcp     # run the read-only MCP server over stdio
```

> **Token expiry:** while the OAuth consent screen is in *Testing* mode, Google expires refresh
> tokens after about 7 days. Re-run `make auth` when sync reports an auth error.

### Connect an MCP client

MCP clients start the server from their own working directory, so pass absolute paths through
`env`. For example, in Claude Desktop's `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "email-summarizer": {
      "command": "uv",
      "args": [
        "run", "--directory", "/abs/path/to/email-summarizer",
        "python", "-m", "email_summarizer.mcp_server"
      ],
      "env": {
        "GMAIL__BASE_DIR": "/abs/path/to/email-summarizer",
        "EMAIL_SUMMARIZER_ENV_FILE": "/abs/path/to/email-summarizer/.env"
      }
    }
  }
}
```

To try the tools by hand, use the MCP Inspector:
`npx @modelcontextprotocol/inspector uv run python -m email_summarizer.mcp_server`.

## Development

| Command | What it does |
|---|---|
| `make help` | List all commands |
| `make test` | Run the test suite with coverage |
| `make lint` | Run ruff lint and format checks |
| `make format` | Fix lint issues and format the code |

Pre-commit hooks run ruff, mypy, bandit, gitleaks secret scanning and conventional-commit
message checks. Install them once with
`uv run pre-commit install && uv run pre-commit install --hook-type commit-msg`.

### Project layout

```
src/email_summarizer/
├── cli/          entry points for `make auth` and `make sync`
├── config/       pydantic-settings configuration, loaded from the environment
├── gmail/        OAuth, API client, query building, MIME parsing, service layer
├── ingestion/    where synced emails are written (sinks)
├── mcp_server/   FastMCP server exposing the read-only tools
├── models/       typed models (RawEmail, tool response views)
├── prompts/      system prompt for the agent
├── state/        persisted sync state (history ID, last run)
├── utils/        logging and file helpers
└── agent/        the agent itself (Phase 5)
tests/            mirrors src/ layout
docs/             delivery phases and per-phase design documents
```

## Security and privacy

- OAuth with the `gmail.readonly` scope only. The app never asks for a password and can't
  modify your mailbox.
- `secrets/` (OAuth client and token) and `.env` are gitignored. Never commit them.
- Logs never include email body text.
- Only allow-listed senders are fetched or returned, and the server enforces this on every read.

## Documentation

- [docs/PHASES.md](docs/PHASES.md): delivery plan, target architecture and decisions made so far
- [docs/phases/](docs/phases/): one design document per phase
- [project.md](project.md): product specification and target tech stack
- [skills.md](skills.md): coding standards and conventions
