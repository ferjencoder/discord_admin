from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from datetime import date, datetime
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import aiohttp

from settings import Settings


class DataUnavailable(RuntimeError):
    pass


@dataclass(frozen=True)
class RosterMatch:
    name: str
    score: float


@dataclass(frozen=True)
class ChestStats:
    player: str
    week_label: str
    points: int
    chests: int
    target: int
    met_target: bool
    breakdown: dict[str, int]


@dataclass(frozen=True)
class ChestRankEntry:
    name: str
    points: int
    chests: int
    met_target: bool


@dataclass(frozen=True)
class ChestLeaderboard:
    week_label: str
    start: str
    end: str
    target: int
    total_points: int
    total_chests: int
    generated: str | None
    members: tuple[ChestRankEntry, ...]


@dataclass(frozen=True)
class ScheduleItem:
    time: str
    title: str
    details: str
    ping: bool = False


class DataProvider:
    def __init__(self, settings: Settings, session: aiohttp.ClientSession):
        self.settings = settings
        self.session = session
        self._cache: dict[str, tuple[float, Any]] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def _lock(self, key: str) -> asyncio.Lock:
        if key not in self._locks:
            self._locks[key] = asyncio.Lock()
        return self._locks[key]

    async def _load_json(
        self,
        key: str,
        url: str | None,
        path: Path,
        *,
        ozy_api_auth: bool = False,
    ) -> Any:
        now = time.monotonic()
        cached = self._cache.get(key)
        if cached and now - cached[0] < self.settings.data_cache_seconds:
            return cached[1]

        async with self._lock(key):
            now = time.monotonic()
            cached = self._cache.get(key)
            if cached and now - cached[0] < self.settings.data_cache_seconds:
                return cached[1]

            if url:
                try:
                    timeout = aiohttp.ClientTimeout(total=self.settings.http_timeout_seconds)
                    headers = {}
                    if ozy_api_auth and self.settings.ozy_data_api_token:
                        headers["X-OZY-Admin-Token"] = self.settings.ozy_data_api_token
                    async with self.session.get(url, timeout=timeout, headers=headers) as response:
                        if response.status != 200:
                            raise DataUnavailable(f"{key} source returned HTTP {response.status}")
                        data = await response.json(content_type=None)
                except (aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError) as exc:
                    if cached:
                        return cached[1]
                    raise DataUnavailable(f"Could not load {key} from URL: {exc}") from exc
            else:
                try:
                    text = await asyncio.to_thread(path.read_text, encoding="utf-8")
                    data = json.loads(text)
                except FileNotFoundError as exc:
                    if cached:
                        return cached[1]
                    raise DataUnavailable(f"{key} file not found: {path}") from exc
                except (OSError, json.JSONDecodeError) as exc:
                    if cached:
                        return cached[1]
                    raise DataUnavailable(f"Could not load {key} file {path}: {exc}") from exc

            self._cache[key] = (time.monotonic(), data)
            return data

    def invalidate(self, key: str | None = None) -> None:
        if key is None:
            self._cache.clear()
        else:
            self._cache.pop(key, None)

    async def roster(self) -> dict[str, dict[str, Any]]:
        raw = await self._load_json(
            "roster",
            self.settings.roster_url,
            self.settings.roster_file,
            ozy_api_auth=True,
        )
        members = raw.get("members", raw) if isinstance(raw, dict) else raw

        result: dict[str, dict[str, Any]] = {}
        if isinstance(members, dict):
            for name, info in members.items():
                if not isinstance(name, str):
                    continue
                payload = dict(info) if isinstance(info, dict) else {}
                if str(payload.get("status", "active")).casefold() == "removed":
                    continue
                result[name] = payload
            return result

        if isinstance(members, list):
            for item in members:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name", "")).strip()
                if not name:
                    continue
                if str(item.get("status", "active")).casefold() == "removed":
                    continue
                result[name] = dict(item)
            return result

        raise DataUnavailable("Roster JSON must contain a 'members' object/list")

    async def exact_roster_name(self, candidate: str) -> str | None:
        candidate = candidate.strip()
        if not candidate:
            return None
        roster = await self.roster()
        lookup = {name.casefold(): name for name in roster}
        return lookup.get(candidate.casefold())

    async def roster_suggestions(self, candidate: str, limit: int = 3) -> list[RosterMatch]:
        candidate = candidate.strip().casefold()
        if not candidate:
            return []
        roster = await self.roster()
        matches = [
            RosterMatch(name=name, score=SequenceMatcher(None, candidate, name.casefold()).ratio())
            for name in roster
        ]
        matches.sort(key=lambda m: (-m.score, m.name.casefold()))
        return matches[:limit]

    async def member_info(self, game_name: str) -> dict[str, Any] | None:
        return await self.resolve_roster_member(game_name=game_name)

    async def resolve_roster_member(
        self,
        *,
        game_name: str | None = None,
        game_user_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Resolve a roster identity by stable TB user_id first, then exact name."""
        roster = await self.roster()
        stable_id = (game_user_id or "").strip()
        if stable_id:
            for name, info in roster.items():
                if str(info.get("user_id", "")).strip() == stable_id:
                    return {"name": name, **info}

        candidate = (game_name or "").strip()
        if not candidate:
            return None
        lookup = {name.casefold(): name for name in roster}
        canonical = lookup.get(candidate.casefold())
        if canonical is None:
            return None
        return {"name": canonical, **roster[canonical]}

    async def chest_stats(self, game_name: str, today: date | None = None) -> ChestStats | None:
        raw = await self._load_json(
            "chests",
            self.settings.chest_data_url,
            self.settings.chest_data_file,
            ozy_api_auth=True,
        )
        if not isinstance(raw, dict):
            raise DataUnavailable("Chest data must be a JSON object")

        weeks = raw.get("weeks") or []
        if not isinstance(weeks, list) or not weeks:
            return None

        today = today or datetime.now(self.settings.timezone).date()
        selected = None
        for week in weeks:
            if not isinstance(week, dict):
                continue
            try:
                start = date.fromisoformat(str(week.get("start")))
                end = date.fromisoformat(str(week.get("end")))
            except (TypeError, ValueError):
                continue
            if start <= today <= end:
                selected = week
                break
        if selected is None:
            selected = next((w for w in weeks if isinstance(w, dict)), None)
        if selected is None:
            return None

        target = int(raw.get("weekly_target") or selected.get("weekly_target") or 0)
        lookup_name = game_name.casefold()
        for member in selected.get("members") or []:
            if not isinstance(member, dict):
                continue
            name = str(member.get("name", ""))
            if name.casefold() != lookup_name:
                continue
            breakdown_raw = member.get("breakdown") or {}
            breakdown = {
                str(k): int(v or 0)
                for k, v in breakdown_raw.items()
                if isinstance(v, (int, float)) and int(v or 0) > 0
            }
            points = int(member.get("points") or 0)
            chests = int(member.get("chests") or 0)
            met_target = bool(member.get("met_target")) or (target > 0 and points >= target)
            return ChestStats(
                player=name,
                week_label=str(selected.get("label") or f"{selected.get('start', '')} - {selected.get('end', '')}"),
                points=points,
                chests=chests,
                target=target,
                met_target=met_target,
                breakdown=breakdown,
            )
        return None

    async def chest_leaderboard(self, today: date | None = None) -> ChestLeaderboard | None:
        """Return the current chest ranking using the active roster as authority.

        Players missing from chest_data.json are included with zero points. Players
        present in chest data but absent from the active roster are excluded.
        """
        raw = await self._load_json(
            "chests",
            self.settings.chest_data_url,
            self.settings.chest_data_file,
            ozy_api_auth=True,
        )
        if not isinstance(raw, dict):
            raise DataUnavailable("Chest data must be a JSON object")

        weeks = raw.get("weeks") or []
        if not isinstance(weeks, list) or not weeks:
            return None

        today = today or datetime.now(self.settings.timezone).date()
        selected = None
        for week in weeks:
            if not isinstance(week, dict):
                continue
            try:
                start = date.fromisoformat(str(week.get("start")))
                end = date.fromisoformat(str(week.get("end")))
            except (TypeError, ValueError):
                continue
            if start <= today <= end:
                selected = week
                break
        if selected is None:
            selected = next((w for w in weeks if isinstance(w, dict)), None)
        if selected is None:
            return None

        target = int(raw.get("weekly_target") or selected.get("weekly_target") or 0)
        chest_lookup: dict[str, dict[str, Any]] = {}
        for item in selected.get("members") or []:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            if name:
                chest_lookup[name.casefold()] = item

        roster = await self.roster()
        entries: list[ChestRankEntry] = []
        for roster_name in roster:
            item = chest_lookup.get(roster_name.casefold(), {})
            points = int(item.get("points") or 0)
            chests = int(item.get("chests") or 0)
            met_target = bool(item.get("met_target")) or (target > 0 and points >= target)
            entries.append(
                ChestRankEntry(
                    name=roster_name,
                    points=points,
                    chests=chests,
                    met_target=met_target,
                )
            )

        entries.sort(key=lambda item: (-item.points, -item.chests, item.name.casefold()))
        total_points = sum(item.points for item in entries)
        total_chests = sum(item.chests for item in entries)

        return ChestLeaderboard(
            week_label=str(selected.get("label") or f"{selected.get('start', '')} - {selected.get('end', '')}"),
            start=str(selected.get("start") or ""),
            end=str(selected.get("end") or ""),
            target=target,
            total_points=total_points,
            total_chests=total_chests,
            generated=str(raw.get("generated")) if raw.get("generated") not in (None, "") else None,
            members=tuple(entries),
        )

    async def chats(self) -> list[dict[str, str]]:
        raw = await self._load_json("chats", None, self.settings.chats_file)
        items = raw.get("chats", raw) if isinstance(raw, dict) else raw
        if not isinstance(items, list):
            raise DataUnavailable("Chats file must contain a list or {'chats': [...]}")
        result = []
        for item in items:
            if not isinstance(item, dict):
                continue
            label = str(item.get("label", "")).strip()
            name = str(item.get("name", "")).strip()
            key = str(item.get("key", "")).strip()
            if label and name:
                result.append({"key": key or label.casefold().replace(" ", "-"), "label": label, "name": name})
        return result

    async def schedule_for_date(self, target_date: date) -> list[ScheduleItem]:
        raw = await self._load_json(
            "schedule",
            self.settings.schedule_url,
            self.settings.schedule_file,
        )
        events = raw.get("events", raw) if isinstance(raw, dict) else raw
        if not isinstance(events, list):
            raise DataUnavailable("Schedule must contain an 'events' list")

        weekday = target_date.strftime("%A").casefold()
        results: list[ScheduleItem] = []
        for event in events:
            if not isinstance(event, dict):
                continue

            event_date = str(event.get("date", "")).strip()
            if event_date:
                try:
                    if date.fromisoformat(event_date) != target_date:
                        continue
                except ValueError:
                    continue
            else:
                weekdays = event.get("weekdays")
                if weekdays:
                    allowed = {str(x).casefold() for x in weekdays}
                    if weekday not in allowed:
                        continue
                else:
                    # No date/weekday means daily.
                    pass

            title = str(event.get("title", "")).strip()
            event_time = str(event.get("time", "")).strip()
            if not title or not event_time:
                continue
            details = str(event.get("details", "")).strip()
            results.append(
                ScheduleItem(
                    time=event_time,
                    title=title,
                    details=details,
                    ping=bool(event.get("ping", False)),
                )
            )

        def sort_key(item: ScheduleItem):
            try:
                hour, minute = [int(x) for x in item.time.split(":", 1)]
                return hour * 60 + minute
            except Exception:
                return 24 * 60 + 1

        results.sort(key=sort_key)
        return results
