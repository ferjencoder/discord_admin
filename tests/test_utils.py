from datetime import date

from utils import format_chat_directory, format_schedule, safe_code_block


class Item:
    def __init__(self, time, title, details=""):
        self.time = time
        self.title = title
        self.details = details


def test_safe_code_block_neutralizes_fence():
    out = safe_code_block("hello ``` world")
    assert "`\u200b``" in out
    assert out.startswith("```text\n")
    assert out.endswith("\n```")


def test_chat_directory_has_individual_copy_blocks():
    out = format_chat_directory([
        {"label": "Clan Announcements", "name": "OZY ⓝⓔⓦⓢ"},
        {"label": "Chest Tracker", "name": "OZY ⓒⓗⓔⓢⓣ"},
    ])
    assert out.startswith("# OZY Clan Chat Directory")
    assert "### Clan Announcements\n```text\nOZY ⓝⓔⓦⓢ\n```" in out
    assert "### Chest Tracker\n```text\nOZY ⓒⓗⓔⓢⓣ\n```" in out


def test_schedule_format():
    out = format_schedule(date(2026, 8, 21), [Item("14:00", "Reset", "Do the thing")])
    assert "OZY Schedule" in out
    assert "14:00 - Reset" in out
    assert "Do the thing" in out
