from pathlib import Path
import ast


def test_game_name_modal_is_imported():
    """Regression test: the persistent Set game name button must import its modal."""
    tree = ast.parse(Path("bot.py").read_text(encoding="utf-8"))
    imported = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "ozy.discord_ui":
            imported.update(alias.asname or alias.name for alias in node.names)

    assert "GameNameModal" in imported


def test_game_name_modal_reference_exists():
    source = Path("bot.py").read_text(encoding="utf-8")
    assert "GameNameModal(" in source
