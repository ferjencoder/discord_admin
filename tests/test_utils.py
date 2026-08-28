from datetime import date

from ozy.utils import format_chat_directory, format_schedule, safe_code_block


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


def test_chest_ranking_blocks_split_and_keep_exact_names():
    from types import SimpleNamespace
    from ozy.utils import format_chest_ranking_blocks

    members = tuple(
        SimpleNamespace(name=f"Player {i:02d}", points=1000 - i, chests=i, met_target=False)
        for i in range(45)
    )
    board = SimpleNamespace(
        start="2026-08-23",
        end="2026-08-29",
        week_label="23-29 Aug 2026",
        members=members,
    )
    blocks = format_chest_ranking_blocks(board, chunk_size=20)
    assert len(blocks) == 3
    assert "23.08 TO 29.08 - OZY CHESTS - 1/3" in blocks[0]
    assert "  1. Player 00" in blocks[0]
    assert " 20. Player 19" in blocks[0]
    assert " 21. Player 20" in blocks[1]
    assert " 45. Player 44" in blocks[2]
    assert all(block.startswith("```\n") and block.endswith("\n```") for block in blocks)
