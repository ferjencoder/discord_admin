from pathlib import Path


def test_event_create_is_registered_and_required_in_sync():
    source = (Path(__file__).resolve().parents[1] / "bot.py").read_text(encoding="utf-8")
    assert '@self.tree.command(name="event-create"' in source
    assert 'required_commands = {"event-create", "calendar", "today", "time"}' in source
