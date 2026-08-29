from pathlib import Path
import ast


def test_obsolete_membership_ui_is_not_imported():
    tree = ast.parse(Path("bot.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "ozy.discord_ui":
            imported.update(alias.asname or alias.name for alias in node.names)

    assert "GameNameModal" not in imported
    assert "GameNameView" not in imported


def test_discord_ui_contains_no_membership_form():
    source = Path("ozy/discord_ui.py").read_text(encoding="utf-8")
    assert "GameNameModal" not in source
    assert "MembershipVerificationModal" not in source
