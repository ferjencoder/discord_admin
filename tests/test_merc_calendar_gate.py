import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ozy.event_calendar import parse_canonical_calendar
from ozy.sync_signals import MercCalendarGate


def calendar(active):
    now = datetime.now(timezone.utc)
    return parse_canonical_calendar({
        'schema_version': 1, 'fetched_at': now.isoformat(),
        'events': [{'name': 'Mercenary Exchange' if active else 'Other Event',
                    'kind': 'regular', 'starts_at': (now-timedelta(minutes=1)).isoformat(),
                    'ends_at': (now+timedelta(hours=1)).isoformat()}],
    })


def test_real_discovery_signal_reloads_corrected_calendar_before_delivery():
    async def check():
        client = SimpleNamespace(snapshot=calendar(False))
        async def refresh(**kwargs):
            client.snapshot = calendar(True)
        client.refresh = AsyncMock(side_effect=refresh)
        gate = MercCalendarGate(clock=lambda: 100)
        assert not await gate.active(client, signaled=False)
        client.refresh.assert_not_awaited()
        assert await gate.active(client, signaled=True)
        client.refresh.assert_awaited_once_with(force=True)
        assert await gate.active(client, signaled=True)
        assert await gate.active(client, signaled=False)
        assert client.refresh.await_count == 1
    asyncio.run(check())


@pytest.mark.parametrize('fails', [False, True])
def test_inactive_or_failed_refresh_stays_closed_and_bounds_signal_bursts(fails):
    async def check():
        clock = [100]
        client = SimpleNamespace(snapshot=calendar(False), refresh=AsyncMock(
            side_effect=RuntimeError('unavailable') if fails else None))
        gate = MercCalendarGate(clock=lambda: clock[0])
        assert not await gate.active(client, signaled=True)
        for _ in range(10):
            assert not await gate.active(client, signaled=True)
        assert client.refresh.await_count == 1
        clock[0] = 160
        assert not await gate.active(client, signaled=False)
        assert client.refresh.await_count == 1
        assert not await gate.active(client, signaled=True)
        assert client.refresh.await_count == 2
    asyncio.run(check())


def test_missing_client_and_shutdown_do_not_open_gate():
    async def check():
        gate = MercCalendarGate()
        assert not await gate.active(None, signaled=True)
        client = SimpleNamespace(snapshot=None, refresh=AsyncMock(side_effect=asyncio.CancelledError))
        with pytest.raises(asyncio.CancelledError):
            await gate.active(client, signaled=True)
    asyncio.run(check())
