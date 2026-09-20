"""Type contract for an email as fetched from gmail."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class RawEmail(BaseModel):
    message_id: str = Field(..., description="The unique ID of the email.")
    thread_id: str = Field(..., description="The ID of the thread this email belongs to.")
    label_ids: list[str] = Field(..., description="List of label IDs associated with the email.")
    sender_id: str = Field(..., description="The ID of the sender of the email.")
    sender_email: str = Field(..., description="The email address of the sender of the email.")
    received_at: datetime = Field(..., description="The date and time when the email was received.")
    subject: str = Field(..., description="The subject of the email.")
    html_body: str | None = Field(None, description="The HTML body content of the email.")
    text_body: str | None = Field(None, description="The plain text body content of the email.")

    @property
    def preferred_body(self) -> tuple[str, Literal["html", "text"]] | None:
        """Return the preferred body of the email.

        Returns:
            tuple[str, Literal["html", "text"]] | None: A tuple containing the body content and its format, or None if no preferred body is available.
        """
        if self.html_body:
            return self.html_body, "html"
        if self.text_body:
            return self.text_body, "text"
        return None
