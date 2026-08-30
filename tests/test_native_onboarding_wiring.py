from pathlib import Path
import json


def test_game_name_is_plain_profile_data_without_roster_validation():
    source = Path("bot.py").read_text(encoding="utf-8")
    block = source.split("async def _submit_game_name", 1)[1].split("async def _resolve_member_game_name", 1)[0]
    assert "set_plain_game_name" in block
    assert "exact_roster_name" not in block
    assert "roster_suggestions" not in block
    assert "linked_user_for_identity" not in block
    assert "_sync_rank_role" not in block


def test_welcome_is_hello_only_and_does_not_gate_access():
    source = Path("bot.py").read_text(encoding="utf-8")
    block = source.split("async def _process_new_member", 1)[1].split("async def on_member_join", 1)[0]
    assert "ALL ABOARD THE CRAZY TRAIN" in block
    assert "_sync_access_roles" not in block
    for forbidden in ("roster", "verify", "approval", "GameNameView"):
        assert forbidden.casefold() not in block.casefold()


def test_native_roles_are_mirrored_to_profile_state():
    source = Path("bot.py").read_text(encoding="utf-8")
    assert "def _sync_profile_from_onboarding_roles" in source
    assert 'source="discord-onboarding"' in source
    assert "extract_onboarding_profile(" in source
    assert "self._sync_profile_from_onboarding_roles(after)" in source


def test_onboarding_language_grants_member_access_and_metadata():
    payload = json.loads(Path("config/discord/onboarding.json").read_text(encoding="utf-8"))
    titles = [prompt["title"] for prompt in payload["prompts"]]
    assert titles == [
        "What language do you prefer?",
        "What is your Guardsmen level?",
        "What is your Monsters level?",
        "What is your Specialists level?",
    ]
    language = payload["prompts"][0]
    verified = "1542765587023925298"
    assert language["required"] is True
    for option in language["options"]:
        assert verified in option["role_ids"]
        assert len(option["role_ids"]) == 2


def test_management_commands_cover_names_troops_and_json():
    source = Path("bot.py").read_text(encoding="utf-8")
    assert '@self.tree.command(name="member-name"' in source
    assert '@self.tree.command(name="member-troops"' in source
    assert '@self.tree.command(name="members-json"' in source
    assert "async def _build_members_json" in source
    assert '@self.tree.command(name="access-sync"' not in source


def test_members_json_is_discord_based_not_roster_based():
    source = Path("bot.py").read_text(encoding="utf-8")
    block = source.split("async def _build_members_json", 1)[1].split("async def _submit_game_name", 1)[0]
    assert "for member in guild.members" in block
    assert "await self.data.roster()" not in block
    assert '"game_name": game_name' in block


def test_profile_command_is_read_only_native_profile_view():
    source = Path("bot.py").read_text(encoding="utf-8")
    block = source.split('@self.tree.command(name="profile"', 1)[1].split('@self.tree.command(name="member"', 1)[0]
    assert "Channels & Roles" in block
