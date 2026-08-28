from __future__ import annotations

import asyncio
import json
import re
import logging
from dataclasses import dataclass
from datetime import datetime, time as dt_time, timedelta, timezone
from typing import Optional
from zoneinfo import ZoneInfo

import aiohttp
import discord
from aiohttp import web
from discord import app_commands

from ozy.data_provider import DataProvider, DataUnavailable
from settings import ConfigError, Settings, load_settings
from ozy.state import AdminState
from ozy.utils import format_chat_directory, format_chest_ranking_blocks, format_schedule, safe_code_block, truncate
from ozy.constants import PROFILE_LANGUAGES, PROFILE_LANGUAGE_CODES, PROFILE_LEVELS
from ozy.discord_ui import (
    AnnouncementModal,
    EventScheduleView,
    EventSetupModal,
    MembershipVerificationModal,
    MembershipVerificationRetryView,
    MembershipVerificationView,
    PostVerificationProfileModal,
    PostVerificationProfileView,
    RosterSuggestionView,
    VerificationRejectModal,
    VerificationReviewView,
)
from ozy.event_calendar import (
    TournamentCalendarClient,
    CalendarSourceError,
    build_calendar_chunks,
    build_today_chunks,
    build_today_local_chunks,
    game_day_for_instant,
    reset_label,
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

SCHEDULE_AUDIENCE_CHOICES = [
    app_commands.Choice(name="Clan", value="clan"),
    app_commands.Choice(name="Leadership", value="leadership"),
    app_commands.Choice(name="Both", value="both"),
]

# Minimal-impact source schedule. Public calendar metadata is checked only four
# times per UTC day while we learn the calendar source refresh cadence.
# Akurier regular mini-events are fetched once daily at R+1 (18:00 UTC).
CALENDAR_META_PROBE_TIMES_UTC = ((0, 30), (6, 30), (12, 30), (18, 30))
AKURIER_REFRESH_TIME_UTC = (18, 0)


def _next_utc_slot(now: datetime, slots: tuple[tuple[int, int], ...]) -> datetime:
    candidates = [
        datetime.combine(now.date(), dt_time(hour, minute), tzinfo=timezone.utc)
        for hour, minute in slots
    ]
    for candidate in candidates:
        if candidate > now:
            return candidate
    hour, minute = slots[0]
    return datetime.combine(
        now.date() + timedelta(days=1),
        dt_time(hour, minute),
        tzinfo=timezone.utc,
    )


class OZYAdminBot(discord.Client):
    def __init__(self, settings: Settings):
        intents = discord.Intents.none()
        intents.guilds = True
        intents.members = True

        super().__init__(intents=intents)
        self.settings = settings
        self.tree = app_commands.CommandTree(self)
        self.state = AdminState(
            settings.state_db,
            remote_url=settings.state_remote_url,
            remote_token=settings.state_remote_token,
            remote_timeout_seconds=settings.state_remote_timeout_seconds,
        )
        self._registered_verification_review_ids: set[int] = set()

        self.http_session: aiohttp.ClientSession | None = None
        self.data: DataProvider | None = None
        self.calendar_client: TournamentCalendarClient | None = None
        self.health_runner: web.AppRunner | None = None
        self.background_tasks: list[asyncio.Task] = []
        self._guild_validated = False
        self._message_series_lock = asyncio.Lock()

        self._register_commands()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def setup_hook(self) -> None:
        # Use aiohttp's normal client defaults. Do not advertise project/bot names
        # in outbound read-only source requests.
        self.http_session = aiohttp.ClientSession()
        self.data = DataProvider(self.settings, self.http_session)
        self.calendar_client = TournamentCalendarClient(self.settings, self.http_session)

        await self._start_health_server()

        # Persistent welcome verification button. Register it before command
        # sync so old welcome messages keep working after a restart/redeploy.
        self.add_view(MembershipVerificationView(self))
        self.add_view(PostVerificationProfileView(self))

        # Re-register persistent leadership approve/reject controls for every
        # pending claim so buttons posted before a restart keep working.
        for request in self.state.all_verification_request_records():
            self._ensure_verification_review_view(request.discord_user_id)

        guild = discord.Object(id=self.settings.server_id)
        self.tree.copy_global_to(guild=guild)
        synced = await self.tree.sync(guild=guild)
        log.info("Synced %d application commands to guild %s", len(synced), self.settings.server_id)
        synced_names = {command.name for command in synced}
        required_commands = {"event-create", "calendar", "today", "time"}
        missing_commands = sorted(required_commands - synced_names)
        if missing_commands:
            raise ConfigError(
                "Discord command sync is incomplete; missing: " + ", ".join(missing_commands)
            )

        self.background_tasks.append(asyncio.create_task(self._daily_schedule_loop(), name="daily-schedule"))
        self.background_tasks.append(asyncio.create_task(self._away_expiry_loop(), name="away-expiry"))
        if self.settings.chest_reset_post_enabled and self.settings.chest_channel_id:
            self.background_tasks.append(asyncio.create_task(self._daily_chest_ranking_loop(), name="daily-chest-ranking"))
        if (
            self.settings.rank_role_map
            or self.settings.verified_role_id
            or self.settings.unverified_role_id
            or self.settings.special_access_role_id
        ):
            self.background_tasks.append(asyncio.create_task(self._roster_access_sync_loop(), name="roster-access-sync"))
        if self.settings.calendar_enabled and (self.settings.calendar_channel_id or self.settings.today_channel_id):
            self.background_tasks.append(asyncio.create_task(self._calendar_refresh_loop(), name="calendar-refresh"))
            self.background_tasks.append(asyncio.create_task(self._akurier_refresh_loop(), name="akurier-refresh"))
            if self.settings.today_enabled and self.settings.today_channel_id:
                self.background_tasks.append(asyncio.create_task(self._calendar_today_loop(), name="calendar-today"))

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

        self.state.close()
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

    async def on_scheduled_event_delete(self, event: discord.ScheduledEvent) -> None:
        """Remove deleted Discord events from the canonical website schedule."""
        if event.guild_id != self.settings.server_id or self.data is None:
            return
        try:
            removed = await self.data.delete_schedule_event(event.id)
            if removed:
                log.info("Removed Discord event %s from OZY website schedule", event.id)
        except DataUnavailable as exc:
            log.warning("Could not remove Discord event %s from website schedule: %s", event.id, exc)

    async def _start_health_server(self) -> None:
        async def health(_: web.Request) -> web.Response:
            return web.json_response(
                {
                    "status": "ok",
                    "discord_ready": self.is_ready(),
                    "guild": self.settings.server_id,
                    "state_backend": self.state.backend,
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
            "LEADERSHIP_SCHEDULE_CHANNEL_ID": self.settings.leadership_schedule_channel_id,
            "CALENDAR_CHANNEL_ID": self.settings.calendar_channel_id,
            "TODAY_CHANNEL_ID": self.settings.today_channel_id,
            "AWAY_CHANNEL_ID": self.settings.away_channel_id,
            "AUDIT_CHANNEL_ID": self.settings.audit_channel_id,
            "CHEST_CHANNEL_ID": self.settings.chest_channel_id,
            "VERIFICATION_CHANNEL_ID": self.settings.verification_channel_id,
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
        role_ids.update(self.settings.language_role_map.values())
        if self.settings.away_role_id:
            role_ids.add(self.settings.away_role_id)
        if self.settings.verified_role_id:
            role_ids.add(self.settings.verified_role_id)
        if self.settings.unverified_role_id:
            role_ids.add(self.settings.unverified_role_id)
        if self.settings.special_access_role_id:
            role_ids.add(self.settings.special_access_role_id)
        if self.settings.announcement_ping_role_id:
            role_ids.add(self.settings.announcement_ping_role_id)
        role_ids.update(self.settings.leadership_role_ids)

        for role_id in role_ids:
            role = guild.get_role(role_id)
            if role is None:
                raise ConfigError(f"Configured role ID {role_id} does not exist in this guild")

        managed_role_ids = set(self.settings.rank_role_map.values())
        managed_role_ids.update(self.settings.language_role_map.values())
        if self.settings.away_role_id:
            managed_role_ids.add(self.settings.away_role_id)
        if self.settings.verified_role_id:
            managed_role_ids.add(self.settings.verified_role_id)
        if self.settings.unverified_role_id:
            managed_role_ids.add(self.settings.unverified_role_id)
        if self.settings.special_access_role_id:
            managed_role_ids.add(self.settings.special_access_role_id)
        for role_id in managed_role_ids:
            role = guild.get_role(role_id)
            if role and role >= me.top_role:
                raise ConfigError(
                    f"Managed role {role.name} ({role.id}) must be below the OZY Admin bot role"
                )

        if managed_role_ids and not me.guild_permissions.manage_roles:
            raise ConfigError("OZY Admin needs Manage Roles for configured rank/access/language role automation")
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
        if not self.settings.verified_role_id or not self.settings.unverified_role_id:
            log.warning(
                "VERIFIED_ROLE_ID and UNVERIFIED_ROLE_ID should both be configured to enforce roster-gated server access"
            )
        if self.settings.chest_reset_post_enabled and not self.settings.chest_channel_id:
            log.warning("CHEST_RESET_POST_ENABLED=true but CHEST_CHANNEL_ID is not configured")

        log.info("Validated OZY Admin configuration for guild %s", guild.name)
        log.info("OZY Admin state storage: %s", self.state.storage_label)

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

    def _can_create_events(self, member: discord.Member | None) -> bool:
        """Allow normal roster-gated members plus leadership/special-access users."""
        if member is None:
            return False
        if self._is_leadership(member):
            return True

        role_ids = {role.id for role in member.roles}
        if self.settings.special_access_role_id and self.settings.special_access_role_id in role_ids:
            return True
        if self.settings.verified_role_id:
            return self.settings.verified_role_id in role_ids
        if self.settings.unverified_role_id and self.settings.unverified_role_id in role_ids:
            return False

        # Backward-compatible fallback for servers that have not configured the
        # roster access roles yet. Once VERIFIED_ROLE_ID is configured, that role
        # becomes the normal-member gate for self-service event creation.
        return True

    async def _require_event_creator(self, interaction: discord.Interaction) -> bool:
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        if self._can_create_events(member):
            return True
        message = "Verify your OZY membership before creating events."
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
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


    def _ensure_verification_review_view(self, discord_user_id: int) -> None:
        if discord_user_id in self._registered_verification_review_ids:
            return
        self.add_view(VerificationReviewView(self, discord_user_id))
        self._registered_verification_review_ids.add(discord_user_id)

    async def _queue_verification_request(
        self,
        member: discord.Member,
        game_name: str,
        *,
        source: str,
        game_user_id: str | None = None,
    ) -> None:
        previous = self.state.get_verification_request_record(member.id)
        self.state.set_verification_request(
            member.id,
            game_name,
            source,
            game_user_id=game_user_id,
        )
        if (
            previous
            and previous.requested_game_name.casefold() == game_name.casefold()
            and previous.queue_channel_id
            and previous.queue_message_id
        ):
            self.state.set_verification_message(
                member.id, previous.queue_channel_id, previous.queue_message_id
            )
        self._ensure_verification_review_view(member.id)
        await self._publish_verification_request(member.id)

    async def _publish_verification_request(self, discord_user_id: int) -> None:
        if not self.settings.verification_channel_id:
            return
        guild = self.get_guild(self.settings.server_id)
        if guild is None:
            return
        request = self.state.get_verification_request_record(discord_user_id)
        if request is None:
            return
        channel = guild.get_channel(self.settings.verification_channel_id)
        if not isinstance(channel, discord.TextChannel):
            return
        member = guild.get_member(discord_user_id)
        embed = discord.Embed(
            title="Roster verification pending",
            color=0xF59E0B,
            timestamp=request.requested_at_utc,
        )
        embed.add_field(
            name="Discord member",
            value=(member.mention if member else f"Discord ID `{discord_user_id}`"),
            inline=False,
        )
        embed.add_field(name="Claimed OZY name", value=request.requested_game_name, inline=True)
        embed.add_field(name="Source", value=request.source, inline=True)
        if request.requested_game_user_id:
            embed.set_footer(text=f"TB user ID: {request.requested_game_user_id}")

        view = VerificationReviewView(self, discord_user_id)
        self._ensure_verification_review_view(discord_user_id)

        # If a request was refreshed, update its existing queue message when possible.
        if request.queue_channel_id == channel.id and request.queue_message_id:
            try:
                message = await channel.fetch_message(request.queue_message_id)
                await message.edit(embed=embed, view=view)
                return
            except discord.HTTPException:
                pass

        try:
            message = await channel.send(
                embed=embed,
                view=view,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.HTTPException as exc:
            log.warning("Verification queue post failed: %s", exc)
            return
        self.state.set_verification_message(discord_user_id, channel.id, message.id)

    async def _mark_verification_queue_resolved(
        self,
        request,
        *,
        decision: str,
        reviewer: discord.Member,
        reason: str,
        role_result: str | None = None,
    ) -> None:
        if not request.queue_channel_id or not request.queue_message_id:
            return
        guild = self.get_guild(self.settings.server_id)
        if guild is None:
            return
        channel = guild.get_channel(request.queue_channel_id)
        if not isinstance(channel, discord.TextChannel):
            return
        color = 0x10B981 if decision == "approved" else 0xEF4444
        title = "Roster verification approved" if decision == "approved" else "Roster verification rejected"
        member = guild.get_member(request.discord_user_id)
        embed = discord.Embed(title=title, color=color, timestamp=datetime.now(timezone.utc))
        embed.add_field(
            name="Discord member",
            value=(member.mention if member else f"Discord ID `{request.discord_user_id}`"),
            inline=False,
        )
        embed.add_field(name="Claimed OZY name", value=request.requested_game_name, inline=True)
        embed.add_field(name="Reviewed by", value=reviewer.mention, inline=True)
        if role_result:
            embed.add_field(name="Role sync", value=truncate(role_result, 1000), inline=False)
        if reason:
            embed.add_field(name="Decision note", value=truncate(reason, 1000), inline=False)
        try:
            message = await channel.fetch_message(request.queue_message_id)
            await message.edit(embed=embed, view=None)
        except discord.HTTPException:
            pass

    async def _review_verification_request(
        self,
        interaction: discord.Interaction,
        *,
        target_user_id: int,
        decision: str,
        reason: str,
    ) -> None:
        guild = interaction.guild
        reviewer = interaction.user if isinstance(interaction.user, discord.Member) else None
        if guild is None or reviewer is None:
            if interaction.response.is_done():
                await interaction.followup.send("Guild unavailable.", ephemeral=True)
            else:
                await interaction.response.send_message("Guild unavailable.", ephemeral=True)
            return

        request = self.state.get_verification_request_record(target_user_id)
        if request is None:
            message = "This verification request is no longer pending."
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
            return

        target = guild.get_member(target_user_id)
        if decision == "rejected":
            resolved = self.state.resolve_verification_request(
                target_user_id,
                decision="rejected",
                reviewed_by_user_id=reviewer.id,
                reason=reason,
            )
            if target:
                await self._sync_access_roles(target, active_roster_member=False)
                try:
                    await target.send(
                        f"Your OZY roster verification for **{request.requested_game_name}** was rejected.\n"
                        f"Reason: {reason}\n\nRun `/verify` again with your precise Total Battle name if needed."
                    )
                except discord.HTTPException:
                    pass
            if resolved:
                await self._mark_verification_queue_resolved(
                    resolved,
                    decision="rejected",
                    reviewer=reviewer,
                    reason=reason,
                )
            await self._audit(
                "Roster verification rejected",
                str(reviewer),
                f"Discord ID: {target_user_id}\nClaimed name: {request.requested_game_name}\nReason: {reason}",
            )
            if interaction.response.is_done():
                await interaction.followup.send("Verification rejected.", ephemeral=True)
            else:
                await interaction.response.send_message("Verification rejected.", ephemeral=True)
            return

        if target is None:
            message = "That Discord member is no longer in the server. Reject the claim or wait for them to rejoin."
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
            return

        assert self.data is not None
        try:
            canonical = await self.data.exact_roster_name(request.requested_game_name)
            if canonical is None:
                raise DataUnavailable("claimed name is no longer in the active OZY roster")
            info = await self.data.member_info(canonical)
            stable_id = str((info or {}).get("user_id", "")).strip() or None
            if request.requested_game_user_id and stable_id and request.requested_game_user_id != stable_id:
                message = "The roster identity changed since this claim was submitted. Ask the member to submit verification again."
                if interaction.response.is_done():
                    await interaction.followup.send(message, ephemeral=True)
                else:
                    await interaction.response.send_message(message, ephemeral=True)
                return
            linked_user = self.state.linked_user_for_identity(canonical, stable_id)
            if linked_user not in (None, target.id):
                other = guild.get_member(linked_user)
                other_name = other.display_name if other else f"Discord ID {linked_user}"
                message = f"**{canonical}** is already linked to **{other_name}**. Resolve that link first."
                if interaction.response.is_done():
                    await interaction.followup.send(message, ephemeral=True)
                else:
                    await interaction.response.send_message(message, ephemeral=True)
                return

            self.state.set_link(
                target.id,
                canonical,
                f"verification-approved:{reviewer.id}",
                game_user_id=stable_id,
            )
            role_result = await self._sync_rank_role(target, canonical)
        except DataUnavailable as exc:
            message = f"Cannot approve right now: {exc}"
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
            return

        resolved = self.state.resolve_verification_request(
            target.id,
            decision="approved",
            reviewed_by_user_id=reviewer.id,
            reason=reason,
        )
        if resolved:
            await self._mark_verification_queue_resolved(
                resolved,
                decision="approved",
                reviewer=reviewer,
                reason=reason,
                role_result=role_result,
            )
        await self._send_post_verification_profile_prompt(target, canonical, role_result=role_result)
        await self._audit(
            "Roster verification approved",
            str(reviewer),
            f"Discord member: {target} ({target.id})\nGame name: {canonical}\nRole sync: {role_result}",
        )
        if interaction.response.is_done():
            await interaction.followup.send(
                f"Approved {target.mention} as **{canonical}**. {role_result}",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        else:
            await interaction.response.send_message(
                f"Approved {target.mention} as **{canonical}**. {role_result}",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )

    def _member_from_interaction(self, interaction: discord.Interaction) -> discord.Member | None:
        if isinstance(interaction.user, discord.Member):
            if interaction.user.guild.id == self.settings.server_id:
                return interaction.user
        guild = self.get_guild(self.settings.server_id)
        if guild is None:
            return None
        return guild.get_member(interaction.user.id)

    def _language_role_for_code(self, guild: discord.Guild, code: str) -> discord.Role | None:
        code = code.strip().upper()
        configured_id = self.settings.language_role_map.get(code)
        if configured_id:
            return guild.get_role(configured_id)
        # Safe fallback for the current OZY server where language roles are
        # named exactly EN / ES / AR / DE / FR / NO / CEB / PT / SV / RU.
        return discord.utils.find(lambda role: role.name.upper() == code, guild.roles)

    def _language_role_ids(self, guild: discord.Guild) -> set[int]:
        ids: set[int] = set(self.settings.language_role_map.values())
        for code in PROFILE_LANGUAGE_CODES:
            role = self._language_role_for_code(guild, code)
            if role:
                ids.add(role.id)
        return ids

    async def _sync_language_role(self, member: discord.Member, code: str) -> str:
        code = code.strip().upper()
        if code not in PROFILE_LANGUAGE_CODES:
            return f"unsupported language {code}"

        target = self._language_role_for_code(member.guild, code)
        if target is None:
            return f"language role {code} is not configured/found"

        me = member.guild.me
        if me is None or target >= me.top_role:
            return f"cannot manage language role {target.name}; check role hierarchy"

        language_ids = self._language_role_ids(member.guild)
        remove_roles = [
            role for role in member.roles
            if role.id in language_ids and role.id != target.id
        ]
        try:
            if remove_roles:
                await member.remove_roles(*remove_roles, reason=f"OZY preferred language -> {code}")
            if target not in member.roles:
                await member.add_roles(target, reason=f"OZY preferred language -> {code}")
        except discord.HTTPException as exc:
            return f"language role update failed: {exc}"
        return f"{code} -> {target.name}"

    async def _open_membership_verification(
        self,
        interaction: discord.Interaction,
        *,
        suggested_name: str | None = None,
    ) -> None:
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        if member is None or member.guild.id != self.settings.server_id:
            await interaction.response.send_message("This verification only works inside the OZY server.", ephemeral=True)
            return
        await interaction.response.send_modal(
            MembershipVerificationModal(
                self,
                member=member,
                suggested_name=suggested_name,
            )
        )

    async def _join_roster_suggestions(self, member: discord.Member, *, limit: int = 5) -> list[str]:
        """Use Discord names only to suggest likely roster identities.

        Suggestions are never treated as proof. The selected name still passes
        through the exact roster claim + leadership approval flow.
        """
        assert self.data is not None
        variants: list[str] = []
        for value in (
            member.display_name,
            getattr(member, "global_name", None),
            member.name,
        ):
            value = str(value or "").strip()
            if value and value.casefold() not in {x.casefold() for x in variants}:
                variants.append(value)

        scored: dict[str, float] = {}
        for value in variants:
            exact = await self.data.exact_roster_name(value)
            if exact:
                scored[exact] = 1.0
            for match in await self.data.roster_suggestions(value, limit=max(limit * 3, 10)):
                scored[match.name] = max(scored.get(match.name, 0.0), match.score)

        ordered = sorted(scored.items(), key=lambda item: (-item[1], item[0].casefold()))
        return [name for name, score in ordered if score >= self.settings.roster_match_threshold][:limit]

    async def _submit_suggested_roster_name(
        self,
        interaction: discord.Interaction,
        selected_name: str,
    ) -> None:
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        if member is None or member.guild.id != self.settings.server_id:
            await interaction.response.send_message("This verification only works inside the OZY server.", ephemeral=True)
            return
        assert self.data is not None
        try:
            canonical, response, outcome = await self._process_verification_claim(
                member,
                selected_name,
                source="join-roster-suggestion",
            )
        except DataUnavailable as exc:
            await interaction.response.send_message(f"Roster unavailable: {exc}", ephemeral=True)
            return

        await self._audit(
            "Roster suggestion selected",
            str(member),
            f"Suggested/selected name: {selected_name}\nCanonical: {canonical or 'none'}\nOutcome: {outcome}",
        )
        if canonical is None:
            await interaction.response.send_message(
                response,
                ephemeral=True,
                view=MembershipVerificationRetryView(self, member.id),
            )
        else:
            await interaction.response.send_message(response, ephemeral=True)

    async def _open_post_verification_profile(self, interaction: discord.Interaction) -> None:
        member = self._member_from_interaction(interaction)
        if member is None:
            await interaction.response.send_message("I could not resolve your OZY server membership.", ephemeral=True)
            return
        assert self.data is not None
        link = self.state.get_link_record(member.id)
        if link is None:
            await interaction.response.send_message(
                "Complete OZY membership verification first. Your language and G/M/S profile unlocks after the roster link is approved.",
                ephemeral=True,
            )
            return
        try:
            info = await self.data.resolve_roster_member(
                game_name=link.game_name,
                game_user_id=link.game_user_id,
            )
        except DataUnavailable as exc:
            await interaction.response.send_message(f"Roster unavailable: {exc}", ephemeral=True)
            return
        if info is None:
            await interaction.response.send_message(
                "Your saved roster identity is not currently active. Ask leadership to review the link before editing your profile.",
                ephemeral=True,
            )
            return
        await interaction.response.send_modal(PostVerificationProfileModal(self, member=member))

    async def _submit_post_verification_profile(
        self,
        interaction: discord.Interaction,
        *,
        preferred_language: str,
        guardsmen_level: int,
        monsters_level: int,
        specialists_level: int,
    ) -> None:
        member = self._member_from_interaction(interaction)
        if member is None:
            await interaction.response.send_message("I could not resolve your OZY server membership.", ephemeral=True)
            return

        language = preferred_language.strip().upper()
        if language not in PROFILE_LANGUAGE_CODES:
            await interaction.response.send_message("Select a supported OZY language.", ephemeral=True)
            return
        if any(level not in PROFILE_LEVELS for level in (guardsmen_level, monsters_level, specialists_level)):
            await interaction.response.send_message("G, M and S levels must each be between 1 and 9.", ephemeral=True)
            return

        link = self.state.get_link_record(member.id)
        if link is None:
            await interaction.response.send_message("Membership verification must be approved before profile setup.", ephemeral=True)
            return
        assert self.data is not None
        try:
            info = await self.data.resolve_roster_member(
                game_name=link.game_name,
                game_user_id=link.game_user_id,
            )
        except DataUnavailable as exc:
            await interaction.response.send_message(f"Roster unavailable: {exc}", ephemeral=True)
            return
        if info is None:
            await interaction.response.send_message("Your linked roster identity is no longer active.", ephemeral=True)
            return

        canonical = str(info["name"])
        stable_id = str(info.get("user_id", "")).strip() or None
        role_result = await self._sync_language_role(member, language)
        if role_result.startswith(("unsupported", "language role", "cannot manage")):
            await interaction.response.send_message(
                f"Your profile was not saved because the language role could not be updated: {role_result}",
                ephemeral=True,
            )
            return

        self.state.set_member_profile_details(
            member.id,
            preferred_language=language,
            guardsmen_level=guardsmen_level,
            monsters_level=monsters_level,
            specialists_level=specialists_level,
            source="post-verification-profile",
            game_name=canonical,
            game_user_id=stable_id,
        )
        self.state.set_link(
            member.id,
            canonical,
            "post-verification-profile-refresh",
            game_user_id=stable_id,
        )
        language_label = dict(PROFILE_LANGUAGES).get(language, language)
        await self._audit(
            "Member profile updated",
            str(member),
            f"Game name: {canonical}\nLanguage: {language}\nG{guardsmen_level} / M{monsters_level} / S{specialists_level}\nRole sync: {role_result}",
        )
        await interaction.response.send_message(
            f"OZY profile saved for **{canonical}**.\n"
            f"Language: **{language_label} ({language})**\n"
            f"Troops: **G{guardsmen_level} / M{monsters_level} / S{specialists_level}**\n"
            f"Language role: {role_result}",
            ephemeral=True,
        )

    async def _send_post_verification_profile_prompt(
        self,
        member: discord.Member,
        canonical: str,
        *,
        role_result: str | None = None,
    ) -> bool:
        profile = self.state.get_member_profile(member.id)
        if profile and profile.profile_complete:
            return True

        embed = discord.Embed(
            title="OZY membership approved",
            description=f"You are verified as **{canonical}**.",
            color=0x10B981,
        )
        if role_result:
            embed.add_field(name="Access", value=role_result, inline=False)
        embed.add_field(
            name="Complete your profile",
            value=(
                "Choose your preferred language and your current **Guardsmen (G)**, "
                "**Monsters (M)** and **Specialists (S)** levels. The language choice "
                "also unlocks the matching language channel."
            ),
            inline=False,
        )
        view = PostVerificationProfileView(self)
        try:
            await member.send(embed=embed, view=view)
            return True
        except discord.HTTPException:
            pass

        if self.settings.welcome_channel_id:
            channel = self.get_channel(self.settings.welcome_channel_id)
            if isinstance(channel, discord.TextChannel):
                try:
                    await channel.send(
                        content=member.mention,
                        embed=embed,
                        view=view,
                        allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
                    )
                    return True
                except discord.HTTPException:
                    pass
        return False


    async def _process_verification_claim(
        self,
        member: discord.Member,
        game_name: str,
        *,
        source: str,
    ) -> tuple[str | None, str, str]:
        """Validate a claimed roster identity and return canonical/message/outcome."""
        assert self.data is not None
        canonical = await self.data.exact_roster_name(game_name)
        if canonical is None:
            suggestions = await self.data.roster_suggestions(game_name, 3)
            text = (
                "That name is not an exact match in the active OZY roster. "
                "Enter the precise Total Battle name you currently use in the clan."
            )
            if suggestions:
                text += "\n\nClosest roster names: " + ", ".join(f"**{m.name}**" for m in suggestions)
            return None, text, "invalid exact roster name"

        info = await self.data.member_info(canonical)
        stable_id = str((info or {}).get("user_id", "")).strip() or None
        self.state.set_member_profile_identity(
            member.id,
            game_name=canonical,
            game_user_id=stable_id,
        )

        existing = self.state.get_link_record(member.id)
        if existing and (
            (stable_id and existing.game_user_id == stable_id)
            or existing.game_name.casefold() == canonical.casefold()
        ):
            self.state.set_link(
                member.id,
                canonical,
                source + "-refresh",
                game_user_id=stable_id,
            )
            self.state.clear_verification_request(member.id)
            role_result = await self._sync_rank_role(member, canonical)
            return canonical, f"You are already linked to **{canonical}**. Role sync: {role_result}.", "already linked"

        linked_user = self.state.linked_user_for_identity(canonical, stable_id)
        if linked_user not in (None, member.id):
            await self._queue_verification_request(
                member,
                canonical,
                source=source + "-name-already-linked",
                game_user_id=stable_id,
            )
            return (
                canonical,
                f"The roster name **{canonical}** is already linked to another Discord account. "
                "Leadership must resolve this before access can be granted.",
                f"pending conflict with Discord ID {linked_user}",
            )

        if self.settings.trust_exact_display_name and member.display_name.casefold() == canonical.casefold():
            self.state.set_link(
                member.id,
                canonical,
                source + "-trusted",
                game_user_id=stable_id,
            )
            self.state.clear_verification_request(member.id)
            role_result = await self._sync_rank_role(member, canonical)
            await self._send_post_verification_profile_prompt(member, canonical, role_result=role_result)
            return canonical, f"Linked to **{canonical}**. Role sync: {role_result}.", f"auto-approved; {role_result}"

        await self._queue_verification_request(
            member,
            canonical,
            source=source,
            game_user_id=stable_id,
        )
        return (
            canonical,
            f"Exact roster name found: **{canonical}**. Your request is waiting for leadership approval. "
            "Normal server access stays locked until the Discord account is linked to that roster identity.",
            "pending leadership approval",
        )

    async def _submit_membership_verification(
        self,
        interaction: discord.Interaction,
        *,
        game_name: str,
    ) -> None:
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        if member is None or member.guild.id != self.settings.server_id:
            await interaction.response.send_message("This verification only works inside the OZY server.", ephemeral=True)
            return

        assert self.data is not None
        try:
            canonical, response, outcome = await self._process_verification_claim(
                member,
                game_name,
                source="verification-modal",
            )
        except DataUnavailable as exc:
            await interaction.response.send_message(
                f"The OZY roster is temporarily unavailable. Membership was not verified: {exc}",
                ephemeral=True,
            )
            return

        await self._audit(
            "Roster verification request",
            str(member),
            f"Game name: {canonical or game_name.strip()}\nOutcome: {outcome}",
        )

        if canonical is None:
            await interaction.response.send_message(
                response,
                ephemeral=True,
                view=MembershipVerificationRetryView(self, member.id),
            )
            return
        await interaction.response.send_message(response, ephemeral=True)

    async def _resolve_member_game_name(self, member: discord.Member) -> str | None:
        assert self.data is not None
        link = self.state.get_link_record(member.id)
        if link:
            info = await self.data.resolve_roster_member(
                game_name=link.game_name,
                game_user_id=link.game_user_id,
            )
            if info:
                canonical = str(info["name"])
                stable_id = str(info.get("user_id", "")).strip() or None
                if canonical != link.game_name or stable_id != link.game_user_id:
                    self.state.set_link(
                        member.id,
                        canonical,
                        "canonicalized",
                        game_user_id=stable_id,
                    )
                self.state.set_member_profile_identity(
                    member.id,
                    game_name=canonical,
                    game_user_id=stable_id,
                )
                return canonical

        # A Discord nickname is not proof of Total Battle identity. Exact-name
        # auto-linking is therefore disabled by default and must be explicitly
        # opted into for trusted/small servers.
        if self.settings.trust_exact_display_name:
            exact = await self.data.exact_roster_name(member.display_name)
            if exact:
                info = await self.data.member_info(exact)
                stable_id = str((info or {}).get("user_id", "")).strip() or None
                linked_user = self.state.linked_user_for_identity(exact, stable_id)
                if linked_user not in (None, member.id):
                    return None
                self.state.set_link(
                    member.id,
                    exact,
                    "trusted-exact-display-name",
                    game_user_id=stable_id,
                )
                return exact
        return None

    async def _sync_access_roles(self, member: discord.Member, active_roster_member: bool) -> str:
        """Apply roster-gated access roles without touching unrelated Discord roles."""
        guild = member.guild
        me = guild.me
        if me is None:
            return "bot guild member unavailable"

        verified = guild.get_role(self.settings.verified_role_id) if self.settings.verified_role_id else None
        unverified = guild.get_role(self.settings.unverified_role_id) if self.settings.unverified_role_id else None
        special = guild.get_role(self.settings.special_access_role_id) if self.settings.special_access_role_id else None
        has_special = bool(special and special in member.roles)

        add_roles: list[discord.Role] = []
        remove_roles: list[discord.Role] = []

        if active_roster_member:
            if verified and verified not in member.roles:
                add_roles.append(verified)
            if unverified and unverified in member.roles:
                remove_roles.append(unverified)
            if special and special in member.roles:
                remove_roles.append(special)
            state = "verified roster member"
        elif has_special:
            if verified and verified in member.roles:
                remove_roles.append(verified)
            if unverified and unverified in member.roles:
                remove_roles.append(unverified)
            state = "special access"
        else:
            if verified and verified in member.roles:
                remove_roles.append(verified)
            if unverified and unverified not in member.roles:
                add_roles.append(unverified)
            state = "unverified"

            # Language channels are post-verification. If Discord Community
            # Onboarding, a stale role, or a manual assignment gave a language
            # role early, remove it while the member is unverified.
            language_ids = self._language_role_ids(guild)
            remove_roles.extend(
                role for role in member.roles
                if role.id in language_ids and role not in remove_roles
            )

        # Roster rank roles are never kept for non-roster exceptions.
        if not active_roster_member:
            managed_rank_ids = set(self.settings.rank_role_map.values())
            remove_roles.extend(
                role for role in member.roles
                if role.id in managed_rank_ids and role not in remove_roles
            )

        try:
            if remove_roles:
                await member.remove_roles(*remove_roles, reason=f"OZY access sync: {state}")
            if add_roles:
                await member.add_roles(*add_roles, reason=f"OZY access sync: {state}")
        except discord.HTTPException as exc:
            return f"access role update failed: {exc}"

        return state

    async def _sync_rank_role(self, member: discord.Member, game_name: str) -> str:
        assert self.data is not None
        info = await self.data.member_info(game_name)
        if not info:
            access_result = await self._sync_access_roles(member, active_roster_member=False)
            return f"roster member not found; {access_result}"

        self.state.set_member_profile_identity(
            member.id,
            game_name=str(info.get("name") or game_name),
            game_user_id=str(info.get("user_id", "")).strip() or None,
        )

        access_result = await self._sync_access_roles(member, active_roster_member=True)
        profile = self.state.get_member_profile(member.id)
        language_result: str | None = None
        if profile and profile.preferred_language:
            language_result = await self._sync_language_role(member, profile.preferred_language)

        def with_language(text: str) -> str:
            if language_result:
                return f"{text}; language {language_result}"
            return text

        rank = str(info.get("rank", "")).strip()
        if not rank:
            return with_language(f"{access_result}; roster rank is blank")
        target_role_id = self.settings.rank_role_map.get(rank.casefold())
        if target_role_id is None:
            return with_language(f"{access_result}; no Discord role configured for roster rank {rank}")

        guild = member.guild
        target_role = guild.get_role(target_role_id)
        if target_role is None:
            return with_language(f"configured role {target_role_id} no longer exists")
        me = guild.me
        if me is None or target_role >= me.top_role:
            return with_language(f"cannot manage role {target_role.name}; check role hierarchy")

        managed_ids = set(self.settings.rank_role_map.values())
        remove_roles = [r for r in member.roles if r.id in managed_ids and r.id != target_role_id]
        try:
            if remove_roles:
                await member.remove_roles(*remove_roles, reason=f"OZY roster rank sync: {rank}")
            if target_role not in member.roles:
                await member.add_roles(target_role, reason=f"OZY roster rank sync: {rank}")
        except discord.HTTPException as exc:
            return with_language(f"Discord role update failed: {exc}")

        if self.settings.auto_sync_nickname and member.display_name != game_name:
            try:
                if me and member.top_role < me.top_role:
                    await member.edit(nick=game_name, reason="OZY roster name sync")
            except discord.HTTPException as exc:
                log.warning("Nickname sync failed for %s: %s", member.id, exc)

        return with_language(f"{access_result}; {rank} -> {target_role.name}")

    # ------------------------------------------------------------------
    # Welcome / roster verification
    # ------------------------------------------------------------------
    async def _process_new_member(self, member: discord.Member) -> None:
        if member.guild.id != self.settings.server_id or member.bot:
            return
        assert self.data is not None

        # New arrivals start without clan access until their stable roster
        # identity is approved. This also strips any language role that may have
        # leaked from Discord Onboarding before verification.
        await self._sync_access_roles(member, active_roster_member=False)

        matched_name: str | None = None
        suggestions: list[str] = []
        role_result: str | None = None
        linked_rejoin = False
        try:
            existing_link = self.state.get_link_record(member.id)
            linked_info = None
            if existing_link:
                linked_info = await self.data.resolve_roster_member(
                    game_name=existing_link.game_name,
                    game_user_id=existing_link.game_user_id,
                )

            if linked_info:
                linked_rejoin = True
                matched_name = str(linked_info["name"])
                stable_id = str(linked_info.get("user_id", "")).strip() or None
                self.state.set_link(
                    member.id,
                    matched_name,
                    "rejoin-existing-link",
                    game_user_id=stable_id,
                )
                role_result = await self._sync_rank_role(member, matched_name)
            elif self.settings.trust_exact_display_name:
                exact = await self.data.exact_roster_name(member.display_name)
                if exact:
                    info = await self.data.member_info(exact)
                    stable_id = str((info or {}).get("user_id", "")).strip() or None
                    linked_user = self.state.linked_user_for_identity(exact, stable_id)
                    if linked_user in (None, member.id):
                        self.state.set_link(
                            member.id,
                            exact,
                            "trusted-join-exact-display-name",
                            game_user_id=stable_id,
                        )
                        matched_name = exact
                        role_result = await self._sync_rank_role(member, exact)
                    else:
                        suggestions = await self._join_roster_suggestions(member)
                else:
                    suggestions = await self._join_roster_suggestions(member)
            else:
                # Safe default: even an exact Discord display-name match is only
                # a suggestion. The user confirms the roster name, then leadership
                # approves the Discord -> stable TB identity claim.
                suggestions = await self._join_roster_suggestions(member)
                if suggestions:
                    matched_name = suggestions[0]
        except DataUnavailable as exc:
            log.warning("Roster unavailable during member join: %s", exc)

        approved_link = bool(matched_name and self.state.get_link_record(member.id))

        if self.settings.welcome_channel_id:
            channel = self.get_channel(self.settings.welcome_channel_id)
            if isinstance(channel, discord.TextChannel):
                embed = discord.Embed(
                    title="Welcome to OZY",
                    description=f"Welcome {member.mention}.",
                    color=0xF59E0B,
                )
                if approved_link and matched_name:
                    embed.add_field(
                        name="Roster identity approved",
                        value=f"Linked as **{matched_name}**.\nAccess sync: {role_result}",
                        inline=False,
                    )
                elif suggestions:
                    embed.add_field(
                        name="We found likely roster names",
                        value=(
                            "Choose your Total Battle name from the list below. A suggestion is **not** proof of identity - "
                            "your claim still goes to leadership for approval.\n\n"
                            + "\n".join(f"- **{name}**" for name in suggestions)
                        ),
                        inline=False,
                    )
                else:
                    embed.add_field(
                        name="Verify your Total Battle name",
                        value=(
                            "I could not confidently match your Discord name to the active OZY roster. "
                            "Click **Verify OZY membership** and enter your exact current Total Battle name."
                        ),
                        inline=False,
                    )
                embed.add_field(
                    name="After approval",
                    value="You will choose your preferred language and enter your **G / M / S** troop levels.",
                    inline=False,
                )
                embed.add_field(
                    name="Useful commands",
                    value="`/verify` - roster identity\n`/profile` - language + G/M/S after approval\n`/chests` - your chest status\n`/away` - register an absence",
                    inline=False,
                )

                if approved_link:
                    profile = self.state.get_member_profile(member.id)
                    view: discord.ui.View | None = (
                        PostVerificationProfileView(self)
                        if not (profile and profile.profile_complete)
                        else None
                    )
                elif suggestions:
                    view = RosterSuggestionView(self, member.id, suggestions)
                else:
                    view = MembershipVerificationView(self)

                await channel.send(
                    content=member.mention,
                    embed=embed,
                    view=view,
                    allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
                )

        if approved_link and matched_name:
            await self._send_post_verification_profile_prompt(member, matched_name, role_result=role_result)

        self.state.mark_welcomed(member.id)
        await self._audit(
            "Member joined",
            str(member),
            f"Discord ID: {member.id}\nExisting roster link: {matched_name if linked_rejoin else 'none'}\n"
            f"Roster suggestions: {', '.join(suggestions) or 'none'}",
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

    async def on_member_remove(self, member: discord.Member) -> None:
        if member.guild.id != self.settings.server_id or member.bot:
            return
        # Preserve an approved Discord -> Total Battle link for safe rejoins, but
        # clear join-session state so the full onboarding flow runs again later.
        self.state.clear_welcomed(member.id)
        pending_request = self.state.get_verification_request_record(member.id)
        if pending_request:
            reviewer = member.guild.me
            reviewer_id = reviewer.id if reviewer else (self.user.id if self.user else 0)
            resolved = self.state.resolve_verification_request(
                member.id,
                decision="cancelled",
                reviewed_by_user_id=reviewer_id,
                reason="Member left the Discord server before verification completed",
            )
            if resolved and reviewer:
                await self._mark_verification_queue_resolved(
                    resolved,
                    decision="cancelled",
                    reviewer=reviewer,
                    reason="Member left the Discord server before verification completed",
                )
        self.state.clear_away(member.id)
        await self._audit(
            "Member left Discord",
            str(member),
            f"Discord ID: {member.id}\nApproved roster link preserved: {self.state.get_link(member.id) or 'none'}",
        )

    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        if after.guild.id != self.settings.server_id or after.bot:
            return

        if before.pending and not after.pending:
            await self._process_new_member(after)

        if {role.id for role in before.roles} != {role.id for role in after.roles}:
            # Keep language selection post-verification. If an unlinked account
            # receives a language/access role from stale Community Onboarding or
            # a manual edit, normalize it back to Unverified. Approved members
            # are normalized to the one preferred language stored in their profile.
            if self.state.get_link_record(after.id) is None:
                await self._sync_access_roles(after, active_roster_member=False)
            else:
                profile = self.state.get_member_profile(after.id)
                if profile and profile.preferred_language:
                    await self._sync_language_role(after, profile.preferred_language)

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
            info = await self.data.member_info(exact)
            stable_id = str((info or {}).get("user_id", "")).strip() or None
            if self.settings.trust_exact_display_name:
                linked_user = self.state.linked_user_for_identity(exact, stable_id)
                if linked_user not in (None, after.id):
                    await self._queue_verification_request(
                        after, exact, source="nickname-name-already-linked", game_user_id=stable_id
                    )
                    await self._audit(
                        "Roster verification requested",
                        str(after),
                        f"Changed Discord name matches {exact}, but that roster identity is already linked to Discord ID {linked_user}",
                    )
                    return
                self.state.set_link(
                    after.id,
                    exact,
                    "trusted-nickname-exact-match",
                    game_user_id=stable_id,
                )
                result = await self._sync_rank_role(after, exact)
                action = "Roster auto-link"
                details = f"Matched changed Discord name to {exact}; role sync: {result}"
            else:
                await self._queue_verification_request(
                    after, exact, source="nickname-exact-match", game_user_id=stable_id
                )
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
    async def _post_schedule(self, target_date, *, force: bool, actor: str, audience: str = "clan") -> bool:
        audience = audience.strip().casefold() or "clan"
        if audience == "leadership":
            channel_id = self.settings.leadership_schedule_channel_id
            dedupe_key = f"leadership_schedule_posted:{target_date.isoformat()}"
            already_posted = bool(self.state.get_value(dedupe_key))
        else:
            channel_id = self.settings.schedule_channel_id
            dedupe_key = None
            already_posted = self.state.schedule_posted(target_date.isoformat())

        if not channel_id:
            return False
        channel = self.get_channel(channel_id)
        if not isinstance(channel, discord.TextChannel):
            return False
        if not force and already_posted:
            return False

        assert self.data is not None
        try:
            items = await self.data.schedule_for_date(target_date, audience=audience)
        except DataUnavailable as exc:
            log.warning("%s schedule unavailable: %s", audience, exc)
            return False
        if not items:
            log.info("No %s schedule items for %s; automatic post skipped", audience, target_date)
            return False

        body = format_schedule(target_date, items)
        if audience == "leadership":
            body = body.replace("## OZY Schedule", "## OZY Leadership Schedule", 1)
        message = await channel.send(body, allowed_mentions=discord.AllowedMentions.none())

        if audience == "leadership":
            self.state.set_value(dedupe_key, f"{channel.id}:{message.id}")
        else:
            self.state.mark_schedule_posted(target_date.isoformat(), channel.id, message.id)
        await self._audit(
            "Daily schedule posted",
            actor,
            f"Audience: {audience}\nDate: {target_date.isoformat()}\nChannel: #{channel.name}\nItems: {len(items)}",
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

            # If the service starts/restarts after today's posting time, catch up
            # whichever audience has not yet been posted.
            clan_missing = not self.state.schedule_posted(now.date().isoformat())
            leadership_key = f"leadership_schedule_posted:{now.date().isoformat()}"
            leadership_missing = bool(
                self.settings.leadership_schedule_channel_id
                and not self.state.get_value(leadership_key)
            )
            if now >= today_target and (clan_missing or leadership_missing):
                try:
                    if clan_missing:
                        await self._post_schedule(now.date(), force=False, actor="automatic scheduler", audience="clan")
                    if leadership_missing:
                        await self._post_schedule(now.date(), force=False, actor="automatic scheduler", audience="leadership")
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
                await self._post_schedule(next_target.date(), force=False, actor="automatic scheduler", audience="clan")
                await self._post_schedule(next_target.date(), force=False, actor="automatic scheduler", audience="leadership")
            except Exception:
                log.exception("Automatic daily schedule post failed")

    # ------------------------------------------------------------------
    # Daily chest ranking
    # ------------------------------------------------------------------
    async def _post_chest_ranking(self, target_date, *, force: bool, actor: str) -> bool:
        if not self.settings.chest_channel_id:
            return False
        channel = self.get_channel(self.settings.chest_channel_id)
        if not isinstance(channel, discord.TextChannel):
            return False

        state_key = f"chest_ranking_posted:{target_date.isoformat()}"
        if not force and self.state.get_value(state_key):
            return False

        assert self.data is not None
        # R+0 should always use the newest website snapshot, not a minute-old cache.
        self.data.invalidate("roster")
        self.data.invalidate("chests")
        try:
            leaderboard = await self.data.chest_leaderboard(today=target_date)
        except DataUnavailable as exc:
            log.warning("Chest ranking unavailable: %s", exc)
            return False
        if leaderboard is None or not leaderboard.members:
            log.warning("Chest ranking skipped: no current leaderboard data")
            return False

        blocks = format_chest_ranking_blocks(
            leaderboard,
            chunk_size=self.settings.chest_report_chunk_size,
        )
        if not blocks:
            return False

        message_ids: list[int] = []
        for block in blocks:
            message = await channel.send(
                block,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            message_ids.append(message.id)

        self.state.set_value(state_key, ",".join(str(x) for x in message_ids))
        await self._audit(
            "Chest ranking posted",
            actor,
            (
                f"Date: {target_date.isoformat()}\nChannel: #{channel.name}\n"
                f"Week: {leaderboard.week_label}\nPlayers: {len(leaderboard.members)}\n"
                f"Points: {leaderboard.total_points:,}\nSource generated: {leaderboard.generated or 'unknown'}"
            ),
        )
        return True

    async def _daily_chest_ranking_loop(self) -> None:
        await self.wait_until_ready()
        if not self.settings.chest_reset_post_enabled:
            return

        hour, minute = [int(x) for x in self.settings.chest_reset_post_time_utc.split(":", 1)]
        tz = timezone.utc

        while not self.is_closed():
            now = datetime.now(tz)
            today_target = datetime.combine(now.date(), dt_time(hour, minute), tzinfo=tz)
            state_key = f"chest_ranking_posted:{now.date().isoformat()}"

            if now >= today_target and not self.state.get_value(state_key):
                try:
                    await self._post_chest_ranking(
                        now.date(),
                        force=False,
                        actor="automatic R+0 chest scheduler",
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("Catch-up R+0 chest ranking post failed")

            now = datetime.now(tz)
            next_target = datetime.combine(now.date(), dt_time(hour, minute), tzinfo=tz)
            if next_target <= now:
                next_target += timedelta(days=1)
            try:
                await asyncio.sleep(max(1.0, (next_target - now).total_seconds()))
            except asyncio.CancelledError:
                raise

            try:
                await self._post_chest_ranking(
                    next_target.date(),
                    force=False,
                    actor="automatic R+0 chest scheduler",
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Automatic R+0 chest ranking post failed")

    # ------------------------------------------------------------------
    # Tournament calendar
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

    async def _refresh_calendar(self, *, force: bool, actor: str, refresh_akurier: bool = False) -> tuple[bool, str]:
        if not self.settings.calendar_enabled:
            return False, "Tournament calendar integration is disabled"
        if self.calendar_client is None:
            return False, "Tournament calendar client is unavailable"

        try:
            result = await self.calendar_client.refresh(force=force, refresh_akurier=refresh_akurier)
        except CalendarSourceError as exc:
            await self._audit("Tournament calendar refresh failed", actor, str(exc))
            return False, str(exc)

        snapshot = result.snapshot

        # Record the public source timestamp only when it changes. This lets us
        # learn the calendar source refresh cadence and later reduce the four daily
        # metadata probes to one check 20-30 minutes after its normal update.
        if result.source_last_synced_utc is not None:
            source_iso = result.source_last_synced_utc.isoformat()
            previous_source_iso = self.state.get_value("calendar_source_last_synced_utc")
            if previous_source_iso != source_iso:
                self.state.set_value("calendar_source_last_synced_utc", source_iso)
                observed = datetime.now(timezone.utc).isoformat()
                log.info(
                    "Calendar source timestamp observed: source=%s observed=%s previous=%s",
                    source_iso, observed, previous_source_iso or "none",
                )
                if previous_source_iso:
                    await self._audit(
                        "Calendar source refresh observed",
                        actor,
                        f"Previous source sync: {previous_source_iso}\n"
                        f"New source sync: {source_iso}\n"
                        f"Observed at: {observed}",
                    )

        today = datetime.now(timezone.utc).date()

        rendered_start = self.state.get_value("calendar_start_date")
        window_rolled = rendered_start != today.isoformat()
        if self.settings.calendar_channel_id and (result.changed or force or window_rolled):
            channel = self.get_channel(self.settings.calendar_channel_id)
            if isinstance(channel, discord.TextChannel):
                chunks = build_calendar_chunks(
                    snapshot,
                    start_date=today,
                    days=self.settings.calendar_days,
                    timezone_info=timezone.utc,
                )
                await self._upsert_message_series(
                    channel,
                    state_key="calendar_message_ids",
                    recovery_prefix="OZY Tournament Calendar - Next 30 Days",
                    chunks=chunks,
                )
                self.state.set_value("calendar_start_date", today.isoformat())

        # If today's post already exists, silently keep it current after source changes.
        if self.settings.today_channel_id and result.changed:
            today_key = f"calendar_today_message_ids:{today.isoformat()}"
            if self._state_message_ids(today_key):
                await self._post_calendar_today(today, force=True, actor="calendar refresh")

        if result.changed:
            await self._audit(
                "Tournament calendar updated",
                actor,
                f"Actions: {len(snapshot.actions)}\nMini tournaments: {len(snapshot.mini_tournaments)}\n"
                f"Hash: {snapshot.semantic_hash[:12]}",
            )
        return result.changed, "ok"

    async def _refresh_akurier(self, *, actor: str) -> tuple[bool, str]:
        if not self.settings.calendar_enabled or self.calendar_client is None:
            return False, "Calendar integration is unavailable"
        if self.calendar_client.snapshot is None:
            return False, "No tournament calendar snapshot is available"

        try:
            result = await self.calendar_client.refresh_akurier()
        except CalendarSourceError as exc:
            log.warning("Akurier mini-event refresh skipped: %s", exc)
            return False, str(exc)

        if result.changed:
            today = datetime.now(timezone.utc).date()

            # Akurier mini-events belong in both public schedule surfaces. Update
            # from the cache only; do not make another calendar-source request here.
            if self.settings.calendar_channel_id:
                channel = self.get_channel(self.settings.calendar_channel_id)
                if isinstance(channel, discord.TextChannel):
                    chunks = build_calendar_chunks(
                        result.snapshot,
                        start_date=today,
                        days=self.settings.calendar_days,
                        timezone_info=timezone.utc,
                    )
                    await self._upsert_message_series(
                        channel,
                        state_key="calendar_message_ids",
                        recovery_prefix="OZY Tournament Calendar - Next 30 Days",
                        chunks=chunks,
                    )
                    self.state.set_value("calendar_start_date", today.isoformat())

            if self.settings.today_channel_id:
                await self._post_calendar_today(today, force=True, actor="mini-event refresh")

            await self._audit(
                "Mini events updated",
                actor,
                f"Regular mini events: {len(result.snapshot.mini_tournaments)}",
            )
        return result.changed, "ok"

    async def _post_calendar_today(self, target_date, *, force: bool, actor: str) -> bool:
        if not self.settings.today_channel_id or self.calendar_client is None:
            return False
        channel = self.get_channel(self.settings.today_channel_id)
        if not isinstance(channel, discord.TextChannel):
            return False

        try:
            # Discord rendering uses the in-memory cache. Only contact the calendar source if
            # startup has not populated a snapshot yet.
            if self.calendar_client.snapshot is None:
                await self.calendar_client.refresh(force=True)
        except CalendarSourceError as exc:
            log.warning("Today's tournament post skipped: %s", exc)
            return False
        snapshot = self.calendar_client.snapshot
        if snapshot is None:
            return False

        state_key = f"calendar_today_message_ids:{target_date.isoformat()}"
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
            "Tournament today posted" if not existing else "Tournament today updated",
            actor,
            f"Date: {target_date.isoformat()}\nChannel: #{channel.name}",
        )
        return True

    async def _calendar_refresh_loop(self) -> None:
        await self.wait_until_ready()

        # One startup read is needed to populate the in-memory cache. After that,
        # only lightweight metadata probes run at the fixed times below. Full
        # calendar content is downloaded only when metadata reports a change.
        try:
            await self._refresh_calendar(
                force=self.calendar_client.snapshot is None if self.calendar_client else True,
                actor="startup calendar refresh",
                refresh_akurier=False,
            )
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Startup Tournament calendar refresh failed")

        while not self.is_closed():
            now = datetime.now(timezone.utc)
            target = _next_utc_slot(now, CALENDAR_META_PROBE_TIMES_UTC)
            try:
                await asyncio.sleep(max(1.0, (target - now).total_seconds()))
            except asyncio.CancelledError:
                raise

            try:
                log.info("Running lightweight calendar metadata probe at %s", target.isoformat())
                await self._refresh_calendar(
                    force=False,
                    actor="automatic metadata probe",
                    refresh_akurier=False,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Automatic calendar metadata probe failed")

    async def _akurier_refresh_loop(self) -> None:
        await self.wait_until_ready()
        hour, minute = AKURIER_REFRESH_TIME_UTC

        while not self.is_closed():
            now = datetime.now(timezone.utc)
            today_target = datetime.combine(now.date(), dt_time(hour, minute), tzinfo=timezone.utc)
            last_attempt = self.state.get_value("akurier_last_attempt_date_utc")

            # Catch up once if the service starts after R+1 and today's single
            # Akurier fetch has not yet been attempted. A failed attempt is not
            # hammered with retries; the existing cached/fallback data remains.
            if now >= today_target and last_attempt != now.date().isoformat():
                # On a fresh deploy the calendar startup read may still be running.
                # Wait briefly rather than consuming today's single Akurier attempt
                # before there is a snapshot to attach the mini-events to.
                if self.calendar_client is None or self.calendar_client.snapshot is None:
                    try:
                        await asyncio.sleep(10)
                    except asyncio.CancelledError:
                        raise
                    continue

                self.state.set_value("akurier_last_attempt_date_utc", now.date().isoformat())
                try:
                    log.info("Running once-daily Akurier mini-event refresh at R+1")
                    await self._refresh_akurier(actor="automatic daily mini-event refresh")
                except asyncio.CancelledError:
                    raise
                except Exception:
                    log.exception("Automatic Akurier mini-event refresh failed")
                continue

            next_target = today_target if today_target > now else today_target + timedelta(days=1)
            try:
                await asyncio.sleep(max(1.0, (next_target - now).total_seconds()))
            except asyncio.CancelledError:
                raise

    async def _calendar_today_loop(self) -> None:
        await self.wait_until_ready()
        hour, minute = 17, 0
        tz = timezone.utc

        while not self.is_closed():
            now = datetime.now(tz)
            target = datetime.combine(now.date(), dt_time(hour, minute), tzinfo=tz)

            # Catch up after a restart without duplicating the day's canonical post.
            try:
                await self._post_calendar_today(
                    game_day_for_instant(datetime.now(timezone.utc)),
                    force=False,
                    actor="automatic today scheduler",
                )
            except Exception:
                log.exception("Catch-up tournament today post failed")

            now = datetime.now(tz)
            next_target = datetime.combine(now.date(), dt_time(hour, minute), tzinfo=tz)
            if next_target <= now:
                next_target += timedelta(days=1)
            try:
                await asyncio.sleep(max(1.0, (next_target - now).total_seconds()))
            except asyncio.CancelledError:
                raise

            try:
                await self._post_calendar_today(
                    game_day_for_instant(datetime.now(timezone.utc)),
                    force=False,
                    actor="automatic today scheduler",
                )
            except Exception:
                log.exception("Automatic tournament today post failed")

    # ------------------------------------------------------------------
    # Authoritative roster/access synchronization
    # ------------------------------------------------------------------
    async def _sync_all_member_access(self, *, apply: bool) -> dict[str, int]:
        assert self.data is not None
        self.data.invalidate("roster")
        roster = await self.data.roster()
        exact_lookup = {name.casefold(): name for name in roster}
        user_id_lookup = {
            str(info.get("user_id", "")).strip(): name
            for name, info in roster.items()
            if str(info.get("user_id", "")).strip()
        }
        links = self.state.all_link_records()
        guild = self.get_guild(self.settings.server_id)
        if guild is None:
            raise DataUnavailable("OZY guild unavailable")

        special_role = guild.get_role(self.settings.special_access_role_id) if self.settings.special_access_role_id else None
        counts = {
            "discord_members": 0,
            "roster_linked": 0,
            "special_access": 0,
            "unverified": 0,
            "stale_links": 0,
        }

        for member in guild.members:
            if member.bot:
                continue
            counts["discord_members"] += 1
            link = links.get(member.id)
            canonical = None
            if link:
                if link.game_user_id:
                    canonical = user_id_lookup.get(link.game_user_id)
                if canonical is None:
                    canonical = exact_lookup.get(link.game_name.casefold())
            if canonical:
                counts["roster_linked"] += 1
                if apply:
                    roster_user_id = str(roster[canonical].get("user_id", "")).strip() or None
                    if canonical != link.game_name or roster_user_id != link.game_user_id:
                        self.state.set_link(
                            member.id,
                            canonical,
                            "canonicalized-access-sync",
                            game_user_id=roster_user_id,
                        )
                    await self._sync_rank_role(member, canonical)
                continue

            if link:
                counts["stale_links"] += 1

            has_special = bool(special_role and special_role in member.roles)
            if has_special:
                counts["special_access"] += 1
            else:
                counts["unverified"] += 1
            if apply:
                await self._sync_access_roles(member, active_roster_member=False)

        return counts

    async def _roster_access_sync_loop(self) -> None:
        await self.wait_until_ready()

        while not self.is_closed():
            try:
                await self._sync_all_member_access(apply=True)
            except asyncio.CancelledError:
                raise
            except DataUnavailable as exc:
                # If the website is unavailable, preserve current Discord access
                # rather than mass-revoking members because of an upstream outage.
                log.warning("Roster access sync skipped; authoritative roster unavailable: %s", exc)
            except Exception:
                log.exception("Roster access sync failed")

            try:
                await asyncio.sleep(self.settings.roster_access_sync_minutes * 60)
            except asyncio.CancelledError:
                raise

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

        @self.tree.command(name="verify", description="Verify your OZY roster identity")
        @app_commands.describe(game_name="Optional exact Total Battle name; leave blank to open the verification form")
        async def verify(interaction: discord.Interaction, game_name: Optional[str] = None) -> None:
            if not isinstance(interaction.user, discord.Member):
                await interaction.response.send_message("This command only works inside the OZY server.", ephemeral=True)
                return
            if not game_name:
                await self._open_membership_verification(interaction)
                return
            assert self.data is not None
            try:
                canonical, response, outcome = await self._process_verification_claim(
                    interaction.user,
                    game_name,
                    source="self-verify",
                )
            except DataUnavailable as exc:
                await interaction.response.send_message(f"Roster unavailable: {exc}", ephemeral=True)
                return

            await self._audit(
                "Roster verification request",
                str(interaction.user),
                f"Game name: {canonical or game_name.strip()}\nOutcome: {outcome}",
            )
            if canonical is None:
                await interaction.response.send_message(
                    response,
                    ephemeral=True,
                    view=MembershipVerificationRetryView(self, interaction.user.id),
                )
            else:
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
                info = await self.data.member_info(canonical)
                stable_id = str((info or {}).get("user_id", "")).strip() or None
                linked_user = self.state.linked_user_for_identity(canonical, stable_id)
                if linked_user not in (None, member.id):
                    other = interaction.guild.get_member(linked_user) if interaction.guild else None
                    other_name = other.display_name if other else f"Discord ID {linked_user}"
                    await interaction.response.send_message(
                        f"**{canonical}** is already linked to **{other_name}**. Unlink/reassign that identity before approving another account.",
                        ephemeral=True,
                    )
                    return
                pending_request = self.state.get_verification_request_record(member.id)
                self.state.set_link(
                    member.id,
                    canonical,
                    f"leadership:{interaction.user.id}",
                    game_user_id=stable_id,
                )
                role_result = await self._sync_rank_role(member, canonical)
                await self._send_post_verification_profile_prompt(member, canonical, role_result=role_result)
                if pending_request:
                    resolved = self.state.resolve_verification_request(
                        member.id,
                        decision="approved",
                        reviewed_by_user_id=interaction.user.id,
                        reason="Approved via /member-link",
                    )
                    if resolved and isinstance(interaction.user, discord.Member):
                        await self._mark_verification_queue_resolved(
                            resolved,
                            decision="approved",
                            reviewer=interaction.user,
                            reason="Approved via /member-link",
                            role_result=role_result,
                        )
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
            pending = self.state.all_verification_request_records()
            if not pending:
                await interaction.response.send_message("No pending roster verification requests.", ephemeral=True)
                return
            lines: list[str] = []
            for request in pending[:25]:
                member = interaction.guild.get_member(request.discord_user_id)
                discord_name = member.display_name if member else f"Discord ID {request.discord_user_id}"
                age = discord.utils.format_dt(request.requested_at_utc, style="R")
                lines.append(f"- **{discord_name}** -> `{request.requested_game_name}` - {age}")
            text = "## Pending roster verifications\n" + "\n".join(lines)
            if len(pending) > 25:
                text += f"\n- ...and {len(pending) - 25} more"
            if self.settings.verification_channel_id:
                channel = interaction.guild.get_channel(self.settings.verification_channel_id)
                if isinstance(channel, discord.TextChannel):
                    text += f"\n\nUse the **Approve / Reject** buttons in {channel.mention}."
            else:
                text += "\n\n`VERIFICATION_CHANNEL_ID` is not configured. Leadership can still approve with `/member-link`."
            await interaction.response.send_message(truncate(text, 1900), ephemeral=True)

        @self.tree.command(name="verification-history", description="Leadership: show recent roster verification decisions")
        async def verification_history(interaction: discord.Interaction) -> None:
            if not await self._require_leadership(interaction):
                return
            if interaction.guild is None:
                await interaction.response.send_message("Guild unavailable.", ephemeral=True)
                return
            history = self.state.verification_history(20)
            if not history:
                await interaction.response.send_message("No verification decisions recorded yet.", ephemeral=True)
                return
            lines: list[str] = []
            for item in history:
                member = interaction.guild.get_member(item.discord_user_id)
                member_name = member.display_name if member else f"Discord ID {item.discord_user_id}"
                reviewer = interaction.guild.get_member(item.reviewed_by_user_id)
                reviewer_name = reviewer.display_name if reviewer else f"ID {item.reviewed_by_user_id}"
                marker = "APPROVED" if item.decision == "approved" else item.decision.upper()
                lines.append(
                    f"- **{marker}** - {member_name} -> `{item.requested_game_name}` "
                    f"by **{reviewer_name}** {discord.utils.format_dt(item.reviewed_at_utc, style='R')}"
                )
            await interaction.response.send_message(
                truncate("## Verification history\n" + "\n".join(lines), 1900),
                ephemeral=True,
            )

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

        @self.tree.command(name="profile", description="Complete or edit your OZY language and G/M/S profile")
        async def profile(interaction: discord.Interaction) -> None:
            await self._open_post_verification_profile(interaction)

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
            profile = self.state.get_member_profile(target.id)
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
                    if profile and profile.profile_complete:
                        text += (
                            f" Profile: **{profile.preferred_language}**, "
                            f"**G{profile.guardsmen_level}/M{profile.monsters_level}/S{profile.specialists_level}**."
                        )
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
            if profile:
                if profile.preferred_language:
                    language_label = dict(PROFILE_LANGUAGES).get(profile.preferred_language, profile.preferred_language)
                    embed.add_field(name="Language", value=f"{language_label} ({profile.preferred_language})", inline=True)
                if profile.guardsmen_level is not None:
                    embed.add_field(name="Guardsmen", value=f"G{profile.guardsmen_level}", inline=True)
                if profile.monsters_level is not None:
                    embed.add_field(name="Monsters", value=f"M{profile.monsters_level}", inline=True)
                if profile.specialists_level is not None:
                    embed.add_field(name="Specialists", value=f"S{profile.specialists_level}", inline=True)
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

        @self.tree.command(name="chest-ranking", description="Leadership: preview or post the current OZY chest ranking")
        @app_commands.describe(post="False = private preview; True = post now in IMPORTANT / #chests")
        async def chest_ranking(interaction: discord.Interaction, post: bool = False) -> None:
            if not await self._require_leadership(interaction):
                return
            assert self.data is not None
            await interaction.response.defer(ephemeral=True, thinking=True)
            target_date = datetime.now(self.settings.timezone).date()

            if post:
                try:
                    posted = await self._post_chest_ranking(
                        target_date,
                        force=True,
                        actor=str(interaction.user),
                    )
                except Exception as exc:
                    log.exception("Manual chest ranking post failed")
                    await interaction.followup.send(f"Chest ranking post failed: {exc}", ephemeral=True)
                    return
                await interaction.followup.send(
                    "Chest ranking posted to the configured #chests channel." if posted else "No chest ranking was available to post.",
                    ephemeral=True,
                )
                return

            self.data.invalidate("roster")
            self.data.invalidate("chests")
            try:
                leaderboard = await self.data.chest_leaderboard(today=target_date)
            except DataUnavailable as exc:
                await interaction.followup.send(f"Chest data unavailable: {exc}", ephemeral=True)
                return
            if leaderboard is None:
                await interaction.followup.send("No current chest leaderboard is available.", ephemeral=True)
                return

            blocks = format_chest_ranking_blocks(leaderboard, self.settings.chest_report_chunk_size)
            await interaction.followup.send(
                f"Preview: **{leaderboard.week_label}** - {len(leaderboard.members)} active roster members - {leaderboard.total_points:,} points.",
                ephemeral=True,
            )
            for block in blocks:
                await interaction.followup.send(
                    block,
                    ephemeral=True,
                    allowed_mentions=discord.AllowedMentions.none(),
                )

        @self.tree.command(name="data-status", description="Leadership: verify roster and chest website/API data")
        async def data_status(interaction: discord.Interaction) -> None:
            if not await self._require_leadership(interaction):
                return
            assert self.data is not None
            await interaction.response.defer(ephemeral=True, thinking=True)
            self.data.invalidate("roster")
            self.data.invalidate("chests")

            lines = ["## OZY data status"]
            try:
                roster = await self.data.roster()
                ranks: dict[str, int] = {}
                for info in roster.values():
                    rank = str(info.get("rank", "Unknown") or "Unknown")
                    ranks[rank] = ranks.get(rank, 0) + 1
                rank_text = ", ".join(f"{rank}: {count}" for rank, count in sorted(ranks.items()))
                source = self.settings.roster_url or str(self.settings.roster_file)
                lines.append(f"Roster: **OK** - {len(roster)} active members\nSource: `{source}`\nRanks: {rank_text or 'none'}")
            except DataUnavailable as exc:
                lines.append(f"Roster: **FAILED** - {exc}")

            try:
                board = await self.data.chest_leaderboard(today=datetime.now(self.settings.timezone).date())
                source = self.settings.chest_data_url or str(self.settings.chest_data_file)
                if board is None:
                    lines.append(f"Chests: **NO CURRENT DATA**\nSource: `{source}`")
                else:
                    lines.append(
                        f"Chests: **OK** - {board.week_label}\nSource: `{source}`\n"
                        f"Players ranked: {len(board.members)} - Points: {board.total_points:,} - Chests: {board.total_chests:,}\n"
                        f"Generated: {board.generated or 'unknown'}"
                    )
            except DataUnavailable as exc:
                lines.append(f"Chests: **FAILED** - {exc}")

            await interaction.followup.send("\n\n".join(lines), ephemeral=True)

        @self.tree.command(name="special-access", description="Leadership: grant or revoke non-roster server access")
        @app_commands.describe(member="Discord member", grant="True to grant; False to revoke", reason="Optional audit note")
        async def special_access(
            interaction: discord.Interaction,
            member: discord.Member,
            grant: bool,
            reason: str = "",
        ) -> None:
            if not await self._require_leadership(interaction):
                return
            if not self.settings.special_access_role_id:
                await interaction.response.send_message("SPECIAL_ACCESS_ROLE_ID is not configured.", ephemeral=True)
                return
            role = member.guild.get_role(self.settings.special_access_role_id)
            if role is None:
                await interaction.response.send_message("The configured Special Access role does not exist.", ephemeral=True)
                return

            assert self.data is not None
            try:
                active_game_name = await self._resolve_member_game_name(member)
            except DataUnavailable:
                active_game_name = None

            try:
                if grant:
                    if active_game_name:
                        result = await self._sync_rank_role(member, active_game_name)
                        await interaction.response.send_message(
                            f"{member.mention} is already an active roster member. Normal roster access is authoritative. {result}.",
                            ephemeral=True,
                            allowed_mentions=discord.AllowedMentions.none(),
                        )
                        return
                    if role not in member.roles:
                        await member.add_roles(role, reason=f"OZY special access granted by {interaction.user}: {reason or 'no reason'}")
                    result = await self._sync_access_roles(member, active_roster_member=False)
                    action = "granted"
                else:
                    if role in member.roles:
                        await member.remove_roles(role, reason=f"OZY special access revoked by {interaction.user}: {reason or 'no reason'}")
                    if active_game_name:
                        result = await self._sync_rank_role(member, active_game_name)
                    else:
                        result = await self._sync_access_roles(member, active_roster_member=False)
                    action = "revoked"
            except discord.HTTPException as exc:
                await interaction.response.send_message(f"Discord role update failed: {exc}", ephemeral=True)
                return

            await self._audit(
                f"Special access {action}",
                str(interaction.user),
                f"Member: {member} ({member.id})\nReason: {reason or 'none'}\nResult: {result}",
            )
            await interaction.response.send_message(
                f"Special access {action} for {member.mention}. Access state: {result}.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )

        @self.tree.command(name="access-sync", description="Leadership: preview or apply roster-gated server access")
        @app_commands.describe(apply="False = preview counts; True = apply access and roster-rank roles now")
        async def access_sync(interaction: discord.Interaction, apply: bool = False) -> None:
            if not await self._require_leadership(interaction):
                return
            await interaction.response.defer(ephemeral=True, thinking=True)
            try:
                counts = await self._sync_all_member_access(apply=apply)
            except DataUnavailable as exc:
                await interaction.followup.send(f"Roster unavailable: {exc}", ephemeral=True)
                return

            mode = "APPLIED" if apply else "PREVIEW"
            text = (
                f"**{mode}**\n"
                f"Discord members: **{counts['discord_members']}**\n"
                f"Approved active-roster links: **{counts['roster_linked']}**\n"
                f"Special-access exceptions: **{counts['special_access']}**\n"
                f"Unverified/no access: **{counts['unverified']}**\n"
                f"Saved links no longer in active roster: **{counts['stale_links']}**"
            )
            await self._audit("Roster access sync", str(interaction.user), text)
            await interaction.followup.send(text, ephemeral=True)

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

        @self.tree.command(name="schedule", description="Show today's OZY clan or leadership schedule")
        @app_commands.describe(
            audience="Clan schedule, Leadership schedule, or both",
            public="Post publicly instead of only showing it to you",
        )
        @app_commands.choices(audience=SCHEDULE_AUDIENCE_CHOICES)
        async def schedule(
            interaction: discord.Interaction,
            audience: Optional[app_commands.Choice[str]] = None,
            public: bool = False,
        ) -> None:
            assert self.data is not None
            selected = audience.value if audience else "clan"
            member = interaction.user if isinstance(interaction.user, discord.Member) else None
            if selected in {"leadership", "both"} and not self._is_leadership(member):
                await interaction.response.send_message("Leadership schedule is restricted to Leader/Superior.", ephemeral=True)
                return
            if public and not self._is_leadership(member):
                await interaction.response.send_message("Only leadership can post the schedule publicly.", ephemeral=True)
                return

            today = datetime.now(self.settings.timezone).date()
            try:
                if selected == "both":
                    clan_items = await self.data.schedule_for_date(today, audience="clan")
                    leadership_items = await self.data.schedule_for_date(today, audience="leadership")
                    clan_text = format_schedule(today, clan_items)
                    leadership_text = format_schedule(today, leadership_items).replace(
                        "## OZY Schedule", "## OZY Leadership Schedule", 1
                    )
                    body = clan_text + "\n\n" + leadership_text
                else:
                    items = await self.data.schedule_for_date(today, audience=selected)
                    body = format_schedule(today, items)
                    if selected == "leadership":
                        body = body.replace("## OZY Schedule", "## OZY Leadership Schedule", 1)
            except DataUnavailable as exc:
                await interaction.response.send_message(f"Schedule unavailable: {exc}", ephemeral=True)
                return

            await interaction.response.send_message(
                body,
                ephemeral=not public,
                allowed_mentions=discord.AllowedMentions.none(),
            )

        @self.tree.command(name="schedule-post", description="Leadership: post today's clan or leadership schedule")
        @app_commands.describe(audience="Which configured schedule channel to publish")
        @app_commands.choices(audience=SCHEDULE_AUDIENCE_CHOICES)
        async def schedule_post(
            interaction: discord.Interaction,
            audience: Optional[app_commands.Choice[str]] = None,
        ) -> None:
            if not await self._require_leadership(interaction):
                return
            selected = audience.value if audience else "clan"
            today = datetime.now(self.settings.timezone).date()
            audiences = ("clan", "leadership") if selected == "both" else (selected,)
            posted_labels = []
            for item_audience in audiences:
                posted = await self._post_schedule(
                    today,
                    force=True,
                    actor=str(interaction.user),
                    audience=item_audience,
                )
                if posted:
                    posted_labels.append(item_audience)

            if posted_labels:
                await interaction.response.send_message(
                    "Posted: " + ", ".join(posted_labels) + ".",
                    ephemeral=True,
                )
            else:
                await interaction.response.send_message(
                    "Nothing was posted. Check the configured schedule channel(s) and today's website schedule data.",
                    ephemeral=True,
                )

        @self.tree.command(name="calendar", description="Show the next 30 days of tournament starts")
        @app_commands.describe(public="Post publicly instead of only showing it to you")
        async def calendar(interaction: discord.Interaction, public: bool = False) -> None:
            if public and not self._is_leadership(interaction.user if isinstance(interaction.user, discord.Member) else None):
                await interaction.response.send_message("Only leadership can post the calendar publicly.", ephemeral=True)
                return
            if self.calendar_client is None:
                await interaction.response.send_message("Tournament calendar integration is unavailable.", ephemeral=True)
                return
            await interaction.response.defer(ephemeral=not public, thinking=True)
            try:
                if self.calendar_client.snapshot is None:
                    await self.calendar_client.refresh(force=True)
                snapshot = self.calendar_client.snapshot
                if snapshot is None:
                    raise CalendarSourceError("No calendar snapshot is available")
                chunks = build_calendar_chunks(
                    snapshot,
                    start_date=datetime.now(timezone.utc).date(),
                    days=self.settings.calendar_days,
                    timezone_info=timezone.utc,
                )
                for chunk in chunks:
                    await interaction.followup.send(
                        chunk,
                        ephemeral=not public,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
            except CalendarSourceError as exc:
                await interaction.followup.send(f"Calendar unavailable: {exc}", ephemeral=True)

        @self.tree.command(name="today", description="Show today's tournament activity")
        @app_commands.describe(public="Post publicly instead of only showing it to you")
        async def today(interaction: discord.Interaction, public: bool = False) -> None:
            if public and not self._is_leadership(interaction.user if isinstance(interaction.user, discord.Member) else None):
                await interaction.response.send_message("Only leadership can post today's schedule publicly.", ephemeral=True)
                return
            if self.calendar_client is None:
                await interaction.response.send_message("Tournament calendar integration is unavailable.", ephemeral=True)
                return
            await interaction.response.defer(ephemeral=not public, thinking=True)
            try:
                if self.calendar_client.snapshot is None:
                    await self.calendar_client.refresh(force=True)
                snapshot = self.calendar_client.snapshot
                if snapshot is None:
                    raise CalendarSourceError("No calendar snapshot is available")
                target_date = datetime.now(timezone.utc).date()
                chunks = build_today_chunks(snapshot, target_date=target_date, timezone_info=timezone.utc)
                for chunk in chunks:
                    await interaction.followup.send(
                        chunk,
                        ephemeral=not public,
                        allowed_mentions=discord.AllowedMentions.none(),
                    )
            except CalendarSourceError as exc:
                await interaction.followup.send(f"Today's calendar is unavailable: {exc}", ephemeral=True)

        @self.tree.command(name="time", description="Show today's OZY events in your chosen local timezone")
        @app_commands.describe(zone="Timezone to convert the current game-day schedule to")
        @app_commands.choices(zone=TIMEZONE_CHOICES)
        async def time_converter(interaction: discord.Interaction, zone: app_commands.Choice[str]) -> None:
            if self.calendar_client is None:
                await interaction.response.send_message("Tournament calendar integration is unavailable.", ephemeral=True)
                return
            await interaction.response.defer(ephemeral=True, thinking=True)
            try:
                if self.calendar_client.snapshot is None:
                    await self.calendar_client.refresh(force=True)
                snapshot = self.calendar_client.snapshot
                if snapshot is None:
                    raise CalendarSourceError("No calendar snapshot is available")
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

        @self.tree.command(name="calendar-refresh", description="Leadership: refresh the tournament calendar and update Discord")
        async def calendar_refresh(interaction: discord.Interaction) -> None:
            if not await self._require_leadership(interaction):
                return
            await interaction.response.defer(ephemeral=True, thinking=True)
            changed, status = await self._refresh_calendar(
                force=True,
                actor=str(interaction.user),
                refresh_akurier=True,
            )
            if status != "ok":
                await interaction.followup.send(f"Refresh failed: {status}", ephemeral=True)
            else:
                await interaction.followup.send(
                    "Tournament calendar refreshed. " + ("Discord calendar updated." if changed else "No event changes detected."),
                    ephemeral=True,
                )

        @self.tree.command(name="calendar-status", description="Leadership: show Tournament calendar integration status")
        async def calendar_status(interaction: discord.Interaction) -> None:
            if not await self._require_leadership(interaction):
                return
            snapshot = self.calendar_client.snapshot if self.calendar_client else None
            last_success = self.calendar_client.last_success_utc if self.calendar_client else None
            last_error = self.calendar_client.last_error if self.calendar_client else "client unavailable"
            lines = [
                f"Enabled: **{self.settings.calendar_enabled}**",
                f"Realm: **{self.settings.calendar_realm}**",
                "Calendar probes: **00:30, 06:30, 12:30, 18:30 UTC**",
                "Akurier mini-events: **18:00 UTC / R+1, once daily**",
                f"Cached actions: **{len(snapshot.actions) if snapshot else 0}**",
                f"Cached mini tournaments: **{len(snapshot.mini_tournaments) if snapshot else 0}**",
                f"Source last synced: **{self.calendar_client.last_meta_utc.isoformat() if self.calendar_client and self.calendar_client.last_meta_utc else 'unknown'}**",
                f"Last metadata check: **{self.calendar_client.last_meta_checked_utc.isoformat() if self.calendar_client and self.calendar_client.last_meta_checked_utc else 'never'}**",
                f"Last Akurier success: **{self.calendar_client.last_akurier_success_utc.isoformat() if self.calendar_client and self.calendar_client.last_akurier_success_utc else 'never'}**",
                f"Last successful calendar fetch/probe: **{last_success.isoformat() if last_success else 'never'}**",
                f"Last error: **{last_error or 'none'}**",
            ]
            await interaction.response.send_message("\n".join(lines), ephemeral=True)


        @self.tree.command(name="event-create", description="Create and publish an OZY scheduled event")
        async def event_create(interaction: discord.Interaction) -> None:
            if not await self._require_event_creator(interaction):
                return
            if interaction.guild is None:
                await interaction.response.send_message("This command only works inside the OZY server.", ephemeral=True)
                return
            # Open the native form immediately. Discord now supports ChannelSelect
            # components inside modals, so no preliminary selector message is needed.
            await interaction.response.send_modal(EventSetupModal(self, creator_id=interaction.user.id))

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
