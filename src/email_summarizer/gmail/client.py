"""Gmail client for interacting with the Gmail API. Synchronous by design."""

from collections.abc import Iterator
from typing import Any

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from loguru import logger


class HistoryIdExpiredError(Exception):
    """Exception raised when the Gmail history ID has expired and requires a full sync."""


class GmailClient:
    def __init__(self, credentials: Credentials, max_retries: int = 3) -> None:
        # self.credentials = credentials
        self._service = build("gmail", "v1", credentials=credentials)
        self._max_retries = max_retries

    # Execute a Gmail API request with retry logic
    def _exec(self, request: Any) -> Any:
        return request.execute(num_retries=self._max_retries)

    def get_current_history_id(self, user_id: str = "me") -> str:
        """Get the current history ID for the specified user.

        Args:
            user_id (str): The ID of the user. Defaults to "me".

        Returns:
            str: The current history ID.
        """
        try:
            profile = self._exec(self._service.users().getProfile(userId=user_id))
            return profile["historyId"]
        except HttpError as e:
            logger.error(f"Failed to get current history ID: {e}")
            raise

    def get_message_ids(self, query: str) -> Iterator[str]:
        """Get a list of message IDs matching the specified query.

        Args:
            query (str): The query string to filter messages.

        Yields:
            str: Message ID matching the query.
        Raises:
            HttpError: If the request to the Gmail API fails.
        """
        try:
            response = self._exec(self._service.users().messages().list(userId="me", q=query))
            while response:
                for message in response.get("messages", []):
                    yield message["id"]
                if "nextPageToken" in response:
                    response = self._exec(
                        self._service.users()
                        .messages()
                        .list(userId="me", q=query, pageToken=response["nextPageToken"])
                    )
                else:
                    break
        except HttpError as e:
            logger.error(f"Failed to get list of message IDs: {e}")
            raise

    def get_message(
        self,
        message_id: str,
        fmt: str = "full",
        metadata_headers: list[str] | None = None,
    ) -> dict[str, Any] | None:
        """Get the details of a specific message by its ID.

        Args:
            message_id (str): The ID of the message to retrieve.
            fmt (str): The format of the message. Defaults to "full".
            metadata_headers (list[str] | None): List of metadata headers to include. Defaults to None.

        Returns:
            dict[str, Any] | None: The message details if found, otherwise None.
        Raises:
            HttpError: If the request to the Gmail API fails.
        """
        try:
            if fmt == "metadata" and metadata_headers is not None:
                response = self._exec(
                    self._service.users()
                    .messages()
                    .get(
                        userId="me",
                        id=message_id,
                        format=fmt,
                        metadataHeaders=metadata_headers,
                    )
                )
            else:
                response = self._exec(
                    self._service.users().messages().get(userId="me", id=message_id, format=fmt)
                )
            return response
        except HttpError as e:
            if e.resp.status == 404:
                logger.warning(f"Message {message_id} not found (404).")
                return None
            logger.error(f"Failed to get message {message_id}: {e}")
            raise

    def list_added_message_ids(self, start_history_id: str) -> tuple[list[str], str]:
        """Return new messages in the user's mailbox since the specified history ID.

        Args:
            start_history_id (str): The history ID to start listing from.

        Returns:
            tuple[list[str], str]: A tuple containing a list of new message IDs and the latest history ID.
        Raises:
            HttpError: If the request to the Gmail API fails.
        """
        try:
            response = self._exec(
                self._service.users()
                .history()
                .list(
                    userId="me",
                    startHistoryId=start_history_id,
                    historyTypes=["messageAdded"],
                )
            )
            message_ids = []
            latest_history_id = start_history_id
            while response:
                for history_record in response.get("history", []):
                    for added in history_record.get("messagesAdded", []):
                        message_id = added["message"]["id"]
                        if message_id not in message_ids:
                            message_ids.append(message_id)
                if "nextPageToken" in response:
                    response = self._exec(
                        self._service.users()
                        .history()
                        .list(
                            userId="me",
                            startHistoryId=start_history_id,
                            historyTypes=["messageAdded"],
                            pageToken=response["nextPageToken"],
                        )
                    )
                else:
                    break
            if response and "historyId" in response:
                latest_history_id = response["historyId"]
            return message_ids, latest_history_id
        except HttpError as e:
            if e.resp.status == 404:
                logger.warning(f"History ID {start_history_id} expired (404).")
                raise HistoryIdExpiredError(f"History ID {start_history_id} expired.") from e
            logger.error(
                f"Failed to list added message IDs since history ID {start_history_id}: {e}"
            )
            raise
