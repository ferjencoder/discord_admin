from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DISCORD_API = "https://discord.com/api/v10"
ADMINISTRATOR = 1 << 3
VIEW_CHANNEL = 1 << 10

RESTRICTED_CATEGORY_IDS = {
    "ADMIN": 1540583740479381616,
    "LEADERSHIP": 1540527295301816411,
}

LANGUAGE_CHANNEL_IDS = {
    "EN": 1536508569338253332,
    "ES": 1536508632785748108,
    "PT": 1536510376617967616,
    "SV": 1536510464144441515,
    "DE": 1536508684081827880,
    "CEB": 1536508734530920570,
    "FR": 1536525721584017548,
    "RU": 1538166128017412096,
    "AR": 1538166161873567794,
    "NO": 1538637390149587025,
}
TROOP_METADATA_ROLE_NAMES = {
    *(f"G{i}" for i in range(1, 10)),
    *(f"M{i}" for i in range(1, 10)),
    *(f"S{i}" for i in range(1, 10)),
}


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


class Result:
    def __init__(self):
        self.failures = 0
        self.warnings = 0

    def ok(self, text: str) -> None:
        print(f"[PASS] {text}")

    def warn(self, text: str) -> None:
        self.warnings += 1
        print(f"[WARN] {text}")

    def fail(self, text: str) -> None:
        self.failures += 1
        print(f"[FAIL] {text}")


def env(name: str) -> str:
    return os.getenv(name, "").strip()


def request_json(
    url: str,
    *,
    headers: dict[str, str] | None = None,
    method: str = "GET",
    timeout: float = 12.0,
):
    req = urllib.request.Request(
        url,
        method=method,
        headers={
            "User-Agent": "OZY-Admin-Preflight/1.0",
            "Accept": "application/json",
            **(headers or {}),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as response:
            raw = response.read()
            body = None
            if raw:
                try:
                    body = json.loads(raw.decode("utf-8"))
                except Exception:
                    body = raw
            return response.status, body, dict(response.headers)
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            body = json.loads(raw.decode("utf-8")) if raw else None
        except Exception:
            body = raw.decode("utf-8", errors="replace") if raw else None
        return exc.code, body, dict(exc.headers)


def check_timezone(r: Result) -> None:
    for key in ("UTC", env("SCHEDULE_TIMEZONE") or "America/Argentina/Buenos_Aires"):
        try:
            ZoneInfo(key)
            r.ok(f"Timezone database resolves {key}")
        except ZoneInfoNotFoundError:
            r.fail(f"Timezone database cannot resolve {key} - run: py -m pip install -r requirements.txt")


def check_required_env(r: Result) -> None:
    for name in ("DISCORD_TOKEN", "SERVER_ID"):
        if env(name):
            r.ok(f"{name} is configured")
        else:
            r.fail(f"{name} is missing")

    if env("STATE_REMOTE_URL"):
        if env("STATE_REMOTE_TOKEN"):
            r.ok("Remote state token is configured")
        else:
            r.fail("STATE_REMOTE_URL is set but STATE_REMOTE_TOKEN is missing")

    if env("ROSTER_URL") and not env("OZY_DATA_API_TOKEN"):
        r.fail("ROSTER_URL is configured but OZY_DATA_API_TOKEN is missing")


def check_data_api(r: Result) -> None:
    token = env("OZY_DATA_API_TOKEN")
    headers = {"X-OZY-Admin-Token": token} if token else {}

    roster_url = env("ROSTER_URL")
    if roster_url:
        status, body, _ = request_json(roster_url, headers=headers)
        if status == 200 and isinstance(body, dict):
            members = body.get("members", body)
            if isinstance(members, dict):
                active = [
                    (name, info)
                    for name, info in members.items()
                    if isinstance(info, dict)
                    and str(info.get("status", "active")).lower() not in {"removed", "inactive", "former"}
                ]
                stable = sum(1 for _, info in active if str(info.get("user_id", "")).strip())
                r.ok(f"Roster API reachable for game-data features - {len(active)} active members, {stable} with stable user_id")
                if active and stable < len(active):
                    r.warn(f"{len(active) - stable} active roster members have no stable user_id")
            else:
                r.fail("Roster API returned JSON but no usable members object")
        else:
            r.fail(f"Roster API returned HTTP {status}: {body}")

    chest_url = env("CHEST_DATA_URL")
    if chest_url:
        status, body, _ = request_json(chest_url, headers=headers)
        if status == 200 and isinstance(body, dict):
            r.ok("Chest API reachable and returned JSON")
        else:
            r.fail(f"Chest API returned HTTP {status}: {body}")

    schedule_url = env("SCHEDULE_URL")
    if schedule_url:
        separator = "&" if "?" in schedule_url else "?"
        url = f"{schedule_url}{separator}audience=clan"
        status, body, _ = request_json(url, headers=headers)
        if status == 200 and isinstance(body, (dict, list)):
            r.ok("Clan schedule API reachable and returned JSON")
        else:
            r.fail(f"Schedule API returned HTTP {status}: {body}")


def check_state(r: Result) -> None:
    url = env("STATE_REMOTE_URL")
    token = env("STATE_REMOTE_TOKEN")
    if not url:
        r.warn("STATE_REMOTE_URL is not configured - production persistence will be local-only")
        return

    status, body, headers = request_json(
        url,
        headers={"x-ozy-state-token": token},
        method="HEAD",
    )
    if status == 200:
        r.ok("Remote state endpoint reachable and authenticated")
    elif status == 404:
        r.warn("Remote state endpoint authenticated but no snapshot exists yet")
    elif status == 401:
        r.fail("Remote state endpoint rejected STATE_REMOTE_TOKEN")
    else:
        r.fail(f"Remote state HEAD returned HTTP {status}: {body}")


def parse_id(name: str) -> int | None:
    value = env(name)
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def parse_map_ids(raw: str) -> dict[str, int]:
    result: dict[str, int] = {}
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if ":" not in part:
            continue
        key, value = part.split(":", 1)
        try:
            result[key.strip()] = int(value.strip())
        except ValueError:
            pass
    return result


def check_discord(r: Result) -> None:
    token = env("DISCORD_TOKEN")
    guild_id = env("SERVER_ID")
    if not token or not guild_id:
        return

    headers = {"Authorization": f"Bot {token}"}

    status, me, _ = request_json(f"{DISCORD_API}/users/@me", headers=headers)
    if status != 200 or not isinstance(me, dict):
        r.fail(f"Discord bot token rejected - HTTP {status}")
        return
    r.ok(f"Discord bot authenticated as {me.get('username', 'unknown')}")

    status, guild, _ = request_json(f"{DISCORD_API}/guilds/{guild_id}", headers=headers)
    if status != 200 or not isinstance(guild, dict):
        r.fail(f"Bot cannot read configured guild {guild_id} - HTTP {status}")
        return
    r.ok(f"Configured guild reachable: {guild.get('name', guild_id)}")

    status, roles, _ = request_json(f"{DISCORD_API}/guilds/{guild_id}/roles", headers=headers)
    if status != 200 or not isinstance(roles, list):
        r.fail(f"Could not read Discord roles - HTTP {status}")
        return

    role_by_id = {int(x["id"]): x for x in roles if isinstance(x, dict) and str(x.get("id", "")).isdigit()}
    role_by_name = {str(x.get("name", "")): x for x in roles if isinstance(x, dict)}

    configured_roles: dict[str, int] = {}
    for name in (
        "AWAY_ROLE_ID",
        "VERIFIED_ROLE_ID",
        "UNVERIFIED_ROLE_ID",
        "SPECIAL_ACCESS_ROLE_ID",
        "ANNOUNCEMENT_PING_ROLE_ID",
    ):
        value = parse_id(name)
        if value:
            configured_roles[name] = value

    configured_roles.update({f"RANK:{k}": v for k, v in parse_map_ids(env("RANK_ROLE_MAP")).items()})
    language_map = parse_map_ids(env("LANGUAGE_ROLE_MAP"))
    configured_roles.update({f"LANG:{k}": v for k, v in language_map.items()})

    missing = [(name, rid) for name, rid in configured_roles.items() if rid not in role_by_id]
    if missing:
        for name, rid in missing:
            r.fail(f"Configured Discord role missing: {name}={rid}")
    else:
        r.ok(f"All {len(configured_roles)} configured Discord role IDs exist")

    expected_languages = {"EN", "ES", "AR", "DE", "FR", "NO", "CEB", "PT", "SV", "RU"}
    if language_map:
        missing_langs = expected_languages - set(language_map)
        if missing_langs:
            r.warn("LANGUAGE_ROLE_MAP is missing: " + ", ".join(sorted(missing_langs)))
        else:
            r.ok("LANGUAGE_ROLE_MAP contains all 10 OZY languages")
    else:
        r.fail("LANGUAGE_ROLE_MAP is empty")

    missing_troop_roles = sorted(TROOP_METADATA_ROLE_NAMES - set(role_by_name))
    if missing_troop_roles:
        r.fail("Missing native onboarding metadata roles: " + ", ".join(missing_troop_roles))
    else:
        bad_troop_roles = []
        for name in sorted(TROOP_METADATA_ROLE_NAMES):
            role = role_by_name[name]
            if int(role.get("permissions", "0")) != 0 or role.get("hoist") or role.get("mentionable"):
                bad_troop_roles.append(name)
        if bad_troop_roles:
            r.fail("Troop metadata roles must have zero permissions/not be hoisted or mentionable: " + ", ".join(bad_troop_roles))
        else:
            r.ok("All 27 G/M/S metadata roles exist with zero guild permissions")

    translator = role_by_name.get("OZY Translator")
    if translator:
        permissions = int(translator.get("permissions", "0"))
        if permissions & ADMINISTRATOR:
            r.fail("OZY Translator still has Administrator permission")
        else:
            r.ok("OZY Translator does not have Administrator permission")
    else:
        r.warn("Could not find role named OZY Translator")

    status, channels, _ = request_json(f"{DISCORD_API}/guilds/{guild_id}/channels", headers=headers)
    if status != 200 or not isinstance(channels, list):
        r.fail(f"Could not read Discord channels - HTTP {status}")
        return
    channel_ids = {int(x["id"]) for x in channels if isinstance(x, dict) and str(x.get("id", "")).isdigit()}

    channel_envs = (
        "WELCOME_CHANNEL_ID",
        "GOODBYE_CHANNEL_ID",
        "ANNOUNCEMENT_CHANNEL_ID",
        "SCHEDULE_CHANNEL_ID",
        "LEADERSHIP_SCHEDULE_CHANNEL_ID",
        "CALENDAR_CHANNEL_ID",
        "TODAY_CHANNEL_ID",
        "AWAY_CHANNEL_ID",
        "AUDIT_CHANNEL_ID",
        "CHEST_CHANNEL_ID",
    )
    configured_channels = {name: parse_id(name) for name in channel_envs}
    configured_channels = {k: v for k, v in configured_channels.items() if v}

    missing_channels = [(name, cid) for name, cid in configured_channels.items() if cid not in channel_ids]
    if missing_channels:
        for name, cid in missing_channels:
            r.fail(f"Configured Discord channel missing: {name}={cid}")
    else:
        r.ok(f"All {len(configured_channels)} configured Discord channel IDs exist")

    # GOODBYE_CHANNEL_ID is optional because the bot can fall back to an exact
    # START HERE / #goodbye lookup. Still verify the fallback exists.
    if not parse_id("GOODBYE_CHANNEL_ID"):
        categories = {
            int(x["id"]): x
            for x in channels
            if isinstance(x, dict)
            and str(x.get("id", "")).isdigit()
            and int(x.get("type", -1)) == 4
        }
        start_here_ids = {
            cid for cid, category in categories.items()
            if str(category.get("name", "")).strip().casefold() == "start here"
        }
        goodbye = next(
            (
                x for x in channels
                if isinstance(x, dict)
                and str(x.get("name", "")).strip().casefold() == "goodbye"
                and int(x.get("parent_id") or 0) in start_here_ids
            ),
            None,
        )
        if goodbye:
            r.ok("START HERE / #goodbye found by name (GOODBYE_CHANNEL_ID may remain unset)")
        else:
            r.warn("GOODBYE_CHANNEL_ID is unset and START HERE / #goodbye was not found")

    # Native profile metadata must not bypass the onboarding member-access role.
    channel_by_id = {int(x["id"]): x for x in channels if isinstance(x, dict) and str(x.get("id", "")).isdigit()}
    verified_id = parse_id("VERIFIED_ROLE_ID")
    if verified_id and language_map:
        access_errors = []
        for code, channel_id in LANGUAGE_CHANNEL_IDS.items():
            channel = channel_by_id.get(channel_id)
            if not channel:
                access_errors.append(f"{code}:channel-missing")
                continue
            overwrites = {str(o.get("id")): o for o in channel.get("permission_overwrites", [])}
            language_role_id = language_map.get(code)
            if language_role_id and str(language_role_id) in overwrites:
                access_errors.append(f"{code}:language-role-has-channel-overwrite")
            verified_overwrite = overwrites.get(str(verified_id))
            if not verified_overwrite or not (int(verified_overwrite.get("allow", "0")) & VIEW_CHANNEL):
                access_errors.append(f"{code}:member-access-does-not-allow-view")
            everyone_overwrite = overwrites.get(guild_id)
            if not everyone_overwrite or not (int(everyone_overwrite.get("deny", "0")) & VIEW_CHANNEL):
                access_errors.append(f"{code}:everyone-not-denied-view")
        if access_errors:
            r.fail("Language channel access model is inconsistent: " + ", ".join(access_errors))
        else:
            r.ok("Language channels are onboarding-member gated; language roles are metadata only")

    # Normal onboarded members must never gain visibility into restricted staff categories.
    if verified_id:
        restricted_errors = []
        for label, category_id in RESTRICTED_CATEGORY_IDS.items():
            category = channel_by_id.get(category_id)
            if not category:
                restricted_errors.append(f"{label}:category-missing")
                continue
            overwrites = {str(o.get("id")): o for o in category.get("permission_overwrites", [])}
            everyone_overwrite = overwrites.get(guild_id)
            if not everyone_overwrite or not (int(everyone_overwrite.get("deny", "0")) & VIEW_CHANNEL):
                restricted_errors.append(f"{label}:everyone-not-denied-view")
            verified_overwrite = overwrites.get(str(verified_id))
            if verified_overwrite and (int(verified_overwrite.get("allow", "0")) & VIEW_CHANNEL):
                restricted_errors.append(f"{label}:member-access-explicitly-allows-view")
        if restricted_errors:
            r.fail("Restricted category access is inconsistent: " + ", ".join(restricted_errors))
        else:
            r.ok("ADMIN and LEADERSHIP remain hidden from normal onboarded members")


def main() -> int:
    load_dotenv()
    r = Result()

    print("OZY ADMIN READ-ONLY PREFLIGHT")
    print("=" * 34)

    check_timezone(r)
    check_required_env(r)
    check_data_api(r)
    check_state(r)
    check_discord(r)

    print()
    print("=" * 34)
    print(f"Failures: {r.failures}")
    print(f"Warnings: {r.warnings}")

    if r.failures:
        print("RESULT: FAIL - fix failures before production testing.")
        return 1

    print("RESULT: PASS - ready for live member-flow testing.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
