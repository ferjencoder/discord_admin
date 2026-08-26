from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import load_dotenv

load_dotenv()


class ConfigError(RuntimeError):
    pass


def _required(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise ConfigError(f"Missing required environment variable: {name}")
    return value


def _env_int(name: str, default: int, minimum: int | None = None) -> int:
    raw = os.getenv(name)
    try:
        value = default if raw is None or raw.strip() == "" else int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer") from exc
    if minimum is not None and value < minimum:
        raise ConfigError(f"{name} must be >= {minimum}")
    return value


def _env_float(name: str, default: float, minimum: float | None = None) -> float:
    raw = os.getenv(name)
    try:
        value = default if raw is None or raw.strip() == "" else float(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a number") from exc
    if minimum is not None and value < minimum:
        raise ConfigError(f"{name} must be >= {minimum}")
    return value


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or raw.strip() == "":
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(f"{name} must be true/false")


def _optional_id(name: str) -> int | None:
    raw = os.getenv(name, "").strip()
    if not raw:
        return None
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigError(f"{name} must be a numeric Discord ID") from exc
    if value <= 0:
        raise ConfigError(f"{name} must be a positive Discord ID")
    return value


def _id_set(name: str) -> frozenset[int]:
    raw = os.getenv(name, "").strip()
    if not raw:
        return frozenset()
    values: set[int] = set()
    for token in raw.split(","):
        token = token.strip()
        if not token:
            continue
        try:
            value = int(token)
        except ValueError as exc:
            raise ConfigError(f"{name} must contain comma-separated numeric Discord IDs") from exc
        if value <= 0:
            raise ConfigError(f"{name} IDs must be positive")
        values.add(value)
    return frozenset(values)


def _rank_role_map() -> dict[str, int]:
    raw = os.getenv("RANK_ROLE_MAP", "").strip()
    if not raw:
        return {}
    result: dict[str, int] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair:
            continue
        if ":" not in pair:
            raise ConfigError("RANK_ROLE_MAP entries must look like Rank:ROLE_ID")
        rank, role_id = pair.rsplit(":", 1)
        rank = rank.strip()
        role_id = role_id.strip()
        if not rank or not role_id.isdigit():
            raise ConfigError(f"Invalid RANK_ROLE_MAP entry: {pair!r}")
        result[rank.casefold()] = int(role_id)
    return result


def _troop_level_role_map() -> dict[str, int]:
    raw = os.getenv("TROOP_LEVEL_ROLE_MAP", "").strip()
    if not raw:
        return {}
    result: dict[str, int] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair:
            continue
        if ":" not in pair:
            raise ConfigError("TROOP_LEVEL_ROLE_MAP entries must look like G8:ROLE_ID")
        level, role_id = pair.rsplit(":", 1)
        level = level.strip().upper()
        role_id = role_id.strip()
        if not level or not role_id.isdigit():
            raise ConfigError(f"Invalid TROOP_LEVEL_ROLE_MAP entry: {pair!r}")
        if not level.startswith("G") or not level[1:].isdigit():
            raise ConfigError(f"Invalid troop level {level!r}; expected values like G8 or G9")
        result[level] = int(role_id)
    return result


def _validate_hhmm(name: str, value: str) -> str:
    parts = value.split(":")
    if len(parts) != 2:
        raise ConfigError(f"{name} must be HH:MM")
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError as exc:
        raise ConfigError(f"{name} must be HH:MM") from exc
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ConfigError(f"{name} must be HH:MM")
    return f"{hour:02d}:{minute:02d}"


@dataclass(frozen=True)
class Settings:
    discord_token: str
    server_id: int
    port: int
    render_external_url: str | None
    self_ping_enabled: bool
    self_ping_interval_seconds: int

    leadership_role_ids: frozenset[int]
    rank_role_map: dict[str, int]
    troop_level_role_map: dict[str, int]
    away_role_id: int | None
    verified_role_id: int | None
    unverified_role_id: int | None
    special_access_role_id: int | None

    welcome_channel_id: int | None
    announcement_channel_id: int | None
    announcement_ping_role_id: int | None
    schedule_channel_id: int | None
    calendar_channel_id: int | None
    today_channel_id: int | None
    away_channel_id: int | None
    audit_channel_id: int | None
    chest_channel_id: int | None
    verification_channel_id: int | None

    roster_url: str | None
    roster_file: Path
    chest_data_url: str | None
    chest_data_file: Path
    ozy_data_api_token: str | None
    schedule_url: str | None
    schedule_file: Path
    chats_file: Path
    data_cache_seconds: int
    http_timeout_seconds: float

    schedule_timezone: str
    daily_schedule_time: str
    daily_schedule_enabled: bool

    chest_reset_post_enabled: bool
    chest_reset_post_time_utc: str
    chest_report_chunk_size: int
    roster_access_sync_minutes: int

    calendar_enabled: bool
    calendar_base_url: str
    calendar_realm: str
    calendar_refresh_minutes: int
    calendar_days: int
    today_enabled: bool
    calendar_min_actions: int

    trust_exact_display_name: bool
    auto_sync_nickname: bool
    roster_match_threshold: float
    state_db: Path
    state_database_url: str | None

    @property
    def timezone(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.schedule_timezone)
        except ZoneInfoNotFoundError as exc:
            raise ConfigError(f"Unknown SCHEDULE_TIMEZONE: {self.schedule_timezone}") from exc


def load_settings() -> Settings:
    token = _required("DISCORD_TOKEN")
    try:
        server_id = int(_required("SERVER_ID"))
    except ValueError as exc:
        raise ConfigError("SERVER_ID must be a numeric Discord guild ID") from exc

    timezone_name = os.getenv("SCHEDULE_TIMEZONE", "America/Argentina/Buenos_Aires").strip()
    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ConfigError(f"Unknown SCHEDULE_TIMEZONE: {timezone_name}") from exc

    render_external_url = os.getenv("RENDER_EXTERNAL_URL", "").strip() or None
    calendar_enabled = _env_bool("CALENDAR_ENABLED", True)
    calendar_base_url = os.getenv("CALENDAR_BASE_URL", "").strip().rstrip("/")
    if calendar_enabled and not calendar_base_url:
        raise ConfigError("CALENDAR_BASE_URL is required when CALENDAR_ENABLED=true")

    return Settings(
        discord_token=token,
        server_id=server_id,
        port=_env_int("PORT", 10000, 1),
        render_external_url=render_external_url,
        self_ping_enabled=_env_bool("SELF_PING_ENABLED", bool(render_external_url)),
        self_ping_interval_seconds=_env_int("SELF_PING_INTERVAL_SECONDS", 600, 60),

        leadership_role_ids=_id_set("LEADERSHIP_ROLE_IDS"),
        rank_role_map=_rank_role_map(),
        troop_level_role_map=_troop_level_role_map(),
        away_role_id=_optional_id("AWAY_ROLE_ID"),
        verified_role_id=_optional_id("VERIFIED_ROLE_ID"),
        unverified_role_id=_optional_id("UNVERIFIED_ROLE_ID"),
        special_access_role_id=_optional_id("SPECIAL_ACCESS_ROLE_ID"),

        welcome_channel_id=_optional_id("WELCOME_CHANNEL_ID"),
        announcement_channel_id=_optional_id("ANNOUNCEMENT_CHANNEL_ID"),
        announcement_ping_role_id=_optional_id("ANNOUNCEMENT_PING_ROLE_ID"),
        schedule_channel_id=_optional_id("SCHEDULE_CHANNEL_ID"),
        calendar_channel_id=_optional_id("CALENDAR_CHANNEL_ID"),
        today_channel_id=_optional_id("TODAY_CHANNEL_ID"),
        away_channel_id=_optional_id("AWAY_CHANNEL_ID"),
        audit_channel_id=_optional_id("AUDIT_CHANNEL_ID"),
        chest_channel_id=_optional_id("CHEST_CHANNEL_ID"),
        verification_channel_id=_optional_id("VERIFICATION_CHANNEL_ID"),

        roster_url=os.getenv("ROSTER_URL", "").strip() or None,
        roster_file=Path(os.getenv("ROSTER_FILE", "data/roster.json")).expanduser(),
        chest_data_url=os.getenv("CHEST_DATA_URL", "").strip() or None,
        chest_data_file=Path(os.getenv("CHEST_DATA_FILE", "data/chest_data.json")).expanduser(),
        ozy_data_api_token=os.getenv("OZY_DATA_API_TOKEN", "").strip() or None,
        schedule_url=os.getenv("SCHEDULE_URL", "").strip() or None,
        schedule_file=Path(os.getenv("SCHEDULE_FILE", "data/schedule.json")).expanduser(),
        chats_file=Path(os.getenv("CHATS_FILE", "data/chats.json")).expanduser(),
        data_cache_seconds=_env_int("DATA_CACHE_SECONDS", 60, 5),
        http_timeout_seconds=_env_float("HTTP_TIMEOUT_SECONDS", 10.0, 1.0),

        schedule_timezone=timezone_name,
        daily_schedule_time=_validate_hhmm(
            "DAILY_SCHEDULE_TIME",
            os.getenv("DAILY_SCHEDULE_TIME", "08:00").strip() or "08:00",
        ),
        daily_schedule_enabled=_env_bool("DAILY_SCHEDULE_ENABLED", True),

        chest_reset_post_enabled=_env_bool("CHEST_RESET_POST_ENABLED", False),
        chest_reset_post_time_utc=_validate_hhmm(
            "CHEST_RESET_POST_TIME_UTC",
            os.getenv("CHEST_RESET_POST_TIME_UTC", "17:00").strip() or "17:00",
        ),
        chest_report_chunk_size=_env_int("CHEST_REPORT_CHUNK_SIZE", 20, 1),
        roster_access_sync_minutes=_env_int("ROSTER_ACCESS_SYNC_MINUTES", 10, 1),

        calendar_enabled=calendar_enabled,
        calendar_base_url=calendar_base_url,
        calendar_realm=os.getenv("CALENDAR_REALM", "Regular").strip() or "Regular",
        calendar_refresh_minutes=_env_int("CALENDAR_REFRESH_MINUTES", 30, 5),
        calendar_days=_env_int("CALENDAR_DAYS", 30, 1),
        today_enabled=_env_bool("TODAY_ENABLED", True),
        calendar_min_actions=_env_int("CALENDAR_MIN_ACTIONS", 10, 1),

        trust_exact_display_name=_env_bool("TRUST_EXACT_DISPLAY_NAME", False),
        auto_sync_nickname=_env_bool("AUTO_SYNC_NICKNAME", False),
        roster_match_threshold=_env_float("ROSTER_MATCH_THRESHOLD", 0.78, 0.0),
        state_db=Path(os.getenv("STATE_DB", "data/ozy_admin.sqlite3")).expanduser(),
        state_database_url=(os.getenv("STATE_DATABASE_URL", "").strip() or os.getenv("DATABASE_URL", "").strip() or None),
    )
