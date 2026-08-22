import os

import pytest

from settings import ConfigError, load_settings


def base_env(monkeypatch):
    monkeypatch.setenv("DISCORD_TOKEN", "token")
    monkeypatch.setenv("SERVER_ID", "123")
    monkeypatch.delenv("RANK_ROLE_MAP", raising=False)
    monkeypatch.delenv("LEADERSHIP_ROLE_IDS", raising=False)


def test_load_minimal_settings(monkeypatch):
    base_env(monkeypatch)
    settings = load_settings()
    assert settings.server_id == 123
    assert settings.daily_schedule_time == "08:00"
    assert settings.schedule_timezone == "America/Argentina/Buenos_Aires"
    assert settings.trust_exact_display_name is False


def test_rank_role_map(monkeypatch):
    base_env(monkeypatch)
    monkeypatch.setenv("RANK_ROLE_MAP", "Leader:111,Superior:222")
    settings = load_settings()
    assert settings.rank_role_map["leader"] == 111
    assert settings.rank_role_map["superior"] == 222


def test_invalid_schedule_time(monkeypatch):
    base_env(monkeypatch)
    monkeypatch.setenv("DAILY_SCHEDULE_TIME", "25:99")
    with pytest.raises(ConfigError):
        load_settings()


def test_voltron_defaults(monkeypatch):
    base_env(monkeypatch)
    settings = load_settings()
    assert settings.voltron_calendar_enabled is True
    assert settings.voltron_base_url == "https://nexusportal.voltron.me"
    assert settings.voltron_realm == "Regular"
    assert settings.voltron_refresh_minutes == 30
    assert settings.voltron_calendar_days == 30
    assert settings.voltron_today_time == "08:00"
