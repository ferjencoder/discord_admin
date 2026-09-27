from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

import discord


VIEW_CHANNEL = 1 << 10  # discord.Permissions.view_channel


def load_dotenv(path: Path = Path(".env")) -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        if key and key not in os.environ:
            os.environ[key] = value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Restrict each OZY language channel to the language selected in "
            "Discord Onboarding while keeping the normal member-access role."
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually change Discord. Without this flag the script is dry-run only.",
    )
    parser.add_argument(
        "--config",
        default="config/discord/onboarding.json",
        help="Path to the canonical onboarding JSON.",
    )
    return parser.parse_args()


def load_language_pairs(config_path: Path) -> list[tuple[str, int, int]]:
    payload = json.loads(config_path.read_text(encoding="utf-8"))

    prompt = next(
        (
            p
            for p in payload.get("prompts", [])
            if str(p.get("title", "")).strip().casefold()
            == "what language do you prefer?".casefold()
        ),
        None,
    )
    if not prompt:
        raise SystemExit("Language onboarding prompt not found in config.")

    pairs: list[tuple[str, int, int]] = []
    for option in prompt.get("options", []):
        title = str(option.get("title", "")).strip()
        role_ids = option.get("role_ids") or []
        channel_ids = option.get("channel_ids") or []

        # One of the roles is the language role and one may be the normal
        # member-access role. The language role is the role that is NOT
        # VERIFIED_ROLE_ID.
        if not channel_ids:
            raise SystemExit(f"{title}: no language channel configured.")

        pairs.append((title, 0, int(channel_ids[0])))

    return pairs


def role_map_from_env() -> dict[str, int]:
    raw = os.getenv("LANGUAGE_ROLE_MAP", "").strip()
    if not raw:
        raise SystemExit("LANGUAGE_ROLE_MAP is missing from .env/environment.")

    result: dict[str, int] = {}
    for chunk in raw.split(","):
        chunk = chunk.strip()
        if not chunk or ":" not in chunk:
            continue
        code, role_id = chunk.split(":", 1)
        result[code.strip().upper()] = int(role_id.strip())

    if not result:
        raise SystemExit("LANGUAGE_ROLE_MAP could not be parsed.")
    return result


LANGUAGE_TITLE_TO_CODE = {
    "English": "EN",
    "Español": "ES",
    "Português": "PT",
    "Svenska": "SV",
    "Deutsch": "DE",
    "Bisaya": "CEB",
    "Français": "FR",
    "Русский": "RU",
    "العربية": "AR",
    "Norsk": "NO",
}


def set_view_bit(
    overwrites: dict[discord.abc.Snowflake, discord.PermissionOverwrite],
    target: discord.abc.Snowflake,
    value: bool | None,
) -> None:
    overwrite = overwrites.get(target, discord.PermissionOverwrite())
    overwrite.view_channel = value
    overwrites[target] = overwrite


async def main() -> None:
    args = parse_args()
    load_dotenv()

    token = os.getenv("DISCORD_TOKEN", "").strip()
    guild_id_raw = os.getenv("SERVER_ID", "").strip()
    verified_role_id_raw = os.getenv("VERIFIED_ROLE_ID", "").strip()

    if not token:
        raise SystemExit("DISCORD_TOKEN is missing.")
    if not guild_id_raw:
        raise SystemExit("SERVER_ID is missing.")
    if not verified_role_id_raw:
        raise SystemExit("VERIFIED_ROLE_ID is missing.")

    guild_id = int(guild_id_raw)
    verified_role_id = int(verified_role_id_raw)
    language_role_map = role_map_from_env()

    config_pairs = load_language_pairs(Path(args.config))

    intents = discord.Intents.none()
    intents.guilds = True
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready() -> None:
        try:
            guild = client.get_guild(guild_id)
            if guild is None:
                raise SystemExit(f"Guild {guild_id} is not visible to the bot.")

            verified_role = guild.get_role(verified_role_id)
            if verified_role is None:
                raise SystemExit(f"Member-access role {verified_role_id} not found.")

            everyone = guild.default_role

            print(f"Guild: {guild.name} ({guild.id})")
            print(f"Member-access role: {verified_role.name} ({verified_role.id})")
            print()
            print("LANGUAGE CHANNEL VISIBILITY PLAN")
            print("=" * 52)
            print("@everyone     -> View Channel: DENY")
            print(f"{verified_role.name:<13} -> View Channel: DENY on language channels")
            print("selected language role -> View Channel: ALLOW")
            print("all other existing permission bits are preserved")
            print()

            changes = 0

            for title, _unused, channel_id in config_pairs:
                code = LANGUAGE_TITLE_TO_CODE.get(title)
                if not code:
                    raise SystemExit(f"No language-code mapping for onboarding option: {title}")

                role_id = language_role_map.get(code)
                if not role_id:
                    raise SystemExit(f"{code} is missing from LANGUAGE_ROLE_MAP.")

                role = guild.get_role(role_id)
                channel = guild.get_channel(channel_id)

                if role is None:
                    raise SystemExit(f"{code} role {role_id} not found.")
                if not isinstance(channel, discord.TextChannel):
                    raise SystemExit(f"{title} channel {channel_id} is not a text channel.")

                current = dict(channel.overwrites)
                desired = dict(current)

                # The normal member-access role opens the rest of the clan, but
                # must NOT expose every language channel.
                set_view_bit(desired, everyone, False)
                set_view_bit(desired, verified_role, False)

                # Only the selected language role restores visibility.
                set_view_bit(desired, role, True)

                # Remove stale View Channel ALLOWs from the other language roles.
                # We leave them as "unset", not DENY, because the member-access
                # role already denies viewing and the correct language role may
                # explicitly allow it.
                for other_code, other_role_id in language_role_map.items():
                    if other_role_id == role.id:
                        continue
                    other_role = guild.get_role(other_role_id)
                    if other_role is not None and other_role in desired:
                        set_view_bit(desired, other_role, None)
                        if desired[other_role].is_empty():
                            desired.pop(other_role, None)

                current_view = current.get(role, discord.PermissionOverwrite()).view_channel
                verified_view = current.get(
                    verified_role, discord.PermissionOverwrite()
                ).view_channel

                print(
                    f"#{channel.name:<14} -> {code:<3} "
                    f"(language currently={current_view}, "
                    f"{verified_role.name} currently={verified_view})"
                )

                if current != desired:
                    changes += 1
                    if args.apply:
                        await channel.edit(
                            overwrites=desired,
                            reason="OZY language-channel visibility: selected language only",
                        )
                        print("   APPLIED")
                    else:
                        print("   WOULD CHANGE")
                else:
                    print("   already correct")

            print()
            print("=" * 52)
            print(f"Channels needing changes: {changes}")

            if not args.apply:
                print("DRY RUN ONLY - Discord was not changed.")
                print(
                    f"Apply with: py {Path(__file__).name} --apply"
                )
            else:
                print("APPLIED.")
                print()
                print("Expected result:")
                print("  EN member -> #english only")
                print("  ES member -> #español only")
                print("  etc.")
                print("  Leader/Admin/Translator access is preserved if already configured.")

        finally:
            await client.close()

    await client.start(token)


if __name__ == "__main__":
    asyncio.run(main())
