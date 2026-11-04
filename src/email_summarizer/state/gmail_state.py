"""Where the Gmail sync left off, so the next run only picks up new mail."""

from pathlib import Path

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from email_summarizer.utils.files import write_text_atomic


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


class JsonStateStore:
    """Saves and loads the SyncState to a JSON file."""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> SyncState:
        """Load the SyncState from the JSON file, or return SyncState() if there is no file yet.

        Raises:
            pydantic.ValidationError: If the file is corrupt or doesn't match SyncState.
                Deliberately not treated as "no state": silently starting over would
                re-sync from days_back and hide the problem.
            OSError: If the file exists but can't be read.
        """
        try:
            data = self.path.read_bytes()
        except FileNotFoundError:
            return SyncState()
        return SyncState.model_validate_json(data)

    def save(self, state: SyncState) -> None:
        """Save the SyncState to the JSON file atomically, creating its directory if needed.

        Raises:
            OSError: If the file can't be written; the previous state file is left intact.
        """
        write_text_atomic(self.path, state.model_dump_json(indent=2))
