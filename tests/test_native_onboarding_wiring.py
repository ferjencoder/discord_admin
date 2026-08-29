from pathlib import Path
import json


def test_game_name_modal_only_collects_roster_name():
    source = Path("ozy/discord_ui.py").read_text(encoding="utf-8")
    block = source.split("class GameNameModal", 1)[1].split("class GameNameView", 1)[0]
    assert 'label="Total Battle name"' in block
    assert 'title="Set your Total Battle name"' in block
    assert "preferred_language=" not in block
    assert "guardsmen_level=" not in block


def test_typed_name_mismatch_offers_roster_suggestions():
    source = Path("bot.py").read_text(encoding="utf-8")
    assert "async def _typed_roster_suggestions" in source
    assert "RosterSuggestionView(self, member.id, suggestions)" in source
    assert "await self.data.roster_suggestions(value" in source


def test_exact_roster_match_links_without_approval_queue():
    source = Path("bot.py").read_text(encoding="utf-8")
    block = source.split("async def _process_game_name_claim", 1)[1].split("async def _submit_game_name", 1)[0]
    assert "self.state.set_link(" in block
    assert "await self._sync_rank_role(member, canonical)" in block
    assert "_queue_verification_request" not in block
    assert "leadership approval" not in block.casefold()


def test_native_roles_are_mirrored_to_profile_state():
    source = Path("bot.py").read_text(encoding="utf-8")
    assert "def _sync_profile_from_onboarding_roles" in source
    assert 'source="discord-onboarding"' in source
    assert "extract_onboarding_profile(" in source
    assert "self._sync_profile_from_onboarding_roles(after)" in source


def test_onboarding_only_contains_language_and_gms():
    payload = json.loads(Path("config/discord/onboarding.json").read_text(encoding="utf-8"))
    titles = [prompt["title"] for prompt in payload["prompts"]]
    assert titles == [
        "What language do you prefer?",
        "What is your Guardsmen level?",
        "What is your Monsters level?",
        "What is your Specialists level?",
    ]


def test_management_commands_cover_names_troops_and_json():
    source = Path("bot.py").read_text(encoding="utf-8")
    assert '@self.tree.command(name="member-name"' in source
    assert '@self.tree.command(name="member-troops"' in source
    assert '@self.tree.command(name="members-json"' in source
    assert "async def _build_members_json" in source


def test_profile_command_is_read_only_native_profile_view():
    source = Path("bot.py").read_text(encoding="utf-8")
    block = source.split('@self.tree.command(name="profile"', 1)[1].split('@self.tree.command(name="member"', 1)[0]
    assert "Channels & Roles" in block


def test_first_time_game_identity_is_never_auto_linked_from_discord_display_name():
    source = Path("bot.py").read_text(encoding="utf-8")
    block = source.split("async def _resolve_member_game_name", 1)[1].split("async def _sync_access_roles", 1)[0]
    assert "exact_roster_name(member.display_name)" not in block
    assert "trusted-exact-display-name" not in block

