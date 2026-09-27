from pathlib import Path


def _source(path: str) -> str:
    return Path(path).read_text(encoding="utf-8")


def test_join_flow_is_localized_hello_only_after_onboarding():
    source = _source("bot.py")
    block = source.split("async def _process_new_member", 1)[1].split("async def on_member_join", 1)[0]
    assert "choose_welcome(selection.preferred_language)" in block
    assert "if not selection.complete" in block
    assert "self.state.was_welcomed(member.id)" in block
    for forbidden in ("GameNameView", "roster", "verification", "approve", "reject"):
        assert forbidden.casefold() not in block.casefold()


def test_role_updates_trigger_deferred_welcome_once_onboarding_completes():
    source = _source("bot.py")
    block = source.split("async def on_member_update", 1)[1].split("# ------------------------------------------------------------------\n    # Announcements", 1)[0]
    assert "roles_changed" in block
    assert "if not self.state.was_welcomed(after.id)" in block
    assert "await self._process_new_member(after)" in block


def test_leave_flow_is_localized_goodbye_only():
    source = _source("bot.py")
    block = source.split("async def on_member_remove", 1)[1].split("async def on_member_update", 1)[0]
    assert "choose_goodbye(language)" in block
    assert '_find_start_here_text_channel(member.guild, "goodbye")' in block
    for forbidden in ("verification", "roster", "approve", "reject"):
        assert forbidden.casefold() not in block.casefold()


def test_no_join_verification_ui_or_commands():
    bot_source = _source("bot.py")
    ui_source = _source("ozy/discord_ui.py")
    for token in ("GameNameModal", "GameNameView", "MembershipVerification"):
        assert token not in bot_source
        assert token not in ui_source
    for command in ("verify", "pending-verifications", "verification-history", "special-access", "sync-roles"):
        assert f'@self.tree.command(name="{command}"' not in bot_source


def test_start_here_lookup_accepts_branded_category_name():
    source = _source("bot.py")
    block = source.split("def _is_start_here_category_name", 1)[1].split("def _onboarding_selection", 1)[0]
    assert '.endswith("start here")' in block
    assert '== "start here"' not in block


def test_preflight_accepts_branded_start_here_category():
    source = _source("preflight_ozy_admin.py")
    assert 'parent_name.endswith("start here")' in source
    assert 'Missing #goodbye text channel under the START HERE category' in source
