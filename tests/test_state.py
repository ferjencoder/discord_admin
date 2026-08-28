from datetime import datetime, timedelta, timezone

from ozy.state import AdminState


def test_member_link_roundtrip(tmp_path):
    state = AdminState(tmp_path / "state.sqlite3")
    state.set_link(123, "PeekABoo Death", "test")
    assert state.get_link(123) == "PeekABoo Death"
    state.remove_link(123)
    assert state.get_link(123) is None


def test_away_expiry(tmp_path):
    state = AdminState(tmp_path / "state.sqlite3")
    now = datetime.now(timezone.utc)
    state.set_away(123, "PeekABoo Death", now - timedelta(minutes=1), "test")
    expired = state.expired_away(now)
    assert [x.discord_user_id for x in expired] == [123]
    state.clear_away(123)
    assert state.get_away(123) is None


def test_schedule_dedupe(tmp_path):
    state = AdminState(tmp_path / "state.sqlite3")
    assert not state.schedule_posted("2026-08-21")
    state.mark_schedule_posted("2026-08-21", 10, 20)
    assert state.schedule_posted("2026-08-21")


def test_welcome_dedupe(tmp_path):
    state = AdminState(tmp_path / "state.sqlite3")
    assert not state.was_welcomed(123)
    state.mark_welcomed(123)
    assert state.was_welcomed(123)
    state.clear_welcomed(123)
    assert not state.was_welcomed(123)


def test_verification_request_roundtrip(tmp_path):
    state = AdminState(tmp_path / "state.sqlite3")
    state.set_verification_request(123, "PeekABoo Death", "test")
    assert state.get_verification_request(123) == "PeekABoo Death"
    assert state.all_verification_requests() == {123: "PeekABoo Death"}
    state.clear_verification_request(123)
    assert state.get_verification_request(123) is None


def test_generic_bot_state_roundtrip(tmp_path):
    state = AdminState(tmp_path / "state.sqlite3")
    assert state.get_value("calendar") is None
    state.set_value("calendar", "[1,2,3]")
    assert state.get_value("calendar") == "[1,2,3]"
    state.delete_value("calendar")
    assert state.get_value("calendar") is None


def test_linked_user_for_game_name_is_case_insensitive(tmp_path):
    state = AdminState(tmp_path / "state.sqlite3")
    state.set_link(123, "PeekABoo Death", "test")
    assert state.linked_user_for_game_name("peekaboo death") == 123
    assert state.linked_user_for_game_name("Other") is None


def test_member_link_stable_user_id_survives_name_change(tmp_path):
    state = AdminState(tmp_path / "state.sqlite3")
    state.set_link(123, "Old Name", "test", game_user_id="tb:90741542")

    record = state.get_link_record(123)
    assert record is not None
    assert record.game_name == "Old Name"
    assert record.game_user_id == "tb:90741542"
    assert state.linked_user_for_identity("New Name", "tb:90741542") == 123

    state.set_link(123, "New Name", "canonicalized", game_user_id="tb:90741542")
    record = state.get_link_record(123)
    assert record is not None
    assert record.game_name == "New Name"
    assert record.game_user_id == "tb:90741542"


def test_member_profile_troop_level_roundtrip(tmp_path):
    state = AdminState(tmp_path / "state.sqlite3")
    state.set_troop_level(123, "g9", "onboarding-role")
    profile = state.get_member_profile(123)
    assert profile is not None
    assert profile.troop_level == "G9"
    assert profile.troop_level_source == "onboarding-role"


def test_link_updates_member_profile_identity_without_losing_troop_level(tmp_path):
    state = AdminState(tmp_path / "state.sqlite3")
    state.set_troop_level(123, "G8", "verification-modal")
    state.set_link(123, "PeekABoo Death", "test", game_user_id="tb:90741542")

    profile = state.get_member_profile(123)
    assert profile is not None
    assert profile.game_name == "PeekABoo Death"
    assert profile.game_user_id == "tb:90741542"
    assert profile.troop_level == "G8"


def test_verification_request_records_queue_message_and_history(tmp_path):
    state = AdminState(tmp_path / "state.sqlite3")
    request = state.set_verification_request(
        123,
        "PeekABoo Death",
        "verification-modal",
        game_user_id="tb:90741542",
    )
    assert request.requested_game_user_id == "tb:90741542"
    state.set_verification_message(123, 456, 789)
    request = state.get_verification_request_record(123)
    assert request is not None
    assert request.queue_channel_id == 456
    assert request.queue_message_id == 789

    resolved = state.resolve_verification_request(
        123,
        decision="approved",
        reviewed_by_user_id=999,
        reason="matched in game",
    )
    assert resolved is not None
    assert state.get_verification_request_record(123) is None
    history = state.verification_history()
    assert len(history) == 1
    assert history[0].decision == "approved"
    assert history[0].reviewed_by_user_id == 999
    assert history[0].requested_game_user_id == "tb:90741542"


def test_state_storage_label_for_sqlite(tmp_path):
    state = AdminState(tmp_path / "state.sqlite3")
    assert state.backend == "sqlite"
    assert "SQLite" in state.storage_label


def test_web_snapshot_roundtrip_across_fresh_local_files(tmp_path, monkeypatch):
    remote = {"payload": None}

    def fake_remote_request(self, method, body=None):
        if method == "GET":
            return remote["payload"]
        if method == "PUT":
            remote["payload"] = bytes(body or b"")
            return b'{"ok":true}'
        raise AssertionError(method)

    monkeypatch.setattr(AdminState, "_remote_request", fake_remote_request)

    first = AdminState(
        tmp_path / "first.sqlite3",
        remote_url="https://ozy.com.ar/api/ozy-admin/state",
        remote_token="x" * 32,
    )
    assert first.backend == "web-snapshot"
    first.set_link(123, "PeekABoo Death", "test", game_user_id="tb:90741542")
    first.set_troop_level(123, "G9", "onboarding-role")
    assert remote["payload"] is not None
    assert remote["payload"].startswith(AdminState.SQLITE_HEADER)
    first.close()

    second = AdminState(
        tmp_path / "second.sqlite3",
        remote_url="https://ozy.com.ar/api/ozy-admin/state",
        remote_token="x" * 32,
    )
    record = second.get_link_record(123)
    assert record is not None
    assert record.game_name == "PeekABoo Death"
    assert record.game_user_id == "tb:90741542"
    profile = second.get_member_profile(123)
    assert profile is not None
    assert profile.troop_level == "G9"
    assert "OZY Web snapshot" in second.storage_label
    second.close()
