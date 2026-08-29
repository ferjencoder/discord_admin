from pathlib import Path

from ozy.state import AdminState


def test_post_verification_profile_persists_language_and_gms(tmp_path: Path):
    db = tmp_path / "state.sqlite3"
    state = AdminState(db)
    try:
        state.set_link(1001, "Red Jane", "test", game_user_id="tb:90696881")
        state.set_member_profile_details(
            1001,
            preferred_language="ES",
            guardsmen_level=9,
            monsters_level=8,
            specialists_level=7,
            source="test-profile",
            game_name="Red Jane",
            game_user_id="tb:90696881",
        )
        profile = state.get_member_profile(1001)
        assert profile is not None
        assert profile.preferred_language == "ES"
        assert profile.guardsmen_level == 9
        assert profile.monsters_level == 8
        assert profile.specialists_level == 7
        assert profile.profile_complete is True
        # Legacy compatibility remains available while old consumers migrate.
        assert profile.troop_level == "G9"
    finally:
        state.close()

    # Re-open to prove this is persisted state, not an in-memory value.
    state2 = AdminState(db)
    try:
        profile = state2.get_member_profile(1001)
        assert profile is not None
        assert profile.preferred_language == "ES"
        assert (profile.guardsmen_level, profile.monsters_level, profile.specialists_level) == (9, 8, 7)
    finally:
        state2.close()


def test_legacy_guard_level_migrates_to_guardsmen(tmp_path: Path):
    db = tmp_path / "state.sqlite3"
    state = AdminState(db)
    try:
        state.set_troop_level(2002, "G6", "legacy-test")
    finally:
        state.close()

    # Simulate an old DB by clearing the migrated G field, then re-run schema init.
    import sqlite3
    conn = sqlite3.connect(db)
    try:
        conn.execute("UPDATE member_profiles SET guardsmen_level=NULL WHERE discord_user_id=2002")
        conn.commit()
    finally:
        conn.close()

    state2 = AdminState(db)
    try:
        profile = state2.get_member_profile(2002)
        assert profile is not None
        assert profile.guardsmen_level == 6
        assert profile.profile_complete is False
    finally:
        state2.close()


def test_plain_game_name_clears_legacy_stable_identity(tmp_path):
    state = AdminState(tmp_path / "state.sqlite3")
    state.set_link(123, "OldName", "legacy", game_user_id="999")
    state.set_plain_game_name(123, "New Name", "member-entered")
    link = state.get_link_record(123)
    assert link is not None
    assert link.game_name == "New Name"
    assert link.game_user_id is None
    profile = state.get_member_profile(123)
    assert profile is not None
    assert profile.game_name == "New Name"
    assert profile.game_user_id is None
