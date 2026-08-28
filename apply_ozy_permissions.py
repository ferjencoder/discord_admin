from __future__ import annotations

import argparse
import asyncio
import os

import discord
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
SERVER_ID = int(os.getenv("SERVER_ID", "1536505730532638820") or 0)

ROLE = {
    "leader": 1536675686029467658,
    "superior": 1536676026288181319,
    "verified": 1542765587023925298,
    "unverified": 1542765585606254634,
    "special": 1542765588475281408,
    "admin_bot": 1540666943411978272,
    "translator": 1536505859193180162,
}

CATEGORY = {
    "start": 1540721682459787375,
    "admin": 1540583740479381616,
    "leadership": 1540527295301816411,
    "important": 1540486733572079736,
    "resources": 1540202195637633104,
    "general": 1536750253036806275,
    "game": 1540178537078726816,
    "voice": 1536505730989957334,
}

# Translation policy:
# - OZY Translator may read/post in START HERE, IMPORTANT, RESOURCES,
#   GAME TALK, GENERAL language channels, and LEADERSHIP.
# - OZY Translator is deliberately excluded from ADMIN.
# - VOICE needs no translator permission.
#
# This script intentionally does NOT rewrite individual language channels.
# They already use per-language role overwrites and translator access.


def parse_args():
    p = argparse.ArgumentParser(
        description="Dry-run/apply OZY category access policy with scoped OZY Translator access."
    )
    p.add_argument("--apply", action="store_true", help="Apply changes. Default is dry-run.")
    return p.parse_args()


def ow(**kwargs):
    return discord.PermissionOverwrite(**kwargs)


class Fixer(discord.Client):
    def __init__(self, *, apply_changes: bool, **kwargs):
        super().__init__(**kwargs)
        self.apply_changes = apply_changes

    def role(self, guild: discord.Guild, key: str) -> discord.Role:
        role = guild.get_role(ROLE[key])
        if role is None:
            raise RuntimeError(f"Missing role {key} / {ROLE[key]}")
        return role

    def category(self, guild: discord.Guild, key: str) -> discord.CategoryChannel:
        channel = guild.get_channel(CATEGORY[key])
        if not isinstance(channel, discord.CategoryChannel):
            raise RuntimeError(f"Missing category {key} / {CATEGORY[key]}")
        return channel

    async def set_category(self, guild, key, desired):
        category = self.category(guild, key)
        print(f"\n[{key.upper()}] {category.name}")

        for target, overwrite in desired.items():
            target_name = getattr(target, "name", str(target))
            explicit = {name: value for name, value in overwrite if value is not None}
            print(f"  {target_name} -> {explicit}")

        if self.apply_changes:
            await category.edit(
                overwrites=desired,
                reason="OZY access policy + scoped translator access",
            )
            print("  APPLIED")
        else:
            print("  DRY RUN")

    async def on_ready(self):
        try:
            guild = self.get_guild(SERVER_ID)
            if guild is None:
                raise RuntimeError(f"Guild {SERVER_ID} is not in bot cache.")

            everyone = guild.default_role
            leader = self.role(guild, "leader")
            superior = self.role(guild, "superior")
            verified = self.role(guild, "verified")
            special = self.role(guild, "special")
            admin_bot = self.role(guild, "admin_bot")
            translator = self.role(guild, "translator")

            leadership_chat = ow(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                add_reactions=True,
                embed_links=True,
                attach_files=True,
                send_messages_in_threads=True,
            )

            member_chat = ow(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                add_reactions=True,
                embed_links=True,
                attach_files=True,
                send_messages_in_threads=True,
            )

            admin_bot_access = ow(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                add_reactions=True,
                embed_links=True,
                attach_files=True,
                send_messages_in_threads=True,
                manage_messages=True,
            )

            # Translator needs enough permissions to read the source message and
            # post reaction-requested translations. It does NOT need Administrator.
            translator_access = ow(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                add_reactions=True,
                embed_links=True,
                attach_files=True,
                send_messages_in_threads=True,
                external_emojis=True,
                external_stickers=True,
            )

            # START HERE - public/read-only to users; translator can answer flag reactions.
            await self.set_category(guild, "start", {
                everyone: ow(
                    view_channel=True,
                    send_messages=False,
                    send_messages_in_threads=False,
                ),
                admin_bot: admin_bot_access,
                translator: translator_access,
            })

            # ADMIN - internal admin data. Translator explicitly excluded.
            await self.set_category(guild, "admin", {
                everyone: ow(view_channel=False),
                leader: leadership_chat,
                superior: leadership_chat,
                admin_bot: admin_bot_access,
                translator: ow(view_channel=False),
            })

            # LEADERSHIP - private to leadership, but translator is intentionally allowed
            # so flag reactions work there.
            await self.set_category(guild, "leadership", {
                everyone: ow(view_channel=False),
                leader: leadership_chat,
                superior: leadership_chat,
                admin_bot: admin_bot_access,
                translator: translator_access,
            })

            readonly_member = ow(
                view_channel=True,
                send_messages=False,
                send_messages_in_threads=False,
            )
            leadership_important = ow(
                view_channel=True,
                send_messages=True,
                send_messages_in_threads=True,
                read_message_history=True,
            )

            # IMPORTANT - members read-only, translator can post requested translations.
            await self.set_category(guild, "important", {
                everyone: ow(
                    view_channel=False,
                    send_messages=False,
                    send_messages_in_threads=False,
                ),
                verified: readonly_member,
                special: readonly_member,
                leader: leadership_important,
                superior: leadership_important,
                admin_bot: admin_bot_access,
                translator: translator_access,
            })

            # RESOURCES - members read-only, translator can post requested translations.
            await self.set_category(guild, "resources", {
                everyone: ow(
                    view_channel=False,
                    send_messages=False,
                    send_messages_in_threads=False,
                ),
                verified: readonly_member,
                special: readonly_member,
                leader: leadership_important,
                superior: leadership_important,
                admin_bot: admin_bot_access,
                translator: translator_access,
            })

            # GAME TALK - normal clan chat + reaction translation.
            await self.set_category(guild, "game", {
                everyone: ow(view_channel=False),
                verified: member_chat,
                special: member_chat,
                leader: member_chat,
                superior: member_chat,
                admin_bot: admin_bot_access,
                translator: translator_access,
            })

            # VOICE - translator does not need access.
            voice_access = ow(
                view_channel=True,
                connect=True,
                speak=True,
                stream=True,
                use_voice_activation=True,
            )
            await self.set_category(guild, "voice", {
                everyone: ow(view_channel=False, connect=False),
                verified: voice_access,
                special: voice_access,
                leader: voice_access,
                superior: voice_access,
                admin_bot: ow(view_channel=True, connect=True, speak=True),
                translator: ow(view_channel=False, connect=False),
            })

            # GENERAL - language channels are individually unsynced and keep their
            # existing language-role + translator overwrites.
            await self.set_category(guild, "general", {
                everyone: ow(view_channel=False),
                admin_bot: admin_bot_access,
                translator: translator_access,
            })

            print("\nPOLICY SUMMARY")
            print("  Translator ALLOWED: START HERE, IMPORTANT, RESOURCES, GAME TALK, GENERAL, LEADERSHIP")
            print("  Translator BLOCKED: ADMIN, VOICE")
            print("  Individual language channel overwrites were NOT changed.")
            print()
            print("SECURITY CHECK")
            if translator.permissions.administrator:
                print("  WARNING: OZY Translator still has guild Administrator.")
                print("  Administrator bypasses every category overwrite above, including ADMIN.")
                print("  Remove Administrator from the Translator integration before relying on privacy.")
            else:
                print("  OK: OZY Translator does not have Administrator.")
            print()
            print("LANGUAGE ROLE CHECK")
            print("  Do not grant EN/ES/DE/etc. roles to Unverified members.")
            print("  Discord cannot enforce 'Verified AND language-role' with independent role overwrites.")
            if not self.apply_changes:
                print("\nNo Discord changes were made. Re-run with --apply after reviewing this output.")
        finally:
            await self.close()


async def main():
    args = parse_args()
    if not TOKEN:
        raise SystemExit("DISCORD_TOKEN is missing.")
    if not SERVER_ID:
        raise SystemExit("SERVER_ID is missing.")

    intents = discord.Intents.none()
    intents.guilds = True

    client = Fixer(apply_changes=args.apply, intents=intents)
    await client.start(TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
