import imaplib
import os
from email import message_from_bytes
from email.message import Message, EmailMessage
from email.policy import default
from typing import List, Tuple, Any
from dotenv import load_dotenv
from email_summarizer.models.models import UserInfo, EmailModel
from email_summarizer.utils.retry import retry_with_backoff, RetryError

load_dotenv()  # Load environment variables from .env file


class EmailIngestor:
    def __init__(self):
        self.mail: imaplib.IMAP4_SSL | None = None
        required_vars: list[str] = [
            "USER_ID",
            "EMAIL_ADDRESS",
            "USER_NAME",
            "EMAIL_PASSWORD",
        ]
        missing_vars: list[str] = [
            var_name for var_name in required_vars if not os.getenv(key=var_name)
        ]
        if missing_vars:
            missing: str = ", ".join(missing_vars)
            raise ValueError(f"Missing required environment variables: {missing}")

        try:
            self.user_info = UserInfo(
                user_id=os.environ["USER_ID"],
                email=os.environ["EMAIL_ADDRESS"],
                name=os.environ["USER_NAME"],
            )
            self._email_password: str = os.environ["EMAIL_PASSWORD"]
        except ValueError as e:
            raise ValueError(f"Invalid user information: {e}")

    # Connect securely via IMAP
    # Replace with actual password retrieval logic
    def connect_to_email(self, mailbox: str = "inbox") -> None:
        """Connect to the email server using IMAP.
        Raises:
            ConnectionError: If the connection to the email server fails.
        """
        try:

            def _connect() -> None:
                self.mail = imaplib.IMAP4_SSL(host="imap.gmail.com", timeout=30)
                self.mail.login(
                    user=self.user_info.email, password=self._email_password
                )
                status, _ = self.mail.select(mailbox=mailbox)
                if status != "OK":
                    raise imaplib.IMAP4.error(f"Failed to select mailbox: {mailbox}")

            retry_with_backoff(
                operation=_connect,
                exceptions=(imaplib.IMAP4.error, OSError, TimeoutError),
                operation_name="IMAP connect",
            )
        except imaplib.IMAP4.error as e:
            raise ConnectionError(f"Failed to connect to email server: {e}")
        except RetryError as e:
            raise ConnectionError(f"Failed to connect to email server: {e}") from e

    def fetch_emails(self, search_criteria: str = "ALL") -> Tuple[str, List[bytes]]:
        """Fetch emails based on search criteria.
        Args:
            search_criteria (str): The IMAP search criteria (default is 'ALL').
        Raises:
            RuntimeError: If there is an error fetching emails.
        """
        if self.mail is None:
            raise RuntimeError(
                "Email connection is not initialized. Call connect_to_email first."
            )
        mail_client = self.mail

        try:

            def _search() -> Tuple[str, list[bytes]]:
                status, messages = mail_client.search(None, search_criteria)
                if status != "OK":
                    raise imaplib.IMAP4.error("Search returned non-OK status")
                return status, messages

            status, messages = retry_with_backoff(
                operation=_search,
                exceptions=(imaplib.IMAP4.error, OSError, TimeoutError),
                operation_name="IMAP search",
            )
            if status != "OK":
                raise RuntimeError("Failed to fetch emails.")
            email_ids: List[bytes] = (
                messages[0].split() if messages and messages[0] else []
            )
            return status, email_ids
        except RetryError as e:
            raise RuntimeError(f"Error fetching emails: {e}") from e
        except Exception as e:
            raise RuntimeError(f"Error fetching emails: {e}")

    def fetch_raw_messages(
        self, email_ids: List[bytes], limit: int | None = None
    ) -> List[bytes]:
        """Fetch RFC822 raw message bytes for the given IMAP message IDs."""
        if self.mail is None:
            raise RuntimeError(
                "Email connection is not initialized. Call connect_to_email first."
            )
        mail_client = self.mail

        selected_ids = email_ids[:limit] if limit is not None else email_ids
        raw_messages: List[bytes] = []

        for email_id in selected_ids:
            message_id = email_id.decode("utf-8", errors="replace")
            try:

                def _fetch() -> Tuple[str, list[Any]]:
                    return mail_client.fetch(
                        message_set=message_id, message_parts="(RFC822)"
                    )

                status, data = retry_with_backoff(
                    operation=_fetch,
                    exceptions=(imaplib.IMAP4.error, OSError, TimeoutError),
                    operation_name=f"IMAP fetch message {message_id}",
                )
            except RetryError:
                continue

            if status != "OK" or not data:
                continue

            for item in data:
                if (
                    isinstance(item, tuple)
                    and len(item) == 2
                    and isinstance(item[1], (bytes, bytearray))
                ):
                    raw_messages.append(bytes(item[1]))
                    break

        return raw_messages

    @staticmethod
    def _decode_text_payload(part: Message) -> str:
        payload = part.get_payload(decode=True)
        if payload is None:
            return ""
        charset = part.get_content_charset() or "utf-8"
        if isinstance(payload, (bytes, bytearray)):
            return bytes(payload).decode(charset, errors="replace").strip()
        return str(payload).strip()

    @staticmethod
    def _extract_body(message: Message) -> str:
        """Extract plain text body, falling back to HTML payload when needed."""
        if not message.is_multipart():
            return EmailIngestor._decode_text_payload(message)

        plain_parts: List[str] = []
        html_parts: List[str] = []

        for part in message.walk():
            content_type = part.get_content_type()
            if content_type == "text/plain":
                text = EmailIngestor._decode_text_payload(part)
                if text:
                    plain_parts.append(text)
                continue
            if content_type == "text/html":
                text = EmailIngestor._decode_text_payload(part)
                if text:
                    html_parts.append(text)

        if plain_parts:
            return "\n".join(plain_parts)
        if html_parts:
            return "\n".join(html_parts)
        return ""

    def map_raw_messages_to_email_model(
        self, raw_messages: List[bytes]
    ) -> List[EmailModel]:
        """Map raw RFC822 email bytes to EmailModel entries."""
        email_models: List[EmailModel] = []
        for raw_message in raw_messages:
            parsed_message: EmailMessage[Any, Any] = message_from_bytes(
                raw_message, policy=default
            )
            email_models.append(
                EmailModel(
                    subject=str(parsed_message.get("subject", "")).strip(),
                    sender=str(parsed_message.get("from", "")).strip(),
                    date=str(parsed_message.get("date", "")).strip(),
                    body=self._extract_body(parsed_message),
                )
            )
        return email_models

    def map_raw_messsages_to_email_model(
        self, raw_messages: List[bytes]
    ) -> List[EmailModel]:
        """Backward-compatible alias for the typo'd method name."""
        return self.map_raw_messages_to_email_model(raw_messages)
