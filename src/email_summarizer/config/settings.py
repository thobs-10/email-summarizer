"""Application configuration models."""

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Self

from pydantic import BaseModel, Field, field_validator, model_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# Deliberately simple: exact addresses only (no "@domain" wildcards), no whitespace.
_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class EmailSettings(BaseSettings):
    """Email access configuration."""

    imap_host: str = Field(default="imap.gmail.com")
    imap_port: int = Field(default=993)
    imap_username: str = Field(default="")
    imap_app_password: str = Field(default="")
    mailbox: str = Field(default="INBOX")
    days_back: int = Field(default=7, ge=1, le=90)
    max_emails: int = Field(default=50, ge=1, le=1000)


class RunSettings(BaseSettings):
    """Runtime behavior configuration."""

    log_level: str = Field(default="INFO")
    output_dir: str = Field(default="outputs")


class GmailSettings(BaseModel):
    """Gmail API access configuration, loaded from GMAIL__* environment variables.

    A plain BaseModel rather than BaseSettings: a nested BaseSettings built via
    default_factory would also read unprefixed variables such as TOKEN_PATH.
    """

    base_dir: Path | None = Field(
        default=None,
        description="Directory relative paths resolve against. Defaults to the current "
        "working directory; set it when an MCP client launches the server from elsewhere.",
    )
    client_secret_file: Path = Path("secrets/credentials.json")
    token_path: Path = Path("secrets/token.json")
    state_path: Path = Path("data/state/gmail_sync.json")
    raw_output_dir: Path = Path("data/raw")
    # NoDecode lets the env value be "a@x.com,b@y.com" instead of a JSON list.
    newsletter_senders: Annotated[list[str], NoDecode] = Field(default_factory=list)
    days_back: int = Field(default=7, ge=1, le=90)
    max_results: int = Field(default=100, ge=1, le=500)
    max_retries: int = Field(default=3, ge=0, le=10)

    @field_validator("newsletter_senders", mode="before")
    @classmethod
    def _parse_senders(cls, value: object) -> object:
        """Accept a JSON list or a comma-separated string from the environment."""
        if not isinstance(value, str):
            return value
        stripped = value.strip()
        if stripped.startswith("["):
            return json.loads(stripped)
        return stripped.split(",")

    @field_validator("newsletter_senders")
    @classmethod
    def _normalise_senders(cls, senders: list[str]) -> list[str]:
        """Lower-case, drop blanks and duplicates (keeping order), and reject non-addresses."""
        normalised = [sender.strip().lower() for sender in senders if sender.strip()]
        invalid = [sender for sender in normalised if not _EMAIL_PATTERN.fullmatch(sender)]
        if invalid:
            raise ValueError(f"not exact email addresses: {invalid}")
        return list(dict.fromkeys(normalised))

    @model_validator(mode="after")
    def _resolve_paths(self) -> Self:
        """Make every path absolute so behaviour doesn't depend on the launch directory."""
        base = (self.base_dir or Path.cwd()).expanduser().resolve()
        self.base_dir = base
        for name in ("client_secret_file", "token_path", "state_path", "raw_output_dir"):
            path: Path = getattr(self, name).expanduser()
            setattr(self, name, path if path.is_absolute() else (base / path).resolve())
        return self


class AppSettings(BaseSettings):
    """Top-level application settings."""

    email: EmailSettings = Field(default_factory=EmailSettings)
    run: RunSettings = Field(default_factory=RunSettings)
    gmail: GmailSettings = Field(default_factory=GmailSettings)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    """Load and cache app settings from environment."""
    return AppSettings()
