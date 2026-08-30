import ast
from pathlib import Path


def test_bot_ui_imports_are_valid():
    source = Path("bot.py").read_text(encoding="utf-8")
    ast.parse(source)
    assert "AnnouncementModal" in source
    assert "EventSetupModal" in source
    assert "GameNameModal" not in source
    assert "GameNameView" not in source
