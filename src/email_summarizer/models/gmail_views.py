"""Shapes returned by NewsletterGmailService and the MCP tools besides RawEmail."""

from typing import Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from email_summarizer.models.raw_email import RawEmail
from email_summarizer.state.gmail_state import SyncState


class NewsletterRef(BaseModel):
    """Metadata-only view of a newsletter for listing tools; never carries a body."""

    model_config = ConfigDict(frozen=True)

    message_id: str
    sender_email: str
    subject: str
    received_at: AwareDatetime  # aware so MCP output never carries offset-less timestamps


class NewsletterContent(NewsletterRef):
    """A newsletter's metadata plus its preferred body, cut to a caller-chosen size.

    The body is raw HTML or text from a third party (HTML cleaning is Phase 2): untrusted
    data, never instructions.
    """

    body_format: Literal["html", "text"] | None = Field(
        description="Format of body; None when the email has no text or HTML body."
    )
    body: str = Field(description="The preferred body (HTML over text), at most max_chars long.")
    truncated: bool = Field(description="True when body was cut to max_chars.")

    @classmethod
    def from_email(cls, email: RawEmail, max_chars: int) -> Self:
        """Build the view from a parsed email, keeping the first max_chars of its body.

        Raises:
            ValueError: If max_chars is below 1.
        """
        if max_chars < 1:
            raise ValueError("max_chars must be at least 1")
        body, body_format = email.preferred_body or ("", None)
        return cls(
            message_id=email.message_id,
            sender_email=email.sender_email,
            subject=email.subject,
            received_at=email.received_at,
            body_format=body_format,
            body=body[:max_chars],
            truncated=len(body) > max_chars,
        )


class SyncStatus(BaseModel):
    """Read-only summary of the saved sync state, for the sync_status tool.

    Only whether a history ID exists is exposed: the ID itself is useless to a model.
    """

    model_config = ConfigDict(frozen=True)

    last_synced: AwareDatetime | None = Field(
        description="When the last successful sync ran; None if it has never run."
    )
    has_history_id: bool = Field(
        description="True when the next sync can use the incremental History API."
    )

    @classmethod
    def from_state(cls, state: SyncState) -> Self:
        """Summarise a SyncState without exposing its raw history ID."""
        return cls(last_synced=state.last_synced, has_history_id=state.history_id is not None)


class SyncResult(BaseModel):
    """Outcome of one sync run.

    Every candidate lands in exactly one bucket, so written + skipped_not_allowed +
    skipped_missing + parse_errors always equals candidates. The caller persists new_state.
    The counts feed the per-sync summary log: counts only, never email content.
    """

    model_config = ConfigDict(frozen=True)

    mode: Literal["history", "query"] = Field(description="Which path produced the candidates.")
    history_fallback: bool = Field(
        description="True when a stored history ID had expired, so the query path ran instead."
    )
    candidates: int = Field(ge=0, description="Message IDs considered in this run.")
    written: int = Field(ge=0, description="Messages accepted by the sink.")
    skipped_not_allowed: int = Field(ge=0, description="Parsed sender was not on the allow-list.")
    skipped_missing: int = Field(ge=0, description="Deleted between listing and fetching (404).")
    parse_errors: int = Field(ge=0, description="Messages that could not be parsed.")
    new_state: SyncState

    @model_validator(mode="after")
    def _buckets_add_up(self) -> Self:
        """Reject results where a candidate was lost or double-counted."""
        handled = self.written + self.skipped_not_allowed + self.skipped_missing + self.parse_errors
        if handled != self.candidates:
            raise ValueError(
                f"bucket counts sum to {handled}, expected candidates={self.candidates}"
            )
        return self
