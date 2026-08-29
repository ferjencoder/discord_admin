from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_event_create_is_registered_and_required_in_sync():
    source = _source("bot.py")
    assert '@self.tree.command(name="event-create"' in source
    assert 'required_commands = {"event-create", "calendar", "today", "time"}' in source


def test_simple_member_setup_commands_are_registered():
    bot_source = _source("bot.py")
    ui_source = _source("ozy/discord_ui.py")

    assert 'custom_id="ozy:membership:verify"' in ui_source
    assert 'label="Set game name"' in ui_source
    assert '@self.tree.command(name="game-name"' in bot_source
    assert '@self.tree.command(name="member-name"' in bot_source
    assert '@self.tree.command(name="member-troops"' in bot_source
    assert '@self.tree.command(name="members-json"' in bot_source
    assert '@self.tree.command(name="pending-verifications"' not in bot_source
    assert '@self.tree.command(name="verification-history"' not in bot_source


def test_no_leadership_approval_ui_remains():
    bot_source = _source("bot.py")
    ui_source = _source("ozy/discord_ui.py")

    assert "VerificationReviewView" not in bot_source
    assert "VerificationReviewView" not in ui_source
    assert "VerificationApproveButton" not in ui_source
    assert "VerificationRejectButton" not in ui_source
    assert "pending leadership approval" not in bot_source
