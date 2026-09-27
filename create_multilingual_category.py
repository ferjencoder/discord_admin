#!/usr/bin/env python3
"""
Create an OZY shared multilingual Discord category + channel.

The script asks for:
1. Category name
2. Channel name

It uses the CURRENT OZY Admin .env variables:
    DISCORD_TOKEN=
    SERVER_ID=
    VERIFIED_ROLE_ID=

Optional:
    TRANSLATOR_ROLE_ID=

If TRANSLATOR_ROLE_ID is not set, the script tries to find a role named:
    OZY Translator

Permission model
----------------
@everyone
    View Channel = DENY

Verified
    View Channel = ALLOW
    Send Messages = ALLOW
    Read Message History = ALLOW
    Add Reactions = ALLOW
    Use External Emojis = ALLOW
    Use External Stickers = ALLOW

OZY Translator
    View Channel = ALLOW
    Send Messages = ALLOW
    Read Message History = ALLOW
    Add Reactions = ALLOW
    Embed Links = ALLOW
    Attach Files = ALLOW
    Manage Messages = ALLOW

The created text channel is synced to the category permissions.

Important:
This script enables the Discord permissions needed for flag-reaction translation.
The actual reaction -> translation behavior must already exist in OZY Translator.
"""

from __future__ import annotations

import asyncio
import os
import re
import sys

import discord

try:
    from dotenv import load_dotenv
except ImportError:
    load_dotenv = None


def load_environment() -> None:
    if load_dotenv is not None:
        load_dotenv(dotenv_path=".env", override=False)


def required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"Missing {name} in .env")
    return value


def get_server_id() -> int:
    raw = required_env("SERVER_ID")
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError("SERVER_ID must be numeric.") from exc


def get_verified_role_id() -> int:
    raw = required_env("VERIFIED_ROLE_ID")
    try:
        return int(raw)
    except ValueError as exc:
        raise RuntimeError("VERIFIED_ROLE_ID must be numeric.") from exc


def get_translator_role(guild: discord.Guild) -> discord.Role:
    raw = os.getenv("TRANSLATOR_ROLE_ID", "").strip()

    if raw:
        try:
            role_id = int(raw)
        except ValueError as exc:
            raise RuntimeError("TRANSLATOR_ROLE_ID must be numeric.") from exc

        role = guild.get_role(role_id)
        if role is None:
            raise RuntimeError(
                f"TRANSLATOR_ROLE_ID={role_id} was not found in the server."
            )
        return role

    role = discord.utils.get(guild.roles, name="OZY Translator")
    if role is None:
        raise RuntimeError(
            'Could not find role "OZY Translator". '
            "Set TRANSLATOR_ROLE_ID in .env or create that role."
        )

    return role


def clean_channel_name(value: str) -> str:
    """
    Keep Discord-friendly lowercase channel naming.
    Unicode letters are preserved where possible.
    """
    value = value.strip().lower()
    value = re.sub(r"\s+", "-", value)
    value = re.sub(r"-{2,}", "-", value)
    return value.strip("-")


def ask_names() -> tuple[str, str]:
    print()
    print("OZY SHARED MULTILINGUAL CHANNEL CREATOR")
    print("=" * 48)
    print()

    category_name = input("Category name: ").strip()
    if not category_name:
        raise RuntimeError("Category name cannot be empty.")

    channel_input = input("Channel name: ").strip()
    if not channel_input:
        raise RuntimeError("Channel name cannot be empty.")

    channel_name = clean_channel_name(channel_input)

    print()
    print("Will create/use:")
    print(f"  Category: {category_name}")
    print(f"  Channel:  #{channel_name}")
    print()

    confirm = input("Continue? [y/N]: ").strip().lower()
    if confirm not in {"y", "yes"}:
        print("Cancelled.")
        sys.exit(0)

    return category_name, channel_name


async def main_async() -> None:
    load_environment()

    token = required_env("DISCORD_TOKEN")
    server_id = get_server_id()
    verified_role_id = get_verified_role_id()
    category_name, channel_name = ask_names()

    intents = discord.Intents.none()
    intents.guilds = True

    client = discord.Client(intents=intents)

    @client.event
    async def on_ready() -> None:
        try:
            guild = client.get_guild(server_id)
            if guild is None:
                raise RuntimeError(
                    f"SERVER_ID={server_id} is not visible to this bot."
                )

            verified_role = guild.get_role(verified_role_id)
            if verified_role is None:
                raise RuntimeError(
                    f"VERIFIED_ROLE_ID={verified_role_id} was not found."
                )

            translator_role = get_translator_role(guild)

            print()
            print(f"Server:          {guild.name}")
            print(f"Member role:     {verified_role.name}")
            print(f"Translator role: {translator_role.name}")
            print()

            # ---------------------------------------------------------------
            # Category permissions
            # ---------------------------------------------------------------
            everyone_overwrite = discord.PermissionOverwrite(
                view_channel=False,
            )

            verified_overwrite = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                add_reactions=True,
                use_external_emojis=True,
                use_external_stickers=True,
                send_messages_in_threads=True,
                create_public_threads=True,
            )

            translator_overwrite = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                add_reactions=True,
                embed_links=True,
                attach_files=True,
                manage_messages=True,
                send_messages_in_threads=True,
            )

            overwrites = {
                guild.default_role: everyone_overwrite,
                verified_role: verified_overwrite,
                translator_role: translator_overwrite,
            }

            # ---------------------------------------------------------------
            # Find or create category
            # ---------------------------------------------------------------
            category = discord.utils.get(guild.categories, name=category_name)

            if category is None:
                category = await guild.create_category(
                    category_name,
                    overwrites=overwrites,
                    reason="OZY shared multilingual category",
                )
                print(f"Created category: {category.name}")
            else:
                print(f"Category already exists: {category.name}")
                print("Updating only @everyone, Verified and OZY Translator overwrites...")

                # Preserve all unrelated category overwrites.
                current = dict(category.overwrites)

                current[guild.default_role] = everyone_overwrite
                current[verified_role] = verified_overwrite
                current[translator_role] = translator_overwrite

                await category.edit(
                    overwrites=current,
                    reason="OZY shared multilingual category permissions",
                )

            # ---------------------------------------------------------------
            # Find or create channel
            # ---------------------------------------------------------------
            existing_channel = discord.utils.get(
                guild.text_channels,
                name=channel_name,
            )

            if existing_channel is not None:
                if existing_channel.category_id != category.id:
                    raise RuntimeError(
                        f"#{channel_name} already exists under another category. "
                        "Nothing was moved automatically."
                    )

                channel = existing_channel
                print(f"Channel already exists: #{channel.name}")
            else:
                channel = await guild.create_text_channel(
                    channel_name,
                    category=category,
                    reason="OZY shared multilingual channel",
                )
                print(f"Created channel: #{channel.name}")

            # Sync channel with category.
            await channel.edit(
                sync_permissions=True,
                reason="Sync OZY shared multilingual channel with category",
            )

            print()
            print("DONE")
            print()
            print("Expected access:")
            print("  @everyone      -> cannot view")
            print(f"  {verified_role.name:<14} -> can view/read/write/react")
            print(f"  {translator_role.name:<14} -> can view/read/write/react/embed/manage messages")
            print()
            print("The channel is synced to the category.")
            print()
            print("NOTE:")
            print("This configures Discord permissions for flag-reaction translation.")
            print("The actual flag reaction handling must already be implemented in OZY Translator.")

        except Exception as exc:
            print()
            print(f"ERROR: {exc}", file=sys.stderr)
        finally:
            await client.close()

    await client.start(token)


def main() -> None:
    try:
        asyncio.run(main_async())
    except KeyboardInterrupt:
        print("\nCancelled.")


if __name__ == "__main__":
    main()
