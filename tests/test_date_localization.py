import pytest
from app.slack_router import get_system_prompt, get_slack_user_profile


def test_system_prompt_includes_slack_date_localization():
    prompt = get_system_prompt("Hello there")
    assert "DYNAMIC DATE & TIME LOCALIZATION" in prompt
    assert "<!date^" in prompt
    assert "{date_pretty} at {time}" in prompt


def test_system_prompt_includes_user_timezone():
    profile = {
        "name": "john_doe",
        "display_name": "John Doe",
        "tz": "America/New_York",
        "tz_label": "Eastern Daylight Time",
        "tz_offset": -14400,
    }
    prompt = get_system_prompt("What time is it?", user_profile=profile)
    assert "America/New_York" in prompt
    assert "Eastern Daylight Time" in prompt
    assert "DYNAMIC DATE & TIME LOCALIZATION" in prompt


import asyncio


def test_get_slack_user_profile_fallback():
    profile = asyncio.run(get_slack_user_profile("unknown-user", ""))
    assert profile["name"] == "unknown-user"
    assert profile["tz"] == ""
    assert profile["tz_offset"] == 0
