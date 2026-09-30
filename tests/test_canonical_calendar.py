import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from ozy.event_calendar import CalendarSourceError, TournamentCalendarClient, parse_canonical_calendar, build_today_chunks


def payload():
    return {"schema_version": 1, "fetched_at": "2026-10-01T17:00:00Z", "health": {"status": "ok"}, "events": [
        {"name": "Dark Omens", "kind": "regular", "starts_at": "2026-10-01T17:00:00Z", "ends_at": "2026-10-03T17:00:00Z", "details": "Evolved Mechanics", "actions": [
            {"timestamp": "2026-10-01T17:00:00Z", "action": "STARTS"},
            {"timestamp": "2026-10-02T17:00:00Z", "action": "CONTINUE"},
            {"timestamp": "2026-10-03T17:00:00Z", "action": "ENDS"}]},
        {"name": "Silver Rush", "kind": "mini", "starts_at": "2026-10-01T18:00:00Z", "ends_at": None, "bonus": "+25%"}]}


def test_contract_preserves_dark_omens_actions_and_minis():
    snap = parse_canonical_calendar(payload())
    assert [a.action for a in snap.actions] == ["STARTS", "CONTINUE", "ENDS"]
    assert snap.actions[0].timestamp_utc == datetime(2026,10,1,17,tzinfo=timezone.utc)
    text = '\n'.join(build_today_chunks(snap, target_date=datetime(2026,10,2).date()))
    assert 'Active / continues' in text and 'Dark Omens' in text and 'Evolved Mechanics' in text
    assert snap.semantic_hash == parse_canonical_calendar({**payload(), "fetched_at": "2026-10-01T17:05:00Z"}).semantic_hash


def test_invalid_contract_is_rejected():
    for data in ({}, {"schema_version": 2}, {"schema_version": 1, "events": []}):
        with pytest.raises(CalendarSourceError):
            parse_canonical_calendar(data)


def test_only_canonical_url_is_requested_and_failure_retains_snapshot():
    class Response:
        status = 200
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def json(self, **kwargs): return payload()
    class Session:
        calls = []
        response = Response()
        def get(self, url, **kwargs):
            self.calls.append(url)
            return self.response
    async def run():
        session = Session()
        client = TournamentCalendarClient(SimpleNamespace(calendar_base_url='https://ozy.com.ar/api/ozy/events',http_timeout_seconds=15), session)
        first = await client.refresh()
        assert first.changed
        second = await client.refresh_akurier()
        assert not second.changed
        session.response.status = 503
        failed = await client.refresh(force=True)
        assert failed.snapshot == first.snapshot
        assert client.last_error
        assert session.calls == ['https://ozy.com.ar/api/ozy/events'] * 3
    asyncio.run(run())
