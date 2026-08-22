from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, time as dt_time, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

import aiohttp
import discord
from aiohttp import web
from discord import app_commands

from data_provider import DataProvider, DataUnavailable
from settings import ConfigError, Settings, load_settings
from state import AdminState
from utils import format_chat_directory, format_schedule, safe_code_block, truncate
from voltron_calendar import (
    VoltronCalendarClient,
    VoltronCalendarError,
    build_calendar_chunks,
    build_today_chunks,
    build_today_local_chunks,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("ozy-admin")

TIMEZONE_CHOICES = [
    app_commands.Choice(name="Argentina", value="America/Argentina/Buenos_Aires"),
    app_commands.Choice(name="UTC / Game time", value="UTC"),
    app_commands.Choice(name="United Kingdom", value="Europe/London"),
    app_commands.Choice(name="France / Germany / Spain", value="Europe/Paris"),
    app_commands.Choice(name="Romania / Bulgaria", value="Europe/Bucharest"),
    app_commands.Choice(name="Moscow", value="Europe/Moscow"),
    app_commands.Choice(name="Turkey", value="Europe/Istanbul"),
    app_commands.Choice(name="UAE / Dubai", value="Asia/Dubai"),
    app_commands.Choice(name="India", value="Asia/Kolkata"),
    app_commands.Choice(name="Singapore / Malaysia", value="Asia/Singapore"),
    app_commands.Choice(name="Australia / Sydney", value="Australia/Sydney"),
    app_commands.Choice(name="Brazil", value="America/Sao_Paulo"),
    app_commands.Choice(name="US Eastern", value="America/New_York"),
    app_commands.Choice(name="US Central", value="America/Chicago"),
    app_commands.Choice(name="US Pacific", value="America/Los_Angeles"),
    app_commands.Choice(name="Canada / Toronto", value="America/Toronto"),
    app_commands.Choice(name="South Africa", value="Africa/Johannesburg"),
]


class AnnouncementModal(discord.ui.Modal):
    def __init__(self, bot: "OZYAdminBot", ping: bool):
        super().__init__(title="OZY Announcement", timeout=300)
        self.bot = bot
        self.ping = ping

        self.heading = discord.ui.TextInput(
            label="Title",
            placeholder="Example: Ragnarok Instructions",
            max_length=100,
        )
        self.body = discord.ui.TextInput(
            label="Discord announcement",
            placeholder="Write the announcement members should read in Discord.",
            style=discord.TextStyle.paragraph,
            max_length=3500,
        )
        self.tb_copy = discord.ui.TextInput(
            label="Total Battle copy/paste text (optional)",
            placeholder="Optional compact version for copying into Total Battle.",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=1500,
        )
        self.add_item(self.heading)
        self.add_item(self.body)
        self.add_item(self.tb_copy)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await self.bot.publish_announcement(
            interaction,
            title=str(self.heading.value),
            body=str(self.body.value),
            tb_copy=str(self.tb_copy.value or ""),
            ping=self.ping,
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        log.exception("Announcement modal failed", exc_info=error)
        if interaction.response.is_done():
            await interaction.followup.send("Announcement failed. Check the admin-bot logs.", ephemeral=True)
        else:
            await interaction.response.send_message("Announcement failed. Check the admin-bot logs.", ephemeral=True)


class OZYAdminBot(discord.Client):
    def __init__(self, settings: Settings):
        intents = discord.Intents.none()
        intents.guilds = True
        intents.members = True

        super().__init__(intents=intents)
        self.settings = settings
        self.tree = app_commands.CommandTree(self)
        self.state = AdminState(settings.state_db)

        self.http_session: aiohttp.ClientSession | None = None
        self.data: DataProvider | None = None
        self.voltron: VoltronCalendarClient | None = None
        self.health_runner: web.AppRunner | None = None
        self.background_tasks: list[asyncio.Task] = []
        self._guild_validated = False
        self._message_series_lock = asyncio.Lock()

        self._register_commands()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def setup_hook(self) -> None:
        self.http_session = aiohttp.ClientSession(
            headers={"User-Agent": "OZY-Admin-Bot/1.0"}
        )
        self.data = DataProvider(self.settings, self.http_session)
        self.voltron = VoltronCalendarClient(self.settings, self.http_session)

        await self._start_health_server()

        guild = discord.Object(id=self.settings.server_id)
        self.tree.copy_global_to(guild=guild)
        synced = await self.tree.sync(guild=guild)
        log.info("Synced %d application commands to guild %s", len(synced), self.settings.server_id)

        self.background_tasks.append(asyncio.create_task(self._daily_schedule_loop(), name="daily-schedule"))
        self.background_tasks.append(asyncio.create_task(self._away_expiry_loop(), name="away-expiry"))
        if self.settings.voltron_calendar_enabled and (self.settings.calendar_channel_id or self.settings.today_channel_id):
            self.background_tasks.append(asyncio.create_task(self._voltron_refresh_loop(), name="voltron-refresh"))
            if self.settings.voltron_today_enabled and self.settings.today_channel_id:
                self.background_tasks.append(asyncio.create_task(self._voltron_today_loop(), name="voltron-today"))

        if self.settings.self_ping_enabled and self.settings.render_external_url:
            self.background_tasks.append(asyncio.create_task(self._self_ping_loop(), name="self-ping"))

    async def close(self) -> None:
        for task in self.background_tasks:
            task.cancel()
        if self.background_tasks:
            await asyncio.gather(*self.background_tasks, return_exceptions=True)
        self.background_tasks.clear()

        if self.health_runner is not None:
            await self.health_runner.cleanup()
            self.health_runner = None

        if self.http_session is not None and not self.http_session.closed:
            await self.http_session.close()

        await super().close()

    async def on_ready(self) -> None:
        if not self._guild_validated:
            try:
                await self._validate_guild_configuration()
            except Exception as exc:
                log.critical("Guild/config validation failed: %s", exc)
                await self.close()
                return
            self._guild_validated = True

        log.info("OZY Admin operational as %s (%s)", self.user, self.user.id if self.user else "?")

    async def _start_health_server(self) -> None:
        async def health(_: web.Request) -> web.Response:
            return web.json_response(
                {
                    "status": "ok",
                    "discord_ready": self.is_ready(),
                    "guild": self.settings.server_id,
                    "utc": datetime.now(timezone.utc).isoformat(),
                }
            )

        app = web.Application()
        app.router.add_get("/", health)
        app.router.add_get("/healthz", health)
        self.health_runner = web.AppRunner(app, access_log=None)
        await self.health_runner.setup()
        site = web.TCPSite(self.health_runner, "0.0.0.0", self.settings.port)
        await site.start()
        log.info("Health server listening on port %d", self.settings.port)

    async def _self_ping_loop(self) -> None:
        assert self.http_session is not None
        assert self.settings.render_external_url is not None
        await asyncio.sleep(30)
        url = self.settings.render_external_url.rstrip("/") + "/healthz"
        while not self.is_closed():
            try:
                async with self.http_session.get(
                    url,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as response:
                    if response.status >= 400:
                        log.warning("Self-ping returned HTTP %d", response.status)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("Self-ping failed: %s", exc)
            await asyncio.sleep(self.settings.self_ping_interval_seconds)

    # ------------------------------------------------------------------
    # Guild/config validation
    # ------------------------------------------------------------------
    async def _validate_guild_configuration(self) -> None:
        guild = self.get_guild(self.settings.server_id)
        if guild is None:
            raise ConfigError(f"Bot is not connected to SERVER_ID {self.settings.server_id}")
        me = guild.me
        if me is None:
            raise ConfigError("Could not resolve OZY Admin's guild member")

        configured_channels = {
            "WELCOME_CHANNEL_ID": self.settings.welcome_channel_id,
            "ANNOUNCEMENT_CHANNEL_ID": self.settings.announcement_channel_id,
            "SCHEDULE_CHANNEL_ID": self.settings.schedule_channel_id,
            "CALENDAR_CHANNEL_ID": self.settings.calendar_channel_id,
            "TODAY_CHANNEL_ID": self.settings.today_channel_id,
            "AWAY_CHANNEL_ID": self.settings.away_channel_id,
            "AUDIT_CHANNEL_ID": self.settings.audit_channel_id,
        }
        for label, channel_id in configured_channels.items():
            if channel_id is None:
                continue
            channel = guild.get_channel(channel_id)
            if not isinstance(channel, discord.TextChannel):
                raise ConfigError(f"{label}={channel_id} is not a text channel in this guild")
            perms = channel.permissions_for(me)
            if not (perms.view_channel and perms.send_messages):
                raise ConfigError(f"OZY Admin cannot view/send in #{channel.name} ({label})")
            if label in {"CALENDAR_CHANNEL_ID", "TODAY_CHANNEL_ID"} and not perms.read_message_history:
                raise ConfigError(
                    f"OZY Admin needs Read Message History in #{channel.name} ({label}) "
                    "to recover/update canonical calendar messages after restarts"
                )

        role_ids = set(self.settings.rank_role_map.values())
        if self.settings.away_role_id:
            role_ids.add(self.settings.away_role_id)
        if self.settings.announcement_ping_role_id:
            role_ids.add(self.settings.announcement_ping_role_id)
        role_ids.update(self.settings.leadership_role_ids)

        for role_id in role_ids:
            role = guild.get_role(role_id)
            if role is None:
                raise ConfigError(f"Configured role ID {role_id} does not exist in this guild")

        managed_role_ids = set(self.settings.rank_role_map.values())
        if self.settings.away_role_id:
            managed_role_ids.add(self.settings.away_role_id)
        for role_id in managed_role_ids:
            role = guild.get_role(role_id)
            if role and role >= me.top_role:
                raise ConfigError(
                    f"Managed role {role.name} ({role.id}) must be below the OZY Admin bot role"
                )

        if managed_role_ids and not me.guild_permissions.manage_roles:
            raise ConfigError("OZY Admin needs Manage Roles for configured rank/away role automation")
        if self.settings.auto_sync_nickname and not me.guild_permissions.manage_nicknames:
            raise ConfigError("AUTO_SYNC_NICKNAME=true requires Manage Nicknames")

        if self.settings.announcement_ping_role_id:
            ping_role = guild.get_role(self.settings.announcement_ping_role_id)
            if ping_role and not ping_role.mentionable and not me.guild_permissions.mention_everyone:
                log.warning(
                    "Announcement ping role %s is not mentionable and OZY Admin lacks Mention Everyone; "
                    "ping:true may render the role without notifying members",
                    ping_role.name,
                )

        if not self.settings.rank_role_map:
            log.warning("RANK_ROLE_MAP is empty; roster linking will work but rank roles will not be changed")

        log.info("Validated OZY Admin configuration for guild %s", guild.name)

    # ------------------------------------------------------------------
    # Permissions / utility helpers
    # ------------------------------------------------------------------
    def _is_leadership(self, member: discord.Member | None) -> bool:
        if member is None:
            return False
        if member.guild_permissions.manage_guild:
            return True
        return any(role.id in self.settings.leadership_role_ids for role in member.roles)

    async def _require_leadership(self, interaction: discord.Interaction) -> bool:
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        if self._is_leadership(member):
            return True
        if interaction.response.is_done():
            await interaction.followup.send("Leadership only.", ephemeral=True)
        else:
            await interaction.response.send_message("Leadership only.", ephemeral=True)
        return False

    async def _audit(self, action: str, actor: str, details: str) -> None:
        if not self.settings.audit_channel_id:
            return
        channel = self.get_channel(self.settings.audit_channel_id)
        if not isinstance(channel, discord.TextChannel):
            return
        embed = discord.Embed(title=action, description=truncate(details, 3500), color=0x5865F2)
        embed.add_field(name="Actor", value=truncate(actor, 100), inline=False)
        embed.timestamp = datetime.now(timezone.utc)
        try:
            await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())
        except discord.HTTPException as exc:
            log.warning("Audit log send failed: %s", exc)

    async def _resolve_member_game_name(self, member: discord.Member) -> str | None:
        assert self.data is not None
        linked = self.state.get_link(member.id)
        if linked:
            canonical = await self.data.exact_roster_name(linked)
            if canonical:
                if canonical != linked:
                    self.state.set_link(member.id, canonical, "canonicalized")
                return canonical

        # A Discord nickname is not proof of Total Battle identity. Exact-name
        # auto-linking is therefore disabled by default and must be explicitly
        # opted into for trusted/small servers.
        if self.settings.trust_exact_display_name:
            exact = await self.data.exact_roster_name(member.display_name)
            if exact:
                self.state.set_link(member.id, exact, "trusted-exact-display-name")
                return exact
        return None

    async def _sync_rank_role(self, member: discord.Member, game_name: str) -> str:
        assert self.data is not None
        info = await self.data.member_info(game_name)
        if not info:
            return "roster member not found"

        rank = str(info.get("rank", "")).strip()
        if not rank:
            return "roster rank is blank"
        target_role_id = self.settings.rank_role_map.get(rank.casefold())
        if target_role_id is None:
            return f"no Discord role configured for roster rank {rank}"

        guild = member.guild
        target_role = guild.get_role(target_role_id)
        if target_role is None:
            return f"configured role {target_role_id} no longer exists"
        me = guild.me
        if me is None or target_role >= me.top_role:
            return f"cannot manage role {target_role.name}; check role hierarchy"

        managed_ids = set(self.settings.rank_role_map.values())
        remove_roles = [r for r in member.roles if r.id in managed_ids and r.id != target_role_id]
        try:
            if remove_roles:
                await member.remove_roles(*remove_roles, reason=f"OZY roster rank sync: {rank}")
            if target_role not in member.roles:
                await member.add_roles(target_role, reason=f"OZY roster rank sync: {rank}")
        except discord.HTTPException as exc:
            return f"Discord role update failed: {exc}"

        if self.settings.auto_sync_nickname and member.display_name != game_name:
            try:
                if me and member.top_role < me.top_role:
                    await member.edit(nick=game_name, reason="OZY roster name sync")
            except discord.HTTPException as exc:
                log.warning("Nickname sync failed for %s: %s", member.id, exc)

        return f"{rank} -> {target_role.name}"

    # ------------------------------------------------------------------
    # Welcome / roster verification
    # ------------------------------------------------------------------
    async def _process_new_member(self, member: discord.Member) -> None:
        if member.guild.id != self.settings.server_id or member.bot:
            return
        if self.state.was_welcomed(member.id):
            return
        assert self.data is not None

        matched_name: str | None = None
        suggestions: list[str] = []
        role_result: str | None = None
        try:
            matched_name = await self.data.exact_roster_name(member.display_name)
            if matched_name:
                if self.settings.trust_exact_display_name:
                    self.state.set_link(member.id, matched_name, "trusted-join-exact-display-name")
                    role_result = await self._sync_rank_role(member, matched_name)
                else:
                    self.state.set_verification_request(member.id, matched_name, "join-exact-display-name")
                    role_result = "pending leadership verification"
            else:
                matches = await self.data.roster_suggestions(member.display_name, limit=3)
                suggestions = [m.name for m in matches if m.score >= self.settings.roster_match_threshold]
        except DataUnavailable as exc:
            log.warning("Roster unavailable during member join: %s", exc)

        if self.settings.welcome_channel_id:
            channel = self.get_channel(self.settings.welcome_channel_id)
            if isinstance(channel, discord.TextChannel):
                embed = discord.Embed(
                    title="Welcome to OZY",
                    description=f"Welcome {member.mention}.",
                    color=0xF59E0B,
                )
                if matched_name:
                    if self.settings.trust_exact_display_name:
                        match_text = f"Matched your Discord name to **{matched_name}**.\nRole sync: {role_result}"
                    else:
                        match_text = (
                            f"Your Discord name matches roster player **{matched_name}**, but names are not treated as proof of identity. "
                            "The link is pending leadership confirmation before any rank role is assigned."
                        )
                    embed.add_field(name="Roster match", value=match_text, inline=False)
                else:
                    text = (
                        "I could not match your Discord server name exactly to the current OZY game roster. "
                        "Use `/verify` with your exact Total Battle name so I can link you and set the correct roster role."
                    )
                    if suggestions:
                        text += "\n\nPossible match: " + ", ".join(f"**{name}**" for name in suggestions)
                    embed.add_field(name="Game name check", value=text, inline=False)
                embed.add_field(
                    name="Useful commands",
                    value="`/chests` - your chest status\n`/chats` - copy TB clan chat names\n`/away` - register an absence",
                    inline=False,
                )
                await channel.send(
                    content=member.mention,
                    embed=embed,
                    allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
                )

        self.state.mark_welcomed(member.id)
        await self._audit(
            "Member joined",
            str(member),
            f"Discord ID: {member.id}\nRoster match: {matched_name or 'none'}\nSuggestions: {', '.join(suggestions) or 'none'}",
        )

    async def on_member_join(self, member: discord.Member) -> None:
        if member.guild.id != self.settings.server_id or member.bot:
            return
        # If Membership Screening is enabled, wait until the member is no longer
        # pending so the welcome/role workflow does not race Discord onboarding.
        if member.pending:
            log.info("Member %s joined pending screening; welcome deferred", member.id)
            return
        await self._process_new_member(member)

    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        if after.guild.id != self.settings.server_id or after.bot:
            return

        if before.pending and not after.pending:
            await self._process_new_member(after)

        if before.display_name == after.display_name:
            return
        if self.state.get_link(after.id):
            return
        assert self.data is not None
        try:
            exact = await self.data.exact_roster_name(after.display_name)
        except DataUnavailable:
            return
        if exact:
            if self.settings.trust_exact_display_name:
                self.state.set_link(after.id, exact, "trusted-nickname-exact-match")
                result = await self._sync_rank_role(after, exact)
                action = "Roster auto-link"
                details = f"Matched changed Discord name to {exact}; role sync: {result}"
            else:
                self.state.set_verification_request(after.id, exact, "nickname-exact-match")
                action = "Roster verification requested"
                details = f"Changed Discord name matches {exact}; awaiting leadership approval"
            await self._audit(action, str(after), details)

    # ------------------------------------------------------------------
    # Announcements
    # ------------------------------------------------------------------
    async def publish_announcement(
        self,
        interaction: discord.Interaction,
        *,
        title: str,
        body: str,
        tb_copy: str,
        ping: bool,
    ) -> None:
        if not await self._require_leadership(interaction):
            return
        if not self.settings.announcement_channel_id:
            await interaction.response.send_message("ANNOUNCEMENT_CHANNEL_ID is not configured.", ephemeral=True)
            return
        channel = self.get_channel(self.settings.announcement_channel_id)
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message("Configured announcement channel is unavailable.", ephemeral=True)
            return

        embed = discord.Embed(title=title, description=body, color=0xF59E0B)
        embed.set_footer(text=f"Posted by {interaction.user.display_name}")
        embed.timestamp = datetime.now(timezone.utc)

        content = None
        allowed = discord.AllowedMentions.none()
        if ping and self.settings.announcement_ping_role_id:
            role = interaction.guild.get_role(self.settings.announcement_ping_role_id) if interaction.guild else None
            if role:
                content = role.mention
                allowed = discord.AllowedMentions(everyone=False, users=False, roles=[role])

        sent = await channel.send(content=content, embed=embed, allowed_mentions=allowed)
        if tb_copy.strip():
            await channel.send(
                "### Total Battle copy\n" + safe_code_block(tb_copy.strip()),
                allowed_mentions=discord.AllowedMentions.none(),
            )

        await self._audit(
            "Announcement posted",
            str(interaction.user),
            f"Channel: #{channel.name}\nMessage ID: {sent.id}\nTitle: {title}\nPing: {bool(content)}",
        )
        await interaction.response.send_message(f"Announcement posted in {channel.mention}.", ephemeral=True)

    # ------------------------------------------------------------------
    # Schedule
    # ------------------------------------------------------------------
    async def _post_schedule(self, target_date, *, force: bool, actor: str) -> bool:
        if not self.settings.schedule_channel_id:
            return False
        channel = self.get_channel(self.settings.schedule_channel_id)
        if not isinstance(channel, discord.TextChannel):
            return False
        if not force and self.state.schedule_posted(target_date.isoformat()):
            return False
        assert self.data is not None
        try:
            items = await self.data.schedule_for_date(target_date)
        except DataUnavailable as exc:
            log.warning("Schedule unavailable: %s", exc)
            return False
        if not items:
            log.info("No schedule items for %s; automatic post skipped", target_date)
            return False

        message = await channel.send(
            format_schedule(target_date, items),
            allowed_mentions=discord.AllowedMentions.none(),
        )
        self.state.mark_schedule_posted(target_date.isoformat(), channel.id, message.id)
        await self._audit(
            "Daily schedule posted",
            actor,
            f"Date: {target_date.isoformat()}\nChannel: #{channel.name}\nItems: {len(items)}",
        )
        return True

    async def _daily_schedule_loop(self) -> None:
        await self.wait_until_ready()
        if not self.settings.daily_schedule_enabled:
            return

        hour, minute = [int(x) for x in self.settings.daily_schedule_time.split(":", 1)]
        tz = self.settings.timezone

        while not self.is_closed():
            now = datetime.now(tz)
            today_target = datetime.combine(now.date(), dt_time(hour, minute), tzinfo=tz)

            # If the service starts/restarts after today's posting time, catch up once.
            if now >= today_target and not self.state.schedule_posted(now.date().isoformat()):
                try:
                    await self._post_schedule(now.date(), force=False, actor="automatic scheduler")
                except Exception:
                    log.exception("Catch-up daily schedule post failed")

            now = datetime.now(tz)
            next_target = datetime.combine(now.date(), dt_time(hour, minute), tzinfo=tz)
            if next_target <= now:
                next_target += timedelta(days=1)
            seconds = max(1.0, (next_target - now).total_seconds())
            try:
                await asyncio.sleep(seconds)
            except asyncio.CancelledError:
                raise

            try:
                await self._post_schedule(next_target.date(), force=False, actor="automatic scheduler")
            except Exception:
                log.exception("Automatic daily schedule post failed")

    # ------------------------------------------------------------------
    # Voltron tournament calendar
    # ------------------------------------------------------------------
    def _state_message_ids(self, key: str) -> list[int]:
        raw = self.state.get_value(key)
        if not raw:
            return []
        try:
            values = json.loads(raw)
        except json.JSONDecodeError:
            return []
        if not isinstance(values, list):
            return []
        return [int(x) for x in values if isinstance(x, int) or (isinstance(x, str) and x.isdigit())]

    def _save_message_ids(self, key: str, message_ids: list[int]) -> None:
        self.state.set_value(key, json.dumps(message_ids, separators=(",", ":")))

    async def _recover_bot_messages(self, channel: discord.TextChannel, prefix: str, limit: int = 75) -> list[discord.Message]:
        if self.user is None:
            return []
        found: list[discord.Message] = []
        try:
            async for message in channel.history(limit=limit, oldest_first=False):
                if message.author.id != self.user.id:
                    continue
                if message.content.startswith(prefix):
                    found.append(message)
        except discord.HTTPException as exc:
            log.warning("Could not scan #%s for existing calendar messages: %s", channel.name, exc)
            return []
        found.sort(key=lambda m: m.created_at)
        return found

    async def _upsert_message_series(
        self,
        channel: discord.TextChannel,
        *,
        state_key: str,
        recovery_prefix: str,
        chunks: list[str],
    ) -> list[int]:
        async with self._message_series_lock:
            existing: list[discord.Message] = []
            for message_id in self._state_message_ids(state_key):
                try:
                    message = await channel.fetch_message(message_id)
                except (discord.NotFound, discord.Forbidden, discord.HTTPException):
                    continue
                if self.user and message.author.id == self.user.id:
                    existing.append(message)

            if not existing:
                existing = await self._recover_bot_messages(channel, recovery_prefix)

            ids: list[int] = []
            allowed = discord.AllowedMentions.none()
            for index, chunk in enumerate(chunks):
                if index < len(existing):
                    message = existing[index]
                    if message.content != chunk:
                        await message.edit(content=chunk, allowed_mentions=allowed)
                else:
                    message = await channel.send(chunk, allowed_mentions=allowed)
                ids.append(message.id)

            # Remove obsolete extra chunks, but only bot-authored messages recovered for this series.
            for message in existing[len(chunks):]:
                try:
                    await message.delete()
                except discord.HTTPException:
                    log.warning("Could not delete obsolete calendar chunk %s", message.id)

            self._save_message_ids(state_key, ids)
            return ids

    async def _refresh_voltron(self, *, force: bool, actor: str) -> tuple[bool, str]:
        if not self.settings.voltron_calendar_enabled:
            return False, "Voltron calendar integration is disabled"
        if self.voltron is None:
            return False, "Voltron calendar client is unavailable"

        try:
            result = await self.voltron.refresh(force=force)
        except VoltronCalendarError as exc:
            await self._audit("Voltron calendar refresh failed", actor, str(exc))
            return False, str(exc)

        snapshot = result.snapshot
        today = datetime.now(timezone.utc).date()

        rendered_start = self.state.get_value("voltron_calendar_start_date")
        window_rolled = rendered_start != today.isoformat()
        if self.settings.calendar_channel_id and (result.changed or force or window_rolled):
            channel = self.get_channel(self.settings.calendar_channel_id)
            if isinstance(channel, discord.TextChannel):
                chunks = build_calendar_chunks(
                    snapshot,
                    start_date=today,
                    days=self.settings.voltron_calendar_days,
                    timezone_info=timezone.utc,
                )
                await self._upsert_message_series(
                    channel,
                    state_key="voltron_calendar_message_ids",
                    recovery_prefix="```\nOZY Tournament Calendar - Next 30 Days",
                    chunks=chunks,
                )
                self.state.set_value("voltron_calendar_start_date", today.isoformat())

        # If today's post already exists, silently keep it current after source changes.
        if self.settings.today_channel_id and result.changed:
            today_key = f"voltron_today_message_ids:{today.isoformat()}"
            if self._state_message_ids(today_key):
                await self._post_voltron_today(today, force=True, actor="calendar refresh")

        if result.changed:
            await self._audit(
                "Voltron calendar updated",
                actor,
                f"Actions: {len(snapshot.actions)}\nMini tournaments: {len(snapshot.mini_tournaments)}\n"
                f"Hash: {snapshot.semantic_hash[:12]}",
            )
        return result.changed, "ok"

    async def _post_voltron_today(self, target_date, *, force: bool, actor: str) -> bool:
        if not self.settings.today_channel_id or self.voltron is None:
            return False
        channel = self.get_channel(self.settings.today_channel_id)
        if not isinstance(channel, discord.TextChannel):
            return False

        try:
            await self.voltron.refresh(force=self.voltron.snapshot is None)
        except VoltronCalendarError as exc:
            log.warning("Today's Voltron post skipped: %s", exc)
            return False
        snapshot = self.voltron.snapshot
        if snapshot is None:
            return False

        state_key = f"voltron_today_message_ids:{target_date.isoformat()}"
        if not force and self._state_message_ids(state_key):
            return False

        title_prefix = f"```\nOZY Today - {target_date.strftime('%A %d %B %Y')}"
        existing = self._state_message_ids(state_key)
        if not existing:
            recovered = await self._recover_bot_messages(channel, title_prefix)
            if recovered:
                self._save_message_ids(state_key, [m.id for m in recovered])
                if not force:
                    return False

        chunks = build_today_chunks(snapshot, target_date=target_date, timezone_info=timezone.utc)
        await self._upsert_message_series(
            channel,
            state_key=state_key,
            recovery_prefix=title_prefix,
            chunks=chunks,
        )
        await self._audit(
            "Voltron today posted" if not existing else "Voltron today updated",
            actor,
            f"Date: {target_date.isoformat()}\nChannel: #{channel.name}",
        )
        return True

    async def _voltron_refresh_loop(self) -> None:
        await self.wait_until_ready()
        while not self.is_closed():
            try:
                await self._refresh_voltron(force=self.voltron.snapshot is None if self.voltron else True, actor="automatic calendar refresh")
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Automatic Voltron calendar refresh failed")
            await asyncio.sleep(self.settings.voltron_refresh_minutes * 60)

    async def _voltron_today_loop(self) -> None:
        await self.wait_until_ready()
        hour, minute = [int(x) for x in self.settings.voltron_today_time.split(":", 1)]
        tz = self.settings.timezone

        while not self.is_closed():
            now = datetime.now(tz)
            target = datetime.combine(now.date(), dt_time(hour, minute), tzinfo=tz)

            # Catch up after a restart without duplicating the day's canonical post.
            if now >= target:
                try:
                    await self._post_voltron_today(datetime.now(timezone.utc).date(), force=False, actor="automatic today scheduler")
                except Exception:
                    log.exception("Catch-up Voltron today post failed")

            now = datetime.now(tz)
            next_target = datetime.combine(now.date(), dt_time(hour, minute), tzinfo=tz)
            if next_target <= now:
                next_target += timedelta(days=1)
            try:
                await asyncio.sleep(max(1.0, (next_target - now).total_seconds()))
            except asyncio.CancelledError:
                raise

            try:
                await self._post_voltron_today(datetime.now(timezone.utc).date(), force=False, actor="automatic today scheduler")
            except Exception:
                log.exception("Automatic Voltron today post failed")

    # ------------------------------------------------------------------
    # Away expiry
    # ------------------------------------------------------------------
    async def _away_expiry_loop(self) -> None:
        await self.wait_until_ready()
        while not self.is_closed():
            try:
                expired = self.state.expired_away(datetime.now(timezone.utc))
                guild = self.get_guild(self.settings.server_id)
                if guild:
                    away_role = guild.get_role(self.settings.away_role_id) if self.settings.away_role_id else None
                    for record in expired:
                        member = guild.get_member(record.discord_user_id)
                        if member and away_role and away_role in member.roles:
                            try:
                                await member.remove_roles(away_role, reason="OZY away period expired")
                            except discord.HTTPException:
                                log.exception("Could not remove expired Away role from %s", member.id)
                        self.state.clear_away(record.discord_user_id)
                        if member and self.settings.away_channel_id:
                            channel = guild.get_channel(self.settings.away_channel_id)
                            if isinstance(channel, discord.TextChannel):
                                await channel.send(
                                    f"{member.mention} is no longer marked away.",
                                    allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
                                )
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Away expiry sweep failed")
            await asyncio.sleep(1800)

    # ------------------------------------------------------------------
    # Slash commands
    # ------------------------------------------------------------------
    def _register_commands(self) -> None:
        @self.tree.command(name="chats", description="Show the OZY Total Battle chat-name directory")
        @app_commands.describe(public="Post publicly instead of only showing it to you")
        async def chats(interaction: discord.Interaction, public: bool = False) -> None:
            assert self.data is not None
            try:
                items = await self.data.chats()
            except DataUnavailable as exc:
                await interaction.response.send_message(f"Chat directory unavailable: {exc}", ephemeral=True)
                return
            if public and not self._is_leadership(interaction.user if isinstance(interaction.user, discord.Member) else None):
                await interaction.response.send_message("Only leadership can post the full directory publicly.", ephemeral=True)
                return
            text = format_chat_directory(items)
            if len(text) > 2000:
                await interaction.response.send_message(
                    "The chat directory is too large for one Discord message. Split data/chats.json into fewer entries.",
                    ephemeral=True,
                )
                return
            await interaction.response.send_message(
                text,
                ephemeral=not public,
                allowed_mentions=discord.AllowedMentions.none(),
            )

        @self.tree.command(name="chat", description="Get one Total Battle clan chat name as a copyable block")
        @app_commands.describe(name="Chat key or label")
        async def chat(interaction: discord.Interaction, name: str) -> None:
            assert self.data is not None
            try:
                items = await self.data.chats()
            except DataUnavailable as exc:
                await interaction.response.send_message(f"Chat directory unavailable: {exc}", ephemeral=True)
                return
            query = name.casefold().strip()
            item = next(
                (
                    x for x in items
                    if x["key"].casefold() == query or x["label"].casefold() == query
                ),
                None,
            )
            if item is None:
                await interaction.response.send_message("Unknown chat. Use `/chats` to see the directory.", ephemeral=True)
                return
            await interaction.response.send_message(
                f"### {item['label']}\n{safe_code_block(item['name'])}",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )

        @chat.autocomplete("name")
        async def chat_autocomplete(interaction: discord.Interaction, current: str):
            assert self.data is not None
            try:
                items = await self.data.chats()
            except DataUnavailable:
                return []
            q = current.casefold().strip()
            matches = [x for x in items if not q or q in x["label"].casefold() or q in x["key"].casefold()]
            return [app_commands.Choice(name=x["label"], value=x["key"]) for x in matches[:25]]

        @self.tree.command(name="verify", description="Link your Discord account to your exact Total Battle roster name")
        @app_commands.describe(game_name="Your exact current Total Battle name")
        async def verify(interaction: discord.Interaction, game_name: str) -> None:
            if not isinstance(interaction.user, discord.Member):
                await interaction.response.send_message("This command only works inside the OZY server.", ephemeral=True)
                return
            assert self.data is not None
            try:
                canonical = await self.data.exact_roster_name(game_name)
                if canonical is None:
                    suggestions = await self.data.roster_suggestions(game_name, 3)
                    text = "That exact name is not in the active roster."
                    if suggestions:
                        text += " Did you mean: " + ", ".join(f"**{m.name}**" for m in suggestions)
                    await interaction.response.send_message(text, ephemeral=True)
                    return
                existing = self.state.get_link(interaction.user.id)
                if existing and existing.casefold() == canonical.casefold():
                    role_result = await self._sync_rank_role(interaction.user, canonical)
                    await interaction.response.send_message(
                        f"You are already linked to **{canonical}**. Role sync: {role_result}.",
                        ephemeral=True,
                    )
                    return

                if self.settings.trust_exact_display_name and interaction.user.display_name.casefold() == canonical.casefold():
                    self.state.set_link(interaction.user.id, canonical, "trusted-self-verify")
                    self.state.clear_verification_request(interaction.user.id)
                    role_result = await self._sync_rank_role(interaction.user, canonical)
                    outcome = f"auto-approved; role sync: {role_result}"
                    response = f"Linked to **{canonical}**. Role sync: {role_result}."
                else:
                    self.state.set_verification_request(interaction.user.id, canonical, "self-verify")
                    outcome = "pending leadership approval"
                    response = (
                        f"Verification request recorded for **{canonical}**. "
                        "A leadership member must approve the Discord-to-game link before rank roles are changed."
                    )
            except DataUnavailable as exc:
                await interaction.response.send_message(f"Roster unavailable: {exc}", ephemeral=True)
                return

            await self._audit(
                "Roster verification request",
                str(interaction.user),
                f"Game name: {canonical}\nOutcome: {outcome}",
            )
            await interaction.response.send_message(response, ephemeral=True)

        @verify.autocomplete("game_name")
        async def verify_autocomplete(interaction: discord.Interaction, current: str):
            assert self.data is not None
            try:
                roster = await self.data.roster()
            except DataUnavailable:
                return []
            q = current.casefold().strip()
            if not q:
                names = list(roster)[:25]
            else:
                contains = [name for name in roster if q in name.casefold()]
                if len(contains) < 25:
                    suggestions = await self.data.roster_suggestions(current, 25)
                    ordered = contains + [m.name for m in suggestions if m.name not in contains]
                else:
                    ordered = contains
                names = ordered[:25]
            return [app_commands.Choice(name=name[:100], value=name) for name in names]

        @self.tree.command(name="member-link", description="Leadership: link a Discord member to a Total Battle roster name")
        @app_commands.describe(member="Discord member", game_name="Exact Total Battle roster name")
        async def member_link(interaction: discord.Interaction, member: discord.Member, game_name: str) -> None:
            if not await self._require_leadership(interaction):
                return
            assert self.data is not None
            try:
                canonical = await self.data.exact_roster_name(game_name)
                if canonical is None:
                    await interaction.response.send_message("That exact name is not in the active roster.", ephemeral=True)
                    return
                self.state.set_link(member.id, canonical, f"leadership:{interaction.user.id}")
                self.state.clear_verification_request(member.id)
                role_result = await self._sync_rank_role(member, canonical)
            except DataUnavailable as exc:
                await interaction.response.send_message(f"Roster unavailable: {exc}", ephemeral=True)
                return
            await self._audit(
                "Member link changed",
                str(interaction.user),
                f"Discord member: {member} ({member.id})\nGame name: {canonical}\nRole sync: {role_result}",
            )
            await interaction.response.send_message(
                f"Linked {member.mention} to **{canonical}**. Role sync: {role_result}.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )

        @self.tree.command(name="pending-verifications", description="Leadership: list pending Discord-to-roster verification requests")
        async def pending_verifications(interaction: discord.Interaction) -> None:
            if not await self._require_leadership(interaction):
                return
            if interaction.guild is None:
                await interaction.response.send_message("Guild unavailable.", ephemeral=True)
                return
            pending = self.state.all_verification_requests()
            if not pending:
                await interaction.response.send_message("No pending roster verification requests.", ephemeral=True)
                return
            lines: list[str] = []
            for user_id, game_name in list(pending.items())[:30]:
                member = interaction.guild.get_member(user_id)
                discord_name = member.display_name if member else f"Discord ID {user_id}"
                lines.append(f"- **{discord_name}** -> `{game_name}`")
            text = "## Pending roster verifications\n" + "\n".join(lines)
            if len(pending) > 30:
                text += f"\n- ...and {len(pending) - 30} more"
            text += "\n\nApprove with `/member-link`."
            await interaction.response.send_message(truncate(text, 1900), ephemeral=True)

        @member_link.autocomplete("game_name")
        async def member_link_autocomplete(interaction: discord.Interaction, current: str):
            assert self.data is not None
            try:
                roster = await self.data.roster()
            except DataUnavailable:
                return []
            q = current.casefold().strip()
            candidates = [name for name in roster if not q or q in name.casefold()]
            if q and len(candidates) < 25:
                suggestions = await self.data.roster_suggestions(current, 25)
                candidates += [m.name for m in suggestions if m.name not in candidates]
            return [app_commands.Choice(name=name[:100], value=name) for name in candidates[:25]]

        @self.tree.command(name="member", description="Show roster/link status for yourself or a member")
        @app_commands.describe(member="Optional Discord member; leadership can inspect others")
        async def member_info(interaction: discord.Interaction, member: Optional[discord.Member] = None) -> None:
            requester = interaction.user if isinstance(interaction.user, discord.Member) else None
            target = member or requester
            if target is None:
                await interaction.response.send_message("Guild member unavailable.", ephemeral=True)
                return
            if target.id != interaction.user.id and not self._is_leadership(requester):
                await interaction.response.send_message("Leadership only for other members.", ephemeral=True)
                return
            assert self.data is not None
            try:
                game_name = await self._resolve_member_game_name(target)
                if not game_name:
                    pending = self.state.get_verification_request(target.id)
                    suggestions = await self.data.roster_suggestions(target.display_name, 3)
                    text = "Not yet approved/linked to the roster."
                    if pending:
                        text += f" Pending verification: **{pending}**."
                    elif suggestions:
                        text += " Possible matches: " + ", ".join(m.name for m in suggestions)
                    await interaction.response.send_message(text, ephemeral=True)
                    return
                info = await self.data.member_info(game_name)
            except DataUnavailable as exc:
                await interaction.response.send_message(f"Roster unavailable: {exc}", ephemeral=True)
                return
            if not info:
                await interaction.response.send_message("Linked roster member is no longer active.", ephemeral=True)
                return

            embed = discord.Embed(title=game_name, color=0xF59E0B)
            embed.add_field(name="Discord", value=target.mention, inline=False)
            for label, key in (("Rank", "rank"), ("Hero level", "level"), ("Might", "might"), ("Location", "location"), ("Last seen", "last_seen")):
                value = info.get(key)
                if value not in (None, ""):
                    embed.add_field(name=label, value=str(value), inline=True)
            away = self.state.get_away(target.id)
            if away:
                embed.add_field(
                    name="Away",
                    value=f"Until {discord.utils.format_dt(away.until_utc, style='f')}\n{away.reason}",
                    inline=False,
                )
            await interaction.response.send_message(embed=embed, ephemeral=True)

        @self.tree.command(name="chests", description="Check your current OZY chest points and chest count")
        @app_commands.describe(player="Leadership only: optional roster player to inspect")
        async def chests(interaction: discord.Interaction, player: Optional[str] = None) -> None:
            requester = interaction.user if isinstance(interaction.user, discord.Member) else None
            if requester is None:
                await interaction.response.send_message("This command only works inside the OZY server.", ephemeral=True)
                return
            assert self.data is not None
            try:
                own_game_name = await self._resolve_member_game_name(requester)
                if player:
                    canonical = await self.data.exact_roster_name(player)
                    if canonical is None:
                        await interaction.response.send_message("That player is not in the active roster.", ephemeral=True)
                        return
                    if canonical != own_game_name and not self._is_leadership(requester):
                        await interaction.response.send_message("You can only query your own chest status.", ephemeral=True)
                        return
                    game_name = canonical
                else:
                    game_name = own_game_name
                if not game_name:
                    await interaction.response.send_message(
                        "Your Discord account is not approved/linked to a roster player yet. Run `/verify` with your exact Total Battle name.",
                        ephemeral=True,
                    )
                    return
                stats = await self.data.chest_stats(game_name)
            except DataUnavailable as exc:
                await interaction.response.send_message(f"Chest data unavailable: {exc}", ephemeral=True)
                return

            if stats is None:
                await interaction.response.send_message(
                    f"No chest record was found for **{game_name}** in the current/selected week.",
                    ephemeral=True,
                )
                return

            target_text = f"{stats.points:,} / {stats.target:,}" if stats.target > 0 else f"{stats.points:,}"
            missing = max(0, stats.target - stats.points) if stats.target else 0
            status = "Target met" if stats.met_target else (f"{missing:,} points remaining" if stats.target else "No target configured")
            embed = discord.Embed(title=f"Chest status - {stats.player}", color=0x10B981 if stats.met_target else 0xF59E0B)
            embed.description = stats.week_label
            embed.add_field(name="Points", value=target_text, inline=True)
            embed.add_field(name="Chests", value=f"{stats.chests:,}", inline=True)
            embed.add_field(name="Status", value=status, inline=False)
            if stats.breakdown:
                top = sorted(stats.breakdown.items(), key=lambda kv: (-kv[1], kv[0]))[:8]
                embed.add_field(
                    name="Top chest types",
                    value="\n".join(f"{name}: **{count}**" for name, count in top),
                    inline=False,
                )
            await interaction.response.send_message(embed=embed, ephemeral=True)

        @chests.autocomplete("player")
        async def chests_autocomplete(interaction: discord.Interaction, current: str):
            if not isinstance(interaction.user, discord.Member) or not self._is_leadership(interaction.user):
                return []
            assert self.data is not None
            try:
                roster = await self.data.roster()
            except DataUnavailable:
                return []
            q = current.casefold().strip()
            names = [name for name in roster if not q or q in name.casefold()][:25]
            return [app_commands.Choice(name=name[:100], value=name) for name in names]

        @self.tree.command(name="away", description="Mark yourself away from clan activity")
        @app_commands.describe(days="Number of days away (1-90)", reason="Short reason")
        async def away(interaction: discord.Interaction, days: int, reason: str = "Away") -> None:
            member = interaction.user if isinstance(interaction.user, discord.Member) else None
            if member is None:
                await interaction.response.send_message("This command only works inside the OZY server.", ephemeral=True)
                return
            if not (1 <= days <= 90):
                await interaction.response.send_message("Days must be between 1 and 90.", ephemeral=True)
                return
            reason = reason.strip()[:200] or "Away"
            game_name = None
            try:
                game_name = await self._resolve_member_game_name(member)
            except DataUnavailable:
                pass

            local_now = datetime.now(self.settings.timezone)
            until_local = datetime.combine(
                local_now.date() + timedelta(days=days),
                dt_time(23, 59, 59),
                tzinfo=self.settings.timezone,
            )
            until_utc = until_local.astimezone(timezone.utc)
            self.state.set_away(member.id, game_name, until_utc, reason)

            role_result = "Away role not configured"
            if self.settings.away_role_id:
                role = member.guild.get_role(self.settings.away_role_id)
                if role:
                    try:
                        if role not in member.roles:
                            await member.add_roles(role, reason=f"Away until {until_local.date().isoformat()}")
                        role_result = f"{role.name} assigned"
                    except discord.HTTPException as exc:
                        role_result = f"role update failed: {exc}"

            if self.settings.away_channel_id:
                channel = member.guild.get_channel(self.settings.away_channel_id)
                if isinstance(channel, discord.TextChannel):
                    embed = discord.Embed(title="Member Away", color=0x64748B)
                    embed.description = member.mention
                    embed.add_field(name="Game name", value=game_name or "Not linked", inline=True)
                    embed.add_field(name="Until", value=discord.utils.format_dt(until_utc, style="D"), inline=True)
                    embed.add_field(name="Reason", value=reason, inline=False)
                    await channel.send(
                        embed=embed,
                        allowed_mentions=discord.AllowedMentions(users=False, roles=False, everyone=False),
                    )

            await self._audit(
                "Away set",
                str(member),
                f"Game name: {game_name or 'unlinked'}\nUntil: {until_utc.isoformat()}\nReason: {reason}\n{role_result}",
            )
            await interaction.response.send_message(
                f"Marked away until {discord.utils.format_dt(until_utc, style='D')}. {role_result}.",
                ephemeral=True,
            )

        @self.tree.command(name="back", description="Clear your away status early")
        async def back(interaction: discord.Interaction) -> None:
            member = interaction.user if isinstance(interaction.user, discord.Member) else None
            if member is None:
                await interaction.response.send_message("This command only works inside the OZY server.", ephemeral=True)
                return
            record = self.state.get_away(member.id)
            self.state.clear_away(member.id)
            if self.settings.away_role_id:
                role = member.guild.get_role(self.settings.away_role_id)
                if role and role in member.roles:
                    try:
                        await member.remove_roles(role, reason="Member returned early")
                    except discord.HTTPException:
                        log.exception("Could not remove Away role from %s", member.id)
            await self._audit("Away cleared", str(member), f"Previous record: {record or 'none'}")
            await interaction.response.send_message("Away status cleared.", ephemeral=True)

        @self.tree.command(name="schedule", description="Show today's OZY clan schedule")
        @app_commands.describe(public="Post publicly instead of only showing it to you")
        async def schedule(interaction: discord.Interaction, public: bool = False) -> None:
            assert self.data is not None
            today = datetime.now(self.settings.timezone).date()
            try:
                items = await self.data.schedule_for_date(today)
            except DataUnavailable as exc:
                await interaction.response.send_message(f"Schedule unavailable: {exc}", ephemeral=True)
                return
            if public and not self._is_leadership(interaction.user if isinstance(interaction.user, discord.Member) else None):
                await interaction.response.send_message("Only leadership can post the schedule publicly.", ephemeral=True)
                return
            await interaction.response.send_message(
                format_schedule(today, items),
                ephemeral=not public,
                allowed_mentions=discord.AllowedMentions.none(),
            )

        @self.tree.command(name="schedule-post", description="Leadership: post today's schedule to the configured schedule channel")
        async def schedule_post(interaction: discord.Interaction) -> None:
            if not await self._require_leadership(interaction):
                return
            today = datetime.now(self.settings.timezone).date()
            posted = await self._post_schedule(today, force=True, actor=str(interaction.user))
            if posted:
                await interaction.response.send_message("Today's schedule was posted.", ephemeral=True)
            else:
                await interaction.response.send_message(
                    "Nothing was posted. Check SCHEDULE_CHANNEL_ID and today's schedule data.",
                    ephemeral=True,
                )

        @self.tree.command(name="calendar", description="Show the next 30 days of Voltron tournament starts")
        @app_commands.describe(public="Post publicly instead of only showing it to you")
        async def calendar(interaction: discord.Interaction, public: bool = False) -> None:
            if public and not self._is_leadership(interaction.user if isinstance(interaction.user, discord.Member) else None):
                await interaction.response.send_message("Only leadership can post the calendar publicly.", ephemeral=True)
                return
            if self.voltron is None:
                await interaction.response.send_message("Voltron calendar integration is unavailable.", ephemeral=True)
                return
            await interaction.response.defer(ephemeral=not public, thinking=True)
            try:
                if self.voltron.snapshot is None:
                    await self.voltron.refresh(force=True)
                snapshot = self.voltron.snapshot
                if snapshot is None:
                    raise VoltronCalendarError("No calendar snapshot is available")
                chunks = build_calendar_chunks(
                    snapshot,
                    start_date=datetime.now(timezone.utc).date(),
                    days=self.settings.voltron_calendar_days,
                    timezone_info=timezone.utc,
                )
                for chunk in chunks:
                    await interaction.followup.send(
                        chunk,
                        ephemeral=not public,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
            except VoltronCalendarError as exc:
                await interaction.followup.send(f"Calendar unavailable: {exc}", ephemeral=True)

        @self.tree.command(name="today", description="Show today's Voltron tournament activity")
        @app_commands.describe(public="Post publicly instead of only showing it to you")
        async def today(interaction: discord.Interaction, public: bool = False) -> None:
            if public and not self._is_leadership(interaction.user if isinstance(interaction.user, discord.Member) else None):
                await interaction.response.send_message("Only leadership can post today's schedule publicly.", ephemeral=True)
                return
            if self.voltron is None:
                await interaction.response.send_message("Voltron calendar integration is unavailable.", ephemeral=True)
                return
            await interaction.response.defer(ephemeral=not public, thinking=True)
            try:
                if self.voltron.snapshot is None:
                    await self.voltron.refresh(force=True)
                snapshot = self.voltron.snapshot
                if snapshot is None:
                    raise VoltronCalendarError("No calendar snapshot is available")
                target_date = datetime.now(timezone.utc).date()
                chunks = build_today_chunks(snapshot, target_date=target_date, timezone_info=timezone.utc)
                for chunk in chunks:
                    await interaction.followup.send(
                        chunk,
                        ephemeral=not public,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
            except VoltronCalendarError as exc:
                await interaction.followup.send(f"Today's calendar is unavailable: {exc}", ephemeral=True)

        @self.tree.command(name="time", description="Show today's OZY events in your chosen local timezone")
        @app_commands.describe(zone="Timezone to convert the current game-day schedule to")
        @app_commands.choices(zone=TIMEZONE_CHOICES)
        async def time_converter(interaction: discord.Interaction, zone: app_commands.Choice[str]) -> None:
            if self.voltron is None:
                await interaction.response.send_message("Voltron calendar integration is unavailable.", ephemeral=True)
                return
            await interaction.response.defer(ephemeral=True, thinking=True)
            try:
                if self.voltron.snapshot is None:
                    await self.voltron.refresh(force=True)
                snapshot = self.voltron.snapshot
                if snapshot is None:
                    raise VoltronCalendarError("No calendar snapshot is available")
                target_date = datetime.now(timezone.utc).date()
                chunks = build_today_local_chunks(
                    snapshot,
                    target_date=target_date,
                    timezone_info=ZoneInfo(zone.value),
                    timezone_label=zone.name,
                )
                for chunk in chunks:
                    await interaction.followup.send(
                        chunk,
                        ephemeral=True,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
            except Exception as exc:
                await interaction.followup.send(f"Time conversion unavailable: {exc}", ephemeral=True)

        @self.tree.command(name="calendar-refresh", description="Leadership: refresh Voltron and update the calendar channel")
        async def calendar_refresh(interaction: discord.Interaction) -> None:
            if not await self._require_leadership(interaction):
                return
            await interaction.response.defer(ephemeral=True, thinking=True)
            changed, status = await self._refresh_voltron(force=True, actor=str(interaction.user))
            if status != "ok":
                await interaction.followup.send(f"Refresh failed: {status}", ephemeral=True)
            else:
                await interaction.followup.send(
                    "Voltron calendar refreshed. " + ("Discord calendar updated." if changed else "No event changes detected."),
                    ephemeral=True,
                )

        @self.tree.command(name="calendar-status", description="Leadership: show Voltron calendar integration status")
        async def calendar_status(interaction: discord.Interaction) -> None:
            if not await self._require_leadership(interaction):
                return
            snapshot = self.voltron.snapshot if self.voltron else None
            last_success = self.voltron.last_success_utc if self.voltron else None
            last_error = self.voltron.last_error if self.voltron else "client unavailable"
            lines = [
                f"Enabled: **{self.settings.voltron_calendar_enabled}**",
                f"Realm: **{self.settings.voltron_realm}**",
                f"Refresh: **{self.settings.voltron_refresh_minutes} min**",
                f"Cached actions: **{len(snapshot.actions) if snapshot else 0}**",
                f"Cached mini tournaments: **{len(snapshot.mini_tournaments) if snapshot else 0}**",
                f"Last successful fetch: **{last_success.isoformat() if last_success else 'never'}**",
                f"Last error: **{last_error or 'none'}**",
            ]
            await interaction.response.send_message("\n".join(lines), ephemeral=True)

        @self.tree.command(name="announce", description="Leadership: open the OZY announcement popup")
        @app_commands.describe(ping="Ping the configured announcement notification role")
        async def announce(interaction: discord.Interaction, ping: bool = False) -> None:
            if not await self._require_leadership(interaction):
                return
            if not self.settings.announcement_channel_id:
                await interaction.response.send_message("ANNOUNCEMENT_CHANNEL_ID is not configured.", ephemeral=True)
                return
            await interaction.response.send_modal(AnnouncementModal(self, ping=ping))

        @self.tree.command(name="sync-roles", description="Leadership: preview or apply roster-based rank role synchronization")
        @app_commands.describe(apply="False = preview only; True = actually change roles")
        async def sync_roles(interaction: discord.Interaction, apply: bool = False) -> None:
            if not await self._require_leadership(interaction):
                return
            if interaction.guild is None:
                await interaction.response.send_message("Guild unavailable.", ephemeral=True)
                return
            assert self.data is not None
            await interaction.response.defer(ephemeral=True, thinking=True)
            try:
                roster = await self.data.roster()
            except DataUnavailable as exc:
                await interaction.followup.send(f"Roster unavailable: {exc}", ephemeral=True)
                return

            links = self.state.all_links()
            exact_lookup = {name.casefold(): name for name in roster}
            matched = 0
            changed = 0
            unresolved: list[str] = []
            results: list[str] = []

            for member in interaction.guild.members:
                if member.bot:
                    continue
                game_name = links.get(member.id)
                if game_name:
                    game_name = exact_lookup.get(game_name.casefold())
                if not game_name:
                    unresolved.append(member.display_name)
                    continue
                matched += 1

                info = roster[game_name]
                rank = str(info.get("rank", "")).strip()
                target_id = self.settings.rank_role_map.get(rank.casefold()) if rank else None
                target_role = interaction.guild.get_role(target_id) if target_id else None
                current_managed = [r for r in member.roles if r.id in set(self.settings.rank_role_map.values())]
                already_correct = bool(target_role and target_role in current_managed and len(current_managed) == 1)
                if not already_correct:
                    changed += 1
                    results.append(f"{member.display_name} -> {game_name} / {rank or '?'}")
                    if apply:
                        await self._sync_rank_role(member, game_name)

            pending_count = len(self.state.all_verification_requests())
            mode = "APPLIED" if apply else "PREVIEW"
            summary = (
                f"**{mode}**\nApproved roster links: **{matched}**\nRole changes needed: **{changed}**\n"
                f"Pending verifications: **{pending_count}**\nUnresolved Discord names: **{len(unresolved)}**"
            )
            details = results[:15]
            if details:
                summary += "\n\n" + "\n".join(f"- {x}" for x in details)
                if len(results) > 15:
                    summary += f"\n- ...and {len(results) - 15} more"
            if unresolved:
                summary += "\n\nUnresolved sample: " + ", ".join(unresolved[:10])

            await self._audit(
                "Roster role sync",
                str(interaction.user),
                f"Mode: {mode}\nMatched: {matched}\nChanges: {changed}\nUnresolved: {len(unresolved)}",
            )
            await interaction.followup.send(truncate(summary, 1900), ephemeral=True)

        @self.tree.error
        async def tree_error(interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
            log.exception("Application command error", exc_info=error)
            message = "Command failed. The error has been logged."
            try:
                if interaction.response.is_done():
                    await interaction.followup.send(message, ephemeral=True)
                else:
                    await interaction.response.send_message(message, ephemeral=True)
            except discord.HTTPException:
                pass


async def main() -> None:
    try:
        settings = load_settings()
    except ConfigError as exc:
        raise SystemExit(f"Configuration error: {exc}") from exc

    bot = OZYAdminBot(settings)
    try:
        await bot.start(settings.discord_token, reconnect=True)
    finally:
        if not bot.is_closed():
            await bot.close()


if __name__ == "__main__":
    asyncio.run(main())
