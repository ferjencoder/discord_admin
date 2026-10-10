import asyncio
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
from aiohttp import web
from aiohttp.test_utils import TestClient,TestServer
from ozy.sync_signals import merc_event_active,run_changes,install_sync_signal
from ozy.event_calendar import CalendarSnapshot,CalendarAction

def test_merc_requests_only_inside_known_exchange_window():
    at=datetime(2026,10,10,17,tzinfo=timezone.utc)
    start=CalendarAction(at,'STARTS','Mercenaries Exchange')
    end=CalendarAction(at+timedelta(days=2),'ENDS','Mercenaries Exchange')
    snapshot=CalendarSnapshot((start,end),(),'test',fetched_at_utc=at)
    assert merc_event_active(snapshot,at)
    assert merc_event_active(snapshot,at+timedelta(hours=1))
    assert not merc_event_active(snapshot,at-timedelta(seconds=1))
    assert not merc_event_active(snapshot,at+timedelta(days=2))
    assert not merc_event_active(snapshot,at+timedelta(hours=27))
    assert not merc_event_active(CalendarSnapshot((start,),(),'test',fetched_at_utc=at),at)
    assert not merc_event_active(None,at)

def test_idle_sync_does_not_poll_and_notifications_coalesce():
    async def check():
        wake=asyncio.Event();calls=[]
        async def drain():calls.append(1);return 0
        task=asyncio.create_task(run_changes(wake,drain,lambda:False))
        try:
            await asyncio.sleep(0.02);assert len(calls)==1
            await asyncio.sleep(0.02);assert len(calls)==1
            wake.set();wake.set();await asyncio.sleep(0.02);assert len(calls)==2
            await asyncio.sleep(0.02);assert len(calls)==2
        finally:
            task.cancel()
            try:await task
            except asyncio.CancelledError:pass
    asyncio.run(check())

def test_failed_change_retries_then_returns_to_idle():
    async def check():
        wake=asyncio.Event();calls=[];delays=[]
        async def drain():calls.append(1);return len(calls)<3
        async def sleep(delay):delays.append(delay);await asyncio.sleep(0)
        task=asyncio.create_task(run_changes(wake,drain,lambda:False,sleep))
        try:
            await asyncio.sleep(0.02);assert len(calls)==3;assert delays==[60,120]
            await asyncio.sleep(0.02);assert len(calls)==3
        finally:
            task.cancel()
            try:await task
            except asyncio.CancelledError:pass
    asyncio.run(check())

def test_private_change_notification_does_not_allow_public_wakeups():
    async def check():
        wake=asyncio.Event();app=web.Application();install_sync_signal(app,wake,token='t'*64)
        async with TestClient(TestServer(app)) as client:
            assert (await client.post('/internal/website-changed')).status==401
            assert not wake.is_set()
            assert (await client.post('/internal/website-changed',headers={'Authorization':'Bearer '+'t'*64})).status==202
            assert wake.is_set()
    asyncio.run(check())
