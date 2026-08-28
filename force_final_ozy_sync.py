from __future__ import annotations

import argparse
import asyncio
import os

import discord
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
SERVER_ID = int(os.getenv("SERVER_ID", "1536505730532638820") or 0)

TARGETS = {
    1536548380116000819: "IMPORTANT / calendar",
    1536666247981310012: "RESOURCES / epics-guide",
    1536548269080051762: "IMPORTANT / announcements",
}

TRANSLATOR_ROLE_ID = 1536505859193180162


def parse_args():
    parser = argparse.ArgumentParser(
        description="Force the final three OZY channels to copy their category overwrites."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply changes. Default is dry-run.",
    )
    return parser.parse_args()


def overwrite_signature(overwrites):
    rows = []
    for target, overwrite in overwrites.items():
        allow, deny = overwrite.pair()
        rows.append((target.id, allow.value, deny.value))
    return sorted(rows)


class FinalSync(discord.Client):
    def __init__(self, *, apply_changes: bool, **kwargs):
        super().__init__(**kwargs)
        self.apply_changes = apply_changes

    async def on_ready(self):
        try:
            guild = self.get_guild(SERVER_ID)
            if guild is None:
                raise RuntimeError(f"Guild {SERVER_ID} not found.")

            print(f"Guild: {guild.name} ({guild.id})")
            print(f"Mode: {'APPLY' if self.apply_changes else 'DRY RUN'}")
            print()

            for channel_id, label in TARGETS.items():
                channel = guild.get_channel(channel_id)

                if channel is None:
                    print(f"ERROR: {label} ({channel_id}) not found")
                    continue

                category = channel.category
                if category is None:
                    print(f"ERROR: {label} has no category")
                    continue

                channel_sig = overwrite_signature(channel.overwrites)
                category_sig = overwrite_signature(category.overwrites)
                already_equal = channel_sig == category_sig

                print(f"{label}")
                print(f"  channel type: {channel.type}")
                print(f"  permissions_synced: {getattr(channel, 'permissions_synced', False)}")
                print(f"  overwrite set equals category: {already_equal}")

                if getattr(channel, "permissions_synced", False) and already_equal:
                    print("  OK - already synced")
                    continue

                if not self.apply_changes:
                    print("  WOULD COPY category overwrites")
                    continue

                # Force an exact copy of the category overwrites rather than relying
                # on edit(sync_permissions=True), which did not persist for these
                # three channels in the previous pass.
                await channel.edit(
                    overwrites=category.overwrites,
                    reason="Force final OZY category permission sync",
                )

                # Re-fetch from Discord so verification uses server state, not cache.
                refreshed = await self.fetch_channel(channel.id)
                refreshed_equal = overwrite_signature(refreshed.overwrites) == category_sig
                refreshed_synced = getattr(refreshed, "permissions_synced", False)

                print(f"  AFTER: permissions_synced={refreshed_synced}")
                print(f"  AFTER: overwrite set equals category={refreshed_equal}")

                if not refreshed_equal:
                    print("  WARNING: Discord did not retain the category overwrite set.")
                elif refreshed_synced:
                    print("  OK - synced")
                else:
                    print(
                        "  NOTE: permissions are now identical even though Discord "
                        "still reports permissions_synced=false."
                    )

            print()
            translator = guild.get_role(TRANSLATOR_ROLE_ID)
            if translator is None:
                print("WARNING: OZY Translator role not found.")
            elif translator.permissions.administrator:
                print("ACTION REQUIRED: OZY Translator still has Administrator.")
                print(
                    "Administrator bypasses the ADMIN category deny. Remove that "
                    "permission from the Translator application's Discord permissions."
                )
            else:
                print("OK: OZY Translator does not have Administrator.")

            print()
            if self.apply_changes:
                print("Next: py export_discord_channels.py")
            else:
                print("No changes made.")
                print("Apply with: py force_final_ozy_sync.py --apply")
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

    client = FinalSync(apply_changes=args.apply, intents=intents)
    await client.start(TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
