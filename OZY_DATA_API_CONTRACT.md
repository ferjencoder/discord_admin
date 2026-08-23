# OZY Admin Data API Contract

## Purpose

OZY Admin must consume the same normalized, published OZY datasets used by the website. It must not read Google Sheets, PeekABoo local files, or raw Total Battle/session data directly.

Canonical flow:

```text
PeekABoo / Sheets
      -> normalized website dataset
      -> persistent OZY web data store
      -> read-only OZY Admin API
      -> Discord bot
```

## Authentication

Use a dedicated read-only bot secret, separate from both website member JWTs and the PeekABoo upload token.

Recommended environment variable on the bot:

```text
OZY_DATA_API_TOKEN=<secret>
```

OZY Admin sends it only to `ROSTER_URL` and `CHEST_DATA_URL` as:

```http
X-OZY-Admin-Token: <secret>
```

Do not reuse `PEEKABOO_SYNC_TOKEN`. That token authorizes publishing and should not grant read access to the Discord bot.

## Recommended endpoints

Any stable URLs are acceptable. For example:

```text
https://ozy-clan.netlify.app/.netlify/functions/ozy-admin-data?dataset=roster
https://ozy-clan.netlify.app/.netlify/functions/ozy-admin-data?dataset=chests
```

Then configure Render:

```text
ROSTER_URL=https://ozy-clan.netlify.app/.netlify/functions/ozy-admin-data?dataset=roster
CHEST_DATA_URL=https://ozy-clan.netlify.app/.netlify/functions/ozy-admin-data?dataset=chests
OZY_DATA_API_TOKEN=<same read-only secret configured in Netlify>
```

The function must read the currently published persistent OZY dataset. For bot access control it must not silently fall back to obsolete HOT/K305 bundled files.

If the authoritative published roster is unavailable or invalid, return a non-2xx response. The bot is designed to preserve existing Discord access during a roster API outage instead of mass-revoking members.

## Roster response

The preferred normalized schema is:

```json
{
  "generated": "2026-08-23T16:55:00Z",
  "clan_tag": "OZY",
  "kingdom": 1030,
  "members": {
    "Prince": {
      "status": "active",
      "user_id": "tb:90690612",
      "rank": "Leader"
    },
    "PeekABoo Death": {
      "status": "active",
      "user_id": "tb:90741542",
      "rank": "Superior"
    }
  }
}
```

Requirements:

- member object keys are the exact current Total Battle names
- `status` determines current membership; `removed` members are not active
- `user_id` is the durable player identity and should be present whenever available
- `rank` uses the canonical Total Battle rank names expected by Discord role mapping
- do not expose website PINs, auth hashes, JWTs, sync tokens, or other credentials

OZY Admin also accepts a list form where each member object includes its own `name`, but the keyed form above is preferred.

## Chest response

Preferred schema:

```json
{
  "generated": "2026-08-23T16:55:00Z",
  "weekly_target": 1000,
  "weeks": [
    {
      "label": "23.08 TO 29.08",
      "start": "2026-08-23",
      "end": "2026-08-29",
      "total_points": 12345,
      "total_chests": 987,
      "members": [
        {
          "name": "Prince",
          "points": 1400,
          "chests": 82,
          "met_target": true
        }
      ]
    }
  ]
}
```

The bot treats the roster endpoint as authoritative for membership. When building the Discord ranking it:

- includes every active roster member, including players with 0 points
- ignores chest rows for names no longer in the active roster
- sorts by points descending, then chests descending, then exact name
- posts the result in copyable triple-backtick blocks

## R+0 publishing

The Discord chest post is scheduled at canonical Total Battle R+0:

```text
17:00 UTC daily
```

Optional bot override:

```text
CHEST_RESET_POST_TIME_UTC=17:00
```

The display timezone used by calendar/onboarding features must not change the chest reset boundary.

## Failure and security rules

- Fail closed on invalid bot token with `401` or `403`.
- Fail non-2xx if the authoritative OZY dataset is unavailable.
- Do not return a stale HOT/K305 fallback as OZY truth.
- Do not expose private website authentication data.
- Do not give the bot write access to the website data store.
- Keep the bot token in Netlify/Render environment variables only.
- Rotate the read token independently of the PeekABoo sync token and Discord bot token.
