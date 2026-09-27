import asyncio
import json
import time
from datetime import datetime, timezone

import aiohttp
import pytest

from ozy.data_provider import DataProvider, DataUnavailable
from test_data_provider import make_settings


def provider(tmp_path, **extra):
    settings = make_settings(tmp_path)
    data = {"weekly_target": 100, "weeks": [
        {"start_at": "2026-09-06T17:00Z", "end_at": "2026-09-13T17:00Z",
         "members": [{"name": "Alpha", "points": 50, "chests": 12}]},
        {"start_at": "2026-09-13T17:00Z", "end_at": "2026-09-20T17:00Z",
         "members": [{"name": "Alpha", "points": 75, "chests": 15}]}], **extra}
    settings.chest_data_file.write_text(json.dumps(data), encoding="utf-8")
    settings.roster_file.write_text(json.dumps({"members": {"Alpha": {"user_id": "1"}}}), encoding="utf-8")
    return DataProvider(settings, None)


@pytest.mark.parametrize("instant,points", [
    ("2026-09-13T16:59:59+00:00", 50),
    ("2026-09-13T17:00:00+00:00", 75),
    ("2026-09-13T14:00:00-03:00", 75),
    ("2026-09-14T02:00:00+09:00", 75),
])
def test_exact_reset_and_display_timezone(tmp_path, instant, points):
    p = provider(tmp_path)
    async def run():
        at = datetime.fromisoformat(instant)
        assert (await p.chest_stats("Alpha", today=at)).points == points
        board = await p.chest_leaderboard(today=at)
        assert board.total_points == points
        assert "activation unconfirmed" in board.source_note
    asyncio.run(run())


def test_no_historical_fallback(tmp_path):
    p = provider(tmp_path)
    async def run():
        at = datetime(2026, 9, 21, tzinfo=timezone.utc)
        assert await p.chest_stats("Alpha", at) is None
        assert await p.chest_leaderboard(at) is None
    asyncio.run(run())


@pytest.mark.parametrize("extra", [{"mode": "shadow"}, {"counter": {"active": False}},
                                      {"clan_tag": "SAM"}, {"clan_id": "123"},
                                      {"roster_meta": {"clan": "HAS"}}, {"error": "bad"}])
def test_wrong_clan_and_inactive_sources_rejected(tmp_path, extra):
    p = provider(tmp_path, **extra)
    with pytest.raises(DataUnavailable):
        asyncio.run(p.chest_leaderboard(datetime(2026, 9, 10, tzinfo=timezone.utc)))


def test_website_quantities_points_target_and_identity(tmp_path):
    p = provider(tmp_path, weekly_target=999, weeks=[{
        "start_at": "2026-09-06T17:00Z", "end_at": "2026-09-13T17:00Z", "weekly_target": 0,
        "members": [{"name": "Old Alpha", "user_id": "1", "points": 1234, "chests": 80, "met_target": True}]}])
    board = asyncio.run(p.chest_leaderboard(datetime(2026, 9, 10, tzinfo=timezone.utc)))
    assert board.target == 0
    assert (board.members[0].name, board.total_points, board.total_chests) == ("Alpha", 1234, 80)
    assert not board.members[0].met_target
    stats = asyncio.run(p.chest_stats("Alpha", datetime(2026, 9, 10, tzinfo=timezone.utc)))
    assert (stats.player, stats.points, stats.chests) == ("Alpha", 1234, 80)
    assert asyncio.run(p.resolve_roster_member(game_name="Alpha", game_user_id="wrong")) is None


@pytest.mark.parametrize("error", [aiohttp.ClientConnectionError("contains secret URL"), asyncio.TimeoutError(), json.JSONDecodeError("bad", "", 0)])
def test_expired_cache_never_hides_fetch_failure(tmp_path, error):
    p = provider(tmp_path)
    class Session:
        def get(self, *args, **kwargs):
            raise error
    p.session = Session()
    p._cache["chests"] = (time.monotonic() - 1000, {"old": True})
    with pytest.raises(DataUnavailable) as exc:
        asyncio.run(p._load_json("chests", "https://example.test", p.settings.chest_data_file))
    assert "secret" not in str(exc.value)


def test_http_auth_and_redirects(tmp_path):
    p = provider(tmp_path)
    class Response:
        status = 401
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
    class Session:
        def get(self, *args, **kwargs):
            assert kwargs["allow_redirects"] is False
            return Response()
    p.session = Session()
    p._cache["chests"] = (time.monotonic() - 1000, {})
    with pytest.raises(DataUnavailable, match="401"):
        asyncio.run(p._load_json("chests", "https://example.test", p.settings.chest_data_file, ozy_api_auth=True))


def test_freshness_visible_in_ranking(tmp_path):
    from ozy.utils import format_chest_ranking_blocks
    p = provider(tmp_path, generated="2026-01-01T00:00Z")
    board = asyncio.run(p.chest_leaderboard(datetime(2026, 9, 10, tzinfo=timezone.utc)))
    rendered = format_chest_ranking_blocks(board)[0]
    assert "older than 24 hours" in rendered
    assert "17:00 UTC (end exclusive)" in rendered


def test_invalid_counts_are_data_unavailable(tmp_path):
    p = provider(tmp_path)
    for value in ("bad", -1, 1.5, True):
        with pytest.raises(DataUnavailable):
            p._count(value)


def test_identity_conflict_and_ambiguity_fail_closed(tmp_path):
    p = provider(tmp_path)
    for members in ([{"name": "Alpha", "user_id": "other"}],
                    [{"name": "Alpha"}, {"name": "Alpha"}]):
        with pytest.raises(DataUnavailable):
            p._chest_member({"members": members}, "Alpha", "1")


def test_exact_spelling_wins_over_case_variant(tmp_path):
    p = provider(tmp_path)
    rows = [{"name": "Rega", "points": 20973}, {"name": "REGA", "points": 200}]
    assert p._chest_member({"members": rows}, "Rega", None)["points"] == 20973
    with pytest.raises(DataUnavailable):
        p._chest_member({"members": rows}, "rega", None)
