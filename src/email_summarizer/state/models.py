"""Core state and data contracts for the agent."""

from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field


class EmailCategory(StrEnum):
    """High-level classification output for an email."""

    NEWSLETTER = "newsletter"
    OTHER = "other"


class EmailMessage(BaseModel):
    """Normalized email object used throughout the pipeline."""

    message_id: str = Field(min_length=1)
    sender: str = Field(min_length=1)
    subject: str = Field(default="")
    sent_at: datetime
    body_text: str = Field(default="")


class FilterResult(BaseModel):
    """Newsletter classification result."""

    category: EmailCategory
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(default="")


class SummaryResult(BaseModel):
    """Summary and extracted key points for a single email."""

    message_id: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    key_points: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.utcnow)
