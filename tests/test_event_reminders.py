import pytest

from ozy.event_reminders import parse_reminder_plan


def test_omens_preset():
    reminders = parse_reminder_plan("OMENS")
    assert [item.offset_minutes for item in reminders] == [-120, 0, 60]
    assert reminders[1].start_event is True
    assert "ESSENCE" in reminders[1].message
    assert "SUMMONS" in reminders[1].message


def test_custom_reminders_support_hours_and_minutes():
    reminders = parse_reminder_plan("-2h | Ready; 0 | Go; +90m | Follow up")
    assert [item.offset_minutes for item in reminders] == [-120, 0, 90]


def test_duplicate_offsets_rejected():
    with pytest.raises(ValueError):
        parse_reminder_plan("0 | A; 0 | B")
