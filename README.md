# OZY Admin Bot

See `CALENDAR_SETUP.md` for the Voltron/Discord calendar deployment walkthrough.

A separate, least-privilege Discord operations bot for the OZY Total Battle clan.

It is intentionally separate from `OZY Translator`. The translator only translates. OZY Admin handles roster-aware member workflows, clan announcements, schedules, absences, chest queries, and copyable Total Battle chat names.

## Included features

### Member onboarding and roster verification

- Greets new Discord members in `WELCOME_CHANNEL_ID`.
- Compares their Discord server display name/nickname to the active Total Battle roster.
- Exact display-name match:
  - identifies the likely Total Battle player;
  - with the safe default `TRUST_EXACT_DISPLAY_NAME=false`, creates a pending verification request only;
  - leadership approves with `/member-link` before any rank role is granted.
- No exact match:
  - suggests close roster names when the fuzzy score is above `ROSTER_MATCH_THRESHOLD`;
  - tells the member to use `/verify` with their exact Total Battle name.
- `/verify` is a request, not proof of identity. It does not grant rank roles under the safe default.
- After leadership approves the link, the configured roster rank role is synchronized.
- Nickname synchronization is optional and only runs after an approved link exists.
- If an unlinked member later changes their server nickname to an exact roster name, the safe default creates/updates a pending verification request; it does not grant the rank role until leadership approves the link.

Important: Discord Onboarding does not provide this bot with a free-text "Total Battle name" answer. The candidate name is the member's Discord server display name. `/verify` is the explicit correction/link mechanism.

### Roster roles

`RANK_ROLE_MAP` maps Total Battle ranks to Discord role IDs. Example:

```env
RANK_ROLE_MAP=Leader:111111111111111111,Superior:222222222222222222,Officer:333333333333333333,Veteran:444444444444444444,Soldier:555555555555555555
```

The bot only manages roles listed in this map. It does not replace or remove unrelated Discord roles.

### Chest status

`/chests`

- resolves the member through the saved link or an exact Discord display-name match;
- reads the current week from PeekABoo-compatible `chest_data.json`;
- shows points, target, chest count, status, and top chest-type counts;
- defaults to ephemeral/private output.

Leadership can optionally inspect a roster player with `/chests player:<name>`.

### Away / back

`/away days:<1-90> reason:<text>`

- persists the absence;
- optionally assigns `AWAY_ROLE_ID`;
- posts the absence into `AWAY_CHANNEL_ID`;
- automatically removes expired Away roles every 30 minutes.

`/back` clears it early.

### Total Battle chat directory

`/chats`

Returns the complete directory using a Markdown heading for each chat and a separate fenced code block containing the exact Total Battle chat name. This gives every name its own copyable block in Discord.

Example rendered structure:

- `# OZY Clan Chat Directory`
- `### Clan Announcements`
- fenced `text` block containing `OZY ⓝⓔⓦⓢ`

`/chat name:<entry>` returns one copyable TB chat name.

The source is `data/chats.json`.

### Announcement popup

Leadership uses:

```text
/announce ping:false
```

Discord opens a modal with:

- Title
- Discord announcement
- Optional Total Battle copy/paste text

The Discord announcement is posted to `ANNOUNCEMENT_CHANNEL_ID`. If TB text is supplied, it is posted immediately afterward as a fenced code block for copying.

`ping:true` can mention only the configured `ANNOUNCEMENT_PING_ROLE_ID`.

### Daily schedule

- `/schedule` shows today's schedule.
- `/schedule-post` forces today's schedule into `SCHEDULE_CHANNEL_ID`.
- Automatic posting runs once per day at `DAILY_SCHEDULE_TIME` in `SCHEDULE_TIMEZONE`.
- If the bot restarts after the configured post time and today's schedule has not been posted, it performs one catch-up post.
- Empty days are not automatically posted.

The schedule can come from `SCHEDULE_URL` or `SCHEDULE_FILE`.

### Voltron tournament calendar

OZY Admin can consume Voltron's public Total Battle calendar directly:

```text
https://nexusportal.voltron.me/api/calendar/content?realm=Regular
```

No Voltron login, cookie, personal account token, browser automation, or SignalR client is required. Automatic checks use Voltron's lightweight `snapshot-meta` endpoint first. Once a good snapshot exists, a temporary metadata failure keeps the cache instead of triggering an unnecessary full-content download.

Behavior:

- `CALENDAR_CHANNEL_ID` holds a rolling **30-day tournament calendar**.
- The calendar is a small canonical message series that is **edited in place**, not reposted on every refresh.
- The 30-day view lists tournament **STARTS** only so the channel remains readable.
- `TODAY_CHANNEL_ID` gets one canonical daily post.
- Today's post includes **starts, active/continues, ends, and regular mini events** for that UTC game date.
- If Voltron changes after today's message was posted, OZY Admin edits the existing message instead of adding another one.
- Repeated reactions/restarts are irrelevant; canonical message IDs are stored in SQLite and can also be recovered by scanning recent bot messages if Render loses the local database.
- Tournament source times are parsed as UTC. Public calendar/today posts use the Total Battle reset clock: **17:00 UTC = R+0**. Fractional offsets such as `R+1.5` are supported.
- Regular mini events are read from `https://akurier.pl/events` **once per UTC day at 18:00 (R+1)**; the separate **for SK below** table is intentionally ignored. Akurier clock values are interpreted as `Europe/Warsaw` and converted to UTC before reset-clock formatting.
- `/time` gives the requesting member an **ephemeral** local-time version of today's schedule for a selected timezone. Discord does not expose a member's country/timezone to the bot, so this is user-selected.
- The parser rejects suspiciously empty snapshots instead of wiping the existing Discord calendar.
- If Voltron is temporarily unavailable, the last good Discord calendar remains untouched.

Commands:

- `/calendar` - view the rolling calendar on demand.
- `/today` - view today's tournament activity on demand.
- `/calendar-refresh` - leadership-only forced refresh/update.
- `/calendar-status` - leadership diagnostics.
- `/time` - private local-time conversion of today's game schedule.

Automatic Voltron traffic is deliberately sparse while the source cadence is being learned: lightweight metadata probes run at **00:30, 06:30, 12:30 and 18:30 UTC**. Full calendar content is downloaded only on startup, when `snapshot-meta` changes, or when leadership explicitly runs `/calendar-refresh`. Source timestamp changes are logged so the probe schedule can later be reduced to one check roughly 20-30 minutes after Voltron's normal refresh.

### Audit log

Administrative actions can be written to `AUDIT_CHANNEL_ID`, including:

- member joins;
- roster links;
- announcements;
- away status changes;
- daily schedule posts;
- role sync runs.

## Slash commands

| Command | Who | Purpose |
|---|---|---|
| `/chats` | Everyone | Copyable TB clan-chat directory |
| `/chat` | Everyone | One copyable TB clan-chat name |
| `/verify` | Everyone | Link Discord account to exact roster name |
| `/member` | Everyone/self, leadership/others | Roster/link status |
| `/chests` | Everyone/self, leadership/others | Current chest status |
| `/away` | Everyone | Register absence |
| `/back` | Everyone | Clear absence |
| `/schedule` | Everyone | View today's OZY-specific schedule |
| `/calendar` | Everyone | View the next 30 days of Voltron tournament starts |
| `/today` | Everyone | View today's Voltron tournament activity |
| `/event-create` | Leadership | Create a Discord scheduled event using category + voice-channel selectors and reset time |
| `/announce` | Leadership | Open announcement modal |
| `/schedule-post` | Leadership | Force-post today's OZY-specific schedule |
| `/calendar-refresh` | Leadership | Force Voltron refresh and update Discord |
| `/calendar-status` | Leadership | Show Voltron source/cache health |
| `/member-link` | Leadership | Approve/link a Discord member to roster |
| `/pending-verifications` | Leadership | Review pending roster-link requests |
| `/sync-roles` | Leadership | Preview/apply rank role sync for approved links |

## Discord Developer Portal

Create a separate application/bot named `OZY Admin`.

Enable the **Server Members Intent**. It is required for reliable member-join/member-cache handling and roster/role synchronization.

The bot does **not** require Message Content Intent because it does not read normal chat messages.

Invite it with both scopes:

- `bot`
- `applications.commands`

### Recommended bot permissions

Start with:

- View Channels
- Send Messages
- Embed Links
- Read Message History
- Manage Roles
- Create Events

Add **Manage Nicknames** only if you deliberately set:

```env
AUTO_SYNC_NICKNAME=true
```

Do not give the bot `Administrator`.

The OZY Admin bot role must sit **above every Discord rank role and the Away role it manages**, but below roles it should never control.

## Environment setup

Copy `.env.example` to `.env` locally. On Render, create the same keys in Environment.

Required:

```env
DISCORD_TOKEN=...
SERVER_ID=...
```

Recommended for the full V1:

```env
WELCOME_CHANNEL_ID=...
ANNOUNCEMENT_CHANNEL_ID=...
SCHEDULE_CHANNEL_ID=...
CALENDAR_CHANNEL_ID=...
TODAY_CHANNEL_ID=...
AWAY_CHANNEL_ID=...
AUDIT_CHANNEL_ID=...
ANNOUNCEMENT_PING_ROLE_ID=...
LEADERSHIP_ROLE_IDS=...
RANK_ROLE_MAP=...
AWAY_ROLE_ID=...
ROSTER_URL=...
CHEST_DATA_URL=...
SCHEDULE_URL=...
VOLTRON_CALENDAR_ENABLED=true
VOLTRON_REALM=Regular
VOLTRON_CALENDAR_DAYS=30
VOLTRON_TODAY_ENABLED=true
VOLTRON_TODAY_TIME=08:00
```

## Roster data

Supported object form:

```json
{
  "members": {
    "PeekABoo Death": {
      "status": "active",
      "rank": "Superior",
      "level": "100",
      "might": "1,000,000",
      "location": "K:1030 X:500 Y:500"
    }
  }
}
```

Members with `"status": "removed"` are ignored.

The bot also accepts a list of member objects with a `name` field.

## Chest data

The bot accepts the current PeekABoo-style structure:

```json
{
  "weekly_target": 1000,
  "weeks": [
    {
      "label": "16-22 Aug 2026",
      "start": "2026-08-16",
      "end": "2026-08-22",
      "members": [
        {
          "name": "PeekABoo Death",
          "points": 1250,
          "chests": 84,
          "met_target": true,
          "breakdown": {
            "L35 epic Crypt": 2
          }
        }
      ]
    }
  ]
}
```

The bot selects the week containing today's configured local date. If none matches, it falls back to the first week supplied.

## Schedule format

`data/schedule.example.json` demonstrates both recurring and date-specific events:

```json
{
  "events": [
    {
      "weekdays": ["Monday", "Tuesday"],
      "time": "14:00",
      "title": "Reset",
      "details": "Optional description"
    },
    {
      "date": "2026-08-21",
      "time": "19:00",
      "title": "One-off clan event",
      "details": "Optional description"
    }
  ]
}
```

An event with neither `date` nor `weekdays` is considered daily.

## Render

Use a separate Render service from the translator.

Start command:

```text
python bot.py
```

The service exposes:

```text
/healthz
```

on Render's `PORT`.

`STATE_DB` stores Discord-to-game links, absences, and daily-post dedupe in SQLite. A free/ephemeral filesystem can lose this state during redeploy/restart. For durable production state, use persistent storage or migrate the small state layer to Postgres.

## Safe rollout

1. Create the `OZY Admin` Discord application/bot.
2. Enable Server Members Intent.
3. Invite with the minimal permissions above.
4. Put the bot role above only the roles it should manage.
5. Configure `SERVER_ID` and channel IDs.
6. Configure `LEADERSHIP_ROLE_IDS`.
7. Configure `RANK_ROLE_MAP` and `AWAY_ROLE_ID`.
8. Point `ROSTER_URL` and `CHEST_DATA_URL` at the canonical PeekABoo data source.
9. Keep `TRUST_EXACT_DISPLAY_NAME=false` and `AUTO_SYNC_NICKNAME=false` initially.
10. Deploy.
11. Test `/chats`, `/verify`, `/chests`, `/away`, `/schedule`.
12. Run `/sync-roles apply:false` first.
13. Inspect the preview.
14. Only then run `/sync-roles apply:true`.
15. Test `/announce` with `ping:false` before enabling a ping role.

## Security boundaries

- Hard-gated to one `SERVER_ID`.
- Leadership commands require either a configured leadership role or Discord Manage Server permission.
- A Discord nickname is not treated as proof of Total Battle identity by default.
- Rank roles are synchronized only for approved Discord-user -> roster links unless you explicitly enable `TRUST_EXACT_DISPLAY_NAME=true`.
- Rank synchronization only touches role IDs explicitly listed in `RANK_ROLE_MAP`.
- The Away workflow only touches `AWAY_ROLE_ID`.
- Announcement pings only allow `ANNOUNCEMENT_PING_ROLE_ID`.
- Normal outbound bot messages suppress arbitrary mentions.
- No `Administrator` permission required.
- No Message Content Intent required.
- Secrets remain in environment variables, not source files.
