"""OAuth for Gmail with the read-only scope.

Two paths, deliberately separate:
- ``load_credentials``: non-interactive, for the sync CLI and the stdio MCP server.
- ``run_interactive_flow``: opens a browser, for ``make auth`` only.
"""

import os
import tempfile
from pathlib import Path

from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from loguru import logger

from email_summarizer.config.settings import GmailSettings

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


class GmailAuthError(Exception):
    """The stored Gmail token is missing or unusable; the user must sign in again."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"{reason} Run `make auth` to sign in again.")


def load_credentials(settings: GmailSettings) -> Credentials:
    """Load the saved token, refreshing and re-saving it if expired. Never opens a browser.

    Raises:
        GmailAuthError: If the token is missing, unreadable, lacks the read-only scope,
            or can't be refreshed.
    """
    path = settings.token_path
    try:
        # No scopes argument: keep the scopes stored in the token so has_scopes() is meaningful.
        creds = Credentials.from_authorized_user_file(str(path))
    except (OSError, ValueError) as exc:  # missing file, bad JSON, or missing fields
        raise GmailAuthError(f"Cannot load Gmail token at {path}.") from exc
    if not creds.has_scopes(SCOPES):
        raise GmailAuthError(f"Gmail token at {path} lacks the read-only scope.")
    if creds.valid:
        return creds
    if not (creds.expired and creds.refresh_token):
        raise GmailAuthError(f"Gmail token at {path} is invalid and has no refresh token.")
    try:
        creds.refresh(Request())  # refreshing the expired token
    except RefreshError as exc:  # revoked, or expired (Testing-mode apps: ~7 days)
        raise GmailAuthError(f"Gmail token at {path} could not be refreshed.") from exc
    _save_token(creds, path)
    logger.info("Refreshed Gmail token saved to {}", path)
    return creds


def run_interactive_flow(settings: GmailSettings) -> Credentials:
    """Run the browser OAuth flow, save the new token and return it.

    Raises:
        FileNotFoundError: If the OAuth client secret file does not exist.
    """
    secret = settings.client_secret_file
    if not secret.is_file():
        raise FileNotFoundError(
            f"OAuth client secret not found at {secret}. Download it from Google Cloud "
            "Console or set GMAIL__CLIENT_SECRET_FILE."
        )
    flow = InstalledAppFlow.from_client_secrets_file(str(secret), SCOPES)
    creds = flow.run_local_server(port=0)
    _save_token(creds, settings.token_path)
    return creds


def _save_token(creds: Credentials, path: Path) -> None:
    """Write the token atomically with owner-only permissions.

    mkstemp creates the file as 0600, and os.replace swaps it in atomically, so a concurrent
    reader (sync CLI vs MCP server) sees the old or the new token, never a partial one.
    """
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as token_file:
            token_file.write(creds.to_json())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
