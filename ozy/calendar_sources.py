"""Bounded private source parsing for the website's small cached calendar."""
import asyncio
import hashlib
import hmac
import os
import re
import time
from datetime import datetime, timedelta, timezone
from html import unescape
from aiohttp import ClientSession, ClientTimeout, web
from ozy.event_calendar import parse_tournament_calendar_html, parse_akurier_mini_events_html

SOURCES = {'nexus': 'https://nexusportal.voltron.me/api/calendar/content?realm=Regular',
           'akurier': 'https://www.akurier.pl/events',
           'totalcalculator': 'https://totalcalculator.org/events.php'}


def iso(value):
    return value.astimezone(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


def parse_source(name, html, now):
    if name == 'nexus':
        snapshot = parse_tournament_calendar_html(html, now)
        actions = list(snapshot.actions)
        events, used_ends, used_actions = [], set(), set()
        for start in (a for a in actions if a.action == 'STARTS'):
            end = next((a for a in actions if a.action == 'ENDS' and a not in used_ends and a.key == start.key and a.timestamp_utc > start.timestamp_utc), None)
            if end:
                used_ends.add(end)
            related = [a for a in actions if a.key == start.key and (a == start or a.action != 'STARTS') and a.timestamp_utc >= start.timestamp_utc and (end is None or a.timestamp_utc <= end.timestamp_utc)]
            used_actions.update(related)
            events.append({'name': start.title, 'details': start.details, 'starts_at': iso(start.timestamp_utc), 'ends_at': iso(end.timestamp_utc) if end else None,
                           'kind': 'regular', 'actions': [{'timestamp': iso(a.timestamp_utc), 'action': a.action, 'name': a.title, 'details': a.details} for a in related]})
        for a in actions:
            if a.action != 'STARTS' and a not in used_actions:
                events.append({'name': a.title, 'details': a.details, 'starts_at': None, 'ends_at': iso(a.timestamp_utc) if a.action == 'ENDS' else None, 'kind': 'regular',
                               'actions': [{'timestamp': iso(a.timestamp_utc), 'action': a.action, 'name': a.title, 'details': a.details}]})
        events.extend({'name': m.title, 'starts_at': iso(m.start_utc), 'ends_at': iso(m.end_utc), 'kind': 'mini'} for m in snapshot.mini_tournaments)
        return events
    if name == 'akurier':
        return [{'name': m.title, 'starts_at': iso(m.start_utc), 'bonus': m.bonus, 'kind': 'mini'} for m in parse_akurier_mini_events_html(html)]
    text = ' '.join(unescape(re.sub('<[^>]*>', ' ', html)).split())
    groups, events = ['Monthly events', 'Biweekly events', 'Weekly events'], []
    for i, marker in enumerate(groups):
        index = text.find(marker)
        if index < 0:
            continue
        start = index + len(marker)
        stops = [text.find(m, start) for m in groups[i+1:] + ['Mini events', 'Note that']]
        section = text[start:min((n for n in stops if n >= 0), default=len(text))]
        currents = re.findall(r'CURRENT:\s*(.*?)$', section)
        upcoming = re.sub(r'CURRENT:\s*.*$', '', section)
        for title, duration in re.findall(r'(.+?)\s+((?:\d+\s+days?\s*)?\d+h\d+m)', upcoming):
            title = ' '.join(title.replace('=', '').split())
            if not title or len(title) > 80:
                continue
            days = int((re.search(r'(\d+)\s+days?', duration) or [None, 0])[1])
            hours, minutes = (int(x) for x in re.search(r'(\d+)h(\d+)m', duration).groups())
            at = now + timedelta(days=days, hours=hours, minutes=minutes)
            at = datetime.fromtimestamp(round(at.timestamp()/60)*60, timezone.utc)
            events.append({'name': title, 'starts_at': iso(at), 'kind': 'regular', 'cycle': marker.split()[0].lower()})
        events.extend({'name': title.strip(), 'starts_at': None, 'reported_current': True, 'kind': 'regular', 'cycle': marker.split()[0].lower()} for title in currents)
    return events


def install_calendar_sources(app, token=None, fetch_html=None, clock=time.time):
    token = os.environ.get('OZY_AUTH_SERVICE_TOKEN', '') if token is None else token
    snapshots, gate = {}, asyncio.Lock()
    last_refresh = 0
    async def fetch_default(url):
        async with ClientSession(timeout=ClientTimeout(total=12), headers={'User-Agent': 'OZY-Calendar/1'}) as session:
            async with session.get(url, allow_redirects=False) as response:
                if response.status != 200:
                    raise ValueError('source unavailable')
                data = bytearray()
                async for chunk in response.content.iter_chunked(65536):
                    data.extend(chunk)
                    if len(data) > 1500000:
                        raise ValueError('source too large')
                return data.decode('utf-8')
    loader = fetch_html or fetch_default
    async def handler(request):
        nonlocal last_refresh
        supplied = request.headers.get('Authorization', '').removeprefix('Bearer ')
        if len(token) < 32 or not hmac.compare_digest(hashlib.sha256(token.encode()).digest(), hashlib.sha256(supplied.encode()).digest()):
            return web.json_response({'error': 'unauthorized'}, status=401)
        if gate.locked():
            return web.json_response({'error': 'busy'}, status=503)
        async with gate:
            now = datetime.now(timezone.utc)
            if clock() - last_refresh >= 300 or not snapshots:
                async def load(name, url):
                    try:
                        html = await loader(url)
                        records = await asyncio.to_thread(parse_source, name, html, now)
                        if not records or len(records) > 300:
                            raise ValueError('invalid calendar')
                        snapshots[name] = {'events': records, 'fetched_at': iso(now)}
                    except Exception:
                        # The website marks dated snapshots stale and expires them.
                        pass
                await asyncio.gather(*(load(n, u) for n, u in SOURCES.items()))
                last_refresh = clock()
            return web.json_response({'providers': snapshots}, headers={'Cache-Control': 'no-store'})
    app.router.add_get('/internal/calendar/v1', handler)
