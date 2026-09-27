#!/usr/bin/env python3
"""
OZY language channel permission fixer.

Goal
----
All normal members can SEE/READ every language channel.
A member can WRITE only in the channel matching their language role.

Safety
------
- DRY RUN by default.
- Only touches the normal member role (Verified/Member) and the matching language role.
- Preserves every other role/member overwrite on every channel.
- Preserves unrelated permission fields on the two roles it edits.
- Does NOT sync channels with their category.

Usage
-----
    py fix_language_channel_permissions.py
    py fix_language_channel_permissions.py --apply

Environment
-----------
Required:
    DISCORD_TOKEN=...
    GUILD_ID=123456789012345678

Optional:
    MEMBER_ROLE_ID=123456789012345678
    MEMBER_ROLE_NAME=Verified

If MEMBER_ROLE_ID is omitted, the script looks for MEMBER_ROLE_NAME,
then "Member", then "Verified".
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from dataclasses import dataclass

import discord

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LanguageChannel:
    role_name: str
    channel_names: tuple[str, ...]


LANGUAGE_CHANNELS = (
    LanguageChannel("EN",  ("english",)),
    LanguageChannel("ES",  ("español", "espanol", "spanish")),
    LanguageChannel("PT",  ("português", "portugues", "portuguese")),
    LanguageChannel("SV",  ("svenska", "swedish")),
    LanguageChannel("DE",  ("deutsch", "german")),
    LanguageChannel("CEB", ("bisaya", "cebuano")),
    LanguageChannel("FR",  ("français", "francais", "french")),
    LanguageChannel("RU",  ("русский", "russian")),
    LanguageChannel("AR",  ("العربية", "arabic")),
    LanguageChannel("NO",  ("norsk", "norwegian")),
)

# Permissions this script intentionally manages.
MEMBER_PERMISSIONS = {
    "view_channel": True,
    "read_message_history": True,
    "send_messages": False,
    "send_messages_in_threads": False,
    "create_public_threads": False,
    "create_private_threads": False,
    "add_reactions": False,
}

LANGUAGE_PERMISSIONS = {
    "view_channel": True,
    "read_message_history": True,
    "send_messages": True,
    "send_messages_in_threads": True,
    "add_reactions": True,
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Fix OZY language channel permissions safely."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually apply changes. Without this flag the script is dry-run only.",
    )
    return parser.parse_args()


def get_token() -> str:
    token = os.getenv("DISCORD_TOKEN") or os.getenv("BOT_TOKEN")
    if not token:
        raise RuntimeError(
            "Missing DISCORD_TOKEN (or BOT_TOKEN) in the environment/.env file."
        )
    return token


def get_guild_id() -> int:
    raw = os.getenv("GUILD_ID")
    if not raw:
        raise RuntimeError("Missing GUILD_ID in the environment/.env file.")
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError("GUILD_ID must be a numeric Discord server ID.") from exc


def find_member_role(guild: discord.Guild) -> discord.Role:
    role_id = os.getenv("MEMBER_ROLE_ID")
    if role_id:
        try:
            role = guild.get_role(int(role_id))
        except ValueError:
            role = None
        if role is None:
            raise RuntimeError(f"MEMBER_ROLE_ID={role_id} was not found in the guild.")
        return role

    preferred = os.getenv("MEMBER_ROLE_NAME")
    candidates = [preferred, "Member", "Verified"]
    for name in candidates:
        if not name:
            continue
        role = discord.utils.get(guild.roles, name=name)
        if role:
            return role

    raise RuntimeError(
        "Could not find the normal member role. "
        "Set MEMBER_ROLE_ID or MEMBER_ROLE_NAME in .env."
    )


def find_role(guild: discord.Guild, role_name: str) -> discord.Role:
    role = discord.utils.get(guild.roles, name=role_name)
    if role is None:
        raise RuntimeError(f'Role "{role_name}" was not found.')
    return role


def find_text_channel(
    guild: discord.Guild,
    possible_names: tuple[str, ...],
) -> discord.TextChannel:
    wanted = {name.casefold() for name in possible_names}

    matches = [
        channel
        for channel in guild.text_channels
        if channel.name.casefold() in wanted
    ]

    if not matches:
        raise RuntimeError(
            "Could not find language channel. Tried: "
            + ", ".join(f"#{name}" for name in possible_names)
        )

    if len(matches) > 1:
        names = ", ".join(f"#{c.name} ({c.id})" for c in matches)
        raise RuntimeError(
            f"Multiple channels matched {possible_names}: {names}. "
            "Make the mapping more specific before applying."
        )

    return matches[0]


def get_overwrite(
    channel: discord.abc.GuildChannel,
    target: discord.Role,
) -> discord.PermissionOverwrite:
    # Returns the target's current explicit overwrite only.
    # Unrelated fields remain intact when we edit this object.
    return channel.overwrites_for(target)


def value_label(value: bool | None) -> str:
    if value is True:
        return "ALLOW"
    if value is False:
        return "DENY"
    return "INHERIT"


def changes_for(
    overwrite: discord.PermissionOverwrite,
    desired: dict[str, bool],
) -> list[tuple[str, bool | None, bool]]:
    changes = []
    for permission_name, desired_value in desired.items():
        current_value = getattr(overwrite, permission_name)
        if current_value != desired_value:
            changes.append((permission_name, current_value, desired_value))
    return changes


def apply_desired(
    overwrite: discord.PermissionOverwrite,
    desired: dict[str, bool],
) -> None:
    for permission_name, desired_value in desired.items():
        setattr(overwrite, permission_name, desired_value)


def print_change_block(
    channel: discord.TextChannel,
    role: discord.Role,
    changes: list[tuple[str, bool | None, bool]],
) -> None:
    print(f"  Role: {role.name}")
    if not changes:
        print("    OK - no changes needed")
        return

    for permission_name, before, after in changes:
        print(
            f"    {permission_name:<28} "
            f"{value_label(before):<7} -> {value_label(after)}"
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

async def run(apply_changes: bool) -> None:
    token = get_token()
    guild_id = get_guild_id()

    intents = discord.Intents.none()
    intents.guilds = True

    client = discord.Client(intents=intents)

    @client.event
    async def on_ready() -> None:
        try:
            guild = client.get_guild(guild_id)
            if guild is None:
                raise RuntimeError(
                    f"Guild {guild_id} is not available to this bot."
                )

            member_role = find_member_role(guild)

            print()
            print("=" * 72)
            print("OZY LANGUAGE CHANNEL PERMISSIONS")
            print("=" * 72)
            print(f"Server:      {guild.name} ({guild.id})")
            print(f"Member role: {member_role.name} ({member_role.id})")
            print(f"Mode:        {'APPLY' if apply_changes else 'DRY RUN'}")
            print()

            plan = []

            # Resolve everything first. If anything is missing, abort before writes.
            for item in LANGUAGE_CHANNELS:
                language_role = find_role(guild, item.role_name)
                channel = find_text_channel(guild, item.channel_names)

                member_ow = get_overwrite(channel, member_role)
                lang_ow = get_overwrite(channel, language_role)

                member_changes = changes_for(member_ow, MEMBER_PERMISSIONS)
                lang_changes = changes_for(lang_ow, LANGUAGE_PERMISSIONS)

                plan.append(
                    (
                        channel,
                        member_role,
                        member_ow,
                        member_changes,
                        language_role,
                        lang_ow,
                        lang_changes,
                    )
                )

            total_changes = 0

            for (
                channel,
                normal_role,
                member_ow,
                member_changes,
                language_role,
                lang_ow,
                lang_changes,
            ) in plan:
                print(f"#{channel.name} ({channel.id})")
                print_change_block(channel, normal_role, member_changes)
                print_change_block(channel, language_role, lang_changes)

                total_changes += len(member_changes) + len(lang_changes)
                print()

            print("-" * 72)
            print(f"Permission fields to change: {total_changes}")

            if not apply_changes:
                print()
                print("DRY RUN ONLY - nothing was changed.")
                print("Review the output, then run:")
                print("  py fix_language_channel_permissions.py --apply")
                return

            if total_changes == 0:
                print("Everything is already configured correctly.")
                return

            print()
            print("Applying changes...")

            # Apply only the two targeted role overwrites on each channel.
            # Other role/member overwrites are untouched.
            for (
                channel,
                normal_role,
                member_ow,
                member_changes,
                language_role,
                lang_ow,
                lang_changes,
            ) in plan:
                if member_changes:
                    apply_desired(member_ow, MEMBER_PERMISSIONS)
                    await channel.set_permissions(
                        normal_role,
                        overwrite=member_ow,
                        reason="OZY language channel visibility/write restriction",
                    )

                if lang_changes:
                    apply_desired(lang_ow, LANGUAGE_PERMISSIONS)
                    await channel.set_permissions(
                        language_role,
                        overwrite=lang_ow,
                        reason="OZY language channel matching-language write access",
                    )

                if member_changes or lang_changes:
                    print(f"  Updated #{channel.name}")

            print()
            print("DONE.")
            print("No category sync was performed.")
            print("No unrelated role/member overwrites were changed.")

        except Exception as exc:
            print()
            print(f"ERROR: {exc}", file=sys.stderr)
            if apply_changes:
                print(
                    "The script stopped. Review the error before running --apply again.",
                    file=sys.stderr,
                )
        finally:
            await client.close()

    await client.start(token)


def main() -> None:
    args = parse_args()
    asyncio.run(run(args.apply))


if __name__ == "__main__":
    main()
