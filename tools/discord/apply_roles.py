from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

import discord
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN", "").strip()
SERVER_ID = int(os.getenv("SERVER_ID", "0") or 0)


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Create/update OZY Discord roles from a blueprint. Dry-run by default.")
    p.add_argument("blueprint", nargs="?", default="config/discord/roles_blueprint.json")
    p.add_argument("--apply", action="store_true", help="Actually create/update roles.")
    return p.parse_args()


def make_permissions(value) -> discord.Permissions:
    if isinstance(value, int):
        return discord.Permissions(value)
    if isinstance(value, str) and value.isdigit():
        return discord.Permissions(int(value))
    perms = discord.Permissions.none()
    if isinstance(value, list):
        for name in value:
            if not hasattr(perms, name):
                raise ValueError(f"Unknown Discord permission: {name}")
            setattr(perms, name, True)
    return perms


class RoleApplier(discord.Client):
    def __init__(self, *, blueprint: dict, apply_changes: bool, **kwargs):
        super().__init__(**kwargs)
        self.blueprint = blueprint
        self.apply_changes = apply_changes

    async def on_ready(self) -> None:
        try:
            guild = self.get_guild(SERVER_ID)
            if guild is None:
                guild = await self.fetch_guild(SERVER_ID)
            me = guild.me
            if me is None:
                raise RuntimeError("Could not resolve OZY Admin member in the guild.")
            print(f"Guild: {guild.name} ({guild.id})")
            print(f"Mode: {'APPLY' if self.apply_changes else 'DRY RUN'}")
            print(f"Bot highest role: {me.top_role.name} (position {me.top_role.position})")
            existing = {r.name.casefold(): r for r in guild.roles if not r.is_default()}
            created = updated = unchanged = skipped = 0
            for spec in self.blueprint.get("roles", []):
                name = str(spec.get("name", "")).strip()
                if not name:
                    continue
                if spec.get("managed", False):
                    print(f"SKIP managed blueprint role: {name}")
                    skipped += 1
                    continue
                role = existing.get(name.casefold())
                desired_color = discord.Color(int(spec.get("color", 0)))
                desired_perms = make_permissions(spec.get("permissions", []))
                desired_hoist = bool(spec.get("hoist", False))
                desired_mentionable = bool(spec.get("mentionable", False))
                if role is None:
                    print(f"CREATE {name}")
                    if self.apply_changes:
                        role = await guild.create_role(
                            name=name,
                            permissions=desired_perms,
                            color=desired_color,
                            hoist=desired_hoist,
                            mentionable=desired_mentionable,
                            reason="OZY role blueprint",
                        )
                        existing[name.casefold()] = role
                    created += 1
                    continue
                if role.managed:
                    print(f"SKIP managed server role: {role.name}")
                    skipped += 1
                    continue
                changes = {}
                if role.color.value != desired_color.value:
                    changes["color"] = desired_color
                if role.permissions.value != desired_perms.value:
                    changes["permissions"] = desired_perms
                if role.hoist != desired_hoist:
                    changes["hoist"] = desired_hoist
                if role.mentionable != desired_mentionable:
                    changes["mentionable"] = desired_mentionable
                if changes:
                    print(f"UPDATE {role.name}: {', '.join(changes)}")
                    if self.apply_changes:
                        if role >= me.top_role:
                            print("  CANNOT UPDATE: move OZY Admin above this role first.")
                            skipped += 1
                            continue
                        await role.edit(reason="OZY role blueprint", **changes)
                    updated += 1
                else:
                    print(f"OK     {role.name}")
                    unchanged += 1
            print(f"Summary: create={created}, update={updated}, unchanged={unchanged}, skipped={skipped}")
            if not self.apply_changes:
                print("No Discord changes were made. Re-run with --apply when ready.")
        finally:
            await self.close()


async def main() -> None:
    args = parse_args()
    if not TOKEN:
        raise SystemExit("DISCORD_TOKEN is missing.")
    if not SERVER_ID:
        raise SystemExit("SERVER_ID is missing.")
    path = Path(args.blueprint)
    if not path.exists():
        raise SystemExit(f"Blueprint not found: {path}")
    blueprint = json.loads(path.read_text(encoding="utf-8"))
    intents = discord.Intents.none()
    intents.guilds = True
    client = RoleApplier(blueprint=blueprint, apply_changes=args.apply, intents=intents)
    await client.start(TOKEN)


if __name__ == "__main__":
    asyncio.run(main())
