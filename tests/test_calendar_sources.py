from datetime import datetime, timezone
from aiohttp.test_utils import TestClient, TestServer
from aiohttp import web
import asyncio
from ozy.calendar_sources import parse_source, install_calendar_sources


def test_calendar_source_preserves_start_end_and_continuation():
    html='''<div class="vl-cal__row" data-utc="2026-10-09T17:00:00Z"><span>STARTS</span><span>Dragon Fair</span></div>
    <div class="vl-cal__row" data-utc="2026-10-10T17:00:00Z"><span>CONTINUE</span><span>Dragon Fair</span></div>
    <div class="vl-cal__row" data-utc="2026-10-11T17:00:00Z"><span>ENDS</span><span>Dragon Fair</span></div>'''
    rows=parse_source('nexus',html,datetime(2026,10,9,tzinfo=timezone.utc))
    assert len(rows)==1
    assert rows[0]['starts_at']=='2026-10-09T17:00:00.000Z'
    assert rows[0]['ends_at']=='2026-10-11T17:00:00.000Z'
    assert len(rows[0]['actions'])==3


def test_legacy_source_preserves_reported_unknown_ends():
    rows=parse_source('totalcalculator','Weekly events Dragon Fair 1 days 2h30m CURRENT: Ragnarok Mini events',datetime(2026,10,9,17,tzinfo=timezone.utc))
    assert rows[0]['starts_at']=='2026-10-10T19:30:00.000Z'
    assert rows[1]['reported_current'] is True


def test_private_calendar_cache_never_follows_untrusted_urls_and_avoids_duplicate_fetches():
    asyncio.run(check_http())


async def check_http():
    calls=[]
    async def loader(url):
        calls.append(url)
        return '<div class="vl-cal__row" data-utc="2026-10-09T17:00:00Z"><span>STARTS</span><span>Dragon Fair</span></div>'
    app=web.Application();install_calendar_sources(app,token='t'*64,fetch_html=loader)
    async with TestClient(TestServer(app)) as client:
        assert (await client.get('/internal/calendar/v1')).status==401
        headers={'Authorization':'Bearer '+'t'*64}
        first=await client.get('/internal/calendar/v1',headers=headers);assert first.status==200
        data=await first.json();assert data['providers']['nexus']['events'][0]['name']=='Dragon Fair'
        assert (await client.get('/internal/calendar/v1',headers=headers)).status==200
        assert len(calls)==3
