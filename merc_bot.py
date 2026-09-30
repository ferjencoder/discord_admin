"""Dedicated Merc feed: run as a Render Web Service with python merc_bot.py."""
from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
import discord
from aiohttp import web
from dotenv import load_dotenv

from ozy.data_provider import DataProvider, DataUnavailable
from ozy.mercs import MercFeed
from ozy.state import AdminState
from settings import ConfigError, _env_bool, _env_int, _required

log = logging.getLogger("ozy-merc")


@dataclass(frozen=True)
class MercSettings:
    discord_token: str
    server_id: int
    mercs_channel_id: int
    ozy_data_api_token: str
    mercs_poll_seconds: int = 15
    http_timeout_seconds: float = 10
    port: int = 10000
    render_external_url: str = ""
    self_ping_enabled: bool = False
    self_ping_interval_seconds: int = 600
    state_db: Path = Path("data/merc_bot.sqlite3")
    state_remote_url: str | None = None
    state_remote_token: str | None = None


def load_merc_settings():
    load_dotenv()
    interval = _env_int("MERCS_POLL_SECONDS", 15, 5)
    if interval > 60:
        raise ConfigError("MERCS_POLL_SECONDS must be <= 60")
    url = os.getenv("RENDER_EXTERNAL_URL", "").strip().rstrip("/")
    remote = os.getenv("MERCS_STATE_REMOTE_URL", "").strip() or None
    remote_token = os.getenv("MERCS_STATE_REMOTE_TOKEN", "").strip() or None
    if remote and not remote_token:
        raise ConfigError("MERCS_STATE_REMOTE_TOKEN is required for remote state")
    if remote and remote == os.getenv("STATE_REMOTE_URL", "").strip():
        raise ConfigError("Merc bot must not share the Admin state snapshot endpoint")
    return MercSettings(
        # A distinct name prevents accidental reuse of the Admin bot token locally.
        discord_token=_required("MERCS_DISCORD_TOKEN"),
        server_id=_env_int("SERVER_ID", 0, 1),
        mercs_channel_id=_env_int("MERCS_CHANNEL_ID", 0, 1),
        ozy_data_api_token=_required("OZY_DATA_API_TOKEN"),
        mercs_poll_seconds=interval,
        port=_env_int("PORT", 10000, 1),
        render_external_url=url,
        self_ping_enabled=_env_bool("SELF_PING_ENABLED", bool(url)),
        self_ping_interval_seconds=_env_int("SELF_PING_INTERVAL_SECONDS", 600, 60),
        state_db=Path(os.getenv("MERCS_STATE_DB", "data/merc_bot.sqlite3")),
        state_remote_url=remote,
        state_remote_token=remote_token,
    )


class MercBot(discord.Client):
    def __init__(self, settings, state):
        intents = discord.Intents.none()
        intents.guilds = True
        super().__init__(intents=intents)
        self.settings = settings
        self.state = state
        self.session = None
        self.background_tasks = []
        self.feed_status = "starting"
        self.last_api_success = None
        self.last_post = None
        self.fresh_count = 0

    async def setup_hook(self):
        self.session = aiohttp.ClientSession()
        self.data = DataProvider(self.settings, self.session)
        self.background_tasks.append(asyncio.create_task(self.poll(), name="mercs-feed"))
        if self.settings.self_ping_enabled and self.settings.render_external_url:
            self.background_tasks.append(asyncio.create_task(self.self_ping(), name="self-ping"))

    async def poll(self):
        feed = MercFeed(self.state, self.settings.mercs_channel_id)
        failures = 0
        last_health = None
        last_log = 0
        while not self.is_closed():
            await self.wait_until_ready()
            try:
                rows = await self.data.current_mercs()
                self.last_api_success = datetime.now(timezone.utc).isoformat()
                self.fresh_count = len(rows)
                channel = self.get_channel(self.settings.mercs_channel_id)
                if channel is None:
                    channel = await self.fetch_channel(self.settings.mercs_channel_id)
                if (not isinstance(channel, (discord.TextChannel, discord.Thread))
                        or channel.guild.id != self.settings.server_id):
                    raise ValueError("Destination must be a text channel/thread in SERVER_ID")

                async def send(content):
                    await channel.send(content, allowed_mentions=discord.AllowedMentions.none())
                    self.last_post = datetime.now(timezone.utc).isoformat()

                posted, invalid = await feed.publish(rows, send)
                self.feed_status = "invalid-records" if invalid else "polling"
                failures = 0
                if posted:
                    log.info("Posted %d Merc locations", posted)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.feed_status = str(exc) if isinstance(exc, DataUnavailable) else type(exc).__name__
                failures += 1
            now = asyncio.get_running_loop().time()
            if self.feed_status != last_health or now - last_log >= 300:
                log.info("Merc feed: %s; last API count=%d", self.feed_status, self.fresh_count)
                last_health, last_log = self.feed_status, now
            await asyncio.sleep(min(300, self.settings.mercs_poll_seconds * 2 ** min(failures, 5)))

    async def self_ping(self):
        await asyncio.sleep(30)
        while not self.is_closed():
            try:
                async with self.session.get(
                    self.settings.render_external_url + "/healthz",
                    timeout=aiohttp.ClientTimeout(total=10), allow_redirects=False,
                ) as response:
                    if response.status >= 400:
                        log.warning("Self-ping HTTP %d", response.status)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("Self-ping failed (%s)", type(exc).__name__)
            await asyncio.sleep(self.settings.self_ping_interval_seconds)

    async def close(self):
        for task in self.background_tasks:
            task.cancel()
        await asyncio.gather(*self.background_tasks, return_exceptions=True)
        self.background_tasks.clear()
        if self.session:
            await self.session.close()
        self.state.close()
        await super().close()


async def start_health(bot):
    async def health(request):
        return web.json_response({
            "status": "ok", "discord_ready": bot.is_ready(),
            "feed_status": bot.feed_status, "fresh_count": bot.fresh_count,
            "last_api_success": bot.last_api_success, "last_post": bot.last_post,
        })
    app = web.Application()
    app.router.add_get("/", health)
    app.router.add_get("/healthz", health)
    runner = web.AppRunner(app, access_log=None)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", bot.settings.port).start()
    return runner


async def main():
    settings = load_merc_settings()
    state = AdminState(settings.state_db, remote_url=settings.state_remote_url,
                       remote_token=settings.state_remote_token)
    bot = MercBot(settings, state)
    runner = await start_health(bot)
    try:
        await bot.start(settings.discord_token, reconnect=True)
    finally:
        await bot.close()
        await runner.cleanup()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(main())
