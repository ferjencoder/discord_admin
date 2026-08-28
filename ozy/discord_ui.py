from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, time as dt_time, timedelta, timezone

import discord

from ozy.constants import PROFILE_LANGUAGES, PROFILE_LEVELS
from ozy.event_calendar import reset_label

log = logging.getLogger("ozy-admin.ui")

_RESET_INPUT_RE = re.compile(r"^\s*R?\s*([+-]?)\s*(\d{1,2}(?:\.\d{1,2})?)\s*$", re.IGNORECASE)


def _parse_event_date(value: str, *, now_utc: datetime | None = None):
    now_utc = now_utc or datetime.now(timezone.utc)
    text = value.strip().casefold()
    if text == "today":
        return now_utc.date()
    if text == "tomorrow":
        return now_utc.date() + timedelta(days=1)
    for fmt in ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"):
        try:
            return datetime.strptime(value.strip(), fmt).date()
        except ValueError:
            pass
    raise ValueError("Date must be `today`, `tomorrow`, YYYY-MM-DD, DD.MM.YYYY, or DD/MM/YYYY.")


def _event_datetime_from_reset(date_value: str, reset_value: str, *, now_utc: datetime | None = None) -> tuple[datetime, str]:
    target_date = _parse_event_date(date_value, now_utc=now_utc)
    match = _RESET_INPUT_RE.match(reset_value)
    if not match:
        raise ValueError("Reset time must look like `R+4`, `R-3`, or `R+1.5`.")

    sign_text, number_text = match.groups()
    value = float(number_text)
    if sign_text == "-":
        value = -value
    if value < -11 or value > 12:
        raise ValueError("Reset time must be between R-11 and R+12.")

    raw_minutes = value * 60
    rounded_minutes = round(raw_minutes)
    if abs(raw_minutes - rounded_minutes) > 0.001 or rounded_minutes % 15 != 0:
        raise ValueError("Reset time must use 15-minute steps, for example R+1, R+1.5, or R-3.25.")

    # The entered date is the date of R+0 itself. Build the reset instant first
    # and then apply the offset so R+10 on 24 Aug correctly becomes 25 Aug 03:00
    # UTC, while R-3 remains 24 Aug 14:00 UTC.
    reset_start = datetime.combine(target_date, dt_time(17, 0), tzinfo=timezone.utc)
    start = reset_start + timedelta(minutes=rounded_minutes)
    return start, reset_label(start)



@dataclass(slots=True)
class EventDraft:
    creator_id: int
    category_id: int
    event_channel_id: int
    publish_channel_id: int
    name: str
    notes: str


def _selected_channel_id(select: discord.ui.ChannelSelect) -> int | None:
    values = list(select.values)
    if not values:
        return None
    return int(values[0].id)


def _resolve_server_channel(guild: discord.Guild, channel_id: int):
    return guild.get_channel_or_thread(channel_id)


def _channel_is_visible_to(channel, member: discord.Member) -> bool:
    try:
        return bool(channel.permissions_for(member).view_channel)
    except (AttributeError, TypeError):
        return False


def _channel_display(channel) -> str:
    mention = getattr(channel, 'mention', None)
    if mention:
        return mention
    name = getattr(channel, 'name', None)
    return f"#{name}" if name else str(channel)


EVENT_CHANNEL_TYPES = [
    discord.ChannelType.text,
    discord.ChannelType.voice,
    discord.ChannelType.news,
    discord.ChannelType.public_thread,
    discord.ChannelType.private_thread,
    discord.ChannelType.news_thread,
    discord.ChannelType.stage_voice,
    discord.ChannelType.forum,
    discord.ChannelType.media,
]

PUBLISH_CHANNEL_TYPES = [
    discord.ChannelType.text,
    discord.ChannelType.news,
    discord.ChannelType.public_thread,
    discord.ChannelType.private_thread,
    discord.ChannelType.news_thread,
]


class EventSetupModal(discord.ui.Modal):
    """First step of the event wizard: audience/location/content."""

    def __init__(self, bot: "OZYAdminBot", *, creator_id: int):
        super().__init__(title="Create OZY Event", timeout=300)
        self.bot = bot
        self.creator_id = creator_id

        self.category_select = discord.ui.ChannelSelect(
            channel_types=[discord.ChannelType.category],
            placeholder="Select category",
            min_values=1,
            max_values=1,
            required=True,
        )
        self.event_channel_select = discord.ui.ChannelSelect(
            channel_types=EVENT_CHANNEL_TYPES,
            placeholder="Select event channel / location",
            min_values=1,
            max_values=1,
            required=True,
        )
        self.publish_channel_select = discord.ui.ChannelSelect(
            channel_types=PUBLISH_CHANNEL_TYPES,
            placeholder="Select where OZY Admin should publish it",
            min_values=1,
            max_values=1,
            required=True,
        )
        self.event_name = discord.ui.TextInput(
            placeholder="Leadership Meeting",
            max_length=100,
        )
        self.notes = discord.ui.TextInput(
            placeholder="Previous notes, agenda, topics to discuss...",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=800,
        )

        self.add_item(discord.ui.Label(text="Category", component=self.category_select))
        self.add_item(discord.ui.Label(text="Event channel / location", component=self.event_channel_select))
        self.add_item(discord.ui.Label(text="Publish event in", component=self.publish_channel_select))
        self.add_item(discord.ui.Label(text="Event name", component=self.event_name))
        self.add_item(discord.ui.Label(text="Description / agenda / notes", component=self.notes))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.creator_id:
            await interaction.response.send_message("This event builder belongs to another member.", ephemeral=True)
            return
        guild = interaction.guild
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        if guild is None or member is None:
            await interaction.response.send_message("This command only works inside the OZY server.", ephemeral=True)
            return

        category_id = _selected_channel_id(self.category_select)
        event_channel_id = _selected_channel_id(self.event_channel_select)
        publish_channel_id = _selected_channel_id(self.publish_channel_select)
        if not category_id or not event_channel_id or not publish_channel_id:
            await interaction.response.send_message("Select a category, event channel, and publish channel.", ephemeral=True)
            return

        category = guild.get_channel(category_id)
        event_channel = _resolve_server_channel(guild, event_channel_id)
        publish_channel = _resolve_server_channel(guild, publish_channel_id)
        if not isinstance(category, discord.CategoryChannel):
            await interaction.response.send_message("The selected category is no longer available.", ephemeral=True)
            return
        if not _channel_is_visible_to(category, member):
            await interaction.response.send_message("You cannot use that category because you do not have access to it.", ephemeral=True)
            return
        if event_channel is None:
            await interaction.response.send_message("The selected event channel is no longer available.", ephemeral=True)
            return
        if publish_channel is None or not hasattr(publish_channel, "send"):
            await interaction.response.send_message("Choose a text channel or thread where OZY Admin can publish the event.", ephemeral=True)
            return

        # Members can only target places they themselves can see. They do not need
        # Send Messages in a read-only announcement channel because the bot performs
        # the publication on their behalf.
        for label, channel in (("event", event_channel), ("publish", publish_channel)):
            if not _channel_is_visible_to(channel, member):
                await interaction.response.send_message(
                    f"You cannot use that {label} channel because you do not have access to it.",
                    ephemeral=True,
                )
                return

        me = guild.me
        if me is None or not _channel_is_visible_to(publish_channel, me):
            await interaction.response.send_message("OZY Admin cannot view the selected publish channel.", ephemeral=True)
            return

        name = str(self.event_name.value).strip()
        if not name:
            await interaction.response.send_message("Event name is required.", ephemeral=True)
            return

        draft = EventDraft(
            creator_id=self.creator_id,
            category_id=category.id,
            event_channel_id=event_channel_id,
            publish_channel_id=publish_channel_id,
            name=name,
            notes=str(self.notes.value or "").strip(),
        )
        view = EventScheduleView(self.bot, draft)
        await interaction.response.send_message(
            "Event details saved. Click **Set date & time** to finish.\n"
            f"Category: **{category.name}**\n"
            f"Event channel: {_channel_display(event_channel)}\n"
            f"Publish in: {_channel_display(publish_channel)}",
            view=view,
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        log.exception("Event setup modal failed", exc_info=error)
        if interaction.response.is_done():
            await interaction.followup.send("Event setup failed. Check the admin-bot logs.", ephemeral=True)
        else:
            await interaction.response.send_message("Event setup failed. Check the admin-bot logs.", ephemeral=True)


class EventScheduleModal(discord.ui.Modal):
    """Second step: reset date/time and duration, then create + publish."""

    def __init__(self, bot: "OZYAdminBot", draft: EventDraft):
        super().__init__(title="Schedule OZY Event", timeout=300)
        self.bot = bot
        self.draft = draft

        self.event_date = discord.ui.TextInput(
            placeholder="tomorrow or 2026-08-24",
            max_length=20,
        )
        self.reset_time = discord.ui.TextInput(
            placeholder="R+4",
            max_length=10,
        )
        self.duration = discord.ui.TextInput(
            default="60",
            required=True,
            max_length=4,
        )
        self.audience = discord.ui.Select(
            placeholder="Who should see this event?",
            min_values=1,
            max_values=1,
            options=[
                discord.SelectOption(label="Clan", value="clan", description="Visible to all verified OZY members"),
                discord.SelectOption(label="Leadership", value="leadership", description="Leader / Superior schedule only"),
            ],
        )
        self.add_item(discord.ui.Label(
            text="R+0 reset date",
            description="The calendar date on which this game day starts at R+0.",
            component=self.event_date,
        ))
        self.add_item(discord.ui.Label(text="Game reset time", component=self.reset_time))
        self.add_item(discord.ui.Label(text="Duration in minutes", component=self.duration))
        self.add_item(discord.ui.Label(text="Audience", component=self.audience))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.draft.creator_id:
            await interaction.response.send_message("This event builder belongs to another member.", ephemeral=True)
            return
        guild = interaction.guild
        member = interaction.user if isinstance(interaction.user, discord.Member) else None
        if guild is None or member is None:
            await interaction.response.send_message("This command only works inside the OZY server.", ephemeral=True)
            return
        if not await self.bot._require_event_creator(interaction):
            return

        audience = (self.audience.values[0] if self.audience.values else "clan").strip().casefold()
        if audience == "leadership" and not self.bot._is_leadership(member):
            await interaction.response.send_message(
                "Only Leader/Superior can create Leadership schedule events.",
                ephemeral=True,
            )
            return

        category = guild.get_channel(self.draft.category_id)
        event_channel = _resolve_server_channel(guild, self.draft.event_channel_id)
        publish_channel = _resolve_server_channel(guild, self.draft.publish_channel_id)
        if not isinstance(category, discord.CategoryChannel) or event_channel is None or publish_channel is None:
            await interaction.response.send_message("A selected channel was removed. Run `/event-create` again.", ephemeral=True)
            return
        if not hasattr(publish_channel, "send"):
            await interaction.response.send_message("The selected publish destination is not messageable.", ephemeral=True)
            return
        if not _channel_is_visible_to(event_channel, member) or not _channel_is_visible_to(publish_channel, member):
            await interaction.response.send_message("Your access to one of the selected channels has changed.", ephemeral=True)
            return

        try:
            start_time, reset_text = _event_datetime_from_reset(str(self.event_date.value), str(self.reset_time.value))
            duration_minutes = int(str(self.duration.value).strip())
            if duration_minutes < 15 or duration_minutes > 720:
                raise ValueError("Duration must be between 15 and 720 minutes.")
            if start_time <= datetime.now(timezone.utc) + timedelta(minutes=1):
                raise ValueError("The event start must be in the future.")
        except ValueError as exc:
            await interaction.response.send_message(str(exc), ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        description = self.draft.notes[:1000] or None
        event_kwargs = dict(
            name=self.draft.name,
            start_time=start_time,
            end_time=start_time + timedelta(minutes=duration_minutes),
            privacy_level=discord.PrivacyLevel.guild_only,
            description=description,
            reason=f"OZY event created by {interaction.user}",
        )
        if isinstance(event_channel, (discord.VoiceChannel, discord.StageChannel)):
            event_kwargs["channel"] = event_channel
        else:
            # Discord Scheduled Events only attach natively to voice/stage channels.
            # Text/forum/thread/media selections are represented as an external
            # location while still linking the selected channel in our announcement.
            event_kwargs["location"] = f"#{getattr(event_channel, 'name', 'Discord channel')}"[:100]

        try:
            event = await guild.create_scheduled_event(**event_kwargs)
        except discord.Forbidden:
            await interaction.followup.send(
                "I cannot create the Discord Scheduled Event. OZY Admin needs **Create Events / Manage Events** and access to the selected location.",
                ephemeral=True,
            )
            return
        except (discord.HTTPException, TypeError, ValueError) as exc:
            await interaction.followup.send(f"Discord could not create the event: {exc}", ephemeral=True)
            return

        event_url = event.url
        start_ts = int(start_time.timestamp())
        embed = discord.Embed(
            title=self.draft.name,
            description=self.draft.notes or "No agenda or notes provided.",
            color=0x5865F2,
        )
        embed.add_field(name="Game time", value=reset_text, inline=True)
        embed.add_field(name="Starts", value=f"<t:{start_ts}:F>\n<t:{start_ts}:R>", inline=True)
        embed.add_field(name="Duration", value=f"{duration_minutes} min", inline=True)
        embed.add_field(name="Category", value=category.name, inline=True)
        embed.add_field(name="Event channel", value=_channel_display(event_channel), inline=True)
        embed.add_field(name="Audience", value="Leadership" if audience == "leadership" else "Clan", inline=True)
        embed.add_field(name="Created by", value=interaction.user.mention, inline=True)
        embed.set_footer(text="OZY Event")

        link_view = discord.ui.View(timeout=None)
        link_view.add_item(discord.ui.Button(label="Open Discord Event", style=discord.ButtonStyle.link, url=event_url))

        published = None
        publish_error = None
        try:
            published = await publish_channel.send(
                embed=embed,
                view=link_view,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except discord.Forbidden:
            publish_error = "I cannot post in the selected publish channel."
        except discord.HTTPException as exc:
            publish_error = f"Publishing failed: {exc}"

        # The website is the canonical schedule store. Persist the Discord event
        # even if its announcement could not be posted, so schedule data is not lost.
        schedule_sync_error = None
        if self.bot.data is not None:
            payload = {
                "id": str(event.id),
                "discord_event_id": str(event.id),
                "guild_id": str(guild.id),
                "title": self.draft.name,
                "description": self.draft.notes,
                "audience": audience,
                "start_utc": start_time.isoformat().replace("+00:00", "Z"),
                "end_utc": (start_time + timedelta(minutes=duration_minutes)).isoformat().replace("+00:00", "Z"),
                "duration_minutes": duration_minutes,
                "reset_label": reset_text,
                "reset_date": _parse_event_date(str(self.event_date.value)).isoformat(),
                "category_id": str(category.id),
                "category_name": category.name,
                "event_channel_id": str(event_channel.id),
                "event_channel_name": getattr(event_channel, "name", ""),
                "event_channel_type": str(getattr(event_channel, "type", "")),
                "publish_channel_id": str(publish_channel.id),
                "publish_channel_name": getattr(publish_channel, "name", ""),
                "published_message_id": str(published.id) if published is not None else None,
                "discord_event_url": event_url,
                "created_by_discord_id": str(interaction.user.id),
                "created_by_name": interaction.user.display_name,
            }
            try:
                await self.bot.data.upsert_schedule_event(payload)
            except DataUnavailable as exc:
                schedule_sync_error = str(exc)
                log.error("Could not persist Discord event %s to OZY website schedule: %s", event.id, exc)
        else:
            schedule_sync_error = "data provider unavailable"

        status_lines = [
            "Event created.",
            f"**{self.draft.name}** - **{reset_text}**",
            f"Audience: **{'Leadership' if audience == 'leadership' else 'Clan'}**",
        ]
        if published is not None:
            status_lines.append(f"Published in {_channel_display(publish_channel)}")
        elif publish_error:
            status_lines.append(f"Warning: {publish_error}")
        if schedule_sync_error:
            status_lines.append(f"Warning: website schedule sync failed: {schedule_sync_error}")
        else:
            status_lines.append("Website schedule: synced")
        status_lines.append(event_url)
        await interaction.followup.send(
            "\n".join(status_lines),
            ephemeral=True,
            allowed_mentions=discord.AllowedMentions.none(),
        )

        await self.bot._audit(
            "Discord event created",
            str(interaction.user),
            f"{self.draft.name}\n"
            f"Audience: {audience}\n"
            f"Category: {category.name}\n"
            f"Event channel: #{getattr(event_channel, 'name', event_channel.id)}\n"
            f"Published: {('#' + getattr(publish_channel, 'name', str(publish_channel.id)) + ' (' + str(published.id) + ')') if published is not None else 'failed'}\n"
            f"Website schedule: {'failed - ' + schedule_sync_error if schedule_sync_error else 'synced'}\n"
            f"Start: {start_time.isoformat()} ({reset_text})",
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        log.exception("Event schedule modal failed", exc_info=error)
        if interaction.response.is_done():
            await interaction.followup.send("Event creation failed. Check the admin-bot logs.", ephemeral=True)
        else:
            await interaction.response.send_message("Event creation failed. Check the admin-bot logs.", ephemeral=True)


class EventScheduleButton(discord.ui.Button):
    def __init__(self, parent_view: "EventScheduleView"):
        super().__init__(label="Set date & time", style=discord.ButtonStyle.primary)
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.parent_view.draft.creator_id:
            await interaction.response.send_message("This event builder belongs to another member.", ephemeral=True)
            return
        await interaction.response.send_modal(EventScheduleModal(self.parent_view.bot, self.parent_view.draft))


class EventWizardCancelButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Cancel", style=discord.ButtonStyle.secondary)

    async def callback(self, interaction: discord.Interaction) -> None:
        await interaction.response.edit_message(content="Event creation cancelled.", view=None)


class EventScheduleView(discord.ui.View):
    def __init__(self, bot: "OZYAdminBot", draft: EventDraft):
        super().__init__(timeout=300)
        self.bot = bot
        self.draft = draft
        self.add_item(EventScheduleButton(self))
        self.add_item(EventWizardCancelButton())

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.draft.creator_id:
            return True
        await interaction.response.send_message("This event builder belongs to another member.", ephemeral=True)
        return False



class VerificationApproveButton(discord.ui.Button):
    def __init__(self, bot: "OZYAdminBot", target_user_id: int):
        super().__init__(
            label="Approve",
            style=discord.ButtonStyle.success,
            custom_id=f"ozy:verification:approve:{target_user_id}",
        )
        self.bot = bot
        self.target_user_id = target_user_id

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await self.bot._require_leadership(interaction):
            return
        await self.bot._review_verification_request(
            interaction,
            target_user_id=self.target_user_id,
            decision="approved",
            reason="Approved from verification queue",
        )


class VerificationRejectModal(discord.ui.Modal):
    def __init__(self, bot: "OZYAdminBot", target_user_id: int):
        super().__init__(title="Reject roster verification", timeout=300)
        self.bot = bot
        self.target_user_id = target_user_id
        self.reason = discord.ui.TextInput(
            label="Reason",
            placeholder="Example: Wrong Total Battle name - please submit your exact OZY name.",
            style=discord.TextStyle.paragraph,
            required=False,
            max_length=500,
        )
        self.add_item(self.reason)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if not await self.bot._require_leadership(interaction):
            return
        await self.bot._review_verification_request(
            interaction,
            target_user_id=self.target_user_id,
            decision="rejected",
            reason=str(self.reason.value or "").strip() or "Rejected by leadership",
        )


class VerificationRejectButton(discord.ui.Button):
    def __init__(self, bot: "OZYAdminBot", target_user_id: int):
        super().__init__(
            label="Reject",
            style=discord.ButtonStyle.danger,
            custom_id=f"ozy:verification:reject:{target_user_id}",
        )
        self.bot = bot
        self.target_user_id = target_user_id

    async def callback(self, interaction: discord.Interaction) -> None:
        if not await self.bot._require_leadership(interaction):
            return
        await interaction.response.send_modal(VerificationRejectModal(self.bot, self.target_user_id))


class VerificationReviewView(discord.ui.View):
    """Persistent approve/reject controls for one pending roster claim."""

    def __init__(self, bot: "OZYAdminBot", target_user_id: int):
        super().__init__(timeout=None)
        self.bot = bot
        self.target_user_id = target_user_id
        self.add_item(VerificationApproveButton(bot, target_user_id))
        self.add_item(VerificationRejectButton(bot, target_user_id))

class MembershipVerificationModal(discord.ui.Modal):
    """Collect only the claimed Total Battle roster identity.

    Language and G/M/S profile data are deliberately collected after leadership
    approves the Discord -> Total Battle identity link.
    """

    def __init__(
        self,
        bot: "OZYAdminBot",
        *,
        member: discord.Member,
        suggested_name: str | None = None,
    ):
        super().__init__(title="Verify OZY Membership", timeout=300)
        self.bot = bot
        self.member_id = member.id

        # Keep the membership entry modal deliberately conservative.
        # A plain TextInput is supported across Discord modal clients and avoids
        # coupling this critical verification entry point to newer Label layouts.
        self.game_name = discord.ui.TextInput(
            label="Exact Total Battle name",
            placeholder="Exact Total Battle name used in OZY",
            default=(suggested_name or member.display_name)[:100],
            max_length=100,
            required=True,
        )
        self.add_item(self.game_name)

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.member_id:
            await interaction.response.send_message("This verification form belongs to another member.", ephemeral=True)
            return
        await self.bot._submit_membership_verification(
            interaction,
            game_name=str(self.game_name.value),
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        log.exception("Membership verification modal failed", exc_info=error)
        if interaction.response.is_done():
            await interaction.followup.send("Verification failed. Try `/verify` or contact leadership.", ephemeral=True)
        else:
            await interaction.response.send_message("Verification failed. Try `/verify` or contact leadership.", ephemeral=True)


class MembershipVerificationView(discord.ui.View):
    """Persistent entry point for manually entering an exact roster name."""

    def __init__(self, bot: "OZYAdminBot"):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(
        label="Verify OZY membership",
        style=discord.ButtonStyle.primary,
        custom_id="ozy:membership:verify",
    )
    async def verify_button(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        try:
            await self.bot._open_membership_verification(interaction)
        except Exception as exc:
            log.exception("Could not open membership verification modal", exc_info=exc)
            message = "I could not open the verification form. Try `/verify` or contact leadership."
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)


class MembershipVerificationRetryView(discord.ui.View):
    def __init__(self, bot: "OZYAdminBot", member_id: int):
        super().__init__(timeout=300)
        self.bot = bot
        self.member_id = member_id

    @discord.ui.button(label="Enter exact name again", style=discord.ButtonStyle.primary)
    async def retry_button(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if interaction.user.id != self.member_id:
            await interaction.response.send_message("This verification belongs to another member.", ephemeral=True)
            return
        await self.bot._open_membership_verification(interaction)


class RosterSuggestionSelect(discord.ui.Select):
    def __init__(self, bot: "OZYAdminBot", member_id: int, suggestions: list[str]):
        options = [
            discord.SelectOption(label=name[:100], value=name[:100])
            for name in suggestions[:5]
        ]
        super().__init__(
            placeholder="Is one of these your Total Battle name?",
            min_values=1,
            max_values=1,
            options=options,
        )
        self.bot = bot
        self.member_id = member_id

    async def callback(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.member_id:
            await interaction.response.send_message("These roster suggestions belong to another member.", ephemeral=True)
            return
        selected = self.values[0]
        await self.bot._submit_suggested_roster_name(interaction, selected)


class RosterSuggestionView(discord.ui.View):
    """Join-time roster suggestions. Selecting one only creates a pending claim."""

    def __init__(self, bot: "OZYAdminBot", member_id: int, suggestions: list[str]):
        super().__init__(timeout=1800)
        self.bot = bot
        self.member_id = member_id
        if suggestions:
            self.add_item(RosterSuggestionSelect(bot, member_id, suggestions))

    @discord.ui.button(label="Enter a different name", style=discord.ButtonStyle.secondary)
    async def manual_button(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        if interaction.user.id != self.member_id:
            await interaction.response.send_message("This verification belongs to another member.", ephemeral=True)
            return
        await self.bot._open_membership_verification(interaction)


class PostVerificationProfileModal(discord.ui.Modal):
    """Collect preferred language and G/M/S after roster identity approval."""

    def __init__(self, bot: "OZYAdminBot", *, member: discord.Member):
        super().__init__(title="Complete OZY Profile", timeout=300)
        self.bot = bot
        self.member_id = member.id
        profile = bot.state.get_member_profile(member.id)

        language_options = [
            discord.SelectOption(
                label=label,
                value=code,
                default=bool(profile and profile.preferred_language == code),
            )
            for code, label in PROFILE_LANGUAGES
        ]
        self.language = discord.ui.Select(
            placeholder="Preferred language",
            options=language_options,
            min_values=1,
            max_values=1,
            required=True,
        )

        def level_select(prefix: str, current: int | None) -> discord.ui.Select:
            return discord.ui.Select(
                placeholder=f"{prefix} level",
                options=[
                    discord.SelectOption(
                        label=f"{prefix}{level}",
                        value=str(level),
                        default=(current == level),
                    )
                    for level in PROFILE_LEVELS
                ],
                min_values=1,
                max_values=1,
                required=True,
            )

        self.guardsmen = level_select("G", profile.guardsmen_level if profile else None)
        self.monsters = level_select("M", profile.monsters_level if profile else None)
        self.specialists = level_select("S", profile.specialists_level if profile else None)

        self.add_item(discord.ui.Label(text="Preferred language", component=self.language))
        self.add_item(discord.ui.Label(text="Guardsmen level", component=self.guardsmen))
        self.add_item(discord.ui.Label(text="Monsters level", component=self.monsters))
        self.add_item(discord.ui.Label(text="Specialists level", component=self.specialists))

    async def on_submit(self, interaction: discord.Interaction) -> None:
        if interaction.user.id != self.member_id:
            await interaction.response.send_message("This profile form belongs to another member.", ephemeral=True)
            return
        await self.bot._submit_post_verification_profile(
            interaction,
            preferred_language=self.language.values[0],
            guardsmen_level=int(self.guardsmen.values[0]),
            monsters_level=int(self.monsters.values[0]),
            specialists_level=int(self.specialists.values[0]),
        )

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        log.exception("Post-verification profile modal failed", exc_info=error)
        if interaction.response.is_done():
            await interaction.followup.send("Profile update failed. Try `/profile` again.", ephemeral=True)
        else:
            await interaction.response.send_message("Profile update failed. Try `/profile` again.", ephemeral=True)


class PostVerificationProfileView(discord.ui.View):
    """Persistent button used in approval DMs and fallback guild messages."""

    def __init__(self, bot: "OZYAdminBot"):
        super().__init__(timeout=None)
        self.bot = bot

    @discord.ui.button(
        label="Complete OZY profile",
        style=discord.ButtonStyle.primary,
        custom_id="ozy:profile:complete",
    )
    async def profile_button(self, interaction: discord.Interaction, _: discord.ui.Button) -> None:
        await self.bot._open_post_verification_profile(interaction)


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
