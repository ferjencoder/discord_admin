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
