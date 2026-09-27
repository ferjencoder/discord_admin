#!/usr/bin/env python3
"""
OZY Discord - Language channel permissions

Uses the CURRENT OZY Admin .env variable names:

    DISCORD_TOKEN=
    SERVER_ID=
    VERIFIED_ROLE_ID=

Optional:
    LANGUAGE_ROLE_MAP=

Default mode is DRY RUN.
Use --apply only after reviewing the output.

Target model for every language channel:

Verified:
    View Channel              ALLOW
    Read Message History      ALLOW
    Send Messages             DENY
    Send Messages in Threads  DENY
    Create Public Threads     DENY
    Create Private Threads    DENY
    Add Reactions             DENY

Matching language role:
    View Channel              ALLOW
    Read Message History      ALLOW
    Send Messages             ALLOW
    Send Messages in Threads  ALLOW
    Add Reactions             ALLOW

The script:
- DOES NOT sync channel permissions with the category.
- DOES NOT touch @everyone.
- DOES NOT touch Leader, Superior, OZY Admin, OZY Translator, bots, etc.
- DOES NOT replace the complete overwrite list.
- Preserves unrelated permission fields on Verified and the language role.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from dataclasses import dataclass

import discord

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None


# ---------------------------------------------------------------------------
# OZY language configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class LanguageChannel:
    code: str
    channel_names: tuple[str, ...]


LANGUAGE_CHANNELS = (
    LanguageChannel("EN",  ("english",)),
    LanguageChannel("ES",  ("español", "espanol")),
    LanguageChannel("PT",  ("português", "portugues")),
    LanguageChannel("SV",  ("svenska",)),
    LanguageChannel("DE",  ("deutsch",)),
    LanguageChannel("CEB", ("bisaya",)),
    LanguageChannel("FR",  ("français", "francais")),
    LanguageChannel("RU",  ("русский",)),
    LanguageChannel("AR",  ("العربية",)),
    LanguageChannel("NO",  ("norsk",)),
)

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
# Environment
# ---------------------------------------------------------------------------

def load_environment() -> None:
    if load_dotenv is not None:
        # Explicitly load .env from the current working directory.
        load_dotenv(dotenv_path=".env", override=False)


def required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing {name} in .env")
    return value


def discord_token() -> str:
    return required_env("DISCORD_TOKEN")


def server_id() -> int:
    raw = required_env("SERVER_ID")
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError("SERVER_ID must be a numeric Discord server ID.") from exc


def verified_role_id() -> int:
    raw = required_env("VERIFIED_ROLE_ID")
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError("VERIFIED_ROLE_ID must be a numeric Discord role ID.") from exc


# ---------------------------------------------------------------------------
# LANGUAGE_ROLE_MAP support
# ---------------------------------------------------------------------------

def parse_language_role_map() -> dict[str, int]:
    """
    LANGUAGE_ROLE_MAP is optional.

    Supported examples:
      {"EN":123,"ES":456}
      EN:123,ES:456
      EN=123,ES=456

    If it is absent or cannot resolve a specific language, the script falls
    back to finding a Discord role whose name exactly matches EN/ES/etc.
    """
    raw = os.getenv("LANGUAGE_ROLE_MAP", "").strip()
    if not raw:
        return {}

    # JSON object
    try:
        obj = json.loads(raw)
        if isinstance(obj, dict):
            result = {}
            for key, value in obj.items():
                try:
                    result[str(key).upper()] = int(value)
                except (TypeError, ValueError):
                    pass
            if result:
                return result
    except json.JSONDecodeError:
        pass

    # Simple comma/semicolon separated pairs
    result = {}
    normalized = raw.replace(";", ",")
    for part in normalized.split(","):
        part = part.strip()
        if not part:
            continue

        if ":" in part:
            key, value = part.split(":", 1)
        elif "=" in part:
            key, value = part.split("=", 1)
        else:
            continue

        key = key.strip().upper()
        value = value.strip()

        try:
            result[key] = int(value)
        except ValueError:
            continue

    return result


# ---------------------------------------------------------------------------
# Discord lookups
# ---------------------------------------------------------------------------

def find_verified_role(guild: discord.Guild) -> discord.Role:
    rid = verified_role_id()
    role = guild.get_role(rid)
    if role is None:
        raise RuntimeError(
            f"VERIFIED_ROLE_ID={rid} does not exist in server {guild.name}."
        )
    return role


def find_language_role(
    guild: discord.Guild,
    code: str,
    role_map: dict[str, int],
) -> discord.Role:
    mapped_id = role_map.get(code)

    if mapped_id is not None:
        role = guild.get_role(mapped_id)
        if role is None:
            raise RuntimeError(
                f"LANGUAGE_ROLE_MAP maps {code} to role ID {mapped_id}, "
                "but that role does not exist."
            )
        return role

    role = discord.utils.get(guild.roles, name=code)
    if role is None:
        raise RuntimeError(
            f'Could not find language role "{code}". '
            "Add it to LANGUAGE_ROLE_MAP or create a role with that exact name."
        )
    return role


def find_language_channel(
    guild: discord.Guild,
    names: tuple[str, ...],
) -> discord.TextChannel:
    wanted = {name.casefold() for name in names}

    matches = [
        channel
        for channel in guild.text_channels
        if channel.name.casefold() in wanted
    ]

    if not matches:
        attempted = ", ".join(f"#{name}" for name in names)
        raise RuntimeError(f"Could not find channel. Tried: {attempted}")

    if len(matches) > 1:
        found = ", ".join(f"#{c.name} ({c.id})" for c in matches)
        raise RuntimeError(
            f"Multiple channels matched {names}: {found}. "
            "Aborting instead of guessing."
        )

    return matches[0]


# ---------------------------------------------------------------------------
# Permission handling
# ---------------------------------------------------------------------------

def permission_state(value: bool | None) -> str:
    if value is True:
        return "ALLOW"
    if value is False:
        return "DENY"
    return "INHERIT"


def collect_changes(
    overwrite: discord.PermissionOverwrite,
    desired: dict[str, bool],
) -> list[tuple[str, bool | None, bool]]:
    changes = []

    for permission_name, desired_value in desired.items():
        current = getattr(overwrite, permission_name)
        if current != desired_value:
            changes.append((permission_name, current, desired_value))

    return changes


def mutate_overwrite(
    overwrite: discord.PermissionOverwrite,
    desired: dict[str, bool],
) -> discord.PermissionOverwrite:
    for permission_name, desired_value in desired.items():
        setattr(overwrite, permission_name, desired_value)

    return overwrite


def print_role_plan(
    role: discord.Role,
    changes: list[tuple[str, bool | None, bool]],
) -> None:
    print(f"  {role.name} ({role.id})")

    if not changes:
        print("    OK - no changes needed")
        return

    for name, before, after in changes:
        print(
            f"    {name:<30} "
            f"{permission_state(before):<7} -> {permission_state(after)}"
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply changes. Without this flag the script is dry-run only.",
    )
    return parser.parse_args()


async def run(apply: bool) -> None:
    load_environment()

    token = discord_token()
    sid = server_id()
    role_map = parse_language_role_map()

    intents = discord.Intents.none()
    intents.guilds = True

    client = discord.Client(intents=intents)

    @client.event
    async def on_ready() -> None:
        try:
            guild = client.get_guild(sid)
            if guild is None:
                raise RuntimeError(
                    f"SERVER_ID={sid} is not visible to this bot."
                )

            verified = find_verified_role(guild)

            print()
            print("=" * 76)
            print("OZY LANGUAGE CHANNEL PERMISSION FIX")
            print("=" * 76)
            print(f"Server:            {guild.name} ({guild.id})")
            print(f"Verified role:     {verified.name} ({verified.id})")
            print(f"LANGUAGE_ROLE_MAP: {'loaded' if role_map else 'not set - using role names'}")
            print(f"Mode:              {'APPLY' if apply else 'DRY RUN'}")
            print()

            # Resolve the complete plan first.
            # This guarantees missing roles/channels are discovered before writes begin.
            plan = []

            for item in LANGUAGE_CHANNELS:
                channel = find_language_channel(guild, item.channel_names)
                language_role = find_language_role(guild, item.code, role_map)

                verified_overwrite = channel.overwrites_for(verified)
                language_overwrite = channel.overwrites_for(language_role)

                verified_changes = collect_changes(
                    verified_overwrite,
                    MEMBER_PERMISSIONS,
                )
                language_changes = collect_changes(
                    language_overwrite,
                    LANGUAGE_PERMISSIONS,
                )

                plan.append(
                    {
                        "channel": channel,
                        "verified_role": verified,
                        "verified_overwrite": verified_overwrite,
                        "verified_changes": verified_changes,
                        "language_role": language_role,
                        "language_overwrite": language_overwrite,
                        "language_changes": language_changes,
                    }
                )

            total_fields = 0

            for item in plan:
                channel = item["channel"]

                print(f"#{channel.name} ({channel.id})")
                print_role_plan(
                    item["verified_role"],
                    item["verified_changes"],
                )
                print_role_plan(
                    item["language_role"],
                    item["language_changes"],
                )
                print()

                total_fields += len(item["verified_changes"])
                total_fields += len(item["language_changes"])

            print("-" * 76)
            print(f"Permission fields requiring changes: {total_fields}")

            if not apply:
                print()
                print("DRY RUN - Discord was not modified.")
                print()
                print("If the plan above is correct, run:")
                print("  py fix_language_channel_permissions_v2.py --apply")
                return

            if total_fields == 0:
                print()
                print("Nothing to change.")
                return

            print()
            print("Applying...")

            for item in plan:
                channel = item["channel"]

                if item["verified_changes"]:
                    overwrite = mutate_overwrite(
                        item["verified_overwrite"],
                        MEMBER_PERMISSIONS,
                    )

                    await channel.set_permissions(
                        item["verified_role"],
                        overwrite=overwrite,
                        reason="OZY language channels: all members read, language role writes",
                    )

                if item["language_changes"]:
                    overwrite = mutate_overwrite(
                        item["language_overwrite"],
                        LANGUAGE_PERMISSIONS,
                    )

                    await channel.set_permissions(
                        item["language_role"],
                        overwrite=overwrite,
                        reason="OZY language channels: matching language role write access",
                    )

                if item["verified_changes"] or item["language_changes"]:
                    print(f"  Updated #{channel.name}")

            print()
            print("DONE")
            print("- Category permissions were NOT synced.")
            print("- @everyone was NOT changed.")
            print("- Other roles/member overwrites were NOT changed.")

        except Exception as exc:
            print()
            print(f"ERROR: {exc}", file=sys.stderr)
            sys.exit_code = 1
        finally:
            await client.close()

    await client.start(token)


def main() -> None:
    args = parse_args()

    try:
        asyncio.run(run(args.apply))
    except KeyboardInterrupt:
        print("\nCancelled.")


if __name__ == "__main__":
    main()
