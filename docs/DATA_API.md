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

OZY Admin sends it to its configured OZY data integrations and the fixed
`https://ozy.com.ar/api/v1/mercs/current` endpoint as:

```http
X-OZY-Admin-Token: <secret>
```

Do not reuse `PEEKABOO_SYNC_TOKEN`. That token authorizes publishing and should not grant read access to the Discord bot.

## Production endpoints

Current OZY Admin production endpoints:

```text
https://ozy.com.ar/api/v1/roster
https://ozy.com.ar/api/v1/chests/current
https://ozy.com.ar/api/ozy/schedule
```

Configure Render:

```env
ROSTER_URL=https://ozy.com.ar/api/v1/roster
CHEST_DATA_URL=https://ozy.com.ar/api/v1/chests/current
SCHEDULE_URL=https://ozy.com.ar/api/ozy/schedule
OZY_DATA_API_TOKEN=<same private data secret configured in Netlify>
```

The API functions must read the currently published persistent OZY dataset. For bot access control it must not silently fall back to obsolete HOT/K305 bundled files.

If the authoritative published roster is unavailable or invalid, return a non-2xx response. The bot is designed to preserve existing Discord access during a roster API outage instead of mass-revoking members.

## Roster response

The preferred normalized schema is:

```json
{
  "generated": "2026-08-23T16:55:00Z",
  "clan_tag": "OZY",
  "kingdom": 233,
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


## Extraction correctness (September 2026)

The authoritative OZY binding is clan 4423816314895, observer 4423816316525,
kingdom 233. Reject explicit foreign clan tags/IDs. Older published APIs omit
these IDs; absence is not proof of a verified network binding.

Use `start_at` / `end_at` offsets in website weeks, start inclusive and end
exclusive. OZY weeks run Sunday 17:00 UTC to Sunday 17:00 UTC. Member display
timezones never select a reporting week. A missing current week yields no
current result, never the first historical week. Legacy date-only fixtures
use the 17:00 UTC boundary (six-day end labels remain supported).

Both personal results and rankings resolve chest rows by stable `user_id`
when supplied, then exact roster spelling, then an unambiguous case-insensitive
name. Conflicting supplied IDs and ambiguous matches fail. Never merge case
variants without authoritative identity evidence. Active roster members with
no chest row receive zero; removed/non-roster rows are excluded.

Website `points`, `chests`, and category `breakdown` are authoritative. `chests`
is quantity, not unique gift-ID count. Do not rescore source labels in the bot,
or reclassify Union of Triumph personal rewards as Bank/Triumphal gifts.
The selected week's target overrides the dataset default, including explicit
zero. Target status derives from those points and target.

Expired cache entries are not served after failed refreshes. Fetch failures
become DataUnavailable without including potentially sensitive request URLs.
Authenticated reads do not follow redirects. Published snapshot timestamps
appear in personal and ranking output; snapshots older than 24 hours are
marked. This is a freshness warning, not a claim that no gifts were collected.
Explicit shadow/inactive/preview payloads cannot be displayed as official
results. Legacy snapshots without activation metadata say live activation is
unconfirmed. The bot does not activate a counter or read shadow observations.

Validated with isolated unit tests and saved read-only production API responses.
Do not start the bot for extraction tests: bot startup can trigger Discord writes.
