from __future__ import annotations

import argparse
import asyncio
import os

import discord
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
SERVER_ID = int(os.getenv("SERVER_ID", "1536505730532638820") or 0)

ROLE_IDS = {
    "Leader": 1536675686029467658,
    "Superior": 1536676026288181319,
    "OZY Admin": 1540666943411978272,
    "OZY Translator": 1536505859193180162,
    "EN": 1536541947173408839,
    "ES": 1536542118611263609,
    "AR": 1540062337111953448,
    "DE": 1536542327714095166,
    "FR": 1536542281593782292,
    "NO": 1540062640171126904,
    "CEB": 1536542372127572028,
    "PT": 1536542159459586128,
    "SV": 1536542203319681146,
    "RU": 1540062171965431949,
}

LANGUAGE_CHANNELS = {
    1536508569338253332: "EN",   # english
    1536508632785748108: "ES",   # español
    1538166161873567794: "AR",   # العربية
    1536508684081827880: "DE",   # deutsch
    1536525721584017548: "FR",   # français
    1538637390149587025: "NO",   # norsk
    1536508734530920570: "CEB",  # bisaya
    1536510376617967616: "PT",   # português
    1536510464144441515: "SV",   # svenska
    1538166128017412096: "RU",   # русский
}

LEADERSHIP_VOICE_ID = 1540784717161300040
VERIFICATION_HELP_ID = 1542964851805257858


def parse_args():
    parser = argparse.ArgumentParser(
        description="Repair OZY language-channel access and Leadership voice privacy."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply changes. Default is dry-run.",
    )
    return parser.parse_args()


def ow(**kwargs):
    return discord.PermissionOverwrite(**kwargs)


def explicit(overwrite: discord.PermissionOverwrite):
    return {name: value for name, value in overwrite if value is not None}


class Repair(discord.Client):
    def __init__(self, *, apply_changes: bool, **kwargs):
        super().__init__(**kwargs)
        self.apply_changes = apply_changes

    def role(self, guild: discord.Guild, name: str) -> discord.Role:
        role = guild.get_role(ROLE_IDS[name])
        if role is None:
            raise RuntimeError(f"Missing role {name} ({ROLE_IDS[name]})")
        return role

    async def on_ready(self):
        try:
            guild = self.get_guild(SERVER_ID)
            if guild is None:
                raise RuntimeError(f"Guild {SERVER_ID} not found.")

            everyone = guild.default_role
            admin = self.role(guild, "OZY Admin")
            translator = self.role(guild, "OZY Translator")
            leader = self.role(guild, "Leader")
            superior = self.role(guild, "Superior")

            print(f"Guild: {guild.name} ({guild.id})")
            print(f"Mode: {'APPLY' if self.apply_changes else 'DRY RUN'}")
            print()

            language_member = ow(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                add_reactions=True,
                embed_links=True,
                attach_files=True,
                send_messages_in_threads=True,
                external_emojis=True,
            )

            bot_text = ow(
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

            admin_text = ow(
                view_channel=True,
                send_messages=True,
                read_message_history=True,
                add_reactions=True,
                embed_links=True,
                attach_files=True,
                send_messages_in_threads=True,
                manage_messages=True,
            )

            print("LANGUAGE CHANNELS")
            for channel_id, language_role_name in LANGUAGE_CHANNELS.items():
                channel = guild.get_channel(channel_id)
                if not isinstance(channel, discord.TextChannel):
                    print(f"  ERROR: channel {channel_id} not found or not text")
                    continue

                lang_role = self.role(guild, language_role_name)
                desired = {
                    everyone: ow(view_channel=False, send_messages=False),
                    lang_role: language_member,
                    admin: admin_text,
                    translator: bot_text,
                }

                print(
                    f"  {channel.name}: dedicated {language_role_name} access "
                    f"(currently synced={channel.permissions_synced})"
                )

                if self.apply_changes:
                    await channel.edit(
                        overwrites=desired,
                        reason="Restore OZY dedicated language-channel permissions",
                    )
                    refreshed = await self.fetch_channel(channel.id)
                    print(
                        f"    APPLIED -> synced={getattr(refreshed, 'permissions_synced', None)}"
                    )

            print()
            print("LEADERSHIP VOICE")
            voice = guild.get_channel(LEADERSHIP_VOICE_ID)
            if not isinstance(voice, discord.VoiceChannel):
                print("  ERROR: Leadership voice channel not found")
            else:
                leadership_voice = ow(
                    view_channel=True,
                    connect=True,
                    speak=True,
                    stream=True,
                    use_voice_activation=True,
                )
                desired_voice = {
                    everyone: ow(view_channel=False, connect=False),
                    leader: leadership_voice,
                    superior: leadership_voice,
                    admin: ow(view_channel=True, connect=True, speak=True),
                    translator: ow(view_channel=False, connect=False),
                }

                print(
                    f"  {voice.name}: block Translator from voice "
                    f"(currently synced={voice.permissions_synced})"
                )
                if self.apply_changes:
                    await voice.edit(
                        overwrites=desired_voice,
                        reason="Keep OZY Translator out of Leadership voice",
                    )
                    refreshed = await self.fetch_channel(voice.id)
                    print(
                        f"    APPLIED -> synced={getattr(refreshed, 'permissions_synced', None)}"
                    )

            print()
            print("PUBLIC VERIFICATION HELP")
            help_channel = guild.get_channel(VERIFICATION_HELP_ID)
            if not isinstance(help_channel, discord.TextChannel):
                print("  ERROR: verification-help channel not found")
            else:
                public_help = ow(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    add_reactions=True,
                    embed_links=False,
                    attach_files=False,
                    send_messages_in_threads=False,
                )
                desired_help = {
                    everyone: public_help,
                    admin: admin_text,
                    translator: bot_text,
                }

                print(
                    "  verification-help: keep public/chattable, "
                    "but disable public attachments, embeds and thread posting"
                )
                if self.apply_changes:
                    await help_channel.edit(
                        overwrites=desired_help,
                        slowmode_delay=20,
                        reason="Harden public OZY verification-help channel",
                    )
                    print("    APPLIED -> 20s slowmode")

            print()
            if translator.permissions.administrator:
                print("ACTION REQUIRED: OZY Translator still has Administrator.")
                print(
                    "Administrator bypasses all channel overwrites, including ADMIN and "
                    "the Leadership voice block. Remove Administrator from OZY Translator."
                )
            else:
                print("OK: OZY Translator does not have Administrator.")

            print()
            if not self.apply_changes:
                print("No Discord changes were made.")
                print("Apply with: py repair_ozy_language_and_voice_permissions.py --apply")
            else:
                print("Repair applied.")
                print("Next: py export_discord_channels.py")
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

    client = Repair(apply_changes=args.apply, intents=intents)
    await client.start(TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
