"""Unit tests for GmailClient. No network: the Gmail service is fully mocked."""

from unittest.mock import MagicMock, create_autospec, patch

import pytest
from googleapiclient.errors import HttpError
from googleapiclient.http import HttpRequest

# from google.auth.transport.requests import Request
from httplib2 import Response

from email_summarizer.gmail.client import GmailClient, HistoryIdExpiredError

MODULE = "email_summarizer.gmail.client"


# --------------------------------------------------------------------------------------
# Helpers and fixtures
# --------------------------------------------------------------------------------------


def http_error(status: int) -> HttpError:
    return HttpError(Response({"status": status}), b"error")


def make_request(response=None, error=None):
    """Fake googleapiclient HttpRequest.

    create_autospec makes `execute()` enforce the REAL signature (`execute(http=None,
    num_retries=0)`), so a wrong keyword argument fails here instead of in production.
    """
    request = create_autospec(HttpRequest, instance=True)
    if error is not None:
        request.execute.side_effect = error
    else:
        request.execute.return_value = response
    return request


# @pytest.fixture
# def service():
#     """Stand-in for the object returned by googleapiclient's build()."""
#     return MagicMock(name="gmail_service")


# @pytest.fixture
# def client(service):
#     with patch(f"{MODULE}.build", return_value=service):
#         return GmailClient(credentials=MagicMock(name="creds"), max_retries=3)


def users_api(service):
    return service.users.return_value


def messages_api(service):
    return service.users.return_value.messages.return_value


def history_api(service):
    return service.users.return_value.history.return_value


def history_page(records, history_id, next_token=None):
    """Build a history.list response shaped like the real API (both fields present)."""
    page = {
        "history": [
            {
                "id": str(i),
                "messages": [{"id": m, "threadId": m} for m in ids],
                "messagesAdded": [{"message": {"id": m, "threadId": m}} for m in ids],
            }
            for i, ids in enumerate(records, start=1)
        ],
        "historyId": history_id,
    }
    if next_token:
        page["nextPageToken"] = next_token
    return page


# --------------------------------------------------------------------------------------
# Construction and retry plumbing
# --------------------------------------------------------------------------------------


def test_init_builds_gmail_v1_service_with_credentials():
    creds = MagicMock()
    with patch(f"{MODULE}.build") as build:
        GmailClient(creds)
    args, kwargs = build.call_args
    assert args == ("gmail", "v1")
    assert kwargs["credentials"] is creds


def test_exec_passes_retry_count_to_execute(client):
    request = make_request({"ok": True})
    assert client._exec(request) == {"ok": True}
    request.execute.assert_called_once_with(num_retries=3)


# --------------------------------------------------------------------------------------
# get_current_history_id
# --------------------------------------------------------------------------------------


def test_get_current_history_id_returns_profile_history_id(client, service):
    users_api(service).getProfile.return_value = make_request(
        {"emailAddress": "me@example.com", "historyId": "12345"}
    )
    assert client.get_current_history_id() == "12345"
    users_api(service).getProfile.assert_called_once_with(userId="me")


def test_get_current_history_id_propagates_http_error(client, service):
    users_api(service).getProfile.return_value = make_request(error=http_error(401))
    with pytest.raises(HttpError):
        client.get_current_history_id()


# --------------------------------------------------------------------------------------
# get_message_ids
# --------------------------------------------------------------------------------------


def test_get_message_ids_single_page(client, service):
    api = messages_api(service)
    api.list.return_value = make_request({"messages": [{"id": "a"}, {"id": "b"}]})

    assert list(client.get_message_ids("from:x@example.com")) == ["a", "b"]

    kwargs = api.list.call_args.kwargs
    assert kwargs["q"] == "from:x@example.com"
    assert kwargs.get("pageToken") is None


def test_get_message_ids_follows_pagination(client, service):
    api = messages_api(service)
    api.list.side_effect = [
        make_request({"messages": [{"id": "a"}], "nextPageToken": "p2"}),
        make_request({"messages": [{"id": "b"}], "nextPageToken": "p3"}),
        make_request({"messages": [{"id": "c"}]}),
    ]

    assert list(client.get_message_ids("q")) == ["a", "b", "c"]
    tokens = [c.kwargs.get("pageToken") for c in api.list.call_args_list]
    assert tokens == [None, "p2", "p3"]


def test_get_message_ids_empty_result(client, service):
    messages_api(service).list.return_value = make_request({"resultSizeEstimate": 0})
    assert list(client.get_message_ids("q")) == []


def test_get_message_ids_is_lazy(client, service):
    api = messages_api(service)
    api.list.return_value = make_request({"messages": [{"id": "a"}]})

    ids = client.get_message_ids("q")
    api.list.assert_not_called()  # nothing happens until the generator is consumed
    assert next(ids) == "a"
    api.list.assert_called_once()


def test_get_message_ids_propagates_http_error(client, service):
    messages_api(service).list.return_value = make_request(error=http_error(500))
    with pytest.raises(HttpError):
        list(client.get_message_ids("q"))


# --------------------------------------------------------------------------------------
# get_message
# --------------------------------------------------------------------------------------


def test_get_message_full_format(client, service):
    api = messages_api(service)
    api.get.return_value = make_request({"id": "m1", "payload": {}})

    assert client.get_message("m1") == {"id": "m1", "payload": {}}

    kwargs = api.get.call_args.kwargs
    assert kwargs["id"] == "m1"
    assert kwargs["format"] == "full"
    assert kwargs.get("metadataHeaders") is None


def test_get_message_metadata_passes_requested_headers(client, service):
    api = messages_api(service)
    api.get.return_value = make_request({"id": "m1"})

    client.get_message("m1", fmt="metadata", metadata_headers=["From"])

    kwargs = api.get.call_args.kwargs
    assert kwargs["format"] == "metadata"
    assert kwargs["metadataHeaders"] == ["From"]


def test_get_message_returns_none_when_message_was_deleted(client, service):
    messages_api(service).get.return_value = make_request(error=http_error(404))
    assert client.get_message("gone") is None


def test_get_message_propagates_other_http_errors(client, service):
    messages_api(service).get.return_value = make_request(error=http_error(500))
    with pytest.raises(HttpError):
        client.get_message("m1")


# --------------------------------------------------------------------------------------
# list_added_message_ids
# --------------------------------------------------------------------------------------


def test_list_added_collects_ids_and_latest_history_id(client, service):
    api = history_api(service)
    api.list.return_value = make_request(history_page([["a"], ["b", "c"]], "200"))

    ids, latest = client.list_added_message_ids("100")

    assert ids == ["a", "b", "c"]
    assert latest == "200"
    kwargs = api.list.call_args.kwargs
    assert kwargs["startHistoryId"] == "100"
    assert kwargs["historyTypes"] == ["messageAdded"]


def test_list_added_deduplicates_ids(client, service):
    history_api(service).list.return_value = make_request(history_page([["a"], ["a", "b"]], "200"))
    ids, _ = client.list_added_message_ids("100")
    assert ids == ["a", "b"]


def test_list_added_follows_pagination_and_uses_last_page_history_id(client, service):
    api = history_api(service)
    api.list.side_effect = [
        make_request(history_page([["a"]], "150", next_token="p2")),
        make_request(history_page([["b"]], "200")),
    ]

    ids, latest = client.list_added_message_ids("100")

    assert ids == ["a", "b"]
    assert latest == "200"
    calls = api.list.call_args_list
    assert [c.kwargs.get("pageToken") for c in calls] == [None, "p2"]
    assert all(c.kwargs["startHistoryId"] == "100" for c in calls)  # cursor stays fixed


def test_list_added_with_no_changes_returns_new_history_id(client, service):
    history_api(service).list.return_value = make_request({"historyId": "300"})
    assert client.list_added_message_ids("100") == ([], "300")


def test_list_added_keeps_start_id_if_response_has_no_history_id(client, service):
    history_api(service).list.return_value = make_request({})
    assert client.list_added_message_ids("100") == ([], "100")


def test_list_added_translates_404_to_history_expired_error(client, service):
    history_api(service).list.return_value = make_request(error=http_error(404))
    with pytest.raises(HistoryIdExpiredError):
        client.list_added_message_ids("1")


def test_list_added_propagates_other_http_errors(client, service):
    history_api(service).list.return_value = make_request(error=http_error(500))
    with pytest.raises(HttpError):
        client.list_added_message_ids("100")
