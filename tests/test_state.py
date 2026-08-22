from datetime import datetime, timedelta, timezone

from state import AdminState


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
