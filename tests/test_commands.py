from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_required_commands_are_registered_and_required_in_sync():
    source = _source("bot.py")
    required = ("event-create", "calendar", "today", "time")

    for command in required:
        assert f'@self.tree.command(name="{command}"' in source

    assert 'required_commands = {"event-create", "calendar", "today", "time"}' in source


def test_member_admin_commands_are_registered_without_join_verification_ui():
    bot_source = _source("bot.py")
    ui_source = _source("ozy/discord_ui.py")
    assert '@self.tree.command(name="game-name"' in bot_source
    assert '@self.tree.command(name="member-name"' in bot_source
    assert '@self.tree.command(name="member-troops"' in bot_source
    assert '@self.tree.command(name="members-json"' in bot_source
    for token in ("MembershipVerification", "GameNameModal", "GameNameView", "VerificationReviewView"):
        assert token not in bot_source
        assert token not in ui_source


def test_old_membership_commands_are_not_registered():
    source = _source("bot.py")
    for command in ("verify", "pending-verifications", "verification-history", "special-access", "sync-roles"):
        assert f'@self.tree.command(name="{command}"' not in source
