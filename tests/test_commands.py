from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_event_create_is_registered_and_required_in_sync():
    source = _source("bot.py")
    assert '@self.tree.command(name="event-create"' in source
    assert 'required_commands = {"event-create", "calendar", "today", "time"}' in source


def test_membership_verification_flow_is_registered():
    bot_source = _source("bot.py")
    ui_source = _source("ozy/discord_ui.py")
    constants_source = _source("ozy/constants.py")

    assert 'custom_id="ozy:membership:verify"' in ui_source
    assert "class MembershipVerificationModal" in ui_source
    assert 'PROFILE_LANGUAGES = (' in constants_source
    assert 'PROFILE_LEVELS = tuple(range(1, 10))' in constants_source
    assert 'from ozy.onboarding_profile import extract_onboarding_profile' in bot_source
    assert '@self.tree.command(name="profile"' in bot_source
    assert 'async def on_member_remove' in bot_source
    assert '"rejoin-existing-link"' in bot_source


def test_verification_review_ui_is_registered():
    bot_source = _source("bot.py")
    ui_source = _source("ozy/discord_ui.py")

    assert 'custom_id=f"ozy:verification:approve:{target_user_id}"' in ui_source
    assert 'custom_id=f"ozy:verification:reject:{target_user_id}"' in ui_source
    assert '@self.tree.command(name="verification-history"' in bot_source
