"""Bootstrap utilities for early project slices."""

from dataclasses import dataclass

from email_summarizer.config.settings import AppSettings


@dataclass(frozen=True)
class BootstrapReport:
    """Small status object returned by bootstrap routine."""

    imap_host: str
    mailbox: str
    days_back: int
    max_emails: int


def bootstrap_report(settings: AppSettings) -> BootstrapReport:
    """Create a basic report proving typed config is wired correctly."""
    return BootstrapReport(
        imap_host=settings.email.imap_host,
        mailbox=settings.email.mailbox,
        days_back=settings.email.days_back,
        max_emails=settings.email.max_emails,
    )
