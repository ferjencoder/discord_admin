from pathlib import Path


def test_join_modal_collects_complete_profile():
    source = Path("ozy/discord_ui.py").read_text(encoding="utf-8")
    block = source.split("class MembershipVerificationModal", 1)[1].split("class MembershipVerificationView", 1)[0]
    assert 'text="Total Battle name"' in block
    assert 'text="Preferred language"' in block
    assert 'text="Guardsmen level"' in block
    assert 'text="Monsters level"' in block
    assert 'text="Specialists level"' in block
    assert "preferred_language=self.language.values[0]" in block
    assert "guardsmen_level=int(self.guardsmen.values[0])" in block
    assert "monsters_level=int(self.monsters.values[0])" in block
    assert "specialists_level=int(self.specialists.values[0])" in block


def test_roster_suggestion_prefills_complete_form():
    source = Path("ozy/discord_ui.py").read_text(encoding="utf-8")
    block = source.split("class RosterSuggestionSelect", 1)[1].split("class RosterSuggestionView", 1)[0]
    assert "_open_membership_verification(interaction, suggested_name=selected)" in block
    assert "_submit_suggested_roster_name(interaction, selected)" not in block


def test_profile_saved_before_approval_and_queue_refreshed():
    source = Path("bot.py").read_text(encoding="utf-8")
    block = source.split("async def _submit_membership_verification", 1)[1].split("async def _resolve_member_game_name", 1)[0]
    assert "set_member_profile_details(" in block
    assert 'source="verification-submission"' in block
    assert "await self._publish_verification_request(member.id)" in block
