from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ReminderSpec:
    offset_minutes: int
    message: str

    @property
    def start_event(self) -> bool:
        return self.offset_minutes == 0


OMENS_DAY_1_REMINDERS: tuple[ReminderSpec, ...] = (
    ReminderSpec(-120, "PRESENT: Period IV starts in 2 hours. Be ready."),
    ReminderSpec(
        0,
        "ESSENCE: Period IV is open. Deposit now.\n"
        "SUMMONS: HOLD - stay tuned to Clan Chat.",
    ),
    ReminderSpec(60, "PRESENT: Period IV +1 reminder."),
)

_OFFSET_RE = re.compile(r"^([+-]?\d{1,4})(?:\s*(m|min|mins|minute|minutes|h|hr|hrs|hour|hours))?$", re.IGNORECASE)


def _parse_offset_minutes(value: str) -> int:
    text = value.strip()
    match = _OFFSET_RE.fullmatch(text)
    if not match:
        raise ValueError(
            "Reminder offsets must look like `-2h`, `-120`, `0`, `+60`, or `+1h`."
        )

    number_text, unit = match.groups()
    amount = int(number_text)
    normalized_unit = (unit or "m").casefold()
    if normalized_unit.startswith("h"):
        amount *= 60

    if amount < -1440 or amount > 1440:
        raise ValueError("Reminder offsets must be between -24h and +24h from the event start.")
    return amount


def parse_reminder_plan(value: str | None) -> list[ReminderSpec]:
    """Parse an optional reminder plan from the event schedule modal.

    Accepted forms:
      - blank: no reminders
      - OMENS: built-in Day 1 preset
      - custom: `-2h | message; 0 | message; +1h | message`

    A reminder at offset 0 also tells the bot to start the native Discord event.
    """
    text = (value or "").strip()
    if not text:
        return []

    preset_key = re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()
    if preset_key in {"omen", "omens", "omens day 1", "omens day1"}:
        return list(OMENS_DAY_1_REMINDERS)

    raw_parts = [part.strip() for part in re.split(r"[;\n]+", text) if part.strip()]
    if not raw_parts:
        return []
    if len(raw_parts) > 8:
        raise ValueError("Use no more than 8 reminders per event.")

    reminders: list[ReminderSpec] = []
    seen_offsets: set[int] = set()
    for part in raw_parts:
        if "|" not in part:
            raise ValueError(
                "Each reminder must use `offset | message`, for example `-2h | Be ready`."
            )
        offset_text, message = part.split("|", 1)
        offset_minutes = _parse_offset_minutes(offset_text)
        clean_message = message.strip()
        if not clean_message:
            raise ValueError("Reminder messages cannot be empty.")
        if len(clean_message) > 1500:
            raise ValueError("Each reminder message must be 1500 characters or fewer.")
        if offset_minutes in seen_offsets:
            raise ValueError(f"Only one reminder can use the offset {offset_text.strip()}.")
        seen_offsets.add(offset_minutes)
        reminders.append(ReminderSpec(offset_minutes, clean_message))

    reminders.sort(key=lambda item: item.offset_minutes)
    return reminders


def format_reminder_offsets(reminders: list[ReminderSpec]) -> str:
    labels: list[str] = []
    for reminder in reminders:
        minutes = reminder.offset_minutes
        if minutes == 0:
            labels.append("start")
        elif minutes % 60 == 0:
            hours = abs(minutes) // 60
            labels.append(f"{'-' if minutes < 0 else '+'}{hours}h")
        else:
            labels.append(f"{minutes:+d}m")
    return ", ".join(labels)
