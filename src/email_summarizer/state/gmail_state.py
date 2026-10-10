"""Where the Gmail sync left off, so the next run only picks up new mail."""

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class SyncState(BaseModel):
    """Checkpoint saved after each successful sync (persisted as JSON in step 7)."""

    # extra="forbid": a corrupted or hand-edited state file with unknown keys fails loudly.
    model_config = ConfigDict(frozen=True, extra="forbid")

    history_id: str | None = Field(
        default=None,
        min_length=1,  # an empty string must not count as a checkpoint
        description="Gmail history ID recorded at the start of the last sync; None before the first.",
    )
    # Aware only: the sync turns this into an after: epoch, and a naive time would be ambiguous.
    last_synced: AwareDatetime | None = Field(
        default=None, description="When the last successful sync ran; None before the first."
    )
