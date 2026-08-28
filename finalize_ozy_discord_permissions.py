from __future__ import annotations

import argparse
import asyncio
import os

import discord
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
SERVER_ID = int(os.getenv("SERVER_ID", "1536505730532638820") or 0)

EN_ROLE_ID = 1536541947173408839
TRANSLATOR_ROLE_ID = 1536505859193180162

CHANNELS_TO_SYNC = {
    1536548380116000819: "IMPORTANT / calendar",
    1536548269080051762: "IMPORTANT / announcements",
    1536666247981310012: "RESOURCES / epics-guide",
}


def parse_args():
    parser = argparse.ArgumentParser(
        description="Final OZY Discord permission cleanup. Dry-run by default."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply the changes. Without this flag nothing is modified.",
    )
    return parser.parse_args()


class Finalizer(discord.Client):
    def __init__(self, *, apply_changes: bool, **kwargs):
        super().__init__(**kwargs)
        self.apply_changes = apply_changes

    async def on_ready(self):
        try:
            guild = self.get_guild(SERVER_ID)
            if guild is None:
                raise RuntimeError(f"Guild {SERVER_ID} was not found.")

            print(f"Guild: {guild.name} ({guild.id})")
            print(f"Mode: {'APPLY' if self.apply_changes else 'DRY RUN'}")
            print()

            # 1. EN should be a pure language/access marker just like ES/DE/etc.
            en_role = guild.get_role(EN_ROLE_ID)
            if en_role is None:
                print(f"ERROR: EN role {EN_ROLE_ID} not found")
            else:
                if en_role.permissions.value:
                    print(
                        f"EN role has guild-level permissions={en_role.permissions.value}. "
                        "It should be 0 like the other language roles."
                    )
                    if self.apply_changes:
                        await en_role.edit(
                            permissions=discord.Permissions.none(),
                            reason="Normalize OZY language roles",
                        )
                        print("  APPLIED: EN guild-level permissions cleared.")
                else:
                    print("OK: EN guild-level permissions already 0.")

            print()

            # 2. Three child channels remained unsynced in the latest export.
            for channel_id, label in CHANNELS_TO_SYNC.items():
                channel = guild.get_channel(channel_id)
                if channel is None:
                    print(f"ERROR: missing channel {label} ({channel_id})")
                    continue

                if getattr(channel, "permissions_synced", False):
                    print(f"OK: {label} already synced.")
                    continue

                print(f"NEEDS SYNC: {label}")
                if self.apply_changes:
                    await channel.edit(
                        sync_permissions=True,
                        reason="Finalize OZY category permission sync",
                    )
                    print(f"  APPLIED: {label}")

            print()

            # 3. Translator Administrator cannot be safely left enabled.
            translator = guild.get_role(TRANSLATOR_ROLE_ID)
            if translator is None:
                print(f"ERROR: OZY Translator role {TRANSLATOR_ROLE_ID} not found")
            elif translator.permissions.administrator:
                print("ACTION REQUIRED: OZY Translator still has Administrator.")
                print(
                    "Remove Administrator manually in Discord. Administrator bypasses "
                    "the ADMIN category deny and all other channel permission overwrites."
                )
            else:
                print("OK: OZY Translator does not have Administrator.")

            print()
            if not self.apply_changes:
                print("No Discord changes were made.")
                print("Run again with --apply after reviewing the output.")
            else:
                print("Final cleanup applied.")
                print("Now run: py export_discord_channels.py")
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

    client = Finalizer(apply_changes=args.apply, intents=intents)
    await client.start(TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
