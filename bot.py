from __future__ import annotations

import asyncio
import io
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
from ozy.constants import PROFILE_LANGUAGES
from ozy.onboarding_profile import extract_onboarding_profile
from ozy.discord_ui import (
    AnnouncementModal,
    EventScheduleView,
    EventSetupModal,
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
        """Allow leadership or members who completed native Discord Onboarding."""
        if member is None:
            return False
        if self._is_leadership(member):
            return True
        if self.settings.verified_role_id:
            return any(role.id == self.settings.verified_role_id for role in member.roles)
        return True

    async def _require_event_creator(self, interaction: discord.Interaction) -> bool:
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        if self._can_create_events(member):
            return True
        message = "Complete the OZY server onboarding before creating events."
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


    def _member_from_interaction(self, interaction: discord.Interaction) -> discord.Member | None:
        if isinstance(interaction.user, discord.Member):
            if interaction.user.guild.id == self.settings.server_id:
                return interaction.user
        guild = self.get_guild(self.settings.server_id)
        if guild is None:
            return None
        return guild.get_member(interaction.user.id)

    def _sync_profile_from_onboarding_roles(self, member: discord.Member) -> str:
        """Mirror native Discord Onboarding metadata roles into OZY state.

        Discord owns the member-facing language/G/M/S choices. OZY state keeps a
        durable structured mirror for reports, JSON exports and APIs.
        Access is not derived from these metadata roles.
        """
        selection = extract_onboarding_profile(
            ((role.id, role.name) for role in member.roles),
            self.settings.language_role_map,
        )
        if not selection.complete:
            missing: list[str] = []
            if selection.preferred_language is None:
                missing.append("language")
            if selection.guardsmen_level is None:
                missing.append("G")
            if selection.monsters_level is None:
                missing.append("M")
            if selection.specialists_level is None:
                missing.append("S")
            detail = ", ".join(selection.issues or tuple(f"missing {item}" for item in missing))
            return f"onboarding profile incomplete ({detail or 'unknown'})"

        existing = self.state.get_member_profile(member.id)
        desired = (
            selection.preferred_language,
            selection.guardsmen_level,
            selection.monsters_level,
            selection.specialists_level,
        )
        if existing is not None:
            current = (
                existing.preferred_language,
                existing.guardsmen_level,
                existing.monsters_level,
                existing.specialists_level,
            )
            if current == desired:
                return (
                    f"{selection.preferred_language} / G{selection.guardsmen_level} / "
                    f"M{selection.monsters_level} / S{selection.specialists_level}"
                )

        self.state.set_member_profile_details(
            member.id,
            preferred_language=selection.preferred_language,
            guardsmen_level=selection.guardsmen_level,
            monsters_level=selection.monsters_level,
            specialists_level=selection.specialists_level,
            source="discord-onboarding",
            game_name=(existing.game_name if existing else None),
            game_user_id=(existing.game_user_id if existing else None),
        )
        return (
            f"{selection.preferred_language} / G{selection.guardsmen_level} / "
            f"M{selection.monsters_level} / S{selection.specialists_level}"
        )

    async def _set_member_troop_roles(
        self,
        member: discord.Member,
        guardsmen: int,
        monsters: int,
        specialists: int,
    ) -> str:
        desired_names = {f"G{guardsmen}", f"M{monsters}", f"S{specialists}"}
        metadata_roles = [
            role for role in member.roles
            if re.fullmatch(r"[GMS][1-9]", role.name.strip(), flags=re.IGNORECASE)
        ]
        by_name = {role.name.upper(): role for role in member.guild.roles}
        targets = [by_name.get(name) for name in sorted(desired_names)]
        missing = [name for name, role in zip(sorted(desired_names), targets) if role is None]
        if missing:
            return "Missing Discord metadata roles: " + ", ".join(missing)

        remove_roles = [role for role in metadata_roles if role.name.upper() not in desired_names]
        add_roles = [role for role in targets if role is not None and role not in member.roles]
        try:
            if remove_roles:
                await member.remove_roles(*remove_roles, reason="OZY troop profile update")
            if add_roles:
                await member.add_roles(*add_roles, reason="OZY troop profile update")
        except discord.HTTPException as exc:
            return f"Discord role update failed: {exc}"

        # Update state directly because the member_update gateway event may arrive
        # after this interaction response.
        profile = self.state.get_member_profile(member.id)
        language = profile.preferred_language if profile else None
        if not language:
            selection = extract_onboarding_profile(
                ((role.id, role.name) for role in member.roles),
                self.settings.language_role_map,
            )
            language = selection.preferred_language
        if language:
            self.state.set_member_profile_details(
                member.id,
                preferred_language=language,
                guardsmen_level=guardsmen,
                monsters_level=monsters,
                specialists_level=specialists,
                source="leadership-troop-update",
                game_name=(profile.game_name if profile else None),
                game_user_id=(profile.game_user_id if profile else None),
            )
        return "profile updated"

    async def _build_members_json(self, guild: discord.Guild) -> dict:
        """Export current Discord members with native onboarding profile data.

        The website roster is intentionally not consulted. A game name is profile
        information only and can be duplicated or changed freely.
        """
        members: list[dict] = []
        for member in guild.members:
            if member.bot:
                continue
            self._sync_profile_from_onboarding_roles(member)
            profile = self.state.get_member_profile(member.id)
            game_name = self.state.get_link(member.id)
            members.append({
                "discord_user_id": member.id,
                "discord_username": str(member),
                "discord_display_name": member.display_name,
                "game_name": game_name,
                "preferred_language": (profile.preferred_language if profile else None),
                "guardsmen_level": (profile.guardsmen_level if profile else None),
                "monsters_level": (profile.monsters_level if profile else None),
                "specialists_level": (profile.specialists_level if profile else None),
            })

        members.sort(key=lambda item: ((item["game_name"] or item["discord_display_name"] or "").casefold()))
        return {
            "schema_version": 2,
            "generated_at_utc": datetime.now(timezone.utc).isoformat(),
            "guild_id": guild.id,
            "member_count": len(members),
            "members": members,
        }

    async def _submit_game_name(
        self,
        interaction: discord.Interaction,
        *,
        game_name: str,
    ) -> None:
        """Store the member's game name without roster validation or access logic."""
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        if member is None or member.guild.id != self.settings.server_id:
            await interaction.response.send_message("This setup only works inside the OZY server.", ephemeral=True)
            return

        entered = game_name.strip()
        if not entered:
            await interaction.response.send_message("Enter your current Total Battle name.", ephemeral=True)
            return

        previous = self.state.get_link(member.id)
        self.state.set_plain_game_name(member.id, entered, "self-game-name")
        self._sync_profile_from_onboarding_roles(member)

        await self._audit(
            "Member game name",
            str(member),
            f"Previous: {previous or 'none'}\nNew: {entered}",
        )
        await interaction.response.send_message(
            f"Game name saved as **{entered}**. No roster check is performed.",
            ephemeral=True,
        )

    async def _resolve_member_game_name(self, member: discord.Member) -> str | None:
        """Return the member-maintained game-name profile field."""
        return self.state.get_link(member.id)

    # Normal member access is owned entirely by Discord Community Onboarding.
    # OZY Admin never checks the Total Battle roster to grant/revoke access.

    # ------------------------------------------------------------------
    # Welcome / goodbye only
    # ------------------------------------------------------------------
    @staticmethod
    def _is_start_here_category_name(name: str) -> bool:
        # The live category is branded (for example: "❖── 👋 START HERE").
        # Match the semantic suffix instead of requiring the raw Discord name
        # to be exactly "START HERE".
        return str(name or "").strip().casefold().endswith("start here")

    def _find_start_here_text_channel(self, guild: discord.Guild, name: str) -> discord.TextChannel | None:
        wanted = name.casefold().lstrip("#")
        matches: list[discord.TextChannel] = []
        for channel in guild.text_channels:
            if channel.name.casefold() != wanted:
                continue
            category = channel.category
            if category and self._is_start_here_category_name(category.name):
                matches.append(channel)

        # Do not silently choose between duplicates. A single exact channel name
        # under the branded START HERE category is the intended configuration.
        return matches[0] if len(matches) == 1 else None

    async def _process_new_member(self, member: discord.Member) -> None:
        """Post a themed hello only. Discord owns onboarding and access."""
        if member.guild.id != self.settings.server_id or member.bot:
            return

        # Keep profile metadata mirrored for admin/reporting features only.
        self._sync_profile_from_onboarding_roles(member)

        channel: discord.TextChannel | None = None
        if self.settings.welcome_channel_id:
            candidate = self.get_channel(self.settings.welcome_channel_id)
            if isinstance(candidate, discord.TextChannel):
                channel = candidate
        if channel is None:
            channel = self._find_start_here_text_channel(member.guild, "welcome")

        if channel is not None:
            embed = discord.Embed(
                title="🚂 ALL ABOARD THE CRAZY TRAIN!",
                description=(
                    f"Welcome {member.mention} to **[OZY] Odyssey**! 🤘\n\n"
                    "The gates are open, the bats are awake, and the Madhouse just got louder. "
                    "Grab a seat on the Crazy Train and make some noise. 🦇"
                ),
                color=0xF59E0B,
            )
            await channel.send(
                content=member.mention,
                embed=embed,
                allowed_mentions=discord.AllowedMentions(users=True, roles=False, everyone=False),
            )

        self.state.mark_welcomed(member.id)
        await self._audit("Member joined", str(member), f"Discord ID: {member.id}")

    async def on_member_join(self, member: discord.Member) -> None:
        if member.guild.id != self.settings.server_id or member.bot:
            return
        if member.pending:
            log.info("Member %s joined pending screening; welcome deferred", member.id)
            return
        await self._process_new_member(member)

    async def on_member_remove(self, member: discord.Member) -> None:
        """Post a themed goodbye only. No identity/access logic runs on leave."""
        if member.guild.id != self.settings.server_id or member.bot:
            return

        self.state.clear_welcomed(member.id)
        self.state.clear_away(member.id)

        channel = self._find_start_here_text_channel(member.guild, "goodbye")

        if channel is not None:
            display_name = discord.utils.escape_markdown(member.display_name)
            embed = discord.Embed(
                title="🦇 Another Bat Leaves the Belfry",
                description=(
                    f"**{display_name}** has left **[OZY] Odyssey**.\n\n"
                    "The bats raise a wing in salute. Safe travels beyond the gates. 🤘"
                ),
                color=0x6B21A8,
            )
            await channel.send(embed=embed, allowed_mentions=discord.AllowedMentions.none())

        await self._audit("Member left Discord", str(member), f"Discord ID: {member.id}")

    async def on_member_update(self, before: discord.Member, after: discord.Member) -> None:
        if after.guild.id != self.settings.server_id or after.bot:
            return
        if before.pending and not after.pending:
            await self._process_new_member(after)
        if {role.id for role in before.roles} != {role.id for role in after.roles}:
            self._sync_profile_from_onboarding_roles(after)

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

        @self.tree.command(name="game-name", description="Set or update your Total Battle game name")
        @app_commands.describe(game_name="Your current Total Battle name")
        async def game_name_command(interaction: discord.Interaction, game_name: str) -> None:
            if not isinstance(interaction.user, discord.Member):
                await interaction.response.send_message("This command only works inside the OZY server.", ephemeral=True)
                return
            await self._submit_game_name(interaction, game_name=game_name)

        @self.tree.command(name="member-name", description="Leadership: set/correct a member's Total Battle name")
        @app_commands.describe(member="Discord member", game_name="Current Total Battle name")
        async def member_name(interaction: discord.Interaction, member: discord.Member, game_name: str) -> None:
            if not await self._require_leadership(interaction):
                return
            entered = game_name.strip()
            if not entered:
                await interaction.response.send_message("Game name cannot be blank.", ephemeral=True)
                return
            previous = self.state.get_link(member.id)
            self.state.set_plain_game_name(member.id, entered, f"leadership-name:{interaction.user.id}")
            self._sync_profile_from_onboarding_roles(member)
            await self._audit(
                "Member game name changed",
                str(interaction.user),
                f"Discord member: {member} ({member.id})\nPrevious: {previous or 'none'}\nNew: {entered}",
            )
            await interaction.response.send_message(
                f"{member.mention}'s game name is now **{entered}**.",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )

        @self.tree.command(name="member-troops", description="Leadership: update a member's G/M/S levels")
        @app_commands.describe(member="Discord member", guardsmen="1-9", monsters="1-9", specialists="1-9")
        async def member_troops(
            interaction: discord.Interaction,
            member: discord.Member,
            guardsmen: app_commands.Range[int, 1, 9],
            monsters: app_commands.Range[int, 1, 9],
            specialists: app_commands.Range[int, 1, 9],
        ) -> None:
            if not await self._require_leadership(interaction):
                return
            result = await self._set_member_troop_roles(member, int(guardsmen), int(monsters), int(specialists))
            await self._audit(
                "Member troop levels changed",
                str(interaction.user),
                f"Member: {member} ({member.id})\nG{guardsmen} / M{monsters} / S{specialists}\n{result}",
            )
            await interaction.response.send_message(
                f"Updated {member.mention} to **G{guardsmen} / M{monsters} / S{specialists}**. {result}",
                ephemeral=True,
                allowed_mentions=discord.AllowedMentions.none(),
            )

        @self.tree.command(name="members-json", description="Leadership: export Discord members and troop levels as JSON")
        async def members_json(interaction: discord.Interaction) -> None:
            if not await self._require_leadership(interaction):
                return
            if interaction.guild is None:
                await interaction.response.send_message("Guild unavailable.", ephemeral=True)
                return
            await interaction.response.defer(ephemeral=True, thinking=True)
            payload = await self._build_members_json(interaction.guild)
            raw = json.dumps(payload, indent=2, ensure_ascii=False).encode("utf-8")
            filename = datetime.now(timezone.utc).strftime("ozy_members_%Y%m%d_%H%M%SZ.json")
            await interaction.followup.send(
                file=discord.File(io.BytesIO(raw), filename=filename),
                ephemeral=True,
            )

        @self.tree.command(name="profile", description="Show your OZY onboarding profile")
        async def profile(interaction: discord.Interaction) -> None:
            member = self._member_from_interaction(interaction)
            if member is None:
                await interaction.response.send_message("I could not resolve your OZY server membership.", ephemeral=True)
                return
            sync_result = self._sync_profile_from_onboarding_roles(member)
            profile_data = self.state.get_member_profile(member.id)
            if not profile_data or not profile_data.profile_complete:
                await interaction.response.send_message(
                    "Your native Discord onboarding profile is incomplete. Open **Channels & Roles** and choose "
                    "your language plus G/M/S levels, then run `/profile` again.\n\n"
                    f"Current read: {sync_result}",
                    ephemeral=True,
                )
                return
            language_label = dict(PROFILE_LANGUAGES).get(
                profile_data.preferred_language or "", profile_data.preferred_language or "Unknown"
            )
            await interaction.response.send_message(
                f"**OZY profile**\n"
                f"Language: **{language_label} ({profile_data.preferred_language})**\n"
                f"Troops: **G{profile_data.guardsmen_level} / M{profile_data.monsters_level} / S{profile_data.specialists_level}**\n\n"
                "To change these, update your answers in Discord **Channels & Roles**.",
                ephemeral=True,
            )

        @self.tree.command(name="member", description="Show OZY profile for yourself or a member")
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

            self._sync_profile_from_onboarding_roles(target)
            profile = self.state.get_member_profile(target.id)
            game_name = self.state.get_link(target.id)
            embed = discord.Embed(title=(game_name or target.display_name), color=0xF59E0B)
            embed.add_field(name="Discord", value=target.mention, inline=False)
            embed.add_field(name="Game name", value=(game_name or "Not set"), inline=False)
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
                        "Your Discord account is not linked to a roster player yet. Use `/game-name` with your current Total Battle name.",
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
