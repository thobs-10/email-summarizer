# Developer commands. Run `make help` to list them.
.DEFAULT_GOAL := help
.PHONY: help install auth test lint format

help: ## Show available commands
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  %-10s %s\n", $$1, $$2}'

install: ## Install dependencies (including dev tools)
	uv sync --group dev

auth: ## Sign in to Gmail (read-only) in a browser and save the token
	uv run python -m email_summarizer.cli.auth

test: ## Run the test suite
	uv run pytest

lint: ## Lint and check formatting
	uv run ruff check src tests
	uv run ruff format --check src tests

format: ## Fix lint issues and format code
	uv run ruff check --fix src tests
	uv run ruff format src tests
