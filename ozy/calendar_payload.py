"""Canonical event merging and timing; the Worker only stores/serves the result."""
import hashlib
import json
import math
import re
import unicodedata
from datetime import datetime, timezone


def event_key(value):
    value = unicodedata.normalize('NFKD', str(value or '')).encode('ascii', 'ignore').decode().lower()
    key = re.sub('[^a-z0-9]+', '-', value).strip('-')
    return {'mercenaries-exchange': 'mercenary-exchange', 'arachne-s-swarm': 'arachne', 'ancients-treasure': 'ancient-s-treasure'}.get(key, key)


def stamp(value):
    try:
        return datetime.fromisoformat(str(value).replace('Z', '+00:00')).timestamp()
    except (ValueError, TypeError):
        return None


def canonical(snapshots, urls, now=None):
    now = now or datetime.now(timezone.utc)
    instant = now.timestamp()
    at = now.isoformat(timespec='milliseconds').replace('+00:00', 'Z')
    priorities = {'nexus': 1, 'akurier': 1, 'totalcalculator': 3}
    candidates, health = [], {}
    for source, url in urls.items():
        saved = snapshots.get(source)
        fetched = stamp(saved.get('fetched_at')) if saved else None
        age = max(0, math.floor(instant - fetched)) if fetched else None
        usable = age is not None and age <= 86400
        health[source] = {'provider': source, 'url': url, 'ok': bool(usable and age <= 300), 'stale': not usable or age > 300,
                         'fetched_at': saved.get('fetched_at') if saved else None, 'age_seconds': age, 'count': len(saved['events']) if usable else 0, 'error': None if usable else 'Source unavailable'}
        if usable:
            for item in saved['events']:
                candidates.append({**item, 'source': source, 'source_url': url, 'provider_fetched_at': saved['fetched_at'], 'event_key': event_key(item['name']), 'details': item.get('details', '')})
    def rank(item):
        return 0.5 if item['kind'] == 'mini' and item['source'] == 'akurier' else priorities[item['source']]
    candidates.sort(key=lambda e: (rank(e), json.dumps(e, ensure_ascii=False, separators=(',', ':'))))
    merged = []
    for item in candidates:
        start, end = stamp(item.get('starts_at')), stamp(item.get('ends_at'))
        match = None
        for other in merged:
            other_start, other_end = stamp(other.get('starts_at')), stamp(other.get('ends_at'))
            if item['event_key'] != other['event_key'] or item['kind'] != other['kind']:
                continue
            if item['details'] and other['details'] and event_key(item['details']) != event_key(other['details']):
                continue
            same = abs(start-other_start) <= (43200 if item['kind'] == 'regular' else 300) if start is not None and other_start is not None else (
                other_start <= instant < other_end if item.get('reported_current') and other_start is not None and other_end is not None else start is None and other_start is None and item.get('ends_at') == other.get('ends_at'))
            if same:
                match = other
                break
        metadata = {'details': item['details'], 'bonus': item.get('bonus', ''), 'actions': item.get('actions', []), 'source': item['source'], 'source_url': item['source_url'],
                    'starts_at': item.get('starts_at'), 'ends_at': item.get('ends_at'), 'reported_current': bool(item.get('reported_current')), 'fetched_at': item['provider_fetched_at'], 'raw_name': item['name']}
        if match:
            match['source_metadata'].append(metadata)
            if not match.get('ends_at') and item.get('ends_at'):
                match['ends_at'], match['end_basis'] = item['ends_at'], 'secondary-provider'
        else:
            merged.append({**item, 'source_metadata': [metadata]})
    for item in merged:
        if item['kind'] == 'mini' and not item.get('ends_at') and item.get('starts_at'):
            after = sorted((e for e in merged if e['kind'] == 'mini' and e['source'] == item['source'] and e.get('starts_at', '') > item['starts_at']), key=lambda e: e['starts_at'])
            if after:
                item['ends_at'], item['end_basis'] = after[0]['starts_at'], 'next-mini-start'
        start, end = stamp(item.get('starts_at')), stamp(item.get('ends_at'))
        known = start is not None and end is not None and end > start
        current = start <= instant < end if known else False if start is not None and start > instant else None
        status = 'active' if current else 'upcoming' if start is not None and start > instant else 'ended' if known else 'unknown'
        identity = f"{item['kind']}|{item['event_key']}|{event_key(item['details'])}|{item.get('starts_at') or item.get('ends_at') or 'unknown'}"
        item.update(id=hashlib.sha256(identity.encode()).hexdigest()[:24], current=current, status=status, minutes_until=max(0, math.ceil((start-instant)/60)) if start else None,
                    sources=list(dict.fromkeys(m['source'] for m in item['source_metadata'])), timing_conflict=any((m['starts_at'] and item.get('starts_at') and m['starts_at'] != item['starts_at']) or (m['ends_at'] and item.get('ends_at') and m['ends_at'] != item['ends_at']) for m in item['source_metadata']))
    merged.sort(key=lambda e: (e.get('starts_at') or '', e['id']))
    return {'schema_version': 1, 'fetched_at': at, 'generated_at': at, 'poll_after_seconds': 900, 'reset_time_argentina': '14:00', 'reset_time_utc': '17:00',
            'health': {'status': 'unavailable' if not merged else 'degraded' if any(not p['ok'] for p in health.values()) else 'ok', 'providers': health},
            'events': merged, 'groups': {kind: [e for e in merged if e['kind'] == kind and e['status'] in ('active', 'upcoming')] for kind in ('regular', 'mini')},
            'source_url': '/api/ozy/events', 'source_urls': {'regular': '/api/ozy/events', 'mini': '/api/ozy/events'},
            'sources': {kind: {'provider': 'OZY Calendar', 'ok': any(e['kind'] == kind for e in merged), 'count': sum(e['kind'] == kind for e in merged)} for kind in ('regular', 'mini')}}
