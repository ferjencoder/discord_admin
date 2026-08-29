from pathlib import Path


def test_verification_modal_only_collects_roster_name():
    source = Path("ozy/discord_ui.py").read_text(encoding="utf-8")
    block = source.split("class MembershipVerificationModal", 1)[1].split("class MembershipVerificationView", 1)[0]
    assert 'label="Total Battle name"' in block
    assert "preferred_language=" not in block
    assert "guardsmen_level=" not in block
    assert "monsters_level=" not in block
    assert "specialists_level=" not in block


def test_roster_suggestion_submits_in_one_click():
    source = Path("ozy/discord_ui.py").read_text(encoding="utf-8")
    block = source.split("class RosterSuggestionSelect", 1)[1].split("class RosterSuggestionView", 1)[0]
    assert "_submit_suggested_roster_name(interaction, selected)" in block
    assert "_open_membership_verification(interaction, suggested_name=selected)" not in block


def test_native_roles_are_mirrored_to_profile_state():
    source = Path("bot.py").read_text(encoding="utf-8")
    assert "def _sync_profile_from_onboarding_roles" in source
    assert 'source="discord-onboarding"' in source
    assert "extract_onboarding_profile(" in source
    assert "self._sync_profile_from_onboarding_roles(after)" in source


def test_unverified_access_sync_does_not_strip_language_metadata():
    source = Path("bot.py").read_text(encoding="utf-8")
    block = source.split("async def _sync_access_roles", 1)[1].split("async def _sync_rank_role", 1)[0]
    assert "_language_role_ids" not in block
    assert "metadata only" in block


def test_profile_command_is_read_only_native_profile_view():
    source = Path("bot.py").read_text(encoding="utf-8")
    block = source.split('@self.tree.command(name="profile"', 1)[1].split('@self.tree.command(name="member"', 1)[0]
    assert "Channels & Roles" in block
    assert "_open_post_verification_profile" not in block
