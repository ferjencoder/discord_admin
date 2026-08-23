from __future__ import annotations

from datetime import date


def safe_code_block(text: str, language: str = "text") -> str:
    # Prevent user/admin content from terminating the fenced block.
    safe = text.replace("```", "`\u200b``")
    return f"```{language}\n{safe}\n```"


def format_chat_directory(chats: list[dict[str, str]]) -> str:
    parts = ["# OZY Clan Chat Directory"]
    for item in chats:
        parts.append(f"### {item['label']}\n{safe_code_block(item['name'])}")
    return "\n\n".join(parts)


def format_schedule(day: date, items) -> str:
    heading = day.strftime("%A %d %B %Y")
    parts = [f"## OZY Schedule - {heading}"]
    if not items:
        parts.append("No scheduled items have been configured for today.")
        return "\n\n".join(parts)

    for item in items:
        line = f"**{item.time} - {item.title}**"
        if item.details:
            line += f"\n{item.details}"
        parts.append(line)
    return "\n\n".join(parts)


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 1)] + "…"


def _ranking_period(start: str, end: str, fallback: str) -> str:
    try:
        start_day = date.fromisoformat(start).strftime("%d.%m")
        end_day = date.fromisoformat(end).strftime("%d.%m")
        return f"{start_day} TO {end_day}"
    except (TypeError, ValueError):
        return fallback.upper()


def format_chest_ranking_blocks(leaderboard, chunk_size: int = 20) -> list[str]:
    """Build Discord-copyable chest ranking blocks, using about 20 players each."""
    members = list(leaderboard.members)
    if not members:
        return []

    chunk_size = max(1, int(chunk_size))
    chunks = [members[i:i + chunk_size] for i in range(0, len(members), chunk_size)]
    period = _ranking_period(leaderboard.start, leaderboard.end, leaderboard.week_label)
    blocks: list[str] = []

    for chunk_index, chunk in enumerate(chunks, start=1):
        lines = [f"{period} - OZY CHESTS - {chunk_index}/{len(chunks)}"]
        start_rank = (chunk_index - 1) * chunk_size + 1
        for offset, member in enumerate(chunk):
            rank = start_rank + offset
            left = f"{rank:>3}. {member.name}"
            dots = "." * max(2, 34 - len(left))
            lines.append(f"{left} {dots} {member.points:,}")
        blocks.append(safe_code_block("\n".join(lines), language=""))

    return blocks
