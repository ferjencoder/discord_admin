from __future__ import annotations

import asyncio
import csv
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import discord
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
SERVER_ID = int(os.getenv("SERVER_ID", "0") or 0)
OUT_DIR = Path(os.getenv("CHANNEL_EXPORT_DIR", "exports"))


def overwrite_to_dict(target, overwrite: discord.PermissionOverwrite) -> dict:
    allow, deny = overwrite.pair()

    if isinstance(target, discord.Role):
        target_type = "role"
        target_name = target.name
        target_id = str(target.id)
    else:
        target_type = "member"
        target_name = str(target)
        target_id = str(target.id)

    explicit = {}
    for name, value in overwrite:
        if value is not None:
            explicit[name] = value

    return {
        "target_type": target_type,
        "target_id": target_id,
        "target_name": target_name,
        "allow": allow.value,
        "deny": deny.value,
        "explicit": explicit,
    }


def channel_to_dict(channel) -> dict:
    category = getattr(channel, "category", None)

    payload = {
        "id": str(channel.id),
        "name": channel.name,
        "type": str(channel.type),
        "position": channel.position,
        "category_id": str(category.id) if category else None,
        "category_name": category.name if category else None,
        "permissions_synced": getattr(channel, "permissions_synced", None),
        "overwrites": [
            overwrite_to_dict(target, overwrite)
            for target, overwrite in channel.overwrites.items()
        ],
    }

    for attr in (
        "topic",
        "nsfw",
        "slowmode_delay",
        "default_auto_archive_duration",
        "bitrate",
        "user_limit",
        "rtc_region",
        "video_quality_mode",
    ):
        if hasattr(channel, attr):
            value = getattr(channel, attr)
            if hasattr(value, "value"):
                value = value.value
            payload[attr] = value

    return payload


class ChannelExporter(discord.Client):
    async def on_ready(self) -> None:
        try:
            guild = self.get_guild(SERVER_ID)
            if guild is None:
                guild = await self.fetch_guild(SERVER_ID)

            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            OUT_DIR.mkdir(parents=True, exist_ok=True)

            roles = [
                {
                    "id": str(r.id),
                    "name": r.name,
                    "position": r.position,
                    "permissions": r.permissions.value,
                    "managed": r.managed,
                    "hoist": r.hoist,
                    "mentionable": r.mentionable,
                }
                for r in reversed(guild.roles)
            ]

            channels = [channel_to_dict(c) for c in guild.channels]

            payload = {
                "schema_version": 1,
                "exported_at_utc": datetime.now(timezone.utc).isoformat(),
                "guild_id": str(guild.id),
                "guild_name": guild.name,
                "roles": roles,
                "channels": channels,
            }

            json_path = OUT_DIR / f"discord_channels_{guild.id}_{stamp}.json"
            csv_path = OUT_DIR / f"discord_channel_overwrites_{guild.id}_{stamp}.csv"

            json_path.write_text(
                json.dumps(payload, indent=2, ensure_ascii=False),
                encoding="utf-8",
            )

            rows = []
            for c in channels:
                if not c["overwrites"]:
                    rows.append({
                        "channel_id": c["id"],
                        "channel_name": c["name"],
                        "channel_type": c["type"],
                        "category_name": c["category_name"] or "",
                        "permissions_synced": c["permissions_synced"],
                        "target_type": "",
                        "target_id": "",
                        "target_name": "",
                        "allow": "",
                        "deny": "",
                        "explicit": "",
                    })
                    continue

                for ow in c["overwrites"]:
                    rows.append({
                        "channel_id": c["id"],
                        "channel_name": c["name"],
                        "channel_type": c["type"],
                        "category_name": c["category_name"] or "",
                        "permissions_synced": c["permissions_synced"],
                        "target_type": ow["target_type"],
                        "target_id": ow["target_id"],
                        "target_name": ow["target_name"],
                        "allow": ow["allow"],
                        "deny": ow["deny"],
                        "explicit": json.dumps(ow["explicit"], ensure_ascii=False),
                    })

            with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=[
                        "channel_id",
                        "channel_name",
                        "channel_type",
                        "category_name",
                        "permissions_synced",
                        "target_type",
                        "target_id",
                        "target_name",
                        "allow",
                        "deny",
                        "explicit",
                    ],
                )
                writer.writeheader()
                writer.writerows(rows)

            print(f"Guild: {guild.name} ({guild.id})")
            print(f"Channels exported: {len(channels)}")
            print(f"JSON: {json_path.resolve()}")
            print(f"CSV:  {csv_path.resolve()}")
        finally:
            await self.close()


async def main() -> None:
    if not TOKEN:
        raise SystemExit("DISCORD_TOKEN is missing.")
    if not SERVER_ID:
        raise SystemExit("SERVER_ID is missing.")

    intents = discord.Intents.none()
    intents.guilds = True

    client = ChannelExporter(intents=intents)
    await client.start(TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
