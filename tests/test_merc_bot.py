import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
import pytest

import merc_bot
from ozy.state import AdminState


def test_standalone_settings(monkeypatch):
    monkeypatch.setenv("MERCS_DISCORD_TOKEN", "test-token")
    monkeypatch.setenv("OZY_DATA_API_TOKEN", "test-api")
    monkeypatch.setenv("SERVER_ID", "123")
    monkeypatch.setenv("MERCS_CHANNEL_ID", "456")
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://example.onrender.com")
    monkeypatch.delenv("SELF_PING_ENABLED", raising=False)
    settings = merc_bot.load_merc_settings()
    assert settings.self_ping_enabled
    assert settings.self_ping_interval_seconds == 600
    assert settings.state_remote_url is None
    monkeypatch.delenv("MERCS_DISCORD_TOKEN")
    with pytest.raises(merc_bot.ConfigError):
        merc_bot.load_merc_settings()


def test_refuses_admin_snapshot(monkeypatch):
    monkeypatch.setenv("MERCS_STATE_REMOTE_URL", "https://example.test/state")
    monkeypatch.setenv("STATE_REMOTE_URL", "https://example.test/state")
    monkeypatch.setenv("MERCS_STATE_REMOTE_TOKEN", "test")
    with pytest.raises(merc_bot.ConfigError, match="must not share"):
        merc_bot.load_merc_settings()


def test_health_and_clean_shutdown(tmp_path):
    async def run():
        settings = merc_bot.MercSettings("test", 1, 2, "api", port=0)
        client = merc_bot.MercBot(settings, AdminState(tmp_path / "state.db"))
        assert not hasattr(client, "tree")  # No Admin slash-command registration.
        runner = await merc_bot.start_health(client)
        port = runner.addresses[0][1]
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(f"http://127.0.0.1:{port}/healthz") as response:
                    payload = await response.json()
                    assert payload["discord_ready"] is False
                    assert payload["last_api_success"] is None
                    assert "test" not in str(payload)
            await client.setup_hook()
            assert len(client.background_tasks) == 1
            session = client.session
            await client.close()
            assert session.closed
            assert not client.background_tasks
        finally:
            await client.close()
            await runner.cleanup()
    asyncio.run(run())
