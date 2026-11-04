"""Type contract for an email as fetched from gmail."""

import re
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

# Gmail message ids are short alphanumeric strings. Callers check untrusted ids with
# fullmatch: the service before asking Gmail, the JSON sink before using one as a filename.
MESSAGE_ID_PATTERN = re.compile(r"[A-Za-z0-9]{1,64}")


class RawEmail(BaseModel):
    message_id: str = Field(..., description="The unique ID of the email.")
    thread_id: str = Field(..., description="The ID of the thread this email belongs to.")
    label_ids: list[str] = Field(..., description="List of label IDs associated with the email.")
    sender_id: str = Field(
        ...,
        description="Sender display name from the From header (e.g. 'Towards AI, Inc. via "
        "LinkedIn'), or the email address when there is none. Gmail has no sender ID.",
    )
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
