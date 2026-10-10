import asyncio
from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import aiohttp
import pytest

from ozy.mercs import MercFeed
from ozy.state import AdminState
from ozy.data_provider import DataProvider, DataUnavailable
from test_data_provider import make_settings


def row(at, **changes):
    return dict(object_id="123", kingdom=35, x=843, y=603, level=10,
                last_seen=datetime.fromtimestamp(at, timezone.utc).isoformat(), **changes)


def test_read_api_timestamp_takes_precedence():
    from ozy.mercs import Merc
    record = dict(object_id="1374476599231", kind="merc", kingdom=320,
                  x=156, y=312, static_id=400, level=10,
                  first_seen="2026-09-30T01:39:10Z", last_seen="2026-09-30T01:39:10Z",
                  seen_count=1, scanner_id="scanner-02", age_seconds=25)
    merc = Merc.parse(record)
    assert "K:320 X:156 Y:312" in merc.message()
    assert Merc.parse({**record, "seen_at": "2020-01-01T00:00:00Z"}).seen == merc.seen
    record["seen_at"] = record.pop("last_seen")
    assert Merc.parse(record).seen == merc.seen


def test_dedup_updates_restart_and_destination(tmp_path):
    async def run():
        now = datetime.now(timezone.utc).timestamp()
        state = AdminState(tmp_path / "state.db")
        send = AsyncMock()
        feed = MercFeed(state, 1)
        original = row(now)
        await feed.publish([original, original], send, now=now)
        assert send.await_count == 1
        assert send.call_args.args[0] == "```\nK:35 X:843 Y:603\n```"
        # Continuous scanner refresh and a process restart don't repeat the post.
        await MercFeed(state, 1).publish([row(now + 15)], send, now=now + 15)
        assert send.await_count == 1
        await feed.publish([{**row(now + 30), "level": 11}], send, now=now + 30)
        assert send.await_count == 2
        await feed.publish([{**row(now + 45), "x": 844}], send, now=now + 45)
        assert send.await_count == 3
        await feed.publish([row(now + 140)], send, now=now + 140)
        assert send.await_count == 4
        await MercFeed(state, 2).publish([row(now + 140)], send, now=now + 140)
        assert send.await_count == 5
    asyncio.run(run())


def test_location_alias_stale_out_of_order_and_bad_records(tmp_path):
    async def run():
        now = datetime.now(timezone.utc).timestamp()
        send = AsyncMock()
        feed = MercFeed(AdminState(tmp_path / "state.db"), 1)
        await feed.publish([row(now)], send, now=now)
        await feed.publish([{**row(now + 10), "object_id": "another-scanner-id"}], send, now=now + 10)
        await feed.publish([{**row(now), "level": 9}, row(now - 200), None, {},
                            {**row(now), "x": "bad"}], send, now=now)
        assert send.await_count == 1
    asyncio.run(run())


def test_failed_delivery_retries_and_persistence_failure_keeps_memory(tmp_path):
    async def run():
        now = datetime.now(timezone.utc).timestamp()
        state = AdminState(tmp_path / "state.db")
        feed = MercFeed(state, 1)
        send = AsyncMock(side_effect=RuntimeError("send failed"))
        with pytest.raises(RuntimeError):
            await feed.publish([row(now)], send, now=now)
        send.side_effect = None
        await feed.publish([row(now)], send, now=now)
        assert send.await_count == 2
        real_set = state.set_value
        state.set_value = lambda *args: (_ for _ in ()).throw(OSError("disk"))
        with pytest.raises(OSError):
            await feed.publish([{**row(now + 10), "level": 11}], send, now=now + 10)
        assert send.await_count == 3
        state.set_value = real_set
        await feed.publish([{**row(now + 10), "level": 11}], send, now=now + 10)
        assert send.await_count == 3
    asyncio.run(run())


class Response:
    def __init__(self, status=200, payload=None):
        self.status = status
        self.payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def json(self, **kwargs):
        return self.payload


class Session:
    def __init__(self, response):
        self.response = response
        self.calls = []

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if isinstance(self.response, Exception):
            raise self.response
        return self.response


@pytest.mark.parametrize("response", [Response(401), Response(503), Response(302),
                                      Response(payload={"wrong": []}),
                                      asyncio.TimeoutError(), aiohttp.ClientError("secret")])
def test_api_errors_are_safe(tmp_path, response):
    provider = DataProvider(replace(make_settings(tmp_path), ozy_data_api_token="private"), Session(response))
    with pytest.raises(DataUnavailable) as error:
        asyncio.run(provider.current_mercs())
    assert "private" not in str(error.value)
    assert "secret" not in str(error.value)


def test_api_not_cached_and_auth_no_redirects(tmp_path):
    session = Session(Response(payload={"mercs": []}))
    provider = DataProvider(replace(make_settings(tmp_path), ozy_data_api_token="private"), session)
    async def run():
        assert await provider.current_mercs() == []
        assert await provider.current_mercs() == []
    asyncio.run(run())
    assert len(session.calls) == 2
    assert session.calls[0][1]["headers"] == {"X-OZY-Admin-Token": "private"}
    assert session.calls[0][1]["allow_redirects"] is False


def test_background_loop_recovers_and_cancels(tmp_path, monkeypatch):
    from types import SimpleNamespace
    import bot

    async def run():
        sleeps = []
        async def sleep(delay):
            sleeps.append(delay)
            if len(sleeps) == 2:
                raise asyncio.CancelledError()

        channel = SimpleNamespace(guild=SimpleNamespace(id=1), send=AsyncMock())
        client = SimpleNamespace(
            state=AdminState(tmp_path / "state.db"),
            settings=SimpleNamespace(mercs_channel_id=1, server_id=1, mercs_poll_seconds=15),
            data=SimpleNamespace(current_mercs=AsyncMock(side_effect=[DataUnavailable("Merc API HTTP 401"), []])),
            is_closed=lambda: False, wait_until_ready=AsyncMock(), get_channel=lambda _: channel,
        )
        monkeypatch.setattr(bot, "merc_event_active", lambda snapshot: True)
        client.calendar_client = SimpleNamespace(snapshot=None)
        monkeypatch.setattr(bot.discord, "TextChannel", SimpleNamespace)
        monkeypatch.setattr(bot.asyncio, "sleep", sleep)
        with pytest.raises(asyncio.CancelledError):
            await bot.OZYAdminBot._mercs_loop(client)
        assert client.data.current_mercs.await_count == 2
        assert sleeps == [30, 15]
        channel.send.assert_not_awaited()
    asyncio.run(run())
