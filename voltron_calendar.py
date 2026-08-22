from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from dataclasses import dataclass, replace
from datetime import date, datetime, time as dt_time, timedelta, timezone
from html.parser import HTMLParser
from typing import Iterable

import aiohttp

from settings import Settings

log = logging.getLogger("ozy-admin.voltron")

_ACTION_RE = re.compile(r"\b(STARTS|ENDS|CONTINUE)\b", re.IGNORECASE)
_MINI_RE = re.compile(
    r"^(?P<month>[A-Z][a-z]{2})\s+(?P<day>\d{1,2})\s+·\s+"
    r"(?P<start>\d{2}:\d{2})\s+UTC.*?ends\s+(?P<end>\d{2}:\d{2})\s+UTC$",
    re.IGNORECASE,
)
_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}


class VoltronCalendarError(RuntimeError):
    pass


@dataclass(frozen=True)
class CalendarAction:
    timestamp_utc: datetime
    action: str
    title: str
    details: str = ""

    @property
    def key(self) -> tuple[str, str]:
        return (self.title.casefold(), self.details.casefold())


@dataclass(frozen=True)
class MiniTournament:
    start_utc: datetime
    end_utc: datetime
    title: str


@dataclass(frozen=True)
class VoltronSnapshot:
    actions: tuple[CalendarAction, ...]
    mini_tournaments: tuple[MiniTournament, ...]
    semantic_hash: str
    last_synced_utc: datetime | None = None
    fetched_at_utc: datetime | None = None


@dataclass(frozen=True)
class RefreshResult:
    snapshot: VoltronSnapshot
    changed: bool


class _CalendarHTMLParser(HTMLParser):
    """Extract Voltron timeline rows plus a flat text stream without browser rendering."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[tuple[str, list[str]]] = []
        self.tokens: list[str] = []
        self._row: dict[str, object] | None = None
        self._row_depth = 0

    @staticmethod
    def _attrs_dict(attrs) -> dict[str, str]:
        return {str(k): str(v or "") for k, v in attrs}

    @staticmethod
    def _classes(raw: str) -> set[str]:
        return {x for x in raw.split() if x}

    def handle_starttag(self, tag: str, attrs) -> None:
        attr = self._attrs_dict(attrs)
        if self._row is not None:
            # The row boundary itself is a <div>. Only nested divs affect
            # that boundary; void tags such as <img>/<br> do not have
            # closing tags and must not increase the depth counter.
            if tag.casefold() == "div":
                self._row_depth += 1
            return
        if (
            tag.casefold() == "div"
            and "vl-cal__row" in self._classes(attr.get("class", ""))
            and attr.get("data-utc")
        ):
            self._row = {"utc": attr["data-utc"], "texts": []}
            self._row_depth = 1

    def handle_endtag(self, tag: str) -> None:
        if self._row is None or tag.casefold() != "div":
            return
        self._row_depth -= 1
        if self._row_depth <= 0:
            utc_value = str(self._row["utc"])
            texts = list(self._row["texts"])
            self.rows.append((utc_value, texts))
            self._row = None
            self._row_depth = 0

    def handle_data(self, data: str) -> None:
        text = " ".join(data.replace("\xa0", " ").split())
        if not text:
            return
        self.tokens.append(text)
        if self._row is not None:
            cast_texts = self._row["texts"]
            assert isinstance(cast_texts, list)
            cast_texts.append(text)


def _parse_iso_utc(raw: str) -> datetime | None:
    value = raw.strip()
    if not value:
        return None
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _resolve_year(month: int, day: int, reference: datetime) -> int:
    """Choose the year whose month/day is closest to the reference date."""
    candidates: list[tuple[float, int]] = []
    for year in (reference.year - 1, reference.year, reference.year + 1):
        try:
            candidate = datetime(year, month, day, tzinfo=timezone.utc)
        except ValueError:
            continue
        candidates.append((abs((candidate - reference).total_seconds()), year))
    if not candidates:
        return reference.year
    return min(candidates)[1]


def _row_to_action(raw_utc: str, texts: list[str]) -> CalendarAction | None:
    timestamp = _parse_iso_utc(raw_utc)
    if timestamp is None or not texts:
        return None

    action = None
    content: list[str] = []
    found_at = None
    for index, token in enumerate(texts):
        match = _ACTION_RE.search(token)
        if not match:
            continue
        action = match.group(1).upper()
        found_at = index
        remainder = token[match.end():].strip(" :-·")
        if remainder:
            content.append(remainder)
        break

    if action is None or found_at is None:
        return None

    content.extend(texts[found_at + 1 :])
    cleaned: list[str] = []
    for token in content:
        token = " ".join(token.split()).strip()
        if not token:
            continue
        if re.fullmatch(r"\d{1,2}:\d{2}\s+UTC", token, flags=re.IGNORECASE):
            continue
        if token.upper() in {"STARTS", "ENDS", "CONTINUE"}:
            continue
        cleaned.append(token)

    if not cleaned:
        return None
    title = cleaned[0]
    details = " · ".join(cleaned[1:])
    return CalendarAction(timestamp_utc=timestamp, action=action, title=title, details=details)


def _parse_mini_tournaments(tokens: list[str], reference: datetime) -> list[MiniTournament]:
    try:
        start_index = next(i for i, token in enumerate(tokens) if token.casefold() == "mini tournaments")
    except StopIteration:
        return []
    try:
        end_index = next(
            i for i in range(start_index + 1, len(tokens))
            if tokens[i].casefold() == "now running"
        )
    except StopIteration:
        end_index = len(tokens)

    section = tokens[start_index + 1 : end_index]
    results: list[MiniTournament] = []
    for i, token in enumerate(section):
        match = _MINI_RE.match(token)
        if not match:
            continue
        month = _MONTHS.get(match.group("month").casefold())
        if not month:
            continue
        day = int(match.group("day"))
        year = _resolve_year(month, day, reference)
        start_h, start_m = (int(x) for x in match.group("start").split(":"))
        end_h, end_m = (int(x) for x in match.group("end").split(":"))
        try:
            start_dt = datetime(year, month, day, start_h, start_m, tzinfo=timezone.utc)
        except ValueError:
            continue
        end_dt = datetime.combine(start_dt.date(), dt_time(end_h, end_m), tzinfo=timezone.utc)
        if end_dt <= start_dt:
            end_dt += timedelta(days=1)

        title = ""
        for candidate in section[i + 1 : i + 6]:
            lower = candidate.casefold()
            if _MINI_RE.match(candidate):
                break
            if lower.startswith("in ") or "ends in" in lower:
                continue
            if lower.startswith("filter:") or lower.endswith("showing"):
                continue
            title = candidate.strip()
            if title:
                break
        if title:
            results.append(MiniTournament(start_utc=start_dt, end_utc=end_dt, title=title))

    dedup: dict[tuple[datetime, str], MiniTournament] = {}
    for item in results:
        dedup[(item.start_utc, item.title.casefold())] = item
    return sorted(dedup.values(), key=lambda x: (x.start_utc, x.title.casefold()))


def _semantic_hash(actions: Iterable[CalendarAction], minis: Iterable[MiniTournament]) -> str:
    payload = {
        "actions": [
            [a.timestamp_utc.isoformat(), a.action, a.title, a.details]
            for a in actions
        ],
        "minis": [
            [m.start_utc.isoformat(), m.end_utc.isoformat(), m.title]
            for m in minis
        ],
    }
    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def parse_voltron_calendar_html(html: str, reference: datetime | None = None) -> VoltronSnapshot:
    reference = reference or datetime.now(timezone.utc)
    parser = _CalendarHTMLParser()
    parser.feed(html)
    parser.close()

    actions = [action for raw_utc, texts in parser.rows if (action := _row_to_action(raw_utc, texts))]
    actions.sort(key=lambda x: (x.timestamp_utc, x.action, x.title.casefold(), x.details.casefold()))
    minis = _parse_mini_tournaments(parser.tokens, reference)

    return VoltronSnapshot(
        actions=tuple(actions),
        mini_tournaments=tuple(minis),
        semantic_hash=_semantic_hash(actions, minis),
        fetched_at_utc=datetime.now(timezone.utc),
    )


def _parse_last_synced(value: object) -> datetime | None:
    if not isinstance(value, str):
        return None
    return _parse_iso_utc(value)


class VoltronCalendarClient:
    def __init__(self, settings: Settings, session: aiohttp.ClientSession):
        self.settings = settings
        self.session = session
        self._lock = asyncio.Lock()
        self._snapshot: VoltronSnapshot | None = None
        self.last_error: str | None = None
        self.last_success_utc: datetime | None = None

    @property
    def snapshot(self) -> VoltronSnapshot | None:
        return self._snapshot

    @property
    def content_url(self) -> str:
        return self.settings.voltron_base_url.rstrip("/") + "/api/calendar/content"

    @property
    def meta_url(self) -> str:
        return self.settings.voltron_base_url.rstrip("/") + "/api/calendar/snapshot-meta"

    async def _fetch_meta(self) -> datetime | None:
        timeout = aiohttp.ClientTimeout(total=self.settings.http_timeout_seconds)
        try:
            async with self.session.get(
                self.meta_url,
                params={"realm": self.settings.voltron_realm},
                timeout=timeout,
                headers={"Accept": "application/json"},
            ) as response:
                if response.status != 200:
                    return None
                payload = await response.json(content_type=None)
        except (aiohttp.ClientError, asyncio.TimeoutError, json.JSONDecodeError):
            return None
        if isinstance(payload, dict):
            return _parse_last_synced(payload.get("lastSynced"))
        return None

    async def _fetch_content(self) -> str:
        timeout = aiohttp.ClientTimeout(total=self.settings.http_timeout_seconds)
        async with self.session.get(
            self.content_url,
            params={"realm": self.settings.voltron_realm},
            timeout=timeout,
            headers={"Accept": "text/html"},
        ) as response:
            if response.status != 200:
                raise VoltronCalendarError(f"Voltron calendar returned HTTP {response.status}")
            return await response.text()

    async def refresh(self, *, force: bool = False) -> RefreshResult:
        async with self._lock:
            try:
                last_synced = await self._fetch_meta()
                if (
                    not force
                    and self._snapshot is not None
                    and last_synced is not None
                    and self._snapshot.last_synced_utc is not None
                    and last_synced == self._snapshot.last_synced_utc
                ):
                    self.last_success_utc = datetime.now(timezone.utc)
                    self.last_error = None
                    return RefreshResult(self._snapshot, False)

                html = await self._fetch_content()
                parsed = parse_voltron_calendar_html(html)
                if len(parsed.actions) < self.settings.voltron_min_actions:
                    raise VoltronCalendarError(
                        f"Voltron parser produced only {len(parsed.actions)} calendar actions; refusing snapshot"
                    )
                parsed = replace(parsed, last_synced_utc=last_synced)
                changed = self._snapshot is None or parsed.semantic_hash != self._snapshot.semantic_hash
                self._snapshot = parsed
                self.last_success_utc = datetime.now(timezone.utc)
                self.last_error = None
                return RefreshResult(parsed, changed)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = str(exc)
                if self._snapshot is not None:
                    log.warning("Voltron refresh failed; retaining last good snapshot: %s", exc)
                    return RefreshResult(self._snapshot, False)
                if isinstance(exc, VoltronCalendarError):
                    raise
                raise VoltronCalendarError(str(exc)) from exc


def _discord_timestamp(dt: datetime, style: str = "t") -> str:
    return f"<t:{int(dt.timestamp())}:{style}>"


def _event_line(action: CalendarAction) -> str:
    detail = f" - {action.details}" if action.details else ""
    return f"- {_discord_timestamp(action.timestamp_utc)} **{action.title}**{detail}"


def _calendar_start_line(action: CalendarAction, end_utc: datetime | None) -> str:
    detail = f" - {action.details}" if action.details else ""
    ending = f" · ends {_discord_timestamp(end_utc, 'D')} {_discord_timestamp(end_utc)}" if end_utc else ""
    return f"- {_discord_timestamp(action.timestamp_utc)} **{action.title}**{detail}{ending}"


def _match_end_times(actions: Iterable[CalendarAction]) -> dict[CalendarAction, datetime]:
    ends_by_key: dict[tuple[str, str], list[CalendarAction]] = {}
    for action in actions:
        if action.action == "ENDS":
            ends_by_key.setdefault(action.key, []).append(action)
    for values in ends_by_key.values():
        values.sort(key=lambda x: x.timestamp_utc)

    result: dict[CalendarAction, datetime] = {}
    for start in sorted((a for a in actions if a.action == "STARTS"), key=lambda x: x.timestamp_utc):
        candidates = ends_by_key.get(start.key, [])
        while candidates and candidates[0].timestamp_utc < start.timestamp_utc:
            candidates.pop(0)
        if candidates:
            result[start] = candidates.pop(0).timestamp_utc
    return result


def _split_oversized_section(section: str, limit: int) -> list[str]:
    if len(section) <= limit:
        return [section]
    lines = section.splitlines()
    if not lines:
        return [section[:limit]]
    repeated_heading = lines[0] if lines[0].startswith("### ") else ""
    body_lines = lines[1:] if repeated_heading else lines
    pieces: list[str] = []
    current = repeated_heading
    for line in body_lines:
        candidate = line if not current else current + "\n" + line
        if len(candidate) <= limit:
            current = candidate
            continue
        if current and current != repeated_heading:
            pieces.append(current)
        current = (repeated_heading + "\n" + line) if repeated_heading else line
        if len(current) > limit:
            # A single pathological line should not make the entire update fail.
            prefix = repeated_heading + "\n" if repeated_heading else ""
            room = max(100, limit - len(prefix))
            while len(current) > limit:
                pieces.append(prefix + current[len(prefix):len(prefix) + room])
                current = prefix + current[len(prefix) + room:]
    if current:
        pieces.append(current)
    return pieces or [section[:limit]]


def _pack_sections(title: str, sections: list[str], footer: str, limit: int = 1650) -> list[str]:
    # Keep generous headroom below Discord's 2000-character message limit for
    # per-chunk headings and the source/footer line.
    normalized: list[str] = []
    for section in sections:
        normalized.extend(_split_oversized_section(section, limit))

    bodies: list[str] = []
    current = ""
    for section in normalized:
        candidate = section if not current else current + "\n\n" + section
        if len(candidate) <= limit:
            current = candidate
            continue
        if current:
            bodies.append(current)
        current = section
    if current:
        bodies.append(current)
    if not bodies:
        bodies = ["No calendar items available."]

    total = len(bodies)
    rendered: list[str] = []
    for index, body in enumerate(bodies, start=1):
        header = title if total == 1 else f"{title} ({index}/{total})"
        text = f"{header}\n\n{body}"
        if index == total and footer:
            text += "\n\n" + footer
        if len(text) > 2000:
            raise VoltronCalendarError("Rendered calendar chunk exceeds Discord's 2000-character limit")
        rendered.append(text)
    return rendered


def build_calendar_chunks(
    snapshot: VoltronSnapshot,
    *,
    start_date: date,
    days: int,
    timezone_info,
) -> list[str]:
    end_date = start_date + timedelta(days=days)
    starts = [
        action
        for action in snapshot.actions
        if action.action == "STARTS"
        and start_date <= action.timestamp_utc.astimezone(timezone_info).date() < end_date
    ]

    end_times = _match_end_times(snapshot.actions)

    by_day: dict[date, list[CalendarAction]] = {}
    for action in starts:
        local_day = action.timestamp_utc.astimezone(timezone_info).date()
        by_day.setdefault(local_day, []).append(action)

    sections: list[str] = []
    for day in sorted(by_day):
        lines = [f"### {day.strftime('%a %d %b')}"]
        lines.extend(_calendar_start_line(item, end_times.get(item)) for item in by_day[day])
        sections.append("\n".join(lines))

    if not sections:
        sections.append("No tournament starts are currently listed in this 30-day window.")

    source = "https://nexusportal.voltron.me/calendar"
    synced = (
        f" · source synced {_discord_timestamp(snapshot.last_synced_utc, 'R')}"
        if snapshot.last_synced_utc else ""
    )
    footer = f"Times display in each member's Discord timezone. Source: {source}{synced}"
    title = "# OZY Tournament Calendar - Next 30 Days"
    return _pack_sections(title, sections, footer)


def build_today_chunks(
    snapshot: VoltronSnapshot,
    *,
    target_date: date,
    timezone_info,
) -> list[str]:
    groups: dict[str, list[CalendarAction]] = {"STARTS": [], "CONTINUE": [], "ENDS": []}
    for action in snapshot.actions:
        if action.timestamp_utc.astimezone(timezone_info).date() != target_date:
            continue
        if action.action in groups:
            groups[action.action].append(action)

    minis = [
        item for item in snapshot.mini_tournaments
        if item.start_utc.astimezone(timezone_info).date() == target_date
    ]

    sections: list[str] = []
    labels = {"STARTS": "Starts today", "CONTINUE": "Active / continues", "ENDS": "Ends today"}
    for key in ("STARTS", "CONTINUE", "ENDS"):
        items = groups[key]
        if not items:
            continue
        lines = [f"### {labels[key]}"]
        lines.extend(_event_line(item) for item in sorted(items, key=lambda x: x.timestamp_utc))
        sections.append("\n".join(lines))

    if minis:
        lines = ["### Mini tournaments"]
        for item in sorted(minis, key=lambda x: x.start_utc):
            lines.append(
                f"- {_discord_timestamp(item.start_utc)}-{_discord_timestamp(item.end_utc)} **{item.title}**"
            )
        sections.append("\n".join(lines))

    if not sections:
        sections.append("No Voltron tournament activity is currently listed for today.")

    source = "https://nexusportal.voltron.me/calendar"
    synced = (
        f" · source synced {_discord_timestamp(snapshot.last_synced_utc, 'R')}"
        if snapshot.last_synced_utc else ""
    )
    footer = f"Times display in each member's Discord timezone. Source: {source}{synced}"
    title = f"# OZY Today - {target_date.strftime('%A %d %B %Y')}"
    return _pack_sections(title, sections, footer)
