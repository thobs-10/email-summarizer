"""Application configuration models."""

import json
import os
import re
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from pydantic_core import PydanticUseDefault
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

# Exact addresses only (no "@domain" wildcards). Applied after lower-casing. The local part is
# an RFC 5322 dot-atom (no quotes, angle brackets or display names; no leading, trailing or double dots).
_LOCAL_PART = r"[a-z0-9!#$%&'*+/=?^_`{|}~-]+(?:\.[a-z0-9!#$%&'*+/=?^_`{|}~-]+)*"
_DOMAIN = r"(?:[a-z0-9](?:[a-z0-9-]*[a-z0-9])?\.)+(?:[a-z]{2,}|xn--[a-z0-9-]+)"
_EMAIL_PATTERN = re.compile(rf"{_LOCAL_PART}@{_DOMAIN}")

# Fields made absolute by GmailSettings._resolve_paths.
_PATH_FIELDS: tuple[str, ...] = ("client_secret_file", "token_path", "state_path", "raw_output_dir")

ENV_FILE_VAR: str = "EMAIL_SUMMARIZER_ENV_FILE"


class RunSettings(BaseSettings):
    """Runtime behavior configuration."""

    log_level: str = Field(default="INFO")
    output_dir: str = Field(default="outputs")


class GmailSettings(BaseModel):
    """Gmail API access configuration, loaded from GMAIL__* environment variables."""

    # Frozen so paths can't be swapped for unresolved ones after validation.
    model_config = ConfigDict(frozen=True)

    base_dir: Path | None = Field(
        default=None,
        description="Absolute directory relative paths resolve against. Defaults to the "
        "current working directory; set it when an MCP client launches the server elsewhere.",
    )
    client_secret_file: Path = Path("secrets/credentials.json")
    token_path: Path = Path("secrets/token.json")
    state_path: Path = Path("data/state/gmail_sync.json")
    raw_output_dir: Path = Path("data/raw")

    # NoDecode lets the env value be "a@x.com,b@y.com" instead of a JSON list. These will be parsed by the _parse_senders validator.
    newsletter_senders: Annotated[list[str], NoDecode] = Field(default_factory=list)
    days_back: int = Field(default=7, ge=1, le=90)
    max_results: int = Field(default=100, ge=1, le=500)
    max_retries: int = Field(default=3, ge=0, le=10)

    @field_validator("base_dir", *_PATH_FIELDS, mode="before")
    @classmethod
    def _blank_path_uses_default(cls, value: object) -> object:
        """Treat an empty value (e.g. ``GMAIL__TOKEN_PATH=``) as unset rather than as ``.``."""
        if isinstance(value, str) and not value.strip():
            raise PydanticUseDefault()
        return value

    @field_validator("base_dir")
    @classmethod
    def _require_absolute_base_dir(cls, base_dir: Path | None) -> Path | None:
        """Reject a relative base_dir, it would resolve against the cwd, defeating its purpose."""
        if base_dir is None:
            return None
        expanded = base_dir.expanduser()
        if not expanded.is_absolute():
            raise ValueError(f"must be an absolute path, got {str(base_dir)!r}")
        return expanded.resolve()

    @field_validator("newsletter_senders", mode="before")
    @classmethod
    def _parse_senders(cls, value: object) -> object:
        """Accept a JSON list or a comma-separated string"""
        if not isinstance(value, str):
            return value
        stripped = value.strip()
        if stripped.startswith("["):
            try:
                return json.loads(stripped)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSON list: {exc}") from exc
        # Strip quotes
        return [item.strip().strip("\"'") for item in stripped.split(",")]

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
        base = self.base_dir or Path.cwd().resolve()
        object.__setattr__(self, "base_dir", base)
        for name in _PATH_FIELDS:
            path: Path = getattr(self, name).expanduser()
            object.__setattr__(self, name, (base / path).resolve())  # base ignored if absolute
        return self


class AppSettings(BaseSettings):
    """Top-level application settings."""

    run: RunSettings = Field(default_factory=RunSettings)
    gmail: GmailSettings = Field(default_factory=GmailSettings)

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
    )


def _env_file_path() -> Path:
    """Return the .env file to load: $EMAIL_SUMMARIZER_ENV_FILE if set, else ./.env.

    An explicitly configured file must exist; failing fast beats silently running on
    defaults when an MCP client's env block has a typo.
    """
    configured = os.environ.get(ENV_FILE_VAR, "").strip()
    if not configured:
        return Path(".env")
    path = Path(configured).expanduser()
    if not path.is_file():
        raise FileNotFoundError(f"{ENV_FILE_VAR} points to a missing file: {path}")
    return path


@lru_cache(maxsize=1)
def get_settings() -> AppSettings:
    """Load and cache app settings from the environment and the .env file."""
    # _env_file is a real BaseSettings init kwarg; mypy only knows it with the pydantic.mypy plugin.
    return AppSettings(_env_file=_env_file_path())  # type: ignore[call-arg]
