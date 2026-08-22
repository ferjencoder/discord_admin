import asyncio
import json
from datetime import date
from pathlib import Path

from data_provider import DataProvider
from settings import Settings


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        discord_token="x",
        server_id=1,
        port=10000,
        render_external_url=None,
        self_ping_enabled=False,
        self_ping_interval_seconds=600,
        leadership_role_ids=frozenset(),
        rank_role_map={},
        away_role_id=None,
        welcome_channel_id=None,
        announcement_channel_id=None,
        announcement_ping_role_id=None,
        schedule_channel_id=None,
        calendar_channel_id=None,
        today_channel_id=None,
        away_channel_id=None,
        audit_channel_id=None,
        roster_url=None,
        roster_file=tmp_path / "roster.json",
        chest_data_url=None,
        chest_data_file=tmp_path / "chests.json",
        schedule_url=None,
        schedule_file=tmp_path / "schedule.json",
        chats_file=tmp_path / "chats.json",
        data_cache_seconds=60,
        http_timeout_seconds=10.0,
        schedule_timezone="America/Argentina/Buenos_Aires",
        daily_schedule_time="08:00",
        daily_schedule_enabled=True,
        voltron_calendar_enabled=True,
        voltron_base_url="https://nexusportal.voltron.me",
        voltron_realm="Regular",
        voltron_refresh_minutes=30,
        voltron_calendar_days=30,
        voltron_today_time="08:00",
        voltron_today_enabled=True,
        voltron_min_actions=10,
        trust_exact_display_name=False,
        auto_sync_nickname=False,
        roster_match_threshold=0.78,
        state_db=tmp_path / "state.sqlite3",
    )


def test_roster_and_suggestions(tmp_path):
    s = make_settings(tmp_path)
    s.roster_file.write_text(json.dumps({"members": {
        "PeekABoo Death": {"status": "active", "rank": "Superior"},
        "Old": {"status": "removed", "rank": "Soldier"}
    }}), encoding="utf-8")
    provider = DataProvider(s, None)

    async def run():
        roster = await provider.roster()
        assert list(roster) == ["PeekABoo Death"]
        assert await provider.exact_roster_name("peekaboo death") == "PeekABoo Death"
        matches = await provider.roster_suggestions("PeekABoo Deth")
        assert matches[0].name == "PeekABoo Death"

    asyncio.run(run())


def test_current_chest_week(tmp_path):
    s = make_settings(tmp_path)
    s.chest_data_file.write_text(json.dumps({
        "weekly_target": 1000,
        "weeks": [{
            "label": "16-22 Aug 2026",
            "start": "2026-08-16",
            "end": "2026-08-22",
            "members": [{
                "name": "PeekABoo Death",
                "points": 1250,
                "chests": 84,
                "breakdown": {"L35 epic Crypt": 2}
            }]
        }]
    }), encoding="utf-8")
    provider = DataProvider(s, None)

    async def run():
        stats = await provider.chest_stats("peekaboo death", today=date(2026, 8, 21))
        assert stats is not None
        assert stats.points == 1250
        assert stats.chests == 84
        assert stats.met_target is True

    asyncio.run(run())


def test_schedule_date_and_weekday(tmp_path):
    s = make_settings(tmp_path)
    s.schedule_file.write_text(json.dumps({
        "events": [
            {"weekdays": ["Friday"], "time": "14:00", "title": "Reset"},
            {"date": "2026-08-21", "time": "19:00", "title": "War"},
            {"weekdays": ["Monday"], "time": "10:00", "title": "Wrong day"}
        ]
    }), encoding="utf-8")
    provider = DataProvider(s, None)

    async def run():
        items = await provider.schedule_for_date(date(2026, 8, 21))
        assert [x.title for x in items] == ["Reset", "War"]

    asyncio.run(run())
