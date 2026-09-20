"""OAuth for gmail with the read-only scope."""

import os
from pathlib import Path

from dotenv import load_dotenv
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from loguru import logger

from email_summarizer.config.settings import AppSettings, get_settings

Settings = get_settings()

load_dotenv()

# TOKEN_PATH = Path("token.json")


SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def _load_token() -> Credentials | None:
    """Load the OAuth token from the file specified by the TOKEN_PATH environment variable.

    Returns:
        Credentials | None: The loaded credentials, or None if the token file does not exist.
    """
    TOKEN_PATH = Path(os.getenv("TOKEN_PATH", "token.json"))
    if TOKEN_PATH.exists():
        logger.info("Loading token from file.")
        return Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)
    return None


def _save_token(creds: Credentials) -> None:
    """Save the OAuth token to the file specified by the TOKEN_PATH environment variable.

    Args:
        creds (Credentials): The credentials to save.
    """
    TOKEN_PATH = Path(os.getenv("TOKEN_PATH", "token.json"))
    # //! create with owner-only permissions from the start
    with open(
        TOKEN_PATH,
        "w",
        opener=lambda path, flags: os.open(path, flags, 0o600),
    ) as token_file:
        token_file.write(creds.to_json())
    logger.info("Token saved to file.")


def _run_interactive_auth(settings: AppSettings) -> Credentials:
    """Run the interactive OAuth flow to obtain new credentials.

    Args:
        settings (AppSettings): The application settings containing the client secret file path.

    Returns:
        Credentials: The obtained credentials.
    """
    flow = InstalledAppFlow.from_client_secrets_file(settings.client_secret_file, SCOPES)
    creds = flow.run_local_server(port=0)
    _save_token(creds)
    return creds


def get_credentials(settings: AppSettings) -> Credentials:
    """Get the OAuth credentials, either by loading an existing token or running the interactive auth flow.

    Args:
        settings (AppSettings): The application settings containing the client secret file path.

    Returns:
        Credentials: The obtained credentials.
    """
    creds = _load_token()
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                creds.refresh(Request())
                _save_token(creds)
            except RefreshError:
                creds = _run_interactive_auth(settings)
        else:
            creds = _run_interactive_auth(settings)
    return creds
