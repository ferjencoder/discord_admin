from pathlib import Path


def test_event_create_is_registered_and_required_in_sync():
    source = (Path(__file__).resolve().parents[1] / "bot.py").read_text(encoding="utf-8")
    assert '@self.tree.command(name="event-create"' in source
    assert 'required_commands = {"event-create", "calendar", "today", "time"}' in source


def test_membership_verification_flow_is_registered():
    source = (Path(__file__).resolve().parents[1] / "bot.py").read_text(encoding="utf-8")
    assert 'custom_id="ozy:membership:verify"' in source
    assert 'title="Verify OZY Membership"' in source
    assert 'TROOP_LEVELS = tuple(f"G{i}" for i in range(1, 10))' in source
    assert 'async def on_member_remove' in source
    assert '"rejoin-existing-link"' in source
