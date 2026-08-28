from __future__ import annotations

import argparse
import asyncio
import os

import discord
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
SERVER_ID = int(os.getenv("SERVER_ID", "1536505730532638820") or 0)

CATEGORY_IDS_TO_SYNC = {
    1540721682459787375,   # START HERE
    1540583740479381616,        # ADMIN
    1540527295301816411,   # LEADERSHIP
    1540486733572079736,    # IMPORTANT
    1540202195637633104,    # RESOURCES
    1540178537078726816,    # GAME TALK
    1536505730989957334,        # VOICE
}

GENERAL_CATEGORY_ID = 1536750253036806275

# GENERAL is deliberately excluded because each language channel has its own
# EN/ES/AR/DE/FR/NO/CEB/PT/SV/RU role overwrite.


def parse_args():
    p = argparse.ArgumentParser(
        description="Sync OZY child channels to their category permission overwrites."
    )
    p.add_argument("--apply", action="store_true", help="Apply changes. Default is dry-run.")
    return p.parse_args()


class Syncer(discord.Client):
    def __init__(self, *, apply_changes: bool, **kwargs):
        super().__init__(**kwargs)
        self.apply_changes = apply_changes

    async def on_ready(self):
        try:
            guild = self.get_guild(SERVER_ID)
            if guild is None:
                raise RuntimeError(f"Guild {SERVER_ID} not found.")

            targets = []
            skipped_general = []

            for channel in guild.channels:
                if isinstance(channel, discord.CategoryChannel):
                    continue
                if channel.category_id in CATEGORY_IDS_TO_SYNC:
                    targets.append(channel)
                elif channel.category_id == GENERAL_CATEGORY_ID:
                    skipped_general.append(channel)

            print(f"Guild: {guild.name} ({guild.id})")
            print(f"Mode: {'APPLY' if self.apply_changes else 'DRY RUN'}")
            print()
            print("Channels to sync with category permissions:")
            for channel in sorted(targets, key=lambda c: (c.category.position if c.category else 999, c.position)):
                status = "already synced" if getattr(channel, "permissions_synced", False) else "needs sync"
                print(f"  {channel.category.name} / {channel.name} -> {status}")

            print()
            print("GENERAL language channels intentionally left unsynced:")
            for channel in sorted(skipped_general, key=lambda c: c.position):
                print(f"  {channel.name}")

            if not self.apply_changes:
                print()
                print("No Discord changes were made.")
                print("Re-run with --apply to synchronize the listed channels.")
                return

            changed = 0
            unchanged = 0
            failed = 0

            for channel in targets:
                if getattr(channel, "permissions_synced", False):
                    unchanged += 1
                    continue

                try:
                    await channel.edit(
                        sync_permissions=True,
                        reason="Sync OZY channel permissions to category policy",
                    )
                    changed += 1
                    print(f"SYNCED: {channel.category.name} / {channel.name}")
                except Exception as exc:
                    failed += 1
                    print(f"FAILED: {channel.name}: {type(exc).__name__}: {exc}")

            print()
            print(f"Summary: synced={changed}, already_synced={unchanged}, failed={failed}")
            print("Run export_discord_channels.py again and upload the new JSON for verification.")
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

    client = Syncer(apply_changes=args.apply, intents=intents)
    await client.start(TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
