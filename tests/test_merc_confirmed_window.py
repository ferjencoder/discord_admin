import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from ozy.sync_signals import MercCalendarGate, confirmed_merc_window, merc_event_active


def test_confirmed_window_survives_missing_calendar_and_expires(monkeypatch):
    monkeypatch.setenv('MERCS_EVENT_START_UTC', '2026-10-29T17:00:00Z')
    monkeypatch.setenv('MERCS_EVENT_END_UTC', '2026-10-30T17:00:00Z')
    for stamp, expected in [('2026-10-29T16:59:59', False), ('2026-10-29T17:00:00', True),
                            ('2026-10-30T16:59:59', True), ('2026-10-30T17:00:00', False)]:
        assert merc_event_active(None, datetime.fromisoformat(stamp).replace(tzinfo=timezone.utc)) is expected


@pytest.mark.parametrize('start,end', [('', '2026-10-30T17:00:00Z'), ('invalid', 'invalid'),
    ('2026-10-29T17:00:00', '2026-10-30T17:00:00Z'),
    ('2026-10-30T17:00:00Z', '2026-10-29T17:00:00Z'),
    ('2026-10-29T17:00:00Z', '2026-11-02T17:00:00Z')])
def test_invalid_override_never_falls_back_or_refreshes(monkeypatch, start, end):
    monkeypatch.setenv('MERCS_EVENT_START_UTC', start)
    monkeypatch.setenv('MERCS_EVENT_END_UTC', end)
    assert confirmed_merc_window() == ()
    client = SimpleNamespace(snapshot=object(), refresh=AsyncMock())
    assert not asyncio.run(MercCalendarGate().active(client, signaled=True))
    client.refresh.assert_not_awaited()


def test_unconfigured_keeps_calendar_behavior(monkeypatch):
    monkeypatch.delenv('MERCS_EVENT_START_UTC', raising=False)
    monkeypatch.delenv('MERCS_EVENT_END_UTC', raising=False)
    assert confirmed_merc_window() is None
