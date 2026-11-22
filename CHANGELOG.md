# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[Semantic Versioning](https://semver.org/spec/v2.0.0.html). While the version is 0.x, each
completed delivery phase is a minor release and fixes in between are patch releases.

## [Unreleased]

## [0.1.0] - 2026-11-22

Phase 1: Gmail integration via FastMCP. The system can fetch allow-listed newsletters from
Gmail incrementally and expose them to MCP clients through read-only tools.

### Added
- Gmail OAuth with the `gmail.readonly` scope only. `make auth` runs the one-off browser
  sign-in and saves the token with owner-only permissions. The sync CLI and the MCP server
  never open a browser and fail fast with a clear message when the token is missing.
- Settings-driven configuration (`GMAIL__*` variables) with a validated sender allow-list
  that only accepts exact addresses that are safe to embed in a Gmail query.
- A parser that turns Gmail API payloads into typed `RawEmail` records: HTML preferred,
  plain text as fallback, charset-aware decoding, attachments ignored.
- `NewsletterGmailService` with allow-list filtering, single-message fetch, and incremental
  sync using the Gmail history ID, falling back to an `after:` date query when the history ID
  has expired.
- `make sync`: writes new newsletters as JSON files to `data/raw/` and saves sync state only
  after every message is written, so re-running after a failure loses nothing and creates no
  duplicates.
- A FastMCP server over stdio (`make mcp`, or `python -m email_summarizer.mcp_server` for MCP
  clients) with four read-only tools: `list_newsletter_senders`, `find_newsletters`,
  `get_newsletter` and `sync_status`. The allow-list is enforced on the server, inputs and
  output sizes are capped, errors are mapped to fixed safe messages, and there is no tool
  that syncs or writes.
- Developer commands in the `Makefile` (`install`, `auth`, `sync`, `mcp`, `test`, `lint`,
  `format`).
- CI with ruff, bandit, gitleaks and pytest, now also run on pull requests to `dev`.

### Removed
- The legacy IMAP ingestor and its retry helper, leaving the Gmail API as the only Gmail path.
- The empty `Dockerfile` and `docker-compose.yaml` placeholders. The Docker build and publish
  steps are skipped until a real Dockerfile is added in Phase 13.
- Unused `setuptools_scm` configuration and the placeholder `single-agent` script entry.

### Security
- Credentials and tokens live in the gitignored `secrets/` folder, never in environment values
  or the repository.
- Logs carry message IDs, counts and durations only, never subjects, bodies or addresses.
- Newsletter content is treated as untrusted data in every tool description.

[Unreleased]: https://github.com/thobs-10/email-summarizer/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/thobs-10/email-summarizer/releases/tag/v0.1.0
