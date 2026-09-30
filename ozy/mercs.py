"""Fresh map records and durable, per-destination sighting deduplication."""
from __future__ import annotations

import asyncio
import json
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
        return cls(str(row.get("object_id") or ""), number("kingdom"), number("x"),
                   number("y"), number("level") if row.get("level") is not None else None,
                   seen.timestamp())

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

    async def flush(self):
        payload = json.dumps(self.records, sort_keys=True)
        if payload != self.saved:
            await asyncio.to_thread(self.state.set_value, self.key, payload)
            self.saved = payload

    async def publish(self, rows, send, *, now=None):
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
            previous = [self.records[k] for k in merc.keys if k in self.records]
            newest = max((p["seen"] for p in previous), default=0)
            if merc.seen < newest:
                continue  # An out-of-order scanner must not revert newer state.
            duplicate = any(p["seen"] == newest and p["signature"] == merc.signature and merc.seen - p["seen"] <= 90
                            for p in previous)
            if not duplicate:
                await send(merc.message())
                posted += 1
            for key in merc.keys:
                self.records[key] = {"seen": merc.seen, "signature": merc.signature}
            # Persist immediately after a send; retain memory if persistence fails.
            if not duplicate:
                await self.flush()
        self.records = {k: v for k, v in self.records.items() if now - v["seen"] <= 86400}
        await self.flush()
        return posted, invalid
