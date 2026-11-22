"""Tests for the system prompt."""

from email_summarizer.prompts.system_prompt import SYSTEM_PROMPT, build_system_prompt


def test_system_prompt_not_empty() -> None:
    assert SYSTEM_PROMPT.strip() != ""
    assert "system" in SYSTEM_PROMPT.lower()
    assert "prompt" in SYSTEM_PROMPT.lower()


def test_build_system_prompt_not_empty() -> None:
    prompt = build_system_prompt()
    assert prompt.strip() != ""
    assert "system" in prompt.lower()
    assert "prompt" in prompt.lower()


def test_build_system_prompt_returns_string() -> None:
    prompt = build_system_prompt()
    assert isinstance(prompt, str)
    assert prompt.strip() != ""
