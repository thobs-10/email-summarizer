"""One off browser sign-in that saves the Gmail token. Run with ``make auth``."""

import argparse

from loguru import logger
from pydantic import ValidationError

from email_summarizer.config.settings import get_settings
from email_summarizer.gmail.auth import run_interactive_flow


def main(argv: list[str] | None = None) -> int:
    """Run the Gmail OAuth flow and save the token. Returns a process exit code."""
    # argparse only so `--help` explains the command instead of opening a browser.
    argparse.ArgumentParser(
        prog="python -m email_summarizer.cli.auth",
        description="Sign in to Gmail (read-only) in a browser and save the token "
        "to GMAIL__TOKEN_PATH. Run on a machine with a browser; copy the token to headless hosts.",
    ).parse_args(argv)
    try:
        settings = get_settings().gmail
        run_interactive_flow(settings)
    # Missing client secret / EMAIL_SUMMARIZER_ENV_FILE, or invalid GMAIL__* values in .env.
    except (FileNotFoundError, ValidationError) as exc:
        logger.error("Gmail sign-in failed: {}", exc)
        return 1
    logger.info("Gmail token saved to {}", settings.token_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
