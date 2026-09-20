"""Application configuration models."""

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


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


class AppSettings(BaseSettings):
    """Top-level application settings."""

    email: EmailSettings = Field(default_factory=EmailSettings)
    run: RunSettings = Field(default_factory=RunSettings)

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
