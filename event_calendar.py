from __future__ import annotations

import asyncio
import gzip
import hashlib
import json
import logging
import re
import zlib
from dataclasses import dataclass, replace
from datetime import date, datetime, time as dt_time, timedelta, timezone
from html.parser import HTMLParser
from typing import Iterable
from zoneinfo import ZoneInfo

import aiohttp

from settings import Settings

log = logging.getLogger("ozy-admin.calendar")

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

AKURIER_EVENTS_URL = "https://akurier.pl/events"
AKURIER_TIMEZONE = ZoneInfo("Europe/Warsaw")


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
    source_last_synced_utc: datetime | None = None
    source_changed: bool = False


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


class _AkurierEventsHTMLParser(HTMLParser):
    """Read only the regular mini-event table and stop before the SK section."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._stopped = False

    def handle_starttag(self, tag: str, attrs) -> None:
        if self._stopped:
            return
        tag = tag.casefold()
        if tag == "tr":
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []

    def handle_data(self, data: str) -> None:
        text = " ".join(data.replace("\xa0", " ").split())
        if not text:
            return
        if "for sk below" in text.casefold():
            self._stopped = True
            self._row = None
            self._cell = None
            return
        if self._cell is not None:
            self._cell.append(text)

    def handle_endtag(self, tag: str) -> None:
        if self._stopped:
            return
        tag = tag.casefold()
        if tag in {"td", "th"} and self._cell is not None and self._row is not None:
            value = " ".join(self._cell).strip()
            self._row.append(value)
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if self._row:
                self.rows.append(self._row)
            self._row = None
            self._cell = None


def parse_akurier_mini_events_html(html: str) -> list[MiniTournament]:
    parser = _AkurierEventsHTMLParser()
    parser.feed(html)
    parser.close()

    events: list[MiniTournament] = []
    for row in parser.rows:
        if len(row) < 3:
            continue
        raw_date, raw_time, title = row[0].strip(), row[1].strip(), row[2].strip()
        if not re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", raw_date):
            continue
        if not re.fullmatch(r"\d{1,2}:\d{2}", raw_time):
            continue
        if not title or title.casefold() in {"event", "sk event"}:
            continue
        try:
            local = datetime.strptime(f"{raw_date} {raw_time}", "%d.%m.%Y %H:%M").replace(tzinfo=AKURIER_TIMEZONE)
        except ValueError:
            continue
        start_utc = local.astimezone(timezone.utc)
        events.append(MiniTournament(start_utc=start_utc, end_utc=start_utc, title=title))

    dedup: dict[tuple[datetime, str], MiniTournament] = {}
    for item in events:
        dedup[(item.start_utc, item.title.casefold())] = item
    return sorted(dedup.values(), key=lambda x: (x.start_utc, x.title.casefold()))


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
        self.last_meta_utc: datetime | None = None
        self.last_meta_checked_utc: datetime | None = None
        self.last_akurier_success_utc: datetime | None = None
        self.last_akurier_error: str | None = None

    @property
    def snapshot(self) -> VoltronSnapshot | None:
        return self._snapshot

    @property
    def content_url(self) -> str:
        return self.settings.voltron_base_url.rstrip("/") + "/api/calendar/content"

    @property
    def meta_url(self) -> str:
        return self.settings.voltron_base_url.rstrip("/") + "/api/calendar/snapshot-meta"

    async def _fetch_akurier_mini_events(self) -> list[MiniTournament]:
        try:
            async with self.session.get(
                AKURIER_EVENTS_URL,
                headers={
                    "Accept": "text/html",
                    # Ask for plain HTML. Some upstream/CDN responses have still
                    # arrived as zstd, so decompression is disabled below and any
                    # returned encoding is handled explicitly.
                    "Accept-Encoding": "identity",
                },
                auto_decompress=False,
            ) as response:
                if response.status != 200:
                    raise VoltronCalendarError(f"Mini-events source returned HTTP {response.status}")
                body = await response.read()
                content_encoding = (response.headers.get("Content-Encoding") or "").strip().casefold()
                if content_encoding in {"", "identity"}:
                    pass
                elif content_encoding == "gzip":
                    body = gzip.decompress(body)
                elif content_encoding == "deflate":
                    try:
                        body = zlib.decompress(body)
                    except zlib.error:
                        body = zlib.decompress(body, -zlib.MAX_WBITS)
                elif content_encoding == "zstd":
                    try:
                        from compression import zstd  # Python 3.14+ (Render runtime)
                    except ImportError as exc:
                        raise VoltronCalendarError(
                            "Mini-events source returned zstd but this Python runtime has no zstd decoder"
                        ) from exc
                    body = zstd.decompress(body)
                else:
                    raise VoltronCalendarError(
                        f"Unsupported mini-events content encoding: {content_encoding}"
                    )

                charset = response.charset or "utf-8"
                html = body.decode(charset, errors="replace")
            events = parse_akurier_mini_events_html(html)
            if not events:
                raise VoltronCalendarError("Mini-events parser returned no regular events")
            self.last_akurier_success_utc = datetime.now(timezone.utc)
            self.last_akurier_error = None
            return events
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            self.last_akurier_error = str(exc)
            log.warning("Akurier mini-events refresh failed; keeping cached/fallback mini events: %s", exc)
            return []

    async def _fetch_meta(self) -> datetime | None:
        timeout = aiohttp.ClientTimeout(total=self.settings.http_timeout_seconds)
        self.last_meta_checked_utc = datetime.now(timezone.utc)
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
            value = _parse_last_synced(payload.get("lastSynced"))
            if value is not None:
                self.last_meta_utc = value
            return value
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

    async def refresh(self, *, force: bool = False, refresh_akurier: bool = False) -> RefreshResult:
        """Refresh the cached Voltron snapshot with minimal source traffic.

        Automatic probes first read only snapshot-meta. If a snapshot already exists
        and metadata is unchanged (or temporarily unavailable), no full calendar
        download is made. Full content is fetched on startup, when metadata changes,
        or when a leadership user explicitly forces a refresh.
        """
        async with self._lock:
            try:
                previous_source = self._snapshot.last_synced_utc if self._snapshot else None
                last_synced = await self._fetch_meta()
                source_changed = (
                    last_synced is not None
                    and previous_source is not None
                    and last_synced != previous_source
                )

                if not force and self._snapshot is not None:
                    # Minimal-impact behavior: if metadata is temporarily unavailable,
                    # keep the last good snapshot rather than downloading full content.
                    if last_synced is None:
                        self.last_success_utc = datetime.now(timezone.utc)
                        self.last_error = None
                        return RefreshResult(
                            self._snapshot, False,
                            source_last_synced_utc=None,
                            source_changed=False,
                        )

                    if (
                        self._snapshot.last_synced_utc is not None
                        and last_synced == self._snapshot.last_synced_utc
                    ):
                        self.last_success_utc = datetime.now(timezone.utc)
                        self.last_error = None
                        return RefreshResult(
                            self._snapshot, False,
                            source_last_synced_utc=last_synced,
                            source_changed=False,
                        )

                html = await self._fetch_content()
                parsed = parse_voltron_calendar_html(html)
                if len(parsed.actions) < self.settings.voltron_min_actions:
                    raise VoltronCalendarError(
                        f"Voltron parser produced only {len(parsed.actions)} calendar actions; refusing snapshot"
                    )

                # Akurier is intentionally NOT contacted on normal Voltron probes.
                # Its regular mini-event table has its own once-daily scheduler.
                # A forced leadership refresh may explicitly refresh it as well.
                if refresh_akurier:
                    akurier_minis = await self._fetch_akurier_mini_events()
                    if akurier_minis:
                        parsed = replace(
                            parsed,
                            mini_tournaments=tuple(akurier_minis),
                            semantic_hash=_semantic_hash(parsed.actions, akurier_minis),
                        )
                elif self._snapshot is not None and self._snapshot.mini_tournaments:
                    # Preserve the latest independently cached mini-events when the
                    # Voltron calendar itself changes.
                    parsed = replace(
                        parsed,
                        mini_tournaments=self._snapshot.mini_tournaments,
                        semantic_hash=_semantic_hash(parsed.actions, self._snapshot.mini_tournaments),
                    )

                parsed = replace(parsed, last_synced_utc=last_synced)
                changed = self._snapshot is None or parsed.semantic_hash != self._snapshot.semantic_hash
                self._snapshot = parsed
                self.last_success_utc = datetime.now(timezone.utc)
                self.last_error = None
                return RefreshResult(
                    parsed, changed,
                    source_last_synced_utc=last_synced,
                    source_changed=source_changed,
                )
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

    async def refresh_akurier(self) -> RefreshResult:
        """Refresh only the regular Akurier mini-event table.

        This is scheduled once per UTC day at 18:00 (R+1). It never downloads
        Voltron calendar content. If Akurier is unavailable, the current snapshot
        and its existing mini-event fallback are retained.
        """
        async with self._lock:
            if self._snapshot is None:
                raise VoltronCalendarError("No Voltron snapshot is available for mini-event update")

            minis = await self._fetch_akurier_mini_events()
            if not minis:
                return RefreshResult(self._snapshot, False)

            new_hash = _semantic_hash(self._snapshot.actions, minis)
            changed = new_hash != self._snapshot.semantic_hash
            self._snapshot = replace(
                self._snapshot,
                mini_tournaments=tuple(minis),
                semantic_hash=new_hash,
                fetched_at_utc=datetime.now(timezone.utc),
            )
            return RefreshResult(
                self._snapshot,
                changed,
                source_last_synced_utc=self._snapshot.last_synced_utc,
                source_changed=False,
            )


GAME_RESET_UTC_HOUR = 17
GAME_RESET_UTC_MINUTE = 0


def reset_label(dt: datetime) -> str:
    """Return Total Battle reset-clock notation for a UTC timestamp.

    17:00 UTC is R+0. Offsets are wrapped to the nearest reset, so the
    displayed reset clock stays in the familiar R-11 .. R+12 range.
    Half-hours render as .5 (and quarter-hours as .25/.75 if they occur).
    """
    utc = dt.astimezone(timezone.utc)
    event_minutes = utc.hour * 60 + utc.minute
    reset_minutes = GAME_RESET_UTC_HOUR * 60 + GAME_RESET_UTC_MINUTE
    diff = event_minutes - reset_minutes

    # Wrap to the nearest daily reset. Keep +12 rather than -12 at the tie.
    while diff < -12 * 60:
        diff += 24 * 60
    while diff > 12 * 60:
        diff -= 24 * 60

    sign = "+" if diff >= 0 else "-"
    value = abs(diff) / 60
    if value.is_integer():
        rendered = str(int(value))
    else:
        rendered = f"{value:.2f}".rstrip("0").rstrip(".")
    return f"R{sign}{rendered}"


def _clock_time(dt: datetime, timezone_info) -> str:
    return dt.astimezone(timezone_info).strftime("%H:%M")


def _event_line(action: CalendarAction) -> str:
    detail = f" - {action.details}" if action.details else ""
    return f"- {reset_label(action.timestamp_utc)} {action.title}{detail}"


def _calendar_start_line(action: CalendarAction) -> str:
    detail = f" - {action.details}" if action.details else ""
    return f"- {reset_label(action.timestamp_utc)} {action.title}{detail}"


def _local_clock_with_date(dt: datetime, timezone_info, game_date: date) -> str:
    local = dt.astimezone(timezone_info)
    if local.date() == game_date:
        return local.strftime("%H:%M")
    return local.strftime("%a %d %b %H:%M")


def _local_event_line(action: CalendarAction, timezone_info, game_date: date) -> str:
    detail = f" - {action.details}" if action.details else ""
    return f"- {_local_clock_with_date(action.timestamp_utc, timezone_info, game_date)} {action.title}{detail}"

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


def _pack_sections(title: str, sections: list[str], footer: str = "", limit: int = 1600) -> list[str]:
    # Keep generous headroom below Discord's 2000-character message limit.
    # Messages are intentionally wrapped in triple backticks so leadership can
    # copy/paste the schedule without Discord markdown or timestamp markup.
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
        inner = f"{header}\n\n{body}"
        if index == total and footer:
            inner += "\n\n" + footer
        text = f"```\n{inner}\n```"
        if len(text) > 2000:
            raise VoltronCalendarError("Rendered calendar chunk exceeds Discord's 2000-character limit")
        rendered.append(text)
    return rendered


def _pack_fenced_sections(title: str, sections: list[str], limit: int = 1900) -> list[str]:
    """Pack calendar days so every day remains its own copyable code block."""
    normalized: list[str] = []
    for section in sections:
        # Leave enough room for the fences and a repeated heading if one day is huge.
        normalized.extend(_split_oversized_section(section, max(200, limit - 20)))

    if not normalized:
        normalized = ["No calendar items available."]

    bodies: list[list[str]] = []
    current: list[str] = []
    for section in normalized:
        block = f"```\n{section}\n```"
        candidate_blocks = current + [block]
        # Reserve room for a part-numbered title.
        candidate = title + " (99/99)\n\n" + "\n\n".join(candidate_blocks)
        if len(candidate) <= limit:
            current.append(block)
            continue
        if current:
            bodies.append(current)
        current = [block]
    if current:
        bodies.append(current)

    total = len(bodies)
    rendered: list[str] = []
    for index, blocks in enumerate(bodies, start=1):
        header = title if total == 1 else f"{title} ({index}/{total})"
        text = header + "\n\n" + "\n\n".join(blocks)
        if len(text) > 2000:
            raise VoltronCalendarError("Rendered calendar chunk exceeds Discord's 2000-character limit")
        rendered.append(text)
    return rendered

def build_calendar_chunks(
    snapshot: VoltronSnapshot,
    *,
    start_date: date,
    days: int,
    timezone_info=None,
) -> list[str]:
    """Render the public 30-day calendar in Total Battle reset notation.

    Source timestamps are UTC. Calendar dates are UTC dates; clock values are
    intentionally replaced by reset offsets (17:00 UTC == R+0).
    """
    end_date = start_date + timedelta(days=days)
    starts = [
        action
        for action in snapshot.actions
        if action.action == "STARTS"
        and start_date <= action.timestamp_utc.date() < end_date
    ]

    starts_by_day: dict[date, list[CalendarAction]] = {}
    for action in starts:
        starts_by_day.setdefault(action.timestamp_utc.date(), []).append(action)

    minis = [
        item
        for item in snapshot.mini_tournaments
        if start_date <= item.start_utc.date() < end_date
    ]
    minis_by_day: dict[date, list[MiniTournament]] = {}
    for item in minis:
        minis_by_day.setdefault(item.start_utc.date(), []).append(item)

    sections: list[str] = []
    for day in sorted(set(starts_by_day) | set(minis_by_day)):
        lines = [day.strftime("%a %d %b")]
        day_starts = sorted(starts_by_day.get(day, []), key=lambda x: x.timestamp_utc)
        if day_starts:
            lines.extend(_calendar_start_line(item) for item in day_starts)
        day_minis = sorted(minis_by_day.get(day, []), key=lambda x: x.start_utc)
        if day_minis:
            lines.append("Mini Events")
            lines.extend(f"- {reset_label(item.start_utc)} {item.title}" for item in day_minis)
        sections.append("\n".join(lines))

    if not sections:
        sections.append("No tournament starts or mini events are currently listed in this 30-day window.")

    title = "OZY Tournament Calendar - Next 30 Days"
    return _pack_fenced_sections(title, sections)


def build_today_chunks(
    snapshot: VoltronSnapshot,
    *,
    target_date: date,
    timezone_info=None,
) -> list[str]:
    """Render one UTC calendar day in reset-clock notation."""
    groups: dict[str, list[CalendarAction]] = {"STARTS": [], "CONTINUE": [], "ENDS": []}
    for action in snapshot.actions:
        if action.timestamp_utc.date() != target_date:
            continue
        if action.action in groups:
            groups[action.action].append(action)

    minis = [
        item for item in snapshot.mini_tournaments
        if item.start_utc.date() == target_date
    ]

    sections: list[str] = []
    labels = {"STARTS": "Starts today", "CONTINUE": "Active / continues", "ENDS": "Ends today"}

    if groups["STARTS"]:
        lines = [labels["STARTS"]]
        lines.extend(_event_line(item) for item in sorted(groups["STARTS"], key=lambda x: x.timestamp_utc))
        sections.append("\n".join(lines))

    if minis:
        lines = ["Mini Events"]
        for item in sorted(minis, key=lambda x: x.start_utc):
            lines.append(f"- {reset_label(item.start_utc)} {item.title}")
        sections.append("\n".join(lines))

    for key in ("CONTINUE", "ENDS"):
        items = groups[key]
        if not items:
            continue
        lines = [labels[key]]
        lines.extend(_event_line(item) for item in sorted(items, key=lambda x: x.timestamp_utc))
        sections.append("\n".join(lines))

    if not sections:
        sections.append("No Voltron tournament activity is currently listed for today.")

    title = f"OZY Today - {target_date.strftime('%A %d %B %Y')}"
    return _pack_sections(title, sections)


def build_today_local_chunks(
    snapshot: VoltronSnapshot,
    *,
    target_date: date,
    timezone_info,
    timezone_label: str,
) -> list[str]:
    """Render the same UTC-day schedule using one requested local timezone."""
    groups: dict[str, list[CalendarAction]] = {"STARTS": [], "CONTINUE": [], "ENDS": []}
    for action in snapshot.actions:
        if action.timestamp_utc.date() != target_date:
            continue
        if action.action in groups:
            groups[action.action].append(action)

    minis = [
        item for item in snapshot.mini_tournaments
        if item.start_utc.date() == target_date
    ]

    sections: list[str] = []
    labels = {"STARTS": "Starts today", "CONTINUE": "Active / continues", "ENDS": "Ends today"}

    if groups["STARTS"]:
        lines = [labels["STARTS"]]
        lines.extend(_local_event_line(item, timezone_info, target_date) for item in sorted(groups["STARTS"], key=lambda x: x.timestamp_utc))
        sections.append("\n".join(lines))

    if minis:
        lines = ["Mini Events"]
        for item in sorted(minis, key=lambda x: x.start_utc):
            lines.append(f"- {_local_clock_with_date(item.start_utc, timezone_info, target_date)} {item.title}")
        sections.append("\n".join(lines))

    for key in ("CONTINUE", "ENDS"):
        items = groups[key]
        if not items:
            continue
        lines = [labels[key]]
        lines.extend(_local_event_line(item, timezone_info, target_date) for item in sorted(items, key=lambda x: x.timestamp_utc))
        sections.append("\n".join(lines))

    if not sections:
        sections.append("No Voltron tournament activity is currently listed for today.")

    title = f"OZY Today - {timezone_label} - {target_date.strftime('%A %d %B %Y')} (game date UTC)"
    return _pack_sections(title, sections)
