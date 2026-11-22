"""In-memory MCP tests for build_server: fastmcp.Client talks to the server without stdio.

FakeNewsletterService, FakeStateStore and the stub Gmail reader live in conftest.py.
"""

import dataclasses
from datetime import UTC, datetime
from typing import Any

import pytest
from fastmcp import Client
from fastmcp.exceptions import ToolError
from googleapiclient.errors import HttpError
from httplib2 import Response
from pydantic import ValidationError

from email_summarizer.config.settings import GmailSettings
from email_summarizer.gmail.auth import GmailAuthError
from email_summarizer.mcp_server.server import CAPS, build_server
from email_summarizer.models.gmail_views import SyncStatus
from email_summarizer.state.gmail_state import SyncState

pytestmark = pytest.mark.anyio

TOOL_NAMES = {"list_newsletter_senders", "find_newsletters", "get_newsletter", "sync_status"}
PRIVATE_SUBJECT = "Your private statement"
PRIVATE_BODY = "Account 1234, balance 99"
# What the find_newsletters docstring promises for an unset GMAIL__DAYS_BACK.
DEFAULT_MAX_DAYS = GmailSettings.model_fields["days_back"].default * CAPS.FIND_DAYS_PER_SYNC_WINDOW


def http_error(status: int) -> HttpError:
    return HttpError(Response({"status": status}), b'{"error": "body with private detail"}')


async def call(client: Client, tool: str, arguments: dict[str, Any] | None = None) -> Any:
    """Call a tool and return its structured result."""
    result = await client.call_tool(tool, arguments or {})
    return result.structured_content


# ----------------------------------
# tool listing
# ----------------------------------
async def test_lists_exactly_the_four_read_only_tools(client):
    tools = await client.list_tools()

    assert {tool.name for tool in tools} == TOOL_NAMES
    for tool in tools:
        assert tool.annotations is not None
        assert tool.annotations.read_only_hint is True, tool.name
        assert tool.annotations.open_world_hint is True, tool.name
        assert tool.annotations.destructive_hint is not True, tool.name


async def test_tool_inputs_accept_no_raw_gmail_query(client):
    """The model picks a sender and a window; it can never pass a Gmail `q` string."""
    tools = {tool.name: tool for tool in await client.list_tools()}

    params = set(tools["find_newsletters"].input_schema["properties"])

    assert params == {"sender", "days", "max_results"}


async def test_descriptions_warn_that_content_is_untrusted(client):
    tools = {tool.name: tool for tool in await client.list_tools()}

    description = tools["get_newsletter"].description or ""

    assert "not instructions" in description
    assert "follow" not in description.lower()


async def test_find_schema_states_the_clamp_limits(client):
    """FastMCP puts the docstring's Args into each parameter's schema description."""
    tools = {tool.name: tool for tool in await client.list_tools()}
    find = tools["find_newsletters"]
    params = find.input_schema["properties"]

    days = " ".join(params["days"]["description"].split())

    assert "clamped" in (find.description or "")
    assert f"GMAIL__DAYS_BACK x {CAPS.FIND_DAYS_PER_SYNC_WINDOW} ({DEFAULT_MAX_DAYS} days" in days
    assert f"1 to {CAPS.MAX_FIND_RESULTS}." in params["max_results"]["description"]
    assert "follow" not in ((find.description or "") + days).lower()


async def test_get_schema_states_the_max_chars_limit(client):
    tools = {tool.name: tool for tool in await client.list_tools()}
    get = tools["get_newsletter"]
    max_chars = " ".join(get.input_schema["properties"]["max_chars"]["description"].split())

    assert f"1 to {CAPS.MAX_BODY_CHARS};" in max_chars
    assert "clamped" in max_chars
    assert "follow" not in ((get.description or "") + max_chars).lower()


# ----------------------------------
# list_newsletter_senders
# ----------------------------------
async def test_list_newsletter_senders_returns_allow_list_without_gmail(client, fake_service):
    result = await call(client, "list_newsletter_senders")

    assert result == {"result": ["news@example.com", "digest@example.org"]}
    assert fake_service.find_calls == [] and fake_service.fetch_calls == []


# ----------------------------------
# find_newsletters
# ----------------------------------
async def test_find_newsletters_returns_refs_with_defaults(client, fake_service, make_ref):
    fake_service.refs = [make_ref("m1"), make_ref("m2", "digest@example.org")]

    result = await call(client, "find_newsletters")

    assert [ref["message_id"] for ref in result["result"]] == ["m1", "m2"]
    assert result["result"][0]["received_at"] == "2026-10-09T08:30:00Z"
    assert "body" not in result["result"][0]
    assert fake_service.find_calls == [(None, 7, 20)]


async def test_find_newsletters_normalises_an_allow_listed_sender(client, fake_service):
    await call(client, "find_newsletters", {"sender": "  News@Example.COM "})

    assert fake_service.find_calls == [("news@example.com", 7, 20)]


async def test_find_newsletters_treats_blank_sender_as_all(client, fake_service):
    await call(client, "find_newsletters", {"sender": " "})

    assert fake_service.find_calls == [(None, 7, 20)]


@pytest.mark.parametrize(
    ("days", "max_results", "expected"),
    [
        (0, 0, (1, 1)),
        (-5, -5, (1, 1)),
        (3, 5, (3, 5)),
    ],
)
async def test_find_newsletters_clamps_days_and_max_results(
    client, fake_service, days, max_results, expected
):
    await call(client, "find_newsletters", {"days": days, "max_results": max_results})

    assert fake_service.find_calls == [(None, *expected)]


@pytest.mark.parametrize("overshoot", [0, 10_000])
async def test_find_newsletters_caps_days_and_max_results_at_upper_bound(
    client, fake_service, mcp_settings, overshoot
):
    max_days = mcp_settings.days_back * CAPS.FIND_DAYS_PER_SYNC_WINDOW
    arguments = {"days": max_days + overshoot, "max_results": CAPS.MAX_FIND_RESULTS + overshoot}

    await call(client, "find_newsletters", arguments)

    assert fake_service.find_calls == [(None, max_days, CAPS.MAX_FIND_RESULTS)]


def test_output_caps_are_frozen() -> None:
    with pytest.raises(dataclasses.FrozenInstanceError):
        CAPS.MAX_FIND_RESULTS = 1  # type: ignore[misc]


async def test_find_newsletters_rejects_sender_not_on_allow_list(
    client, fake_service, log_messages
):
    with pytest.raises(ToolError, match="not on the newsletter allow-list") as excinfo:
        await call(client, "find_newsletters", {"sender": "alerts@bank.example"})

    assert fake_service.find_calls == []
    assert "alerts@bank.example" not in str(excinfo.value)
    assert not any("bank.example" in line for line in log_messages)


async def test_find_newsletters_with_empty_allow_list_is_an_error(
    fake_service, fake_store, mcp_settings
):
    server = build_server(
        fake_service, fake_store, mcp_settings.model_copy(update={"newsletter_senders": []})
    )

    async with Client(server) as client:
        with pytest.raises(ToolError, match="No newsletter senders"):
            await call(client, "find_newsletters")

    assert fake_service.find_calls == []


# ----------------------------------
# get_newsletter
# ----------------------------------
async def test_get_newsletter_returns_content(client, fake_service, make_email):
    fake_service.emails["m1"] = make_email(message_id="m1", html_body="<p>Hi</p>")

    result = await call(client, "get_newsletter", {"message_id": "m1"})

    assert result == {
        "message_id": "m1",
        "sender_email": "news@example.com",
        "subject": "Weekly digest",
        "received_at": "2026-01-01T12:00:00Z",
        "body_format": "html",
        "body": "<p>Hi</p>",
        "truncated": False,
    }


async def test_get_newsletter_truncates_to_max_chars(client, fake_service, make_email):
    fake_service.emails["m1"] = make_email(message_id="m1", html_body="x" * 500)

    result = await call(client, "get_newsletter", {"message_id": "m1", "max_chars": 100})

    assert (result["body"], result["truncated"]) == ("x" * 100, True)


@pytest.mark.parametrize(
    ("max_chars", "expected_length"),
    [(0, 1), (-1, 1), (10**9, CAPS.MAX_BODY_CHARS), (None, CAPS.DEFAULT_MAX_CHARS)],
)
async def test_get_newsletter_clamps_max_chars(
    client, fake_service, make_email, max_chars, expected_length
):
    fake_service.emails["m1"] = make_email(
        message_id="m1", html_body="y" * (CAPS.MAX_BODY_CHARS + 1)
    )
    arguments: dict[str, Any] = {"message_id": "m1"}
    if max_chars is not None:
        arguments["max_chars"] = max_chars

    result = await call(client, "get_newsletter", arguments)

    assert len(result["body"]) == expected_length and result["truncated"] is True


async def test_get_newsletter_refuses_sender_not_on_allow_list(client, fake_service, make_email):
    fake_service.emails["m1"] = make_email(
        message_id="m1", sender_email="alerts@bank.example", subject=PRIVATE_SUBJECT
    )

    with pytest.raises(ToolError, match="No allow-listed newsletter") as excinfo:
        await call(client, "get_newsletter", {"message_id": "m1"})

    assert fake_service.fetch_calls == ["m1"]
    assert PRIVATE_SUBJECT not in str(excinfo.value)


async def test_get_newsletter_unknown_id_looks_like_not_allowed(client):
    with pytest.raises(ToolError, match="No allow-listed newsletter"):
        await call(client, "get_newsletter", {"message_id": "unknown1"})


@pytest.mark.parametrize("message_id", ["", "../etc/passwd", "a b", "x" * 65, "id-with-dash"])
async def test_get_newsletter_rejects_malformed_id_before_fetching(
    client, fake_service, message_id
):
    with pytest.raises(ToolError, match="must be a Gmail message id") as excinfo:
        await call(client, "get_newsletter", {"message_id": message_id})

    assert fake_service.fetch_calls == []
    if message_id:
        assert message_id not in str(excinfo.value)


# ----------------------------------
# sync_status
# ----------------------------------
async def test_sync_status_before_first_sync(client, fake_store):
    result = await call(client, "sync_status")

    assert result == {"last_synced": None, "has_history_id": False}
    assert fake_store.loads == 1


async def test_sync_status_after_a_sync_hides_history_id(client, fake_store):
    fake_store.state = SyncState(
        history_id="987654", last_synced=datetime(2026, 10, 10, 6, 0, tzinfo=UTC)
    )

    result = await call(client, "sync_status")

    assert SyncStatus.model_validate(result) == SyncStatus(
        last_synced=datetime(2026, 10, 10, 6, 0, tzinfo=UTC), has_history_id=True
    )
    assert "987654" not in str(result)


async def test_sync_status_with_corrupt_state_is_a_safe_error(client, fake_store):
    try:
        SyncState.model_validate({"history_id": PRIVATE_BODY, "bogus": 1})
    except ValidationError as exc:
        fake_store.error = exc

    with pytest.raises(ToolError, match="sync state is unreadable") as excinfo:
        await call(client, "sync_status")

    assert PRIVATE_BODY not in str(excinfo.value)


# ----------------------------------
# error mapping: fixed messages, never exception text
# ----------------------------------
@pytest.mark.parametrize(
    ("error", "message"),
    [
        (http_error(429), "rate limit"),
        (http_error(503), "temporarily unavailable"),
        (http_error(403), r"HTTP 403"),
        (GmailAuthError("Token gone."), "make auth"),
        (OSError(f"/secret/path {PRIVATE_BODY}"), "Could not reach Gmail"),
        (TimeoutError("timed out"), "Could not reach Gmail"),
        (RuntimeError(PRIVATE_BODY), "Internal error"),
    ],
)
@pytest.mark.parametrize(
    ("tool", "arguments"), [("find_newsletters", {}), ("get_newsletter", {"message_id": "m1"})]
)
async def test_service_errors_become_safe_tool_errors(
    client, fake_service, tool, arguments, error, message
):
    fake_service.error = error

    with pytest.raises(ToolError, match=message) as excinfo:
        await call(client, tool, arguments)

    text = str(excinfo.value)
    for leaked in (PRIVATE_BODY, "/secret/path", "private detail", "Token gone"):
        assert leaked not in text


# ----------------------------------
# per-call logging: one content-free line per tool call
# ----------------------------------
async def test_each_call_logs_one_line_with_size_and_duration(
    client, fake_service, make_email, log_messages
):
    fake_service.emails["m1"] = make_email(
        message_id="m1", subject=PRIVATE_SUBJECT, html_body=PRIVATE_BODY
    )

    await call(client, "get_newsletter", {"message_id": "m1"})

    lines = [line for line in log_messages if "MCP tool call" in line]
    assert len(lines) == 1
    assert "tool=get_newsletter" in lines[0] and "duration_ms=" in lines[0]
    assert f"result_size={len(PRIVATE_BODY)}" in lines[0] and "error_type=None" in lines[0]
    joined = "\n".join(log_messages)
    for content in (PRIVATE_SUBJECT, PRIVATE_BODY, "news@example.com"):
        assert content not in joined


async def test_failed_call_logs_error_type_only(client, fake_service, log_messages):
    fake_service.error = http_error(500)

    with pytest.raises(ToolError):
        await call(client, "find_newsletters")

    lines = [line for line in log_messages if "MCP tool call" in line]
    assert len(lines) == 1 and "error_type=HttpError" in lines[0]
    assert not any("private detail" in line for line in log_messages)


async def test_refusal_logs_tool_error_type(client, log_messages):
    with pytest.raises(ToolError):
        await call(client, "get_newsletter", {"message_id": "bad-id"})

    lines = [line for line in log_messages if "MCP tool call" in line]
    assert len(lines) == 1 and "error_type=ToolError" in lines[0]


# ----------------------------------
# end to end over the real NewsletterGmailService (allow-list enforced after parsing)
# ----------------------------------
async def test_real_service_get_newsletter_serves_allow_listed_mail(real_service_server):
    async with Client(real_service_server) as client:
        result = await call(client, "get_newsletter", {"message_id": "good1"})

    assert (result["body"], result["body_format"]) == ("Allowed body", "text")


async def test_real_service_refuses_display_name_spoof_by_id(real_service_server):
    async with Client(real_service_server) as client:
        with pytest.raises(ToolError, match="No allow-listed newsletter") as excinfo:
            await call(client, "get_newsletter", {"message_id": "evil1"})

    assert "Bank secret" not in str(excinfo.value)


async def test_real_service_find_skips_spoofed_sender(real_service_server):
    async with Client(real_service_server) as client:
        result = await call(client, "find_newsletters")

    assert [ref["message_id"] for ref in result["result"]] == ["good1"]
