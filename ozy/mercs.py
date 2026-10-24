"""Fresh map records and durable, per-destination sighting deduplication."""
from __future__ import annotations

import asyncio
import json
import math
from dataclasses import dataclass
from datetime import datetime, timezone


@dataclass(frozen=True)
class Merc:
    object_id: str
    kingdom: int
    x: int
    y: int
    level: int | None
    seen: float
    event_start: float

    @classmethod
    def parse(cls, row: dict) -> Merc:
        def number(name):
            value = row[name]
            if isinstance(value, bool) or str(value) != str(int(value)) or int(value) < 0:
                raise ValueError("Invalid numeric Merc field")
            return int(value)

        # The read API normalizes publisher seen_at into last_seen.
        timestamp = row.get("last_seen") or row.get("seen_at")
        seen = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        if seen.tzinfo is None:
            raise ValueError("Merc seen timestamp must include a timezone")
        event_start = float(row["event_start"]) / 1000
        if not math.isfinite(event_start) or not 0 <= seen.timestamp() - event_start <= 48 * 3600:
            raise ValueError("Invalid Merc event scope")
        return cls(str(row.get("object_id") or ""), number("kingdom"), number("x"),
                   number("y"), number("level") if row.get("level") is not None else None,
                   seen.timestamp(), event_start)

    @property
    def keys(self):
        keys = [f"location:{self.kingdom}:{self.x}:{self.y}"]
        if self.object_id:
            keys.append(f"object:{self.kingdom}:{self.object_id}")
        return keys

    @property
    def signature(self):
        return [self.kingdom, self.x, self.y, self.level]

    def message(self):
        return f"```\nK:{self.kingdom} X:{self.x} Y:{self.y}\n```"


class MercFeed:
    def __init__(self, state, channel_id):
        self.state = state
        self.key = f"merc_feed:v1:{channel_id}"
        self.records = None
        self.saved = None
        self.lock = asyncio.Lock()

    async def flush(self):
        payload = json.dumps(self.records, sort_keys=True)
        if payload != self.saved:
            await asyncio.to_thread(self.state.set_value, self.key, payload)
            self.saved = payload

    async def publish(self, rows, send, *, now=None):
        async with self.lock:
            return await self._publish(rows, send, now=now)

    async def _publish(self, rows, send, *, now=None):
        now = datetime.now(timezone.utc).timestamp() if now is None else now
        if self.records is None:
            raw = await asyncio.to_thread(self.state.get_value, self.key)
            self.records = json.loads(raw) if raw else {}
            if not isinstance(self.records, dict):
                raise ValueError("Invalid Merc feed state")
            self.saved = raw
        # Retry a failed persistence write before delivering anything else.
        await self.flush()
        posted = invalid = 0
        for row in rows:
            try:
                merc = Merc.parse(row)
            except (KeyError, ValueError, TypeError, AttributeError, OverflowError):
                invalid += 1
                continue
            # Recheck freshness as a batch may take time to deliver.
            current = max(now, datetime.now(timezone.utc).timestamp())
            if not -5 <= current - merc.seen <= 90:
                continue
            key = f"event:{int(merc.event_start)}:{merc.keys[0]}"
            if key in self.records:
                continue
            # Adopt existing v1 delivery evidence without clearing any history.
            previous = [self.records[k] for k in merc.keys if k in self.records]
            duplicate = any(p.get("seen", 0) >= merc.event_start for p in previous)
            self.records[key] = {"seen": merc.seen, "signature": merc.signature,
                                 "status": "legacy" if duplicate else "attempted"}
            # Commit locally AND to the production remote snapshot before send.
            # An ambiguous send consumes its claim and is never retried.
            await self.flush()
            if duplicate:
                continue
            await send(merc.message())
            posted += 1
            self.records[key]["status"] = "sent"
            await self.flush()
        await self.flush()
        return posted, invalid
