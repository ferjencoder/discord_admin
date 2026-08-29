from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

API_BASE = "https://discord.com/api/v10"

# Access-control roles must never be assigned by native onboarding.
# Language and G/M/S roles are metadata only and are intentionally allowed.
PROTECTED_ROLE_IDS = {
    "1536675686029467658": "Leader",
    "1536676026288181319": "Superior",
    "1542765585606254634": "Unverified",
    "1542765587023925298": "Verified",
    "1542765588475281408": "Special Access",
}

METADATA_ROLE_IDS = {
    "1536541947173408839": "EN",
    "1536542118611263609": "ES",
    "1540062337111953448": "AR",
    "1536542327714095166": "DE",
    "1536542281593782292": "FR",
    "1540062640171126904": "NO",
    "1536542372127572028": "CEB",
    "1536542159459586128": "PT",
    "1536542203319681146": "SV",
    "1540062171965431949": "RU",
    "1543136199281999882": "G1",
    "1543136201244934144": "G2",
    "1543136203040100456": "G3",
    "1543136205636640778": "G4",
    "1543136208300019764": "G5",
    "1543136210539651102": "G6",
    "1543136212225884230": "G7",
    "1543136214360531044": "G8",
    "1543136216562671708": "G9",
    "1543136218118885418": "M1",
    "1543136220094136351": "M2",
    "1543136221851553812": "M3",
    "1543136224225656845": "M4",
    "1543136225743872052": "M5",
    "1543136227874578473": "M6",
    "1543136229904879727": "M7",
    "1543136231741980735": "M8",
    "1543136234199584788": "M9",
    "1543136236250730597": "S1",
    "1543136237739704415": "S2",
    "1543136239623086082": "S3",
    "1543136241586016396": "S4",
    "1543136243347357716": "S5",
    "1543136245360758834": "S6",
    "1543136247000727622": "S7",
    "1543136248628256860": "S8",
    "1543136250419085313": "S9",
    "1536560870530879509": "Events",
    "1536561639388749955": "Silent",
}


KNOWN_CHANNELS = {
    "1536546915678949488": "rules",
    "1536548034891227166": "welcome",
    "1540724448649289778": "getting-started",
    "1542964851805257858": "verification-help",
    "1536548269080051762": "announcements",
    "1540794850671198288": "chests",
    "1540500516994293920": "today",
    "1536548380116000819": "calendar",
    "1540500979336617985": "events",
    "1540581855898767381": "away",
    "1540202225110880297": "ozy-copy",
    "1536666247981310012": "resources/epics-guide",
    "1536508569338253332": "english",
    "1536508632785748108": "español",
    "1538166161873567794": "العربية",
    "1536508684081827880": "deutsch",
    "1536525721584017548": "français",
    "1538637390149587025": "norsk",
    "1536508734530920570": "bisaya",
    "1536510376617967616": "português",
    "1536510464144441515": "svenska",
    "1538166128017412096": "русский",
    "1540191170250940416": "citadels-talk",
    "1540191229302808596": "stack-talk",
    "1540191303403569222": "cpruns-talk",
    "1540191372668182548": "events-talk",
    "1540191556194279455": "game-talk/epics-guide",
    "1541143977917681704": "leadership/schedule",
    "1540798265614663710": "leadership/chat",
    "1540553126254223481": "leadership/library",
    "1540553166309818438": "leadership/templates",
    "1540784717161300040": "leadership/voice",
}

DEFAULT_SAFE_PUBLIC = {
    "1536546915678949488",
    "1536548034891227166",
    "1540724448649289778",
    "1542964851805257858",
}


def load_dotenv_simple(path: Path = Path(".env")) -> None:
    """Minimal .env loader so this script has no third-party dependency."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or key in os.environ:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]
        os.environ[key] = value


def get_settings() -> tuple[str, str]:
    load_dotenv_simple()
    token = os.getenv("DISCORD_TOKEN", "").strip()
    guild_id = os.getenv("SERVER_ID", "").strip()
    if not token:
        raise SystemExit("DISCORD_TOKEN is missing from the environment/.env")
    if not guild_id:
        raise SystemExit("SERVER_ID is missing from the environment/.env")
    return token, guild_id


def api_request(method: str, token: str, guild_id: str, payload: dict | None = None):
    url = f"{API_BASE}/guilds/{guild_id}/onboarding"
    headers = {
        "Authorization": f"Bot {token}",
        "User-Agent": "OZY-Onboarding-Tool/1.0",
        "Accept": "application/json",
    }
    data = None
    if payload is not None:
        data = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers["Content-Type"] = "application/json"
        headers["X-Audit-Log-Reason"] = "OZY onboarding configuration update"

    request = urllib.request.Request(url, data=data, headers=headers, method=method)

    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            body = response.read()
            if not body:
                return {}
            return json.loads(body.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"Discord API HTTP {exc.code}: {body}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"Discord API request failed: {exc}") from exc


def clean_for_put(config: dict) -> dict:
    """
    Convert a GET response into a valid Modify Guild Onboarding payload.
    Discord requires emoji_id/name/animated on updates rather than the nested emoji object.
    """
    payload = {
        key: config[key]
        for key in ("prompts", "default_channel_ids", "enabled", "mode")
        if key in config
    }

    for prompt in payload.get("prompts", []):
        for option in prompt.get("options", []):
            emoji = option.pop("emoji", None)
            if emoji is not None:
                option.setdefault("emoji_id", emoji.get("id"))
                option.setdefault("emoji_name", emoji.get("name"))
                option.setdefault("emoji_animated", bool(emoji.get("animated", False)))

    return payload


def name_channel(channel_id: str) -> str:
    return KNOWN_CHANNELS.get(str(channel_id), str(channel_id))


def summarize(config: dict) -> None:
    print(f"Guild: {config.get('guild_id', '(payload)')}")
    print(f"Enabled: {config.get('enabled')}")
    print(f"Mode: {config.get('mode')} (0=DEFAULT, 1=ADVANCED)")
    defaults = [str(x) for x in config.get("default_channel_ids", [])]
    print(f"Default channels: {len(defaults)}")
    for cid in defaults:
        marker = "SAFE PUBLIC" if cid in DEFAULT_SAFE_PUBLIC else ""
        print(f"  - {name_channel(cid)} ({cid}) {marker}".rstrip())

    prompts = config.get("prompts", [])
    print(f"Prompts: {len(prompts)}")
    for idx, prompt in enumerate(prompts, 1):
        where = "PRE-JOIN" if prompt.get("in_onboarding") else "POST-JOIN"
        required = "required" if prompt.get("required") else "optional"
        select = "single" if prompt.get("single_select") else "multi"
        print(f"  {idx}. {prompt.get('title', '(untitled)')} [{where}, {required}, {select}]")
        for option in prompt.get("options", []):
            channels = [name_channel(str(x)) for x in option.get("channel_ids", [])]
            roles = [
                PROTECTED_ROLE_IDS.get(str(x), METADATA_ROLE_IDS.get(str(x), str(x)))
                for x in option.get("role_ids", [])
            ]
            print(f"       - {option.get('title', '(untitled)')}")
            if channels:
                print(f"         channels: {', '.join(channels)}")
            if roles:
                print(f"         roles: {', '.join(roles)}")


def lint(config: dict) -> list[str]:
    warnings: list[str] = []

    for prompt in config.get("prompts", []):
        ptitle = prompt.get("title", "(untitled)")
        for option in prompt.get("options", []):
            otitle = option.get("title", "(untitled)")
            for role_id in option.get("role_ids", []):
                role_id = str(role_id)
                if role_id in PROTECTED_ROLE_IDS:
                    warnings.append(
                        f'Prompt "{ptitle}" option "{otitle}" assigns protected role '
                        f'{PROTECTED_ROLE_IDS[role_id]} ({role_id}). '
                        "OZY access policy says native onboarding must not grant access-control roles."
                    )

    for channel_id in map(str, config.get("default_channel_ids", [])):
        if channel_id not in DEFAULT_SAFE_PUBLIC:
            warnings.append(
                f"Default channel {name_channel(channel_id)} ({channel_id}) is not in "
                "the OZY safe-public default set. Review before applying."
            )

    return warnings


def timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def save_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def cmd_export(token: str, guild_id: str, output: Path | None) -> None:
    current = api_request("GET", token, guild_id)
    if output is None:
        output = Path("exports") / f"discord_onboarding_{guild_id}_{timestamp()}.json"
    save_json(output, current)
    summarize(current)
    warnings = lint(current)
    if warnings:
        print("\nOZY POLICY WARNINGS")
        for warning in warnings:
            print(f"  WARNING: {warning}")
    print(f"\nSaved: {output.resolve()}")


def cmd_show(config_path: Path) -> None:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    summarize(config)
    warnings = lint(config)
    if warnings:
        print("\nOZY POLICY WARNINGS")
        for warning in warnings:
            print(f"  WARNING: {warning}")


def cmd_apply(token: str, guild_id: str, config_path: Path, apply_changes: bool) -> None:
    desired_raw = json.loads(config_path.read_text(encoding="utf-8"))
    desired = clean_for_put(desired_raw)
    warnings = lint(desired)

    print("DESIRED ONBOARDING")
    summarize(desired)

    if warnings:
        print("\nOZY POLICY WARNINGS")
        for warning in warnings:
            print(f"  WARNING: {warning}")
        print("\nRefusing to apply while OZY policy warnings exist.")
        raise SystemExit(2)

    current = api_request("GET", token, guild_id)
    backup = Path("exports") / f"discord_onboarding_BACKUP_{guild_id}_{timestamp()}.json"
    save_json(backup, current)
    print(f"\nBackup saved: {backup.resolve()}")

    if not apply_changes:
        print("\nDRY RUN ONLY - nothing changed.")
        print(f"Apply with: py {Path(sys.argv[0]).name} apply {config_path} --apply")
        return

    updated = api_request("PUT", token, guild_id, desired)
    output = Path("exports") / f"discord_onboarding_AFTER_{guild_id}_{timestamp()}.json"
    save_json(output, updated)
    print("\nAPPLIED.")
    summarize(updated)
    print(f"Saved response: {output.resolve()}")


def build_parser():
    parser = argparse.ArgumentParser(
        description="Export, inspect, and safely apply Discord Community Onboarding."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    export = sub.add_parser("export", help="Export current onboarding from Discord.")
    export.add_argument("-o", "--output", type=Path)

    show = sub.add_parser("show", help="Show/lint an onboarding JSON file.")
    show.add_argument("config", type=Path)

    apply_cmd = sub.add_parser("apply", help="Dry-run or apply an onboarding JSON file.")
    apply_cmd.add_argument("config", type=Path)
    apply_cmd.add_argument(
        "--apply",
        action="store_true",
        help="Actually PUT the configuration to Discord. Without this flag it is dry-run.",
    )

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "show":
        cmd_show(args.config)
        return

    token, guild_id = get_settings()

    if args.command == "export":
        cmd_export(token, guild_id, args.output)
    elif args.command == "apply":
        cmd_apply(token, guild_id, args.config, args.apply)


if __name__ == "__main__":
    main()
