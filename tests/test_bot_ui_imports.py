from pathlib import Path
import ast


def test_membership_verification_modal_is_imported():
    """Regression test for the live NameError seen when clicking Verify OZY membership."""
    tree = ast.parse(Path("bot.py").read_text(encoding="utf-8"))
    imported = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "ozy.discord_ui":
            imported.update(alias.asname or alias.name for alias in node.names)

    assert "MembershipVerificationModal" in imported


def test_membership_verification_modal_reference_exists():
    source = Path("bot.py").read_text(encoding="utf-8")
    assert "MembershipVerificationModal(" in source
