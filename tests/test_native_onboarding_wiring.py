from pathlib import Path
import json


def test_game_name_is_discord_nickname_not_roster_link():
    source = Path("bot.py").read_text(encoding="utf-8")
    helper = source.split("def _game_name_from_discord", 1)[1].split("def _find_named_text_channel", 1)[0]
    assert "member.nick or member.display_name" in helper

    command = source.split('@self.tree.command(name="game-name"', 1)[1].split('@self.tree.command(name="member-name"', 1)[0]
    assert "member.edit" not in command  # command delegates
    assert "_set_game_name" in command
    assert "exact_roster_name" not in command
    assert "roster_suggestions" not in command
    assert "set_plain_game_name" not in command


def test_set_game_name_changes_server_nickname_only():
    source = Path("bot.py").read_text(encoding="utf-8")
    block = source.split("async def _set_game_name", 1)[1].split("async def _resolve_member_game_name", 1)[0]
    assert "await member.edit(nick=entered" in block
    assert "exact_roster_name" not in block
    assert "roster_suggestions" not in block
    assert "set_link(" not in block
    assert "set_plain_game_name" not in block


def test_welcome_and_goodbye_have_no_verification_flow():
    source = Path("bot.py").read_text(encoding="utf-8")
    join = source.split("async def _process_new_member", 1)[1].split("async def on_member_join", 1)[0]
    leave = source.split("async def on_member_remove", 1)[1].split("async def on_member_update", 1)[0]
    assert "Welcome to the Madhouse" in join
    assert "approval" not in join.casefold()
    assert "roster" not in join.casefold()
    assert "game-name" not in join.casefold()
    assert "Another Bat Leaves the Belfry" in leave
    assert 'category_name="START HERE"' in leave


def test_native_roles_are_mirrored_to_profile_state():
    source = Path("bot.py").read_text(encoding="utf-8")
    assert "def _sync_profile_from_onboarding_roles" in source
    assert 'source="discord-onboarding"' in source
    assert "extract_onboarding_profile(" in source
    assert "self._sync_profile_from_onboarding_roles(after)" in source


def test_onboarding_has_four_required_profile_questions():
    payload = json.loads(Path("config/discord/onboarding.json").read_text(encoding="utf-8"))
    titles = [prompt["title"] for prompt in payload["prompts"]]
    assert titles == [
        "What language do you prefer?",
        "What is your Guardsmen level?",
        "What is your Monsters level?",
        "What is your Specialists level?",
    ]
    assert all(prompt["required"] is True for prompt in payload["prompts"])
    language = payload["prompts"][0]
    member_access = "1542765587023925298"
    for option in language["options"]:
        assert member_access in option["role_ids"]
        assert len(option["role_ids"]) == 2


def test_management_commands_cover_names_troops_and_json():
    source = Path("bot.py").read_text(encoding="utf-8")
    assert '@self.tree.command(name="member-name"' in source
    assert '@self.tree.command(name="member-troops"' in source
    assert '@self.tree.command(name="members-json"' in source
    assert "async def _build_members_json" in source


def test_members_json_is_discord_based_not_roster_based():
    source = Path("bot.py").read_text(encoding="utf-8")
    block = source.split("async def _build_members_json", 1)[1].split("async def _set_game_name", 1)[0]
    assert "for member in guild.members" in block
    assert "await self.data.roster()" not in block
    assert '"game_name": game_name' in block
    assert '"discord_nickname": member.nick' in block


def test_profile_command_is_read_only_native_profile_view():
    source = Path("bot.py").read_text(encoding="utf-8")
    block = source.split('@self.tree.command(name="profile"', 1)[1].split('@self.tree.command(name="member"', 1)[0]
    assert "Channels & Roles" in block


def test_troop_levels_are_highest_to_lowest_in_onboarding():
    payload = json.loads(Path("config/discord/onboarding.json").read_text(encoding="utf-8"))
    by_title = {prompt["title"]: prompt for prompt in payload["prompts"]}
    assert [o["title"] for o in by_title["What is your Guardsmen level?"]["options"]] == [
        "G9", "G8", "G7", "G6", "G5", "G4", "G3", "G2", "G1"
    ]
    assert [o["title"] for o in by_title["What is your Monsters level?"]["options"]] == [
        "M9", "M8", "M7", "M6", "M5", "M4", "M3", "M2", "M1"
    ]
    assert [o["title"] for o in by_title["What is your Specialists level?"]["options"]] == [
        "S9", "S8", "S7", "S6", "S5", "S4", "S3", "S2", "S1"
    ]


def test_onboarding_payload_has_no_hot_clan_branding():
    text = Path("config/discord/onboarding.json").read_text(encoding="utf-8")
    assert "HOT Clan" not in text
    assert "\"HOT\"" not in text
