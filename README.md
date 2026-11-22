# Single Agent: Gmail Newsletter Summarizer

## Purpose
Build a lightweight Python agent that:
1. Reads Gmail through the Gmail API (read-only OAuth scope).
2. Finds blog/newsletter subscription emails.
3. Summarizes content with a small open-source model.
4. Extracts key points and action items.
5. Writes a local Markdown digest.

The project is local-first, CPU-friendly, and focused on engineering quality.

## Scope

### In Scope (V1)
1. Single Gmail account via the Gmail API (`gmail.readonly` OAuth scope).
2. Manual CLI run (on-demand).
3. Newsletter/blog filtering.
4. Summary + key point extraction.
5. Markdown output.
6. Local persistence for deduplication.

### Out of Scope (V1)
1. Multi-account support.
2. Web UI/dashboard.
3. Always-on background daemon.
4. Cloud-hosted proprietary LLM dependencies.

## Gmail Access
Only senders on the allow-list (`GMAIL__NEWSLETTER_SENDERS`, see `.env.example`) are read.

1. `make auth`: one-off browser sign-in that saves the read-only token to `secrets/token.json`.
   The OAuth consent screen is in Testing mode, so this has to be re-run about weekly.
2. `make sync`: fetch newly arrived allow-listed newsletters into `data/raw/` (incremental).
3. `make mcp`: run the read-only MCP server over stdio, with the tools
   `list_newsletter_senders`, `find_newsletters`, `get_newsletter` and `sync_status`.
   MCP clients launch it directly:
   `uv run --directory /abs/path/to/repo python -m email_summarizer.mcp_server`.

## Functional Requirements
1. Fetch recent emails from selected mailbox/folder.
2. Parse email safely (headers, HTML/plain body).
3. Filter for newsletter/subscription content.
4. Summarize each selected email.
5. Extract key points and optional action items.
6. Avoid processing duplicates.
7. Save digest as Markdown.

## Non-Functional Requirements
1. Security: secrets from environment only; never hardcode credentials.
2. Reliability: retries and timeouts for network/model calls.
3. Observability: structured logs without leaking sensitive body text.
4. Testability: unit tests for all core modules + one integration flow.
5. Maintainability: typed models, clear module boundaries, small functions.
6. Performance: CPU-friendly model/runtime and bounded latency per email.

## Proposed Architecture

Current source layout under src/single_agent:
1. agent: orchestration and control loop.
2. tools: ingestion, filtering, summarization, extraction.
3. models: local model runtime wrappers.
4. config: pydantic settings and environment config.
5. workflows: end-to-end pipeline composition.
6. state: data contracts for email, summary, and evaluation outputs.
7. memory: persistence and deduplication layer.
8. prompts: prompt templates and output contracts.
9. evals: evaluation harness and benchmark datasets.
10. tests: unit and integration tests.
11. utils: shared helpers (logging, parsing, time, ids).

## Agent Framework Evaluation

### Option A: PydanticAI
Pros:
1. Strong typed interfaces and structured outputs.
2. Good developer ergonomics with pydantic models.
3. Simpler than larger orchestration frameworks.

Cons:
1. Smaller ecosystem than LangChain/LlamaIndex.
2. May still be more abstraction than needed for a single pipeline.

Best Use:
Typed agent interactions and predictable output schemas.

### Option B: LangChain
Pros:
1. Large ecosystem and many integrations.
2. Mature patterns for tools/memory/chains.

Cons:
1. Higher abstraction and dependency weight.
2. Can become complex for small, focused projects.

Best Use:
Complex multi-tool systems and rapid prototyping with many providers.

### Option C: LlamaIndex
Pros:
1. Strong data ingestion and retrieval pipelines.
2. Good for document-heavy workflows.

Cons:
1. Less necessary for pure email summarization pipeline.
2. Extra layers if retrieval is not central.

Best Use:
RAG-heavy projects with large document corpora.

### Option D: Minimal Custom Orchestrator (Recommended V1)
Pros:
1. Small dependency surface.
2. Maximum control and debuggability.
3. Best fit for single-purpose pipeline.

Cons:
1. You own more orchestration code.
2. Must enforce patterns manually.

Best Use:
Focused, lightweight systems with clear workflow boundaries.

## Decision for V1
1. Start with minimal custom orchestration + pydantic models/settings.
2. Keep PydanticAI as an optional upgrade path if tool-calling complexity grows.
3. Avoid heavyweight frameworks until the problem scope justifies them.

This keeps V1 simple, fast, and maintainable while preserving future flexibility.

## Tech Stack (Planned)

### Core Language
1. Python 3.12+

### Email and Parsing
1. Gmail API (google-api-python-client, google-auth, google-auth-oauthlib)
2. fastmcp for the read-only MCP server
3. beautifulsoup4 for HTML cleanup
4. lxml for robust parsing

### Model Runtime (Open Source)
1. Ollama for easy local model management (recommended for development)
2. llama-cpp-python for lower-level local deployment path

### Candidate SLMs (CPU-friendly)
1. Phi-3.5-mini-instruct (quantized) as primary candidate.
2. Mistral 7B instruct (quantized) as quality fallback.
3. Tiny models only for classification, not final summaries.

### Data and State
1. sqlite3 (stdlib) or SQLAlchemy (if query complexity grows)

### Validation and Config
1. pydantic
2. pydantic-settings

### Quality Tooling
1. pytest, pytest-asyncio, pytest-cov
2. ruff
3. mypy

### Optional Evaluation Libraries
1. rouge-score
2. bert-score

## Security and Privacy Rules
1. Use OAuth with the `gmail.readonly` scope only; no passwords.
2. Keep `secrets/` (OAuth client secret and token) and `.env` out of version control.
3. Do not log raw email body text in production logs.
4. Enforce the sender allow-list on the server for every read.
5. Minimize retained content and retain only what is necessary.

## Engineering Best Practices
1. Type hints everywhere in non-trivial modules.
2. Pydantic models for all input/output boundaries.
3. Single responsibility per module.
4. Idempotent workflow steps where possible.
5. Clear error taxonomy and retries with backoff.
6. Deterministic prompts and schema-constrained outputs.
7. Tests for parser/filter/summarizer and one end-to-end flow.

## Progress Tracking
Execution plan and checklists are tracked in project.md.
Skills inventory and implementation capabilities are tracked in skills.md.
Agent behavior and orchestration contract are tracked in AGENTS.md.

## Next Milestones
1. Finalize dependencies in pyproject.toml.
2. Define pydantic state models in src/single_agent/state.
3. Implement Gmail API ingestion + parser in src/email_summarizer/gmail.
4. Implement newsletter filter.
5. Integrate local summarizer.
6. Generate first Markdown digest.
7. Add tests and quality gates.
