from __future__ import annotations

import asyncio
import json
import re
import time
from dataclasses import dataclass
from datetime import date, datetime, time as dt_time, timedelta, timezone
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
    source_note: str = ""


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
    source_note: str = ""


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
                    async with self.session.get(url, timeout=timeout, headers=headers, allow_redirects=False) as response:
                        if response.status != 200:
                            raise DataUnavailable(f"{key} source returned HTTP {response.status}")
                        data = await response.json(content_type=None)
                except (aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError) as exc:
                    raise DataUnavailable(f"Could not load {key} from URL ({type(exc).__name__})") from exc
            else:
                try:
                    text = await asyncio.to_thread(path.read_text, encoding="utf-8")
                    data = json.loads(text)
                except FileNotFoundError as exc:
                    raise DataUnavailable(f"{key} file not found: {path}") from exc
                except (OSError, json.JSONDecodeError) as exc:
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
        self._validate_source(raw)
        members = raw.get("members", raw) if isinstance(raw, dict) else raw

        result: dict[str, dict[str, Any]] = {}
        if isinstance(members, dict):
            for name, info in members.items():
                if not isinstance(name, str):
                    continue
                payload = dict(info) if isinstance(info, dict) else {}
                if str(payload.get("status", "active")).casefold() == "removed":
                    continue
                result[name] = {**payload, "name": name}
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
        matches = [name for name in roster if name.casefold() == candidate.casefold()]
        return matches[0] if len(matches) == 1 else None

    async def roster_suggestions(self, candidate: str, limit: int = 3) -> list[RosterMatch]:
        """Return advisory roster-name suggestions only.

        Suggestion scoring is deliberately more tolerant than verification. It
        ignores punctuation/spacing and gives a strong boost when a roster name
        is visibly embedded in a Discord display name such as ``[OZY] Prince``.
        Exact verification still goes through :meth:`exact_roster_name`.
        """
        raw_candidate = candidate.strip()
        if not raw_candidate:
            return []

        def normalized(value: str) -> str:
            return " ".join(re.findall(r"[\w]+", value.casefold(), flags=re.UNICODE))

        def compact(value: str) -> str:
            return normalized(value).replace(" ", "")

        cand_norm = normalized(raw_candidate)
        cand_compact = compact(raw_candidate)
        roster = await self.roster()
        matches: list[RosterMatch] = []

        for name in roster:
            name_norm = normalized(name)
            name_compact = compact(name)
            scores = [
                SequenceMatcher(None, raw_candidate.casefold(), name.casefold()).ratio(),
                SequenceMatcher(None, cand_norm, name_norm).ratio() if cand_norm and name_norm else 0.0,
                SequenceMatcher(None, cand_compact, name_compact).ratio() if cand_compact and name_compact else 0.0,
            ]

            # A Discord display name often contains clan decorations, rank text,
            # or other words around the actual TB name. This is useful for a
            # suggestion, but never sufficient for automatic verification.
            if name_norm and cand_norm and (name_norm in cand_norm or cand_norm in name_norm):
                shorter = min(len(name_compact), len(cand_compact))
                longer = max(len(name_compact), len(cand_compact)) or 1
                containment = shorter / longer
                scores.append(max(0.86, 0.90 + 0.08 * containment))

            cand_tokens = set(cand_norm.split())
            name_tokens = set(name_norm.split())
            if cand_tokens and name_tokens:
                overlap = len(cand_tokens & name_tokens) / len(name_tokens)
                if overlap:
                    scores.append(min(0.97, 0.70 + 0.27 * overlap))

            matches.append(RosterMatch(name=name, score=max(scores)))

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
                    return {**info, "name": name}
            return None

        candidate = (game_name or "").strip()
        if not candidate:
            return None
        matches = [name for name in roster if name.casefold() == candidate.casefold()]
        canonical = matches[0] if len(matches) == 1 else None
        if canonical is None:
            return None
        return {"name": canonical, **roster[canonical]}

    @staticmethod
    def _count(value: Any) -> int:
        if value is None:
            return 0
        try:
            number = int(value)
            if isinstance(value, bool) or number < 0 or float(value) != number:
                raise ValueError
            return number
        except (TypeError, ValueError, OverflowError) as exc:
            raise DataUnavailable("Invalid website chest count or points") from exc

    @staticmethod
    def _validate_source(raw: Any) -> None:
        if not isinstance(raw, dict):
            return
        meta = raw.get("roster_meta") or {}
        for tag in (raw.get("clan_tag"), meta.get("clan")):
            if tag and str(tag).upper() != "OZY":
                raise DataUnavailable("Dataset belongs to a different clan")
        if raw.get("clan_id") and str(raw["clan_id"]) != "4423816314895":
            raise DataUnavailable("Dataset clan ID does not match OZY")
        for source in (raw, raw.get("counter") or {}, raw.get("passive") or {}):
            if str(source.get("mode", "")).lower() in {"shadow", "inactive", "preview"} or source.get("active") is False:
                raise DataUnavailable("Website scoring is shadow/inactive; no official results available")
        if raw.get("error"):
            raise DataUnavailable("Website returned an error payload")

    @staticmethod
    def _timestamp(value: Any) -> datetime:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError("Timestamp requires a timezone")
        return result.astimezone(timezone.utc)

    def _select_week(self, raw: dict, today: date | datetime | None) -> dict | None:
        # Explicit dates are a legacy testing interface, interpreted at R+0 UTC.
        now = today or datetime.now(timezone.utc)
        if not isinstance(now, datetime):
            now = datetime.combine(now, dt_time(17), tzinfo=timezone.utc)
        if now.tzinfo is None:
            raise DataUnavailable("Reporting instant requires a timezone")
        weeks = raw.get("weeks", [])
        if not isinstance(weeks, list):
            raise DataUnavailable("Chest weeks must be a list")
        matches = []
        for week in weeks:
            if not isinstance(week, dict):
                raise DataUnavailable("Invalid chest week")
            try:
                if week.get("start_at") or week.get("end_at"):
                    start, end = self._timestamp(week.get("start_at")), self._timestamp(week.get("end_at"))
                else:
                    start_day, end_day = date.fromisoformat(week["start"]), date.fromisoformat(week["end"])
                    start = datetime.combine(start_day, dt_time(17), tzinfo=timezone.utc)
                    # Legacy exports label the last included day; newer dates label the boundary.
                    if (end_day - start_day).days == 6:
                        end_day += timedelta(days=1)
                    end = datetime.combine(end_day, dt_time(17), tzinfo=timezone.utc)
                if end <= start:
                    raise ValueError
            except (TypeError, ValueError, KeyError) as exc:
                raise DataUnavailable("Invalid website reporting period") from exc
            if start <= now < end:
                if not isinstance(week.get("members"), list) or any(not isinstance(m, dict) for m in week["members"]):
                    raise DataUnavailable("Invalid chest members")
                matches.append(week)
        if len(matches) > 1:
            raise DataUnavailable("Overlapping website reporting periods")
        return matches[0] if matches else None

    def _source_note(self, raw: dict) -> str:
        generated = raw.get("generated")
        try:
            stamp = self._timestamp(generated)
            age = datetime.now(timezone.utc) - stamp
            freshness = " (older than 24 hours)" if age > timedelta(hours=24) else ""
            updated = stamp.strftime("%Y-%m-%d %H:%M UTC") + freshness
        except (TypeError, ValueError):
            updated = "unknown"
        active = (raw.get("counter") or {}).get("activeAt")
        state = "Website counter active" if active else "Published website snapshot; live activation unconfirmed"
        return f"{state}. Source updated: {updated}"

    async def chest_stats(self, game_name: str, today: date | datetime | None = None) -> ChestStats | None:
        raw = await self._load_json(
            "chests",
            self.settings.chest_data_url,
            self.settings.chest_data_file,
            ozy_api_auth=True,
        )
        if not isinstance(raw, dict):
            raise DataUnavailable("Chest data must be a JSON object")

        self._validate_source(raw)
        selected = self._select_week(raw, today)
        if selected is None:
            return None
        target = self._count(selected.get("weekly_target", raw.get("weekly_target", 0)))
        info = await self.resolve_roster_member(game_name=game_name)
        if info is None:
            return None
        name = info["name"]
        member = self._chest_member(selected, name, info.get("user_id"))
        if member is not None:
            breakdown_raw = member.get("breakdown") or {}
            if not isinstance(breakdown_raw, dict):
                raise DataUnavailable("Invalid website chest breakdown")
            breakdown = {
                str(k): self._count(v)
                for k, v in breakdown_raw.items()
                if self._count(v) > 0
            }
            points = self._count(member.get("points"))
            chests = self._count(member.get("chests"))
            met_target = target > 0 and points >= target
            return ChestStats(
                player=name,
                week_label=str(selected.get("label") or f"{selected.get('start', '')} - {selected.get('end', '')}"),
                points=points,
                chests=chests,
                target=target,
                met_target=met_target,
                breakdown=breakdown,
                source_note=self._source_note(raw),
            )
        return None

    @staticmethod
    def _chest_member(week: dict, name: str, user_id: Any) -> dict:
        rows = week["members"]
        stable_id = str(user_id or "").strip()
        identified = [row for row in rows if stable_id and str(row.get("user_id") or "").strip() == stable_id]
        exact = [row for row in rows if str(row.get("name", "")).strip() == name]
        matches = identified or exact or [row for row in rows if str(row.get("name", "")).strip().casefold() == name.casefold()]
        if len(matches) > 1:
            raise DataUnavailable("Ambiguous chest player identity")
        item = matches[0] if matches else {}
        if item.get("user_id") and stable_id and str(item["user_id"]).strip() != stable_id:
            raise DataUnavailable("Chest and roster player IDs disagree")
        return item

    async def chest_leaderboard(self, today: date | datetime | None = None) -> ChestLeaderboard | None:
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

        self._validate_source(raw)
        selected = self._select_week(raw, today)
        if selected is None:
            return None
        target = self._count(selected.get("weekly_target", raw.get("weekly_target", 0)))
        roster = await self.roster()
        entries: list[ChestRankEntry] = []
        for roster_name, info in roster.items():
            item = self._chest_member(selected, roster_name, info.get("user_id"))
            points = self._count(item.get("points"))
            chests = self._count(item.get("chests"))
            met_target = target > 0 and points >= target
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
            start=str(selected.get("start_at") or selected.get("start") or ""),
            end=str(selected.get("end_at") or selected.get("end") or ""),
            target=target,
            total_points=total_points,
            total_chests=total_chests,
            generated=str(raw.get("generated")) if raw.get("generated") not in (None, "") else None,
            members=tuple(entries),
            source_note=self._source_note(raw),
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

    async def schedule_for_date(self, target_date: date, *, audience: str = "clan") -> list[ScheduleItem]:
        audience = audience.strip().casefold() or "clan"
        if audience not in {"clan", "leadership"}:
            raise DataUnavailable(f"Unsupported schedule audience: {audience}")

        url = self.settings.schedule_url
        if url:
            separator = "&" if "?" in url else "?"
            url = f"{url}{separator}audience={audience}"

        raw = await self._load_json(
            f"schedule:{audience}",
            url,
            self.settings.schedule_file,
            ozy_api_auth=bool(url),
        )
        events = raw.get("events", raw) if isinstance(raw, dict) else raw
        if not isinstance(events, list):
            raise DataUnavailable("Schedule must contain an 'events' list")

        weekday = target_date.strftime("%A").casefold()
        results: list[ScheduleItem] = []
        for event in events:
            if not isinstance(event, dict):
                continue

            event_audience = str(event.get("audience", "clan") or "clan").strip().casefold()
            if event_audience != audience:
                continue

            # New canonical website schedule schema: UTC timestamp + audience.
            start_utc = str(event.get("start_utc", "") or "").strip()
            if start_utc:
                try:
                    normalized = start_utc.replace("Z", "+00:00")
                    start_dt = datetime.fromisoformat(normalized)
                    if start_dt.tzinfo is None:
                        start_dt = start_dt.replace(tzinfo=timezone.utc)
                    local_dt = start_dt.astimezone(self.settings.timezone)
                except ValueError:
                    continue
                if local_dt.date() != target_date:
                    continue
                title = str(event.get("title", "") or "").strip()
                if not title:
                    continue
                details = str(
                    event.get("description")
                    or event.get("details")
                    or event.get("notes")
                    or ""
                ).strip()
                results.append(
                    ScheduleItem(
                        time=local_dt.strftime("%H:%M"),
                        title=title,
                        details=details,
                        ping=bool(event.get("ping", False)),
                    )
                )
                continue

            # Backward-compatible local/static schedule schema.
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

    async def upsert_schedule_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Persist a Discord-created event in the canonical OZY website schedule."""
        if not self.settings.schedule_url:
            raise DataUnavailable("SCHEDULE_URL is not configured")
        if not self.settings.ozy_data_api_token:
            raise DataUnavailable("OZY_DATA_API_TOKEN is required to write schedule data")

        timeout = aiohttp.ClientTimeout(total=self.settings.http_timeout_seconds)
        headers = {
            "X-OZY-Admin-Token": self.settings.ozy_data_api_token,
            "Content-Type": "application/json",
        }
        try:
            async with self.session.post(
                self.settings.schedule_url,
                json=payload,
                timeout=timeout,
                headers=headers,
            ) as response:
                if response.status not in {200, 201}:
                    body = (await response.text())[:300]
                    raise DataUnavailable(
                        f"schedule write returned HTTP {response.status}: {body or 'empty response'}"
                    )
                data = await response.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError) as exc:
            raise DataUnavailable(f"Could not write schedule to website: {exc}") from exc

        self.invalidate("schedule:clan")
        self.invalidate("schedule:leadership")
        return data if isinstance(data, dict) else {"ok": True}


    async def upsert_announcement(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Persist a Discord announcement in the OZY website announcement feed."""
        if not self.settings.announcements_url:
            raise DataUnavailable("ANNOUNCEMENTS_URL is not configured")
        if not self.settings.ozy_data_api_token:
            raise DataUnavailable("OZY_DATA_API_TOKEN is required to write announcement data")

        timeout = aiohttp.ClientTimeout(total=self.settings.http_timeout_seconds)
        headers = {
            "X-OZY-Admin-Token": self.settings.ozy_data_api_token,
            "Content-Type": "application/json",
        }
        try:
            async with self.session.post(
                self.settings.announcements_url,
                json=payload,
                timeout=timeout,
                headers=headers,
            ) as response:
                if response.status not in {200, 201}:
                    body = (await response.text())[:300]
                    raise DataUnavailable(
                        f"announcement write returned HTTP {response.status}: {body or 'empty response'}"
                    )
                data = await response.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError) as exc:
            raise DataUnavailable(f"Could not write announcement to website: {exc}") from exc

        return data if isinstance(data, dict) else {"ok": True}

    async def delete_announcement(self, discord_message_id: int | str) -> bool:
        if not self.settings.announcements_url or not self.settings.ozy_data_api_token:
            return False
        separator = "&" if "?" in self.settings.announcements_url else "?"
        url = f"{self.settings.announcements_url}{separator}id={discord_message_id}"
        timeout = aiohttp.ClientTimeout(total=self.settings.http_timeout_seconds)
        headers = {"X-OZY-Admin-Token": self.settings.ozy_data_api_token}
        try:
            async with self.session.delete(url, timeout=timeout, headers=headers) as response:
                if response.status == 404:
                    return False
                if response.status not in {200, 204}:
                    body = (await response.text())[:300]
                    raise DataUnavailable(
                        f"announcement delete returned HTTP {response.status}: {body or 'empty response'}"
                    )
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise DataUnavailable(f"Could not delete announcement from website: {exc}") from exc
        return True

    async def delete_schedule_event(self, discord_event_id: int | str) -> bool:
        if not self.settings.schedule_url or not self.settings.ozy_data_api_token:
            return False
        separator = "&" if "?" in self.settings.schedule_url else "?"
        url = f"{self.settings.schedule_url}{separator}id={discord_event_id}"
        timeout = aiohttp.ClientTimeout(total=self.settings.http_timeout_seconds)
        headers = {"X-OZY-Admin-Token": self.settings.ozy_data_api_token}
        try:
            async with self.session.delete(url, timeout=timeout, headers=headers) as response:
                if response.status == 404:
                    return False
                if response.status not in {200, 204}:
                    body = (await response.text())[:300]
                    raise DataUnavailable(
                        f"schedule delete returned HTTP {response.status}: {body or 'empty response'}"
                    )
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            raise DataUnavailable(f"Could not delete schedule event from website: {exc}") from exc

        self.invalidate("schedule:clan")
        self.invalidate("schedule:leadership")
        return True

