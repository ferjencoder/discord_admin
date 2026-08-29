from __future__ import annotations

import argparse
import asyncio
import os
from pathlib import Path

import discord


CONFIRM_TEXT = "ERASE-ALL-MESSAGES"


def load_dotenv(path: Path = Path(".env")) -> None:
    """Tiny .env loader so the script does not require python-dotenv."""
    if not path.exists():
        return

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()

        if (
            len(value) >= 2
            and value[0] == value[-1]
            and value[0] in {"'", '"'}
        ):
            value = value[1:-1]

        if key and key not in os.environ:
            os.environ[key] = value


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Delete every deletable message from every accessible text/thread "
            "channel in one Discord guild. Channels, roles and permissions are "
            "NOT deleted."
        )
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Actually delete messages. Without this flag the script is dry-run only.",
    )
    parser.add_argument(
        "--confirm",
        default="",
        help=f"Required with --apply. Must equal: {CONFIRM_TEXT}",
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="CHANNEL_ID",
        help="Channel/thread ID to exclude. Can be supplied multiple times.",
    )
    return parser.parse_args()


async def collect_archived_threads(channel: discord.abc.GuildChannel) -> list[discord.Thread]:
    """Best-effort collection of archived threads from text/forum channels."""
    threads: list[discord.Thread] = []

    if not isinstance(channel, (discord.TextChannel, discord.ForumChannel)):
        return threads

    try:
        async for thread in channel.archived_threads(limit=None):
            threads.append(thread)
    except (discord.Forbidden, discord.HTTPException, AttributeError):
        pass

    return threads


async def wipe_message_channel(
    channel: discord.abc.Messageable,
    *,
    label: str,
) -> tuple[int, int]:
    deleted = 0
    failed = 0

    try:
        async for message in channel.history(limit=None, oldest_first=False):
            try:
                await message.delete()
                deleted += 1

                if deleted % 100 == 0:
                    print(f"    {label}: {deleted} deleted")

            except discord.NotFound:
                # Already removed while processing.
                continue
            except (discord.Forbidden, discord.HTTPException) as exc:
                failed += 1
                print(
                    f"    [WARN] Could not delete message {message.id} "
                    f"in {label}: {type(exc).__name__}: {exc}"
                )
    except discord.Forbidden as exc:
        print(f"    [SKIP] Cannot read history in {label}: {exc}")
        failed += 1
    except discord.HTTPException as exc:
        print(f"    [WARN] History read failed in {label}: {exc}")
        failed += 1

    return deleted, failed


async def main() -> None:
    args = parse_args()
    exclude_ids = {int(value) for value in args.exclude}

    load_dotenv()

    token = os.getenv("DISCORD_TOKEN", "").strip()
    guild_id_raw = os.getenv("SERVER_ID", "").strip()

    if not token:
        raise SystemExit("DISCORD_TOKEN is missing from .env/environment.")
    if not guild_id_raw:
        raise SystemExit("SERVER_ID is missing from .env/environment.")

    guild_id = int(guild_id_raw)

    if args.apply and args.confirm != CONFIRM_TEXT:
        raise SystemExit(
            f"--apply requires: --confirm {CONFIRM_TEXT}\n"
            "Nothing was deleted."
        )

    intents = discord.Intents.none()
    intents.guilds = True

    client = discord.Client(intents=intents)

    @client.event
    async def on_ready() -> None:
        try:
            guild = client.get_guild(guild_id)
            if guild is None:
                raise SystemExit(f"Guild {guild_id} is not visible to this bot.")

            print(f"Guild: {guild.name} ({guild.id})")
            print()

            base_channels: list[discord.abc.GuildChannel] = [
                ch
                for ch in guild.channels
                if isinstance(
                    ch,
                    (
                        discord.TextChannel,
                        discord.ForumChannel,
                    ),
                )
            ]

            # Active threads are not included in guild.channels.
            active_threads = list(guild.threads)

            archived_threads: list[discord.Thread] = []
            for ch in base_channels:
                archived_threads.extend(await collect_archived_threads(ch))

            # Deduplicate all threads.
            thread_by_id = {
                thread.id: thread
                for thread in [*active_threads, *archived_threads]
            }

            targets: list[tuple[int, str, discord.abc.Messageable]] = []

            for ch in base_channels:
                # Forum channels themselves do not have a normal message history;
                # their posts are threads. Text/announcement channels do.
                if isinstance(ch, discord.TextChannel):
                    targets.append((ch.id, f"#{ch.name}", ch))

            for thread in thread_by_id.values():
                parent_name = getattr(thread.parent, "name", "unknown-parent")
                targets.append(
                    (
                        thread.id,
                        f"#{parent_name} / thread:{thread.name}",
                        thread,
                    )
                )

            targets = [
                target for target in targets if target[0] not in exclude_ids
            ]
            targets.sort(key=lambda item: item[1].casefold())

            if not args.apply:
                print("DRY RUN - nothing will be deleted.")
                print()
                print("Message-bearing channels/threads that would be wiped:")
                for channel_id, label, _channel in targets:
                    print(f"  {label} ({channel_id})")

                print()
                print(f"Targets: {len(targets)}")
                print()
                print("To erase messages permanently:")
                print(
                    f"  py {Path(__file__).name} "
                    f"--apply --confirm {CONFIRM_TEXT}"
                )
                return

            print("APPLY MODE")
            print("Every deletable message in every listed channel/thread will be removed.")
            print("This cannot be undone.")
            print()

            total_deleted = 0
            total_failed = 0

            for index, (_channel_id, label, channel) in enumerate(targets, start=1):
                print(f"[{index}/{len(targets)}] {label}")
                deleted, failed = await wipe_message_channel(
                    channel,
                    label=label,
                )
                total_deleted += deleted
                total_failed += failed
                print(f"    done: {deleted} deleted, {failed} failed")

            print()
            print("=" * 60)
            print(f"Deleted messages: {total_deleted}")
            print(f"Failures/skips:   {total_failed}")
            print("Channels/roles/permissions were not deleted.")

        finally:
            await client.close()

    await client.start(token)


if __name__ == "__main__":
    asyncio.run(main())
