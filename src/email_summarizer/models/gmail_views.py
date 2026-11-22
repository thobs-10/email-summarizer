"""Shapes returned by NewsletterGmailService besides RawEmail."""

from typing import Literal, Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from email_summarizer.state.gmail_state import SyncState


class NewsletterRef(BaseModel):
    """Metadata-only view of a newsletter for listing tools; never carries a body."""

    model_config = ConfigDict(frozen=True)

    message_id: str
    sender_email: str
    subject: str
    received_at: AwareDatetime  # aware so MCP output never carries offset-less timestamps


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
