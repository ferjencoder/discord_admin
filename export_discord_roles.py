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
OUT_DIR = Path(os.getenv("ROLE_EXPORT_DIR", "exports"))


def role_to_dict(role: discord.Role) -> dict:
    return {
        "id": str(role.id),
        "name": role.name,
        "position": role.position,
        "color": role.color.value,
        "color_hex": f"#{role.color.value:06X}",
        "permissions": role.permissions.value,
        "hoist": role.hoist,
        "mentionable": role.mentionable,
        "managed": role.managed,
        "is_default": role.is_default(),
    }


class RoleExporter(discord.Client):
    async def on_ready(self) -> None:
        try:
            guild = self.get_guild(SERVER_ID)
            if guild is None:
                guild = await self.fetch_guild(SERVER_ID)
            roles = [role_to_dict(r) for r in reversed(guild.roles)]
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            OUT_DIR.mkdir(parents=True, exist_ok=True)
            payload = {
                "schema_version": 1,
                "exported_at_utc": datetime.now(timezone.utc).isoformat(),
                "guild_id": str(guild.id),
                "guild_name": guild.name,
                "roles": roles,
            }
            json_path = OUT_DIR / f"discord_roles_{guild.id}_{stamp}.json"
            csv_path = OUT_DIR / f"discord_roles_{guild.id}_{stamp}.csv"
            json_path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
            with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
                writer = csv.DictWriter(f, fieldnames=[
                    "id", "name", "position", "color_hex", "color",
                    "permissions", "hoist", "mentionable", "managed", "is_default",
                ])
                writer.writeheader()
                writer.writerows(roles)
            print(f"Guild: {guild.name} ({guild.id})")
            print(f"Roles exported: {len(roles)}")
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
    client = RoleExporter(intents=intents)
    await client.start(TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
