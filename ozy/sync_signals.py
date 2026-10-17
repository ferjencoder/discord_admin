"""Change-triggered wake-ups; waiting on an asyncio Event does no database work."""
import asyncio
import hmac
import os
import time
from datetime import datetime, timezone
from aiohttp import web

def merc_event_active(snapshot, now=None):
    if snapshot is None:
        return False
    now = now or datetime.now(timezone.utc)
    fetched = snapshot.fetched_at_utc
    if fetched is None or not 0 <= (now-fetched).total_seconds() <= 26*3600:
        return False
    actions = sorted(snapshot.actions, key=lambda a:a.timestamp_utc)
    for start in actions:
        name = start.title.casefold().replace('’', "'")
        if 'mercenar' not in name or 'exchange' not in name or start.action != 'STARTS':
            continue
        end = next((a for a in actions if a.key == start.key and a.action == 'ENDS' and a.timestamp_utc > start.timestamp_utc), None)
        if end and start.timestamp_utc <= now < end.timestamp_utc:
            return True
    return False


class MercCalendarGate:
    """Refresh an inactive calendar on discovery signals, never on idle ticks."""

    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.next_refresh = 0.0

    async def active(self, client, *, signaled):
        if client is None:
            return False
        if merc_event_active(client.snapshot):
            return True
        if not signaled or self.clock() < self.next_refresh:
            return False
        # Bound bursts and failures to one read per minute. This only reads the
        # website cache; it does not refresh providers or post calendar messages.
        self.next_refresh = self.clock() + 60
        try:
            await client.refresh(force=True)
        except asyncio.CancelledError:
            raise
        except Exception:
            return False
        return merc_event_active(client.snapshot)


def install_sync_signal(app, wake, token=None, merc_wake=None):
    token = os.environ.get('OZY_AUTH_SERVICE_TOKEN','') if token is None else token
    async def changed(request):
        provided=request.headers.get('Authorization','')
        if len(token)<32 or not hmac.compare_digest(provided.encode(),('Bearer '+token).encode()):
            return web.json_response({'error':'unauthorized'},status=401)
        if request.content_length not in (None,0):
            return web.json_response({'error':'body_not_needed'},status=400)
        (merc_wake if request.path=='/internal/mercs-changed' else wake).set()
        return web.json_response({'accepted':True},status=202,headers={'Cache-Control':'no-store'})
    app.router.add_post('/internal/website-changed',changed)
    if merc_wake is not None:app.router.add_post('/internal/mercs-changed',changed)

async def run_changes(wake, drain, is_closed, sleep=asyncio.sleep):
    wake.set()  # One recovery pass after restart; no periodic idle polling.
    failures=0
    while not is_closed():
        await wake.wait()
        wake.clear()
        try:
            pending=await drain()
        except asyncio.CancelledError:
            raise
        except Exception:
            pending=True
        if pending:
            failures+=1
            await sleep(min(3600,60*2**min(failures-1,6)))
            wake.set()
        else:
            failures=0


async def wait_merc_signal(wake, timeout):
    """Wake immediately on discovery; timeout only recovers a lost notification."""
    try:
        await asyncio.wait_for(wake.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        pass
