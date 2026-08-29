from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_event_create_is_registered_and_required_in_sync():
    source = _source("bot.py")
    assert '@self.tree.command(name="event-create"' in source
    assert 'required_commands = {"event-create", "calendar", "today", "time"}' in source


def test_member_profile_management_commands_are_registered():
    source = _source("bot.py")
    assert '@self.tree.command(name="game-name"' in source
    assert '@self.tree.command(name="member-name"' in source
    assert '@self.tree.command(name="member-troops"' in source
    assert '@self.tree.command(name="members-json"' in source
    assert '@self.tree.command(name="pending-verifications"' not in source
    assert '@self.tree.command(name="verification-history"' not in source
    assert '@self.tree.command(name="special-access"' not in source
    assert '@self.tree.command(name="sync-roles"' not in source


def test_join_leave_are_greeting_only():
    source = _source("bot.py")
    join_block = source.split("async def _process_new_member", 1)[1].split("async def on_member_join", 1)[0]
    leave_block = source.split("async def on_member_remove", 1)[1].split("async def on_member_update", 1)[0]

    assert "Welcome to the Madhouse" in join_block
    assert "GameNameView" not in join_block
    assert "roster" not in join_block.casefold()
    assert "Another Bat Leaves the Belfry" in leave_block
    assert 'channel_name="goodbye"' in leave_block
    assert "verification" not in leave_block.casefold()


def test_no_membership_verification_ui_remains():
    bot_source = _source("bot.py")
    ui_source = _source("ozy/discord_ui.py")
    for name in (
        "GameNameModal",
        "GameNameView",
        "MembershipVerificationModal",
        "VerificationReviewView",
        "VerificationApproveButton",
        "VerificationRejectButton",
    ):
        assert name not in bot_source
        assert name not in ui_source
