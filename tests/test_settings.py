import os

import pytest

from settings import ConfigError, load_settings


def base_env(monkeypatch):
    monkeypatch.setenv("DISCORD_TOKEN", "token")
    monkeypatch.setenv("SERVER_ID", "123")
    monkeypatch.delenv("RANK_ROLE_MAP", raising=False)
    monkeypatch.delenv("TROOP_LEVEL_ROLE_MAP", raising=False)
    monkeypatch.delenv("LEADERSHIP_ROLE_IDS", raising=False)
    monkeypatch.setenv("CALENDAR_BASE_URL", "https://calendar.example.test")
    for name in (
        "VERIFIED_ROLE_ID", "UNVERIFIED_ROLE_ID", "SPECIAL_ACCESS_ROLE_ID",
        "CHEST_CHANNEL_ID", "CHEST_RESET_POST_ENABLED", "CHEST_RESET_POST_TIME_UTC",
        "CHEST_REPORT_CHUNK_SIZE", "ROSTER_ACCESS_SYNC_MINUTES", "OZY_DATA_API_TOKEN",
        "VERIFICATION_CHANNEL_ID", "STATE_DATABASE_URL", "DATABASE_URL",
        "STATE_REMOTE_URL", "STATE_REMOTE_TOKEN", "STATE_REMOTE_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)


def test_load_minimal_settings(monkeypatch):
    base_env(monkeypatch)
    settings = load_settings()
    assert settings.server_id == 123
    assert settings.daily_schedule_time == "08:00"
    assert settings.schedule_timezone == "America/Argentina/Buenos_Aires"
    assert settings.trust_exact_display_name is False
    assert settings.chest_reset_post_enabled is False
    assert settings.chest_reset_post_time_utc == "17:00"
    assert settings.chest_report_chunk_size == 20
    assert settings.roster_access_sync_minutes == 10
    assert settings.ozy_data_api_token is None
    assert settings.verification_channel_id is None
    assert settings.state_database_url is None
    assert settings.state_remote_url is None
    assert settings.state_remote_token is None
    assert settings.state_remote_timeout_seconds == 10.0


def test_rank_role_map(monkeypatch):
    base_env(monkeypatch)
    monkeypatch.setenv("RANK_ROLE_MAP", "Leader:111,Superior:222")
    settings = load_settings()
    assert settings.rank_role_map["leader"] == 111
    assert settings.rank_role_map["superior"] == 222


def test_troop_level_role_map(monkeypatch):
    base_env(monkeypatch)
    monkeypatch.setenv("TROOP_LEVEL_ROLE_MAP", "G8:888,G9:999")
    settings = load_settings()
    assert settings.troop_level_role_map == {"G8": 888, "G9": 999}


def test_invalid_troop_level_role_map(monkeypatch):
    base_env(monkeypatch)
    monkeypatch.setenv("TROOP_LEVEL_ROLE_MAP", "eight:888")
    with pytest.raises(ConfigError):
        load_settings()


def test_invalid_schedule_time(monkeypatch):
    base_env(monkeypatch)
    monkeypatch.setenv("DAILY_SCHEDULE_TIME", "25:99")
    with pytest.raises(ConfigError):
        load_settings()


def test_calendar_defaults(monkeypatch):
    base_env(monkeypatch)
    settings = load_settings()
    assert settings.calendar_enabled is True
    assert settings.calendar_base_url == "https://calendar.example.test"
    assert settings.calendar_realm == "Regular"
    assert settings.calendar_refresh_minutes == 30
    assert settings.calendar_days == 30


def test_access_and_chest_settings(monkeypatch):
    base_env(monkeypatch)
    monkeypatch.setenv("VERIFIED_ROLE_ID", "1001")
    monkeypatch.setenv("UNVERIFIED_ROLE_ID", "1002")
    monkeypatch.setenv("SPECIAL_ACCESS_ROLE_ID", "1003")
    monkeypatch.setenv("CHEST_CHANNEL_ID", "2001")
    monkeypatch.setenv("CHEST_RESET_POST_ENABLED", "true")
    monkeypatch.setenv("CHEST_RESET_POST_TIME_UTC", "17:00")
    monkeypatch.setenv("CHEST_REPORT_CHUNK_SIZE", "20")
    monkeypatch.setenv("ROSTER_ACCESS_SYNC_MINUTES", "5")
    monkeypatch.setenv("OZY_DATA_API_TOKEN", "service-secret")
    settings = load_settings()
    assert settings.verified_role_id == 1001
    assert settings.unverified_role_id == 1002
    assert settings.special_access_role_id == 1003
    assert settings.chest_channel_id == 2001
    assert settings.chest_reset_post_enabled is True
    assert settings.chest_reset_post_time_utc == "17:00"
    assert settings.chest_report_chunk_size == 20
    assert settings.roster_access_sync_minutes == 5
    assert settings.ozy_data_api_token == "service-secret"


def test_persistent_state_settings(monkeypatch):
    base_env(monkeypatch)
    monkeypatch.setenv("VERIFICATION_CHANNEL_ID", "3001")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@db.example/ozy")
    settings = load_settings()
    assert settings.verification_channel_id == 3001
    assert settings.state_database_url == "postgresql://user:pass@db.example/ozy"


def test_web_snapshot_state_settings(monkeypatch):
    base_env(monkeypatch)
    monkeypatch.setenv("STATE_REMOTE_URL", "https://ozy.com.ar/api/ozy-admin/state")
    monkeypatch.setenv("STATE_REMOTE_TOKEN", "x" * 32)
    monkeypatch.setenv("STATE_REMOTE_TIMEOUT_SECONDS", "7")
    settings = load_settings()
    assert settings.state_remote_url == "https://ozy.com.ar/api/ozy-admin/state"
    assert settings.state_remote_token == "x" * 32
    assert settings.state_remote_timeout_seconds == 7.0


def test_web_snapshot_requires_token(monkeypatch):
    base_env(monkeypatch)
    monkeypatch.setenv("STATE_REMOTE_URL", "https://ozy.com.ar/api/ozy-admin/state")
    with pytest.raises(ConfigError):
        load_settings()


def test_web_snapshot_and_database_url_are_mutually_exclusive(monkeypatch):
    base_env(monkeypatch)
    monkeypatch.setenv("STATE_REMOTE_URL", "https://ozy.com.ar/api/ozy-admin/state")
    monkeypatch.setenv("STATE_REMOTE_TOKEN", "x" * 32)
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@db.example/ozy")
    with pytest.raises(ConfigError):
        load_settings()
