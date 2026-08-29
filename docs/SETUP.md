# OZY Admin - Setup Checklist

## 1. Create the Discord application

Create a **new** Discord Developer Portal application named `OZY Admin`.

Do not reuse the OZY Translator bot token.

Under **Bot > Privileged Gateway Intents** enable:

- **Server Members Intent**

You do not need Message Content Intent for OZY Admin V1.

## 2. Invite OZY Admin

Use scopes:

- `bot`
- `applications.commands`

Recommended permissions:

- View Channels
- Send Messages
- Embed Links
- Read Message History
- Manage Roles
- Create Events

Only add **Manage Nicknames** if you later enable `AUTO_SYNC_NICKNAME=true`.

Do not grant Administrator.

## 3. Role hierarchy

Move the `OZY Admin` bot role above every role it will manage:

- rank roles configured in `RANK_ROLE_MAP`;
- the Away role configured in `AWAY_ROLE_ID`.

Keep OZY Admin below owner/high-security roles it should never modify.

## 4. Create/choose Discord channels

Recommended channels:

- Welcome / arrivals -> `WELCOME_CHANNEL_ID`
- Clan announcements -> `ANNOUNCEMENT_CHANNEL_ID`
- Clan-specific daily schedule -> `SCHEDULE_CHANNEL_ID`
- Rolling 30-day tournament calendar -> `CALENDAR_CHANNEL_ID`
- Daily tournament summary -> `TODAY_CHANNEL_ID`
- Absence log -> `AWAY_CHANNEL_ID`
- Private bot audit log -> `AUDIT_CHANNEL_ID`

One channel can technically serve more than one purpose, but separate announcement/audit channels are cleaner.

## 5. Create/choose roles

Configure:

- leadership role IDs in `LEADERSHIP_ROLE_IDS`;
- event/announcement ping role in `ANNOUNCEMENT_PING_ROLE_ID`;
- Away role in `AWAY_ROLE_ID`;
- Total Battle rank -> Discord role mapping in `RANK_ROLE_MAP`.

Example only:

```env
LEADERSHIP_ROLE_IDS=111111111111111111,222222222222222222
ANNOUNCEMENT_PING_ROLE_ID=333333333333333333
AWAY_ROLE_ID=444444444444444444
RANK_ROLE_MAP=Leader:555555555555555555,Superior:666666666666666666
LANGUAGE_ROLE_MAP=EN:111111111111111111,ES:222222222222222222,DE:333333333333333333
```

### Native Discord profile

Community Onboarding collects preferred language plus Guardsmen, Monsters and
Specialists levels. Each answer assigns a zero-permission metadata role. OZY
Admin mirrors those roles into `member_profiles` using
`profile_source=discord-onboarding`.

The metadata roles do not grant clan access. `Verified` / `Special Access`
remain the access gate. Members edit language/G/M/S through Discord **Channels
& Roles**; `/profile` only displays the mirrored structured profile.

The authoritative flow is: native Onboarding -> roster identity claim ->
automatic roster-name match -> Verified.

## 6. Connect PeekABoo data

For Render, HTTPS sources are the cleanest option:

```env
ROSTER_URL=https://YOUR-SOURCE/roster.json
CHEST_DATA_URL=https://YOUR-SOURCE/chest_data.json
SCHEDULE_URL=https://YOUR-SOURCE/schedule.json
```

The bot can also use local files when running beside those files.

## 7. Configure Render

Create a separate Render service for OZY Admin.

Minimum:

```env
DISCORD_TOKEN=...
SERVER_ID=...
```

Then add the channel/role/data variables above.

For persistent production state, configure the authenticated `https://ozy.com.ar/api/ozy-admin/state` Netlify Blob endpoint and set `STATE_REMOTE_URL` plus `STATE_REMOTE_TOKEN` in Render. See `docs/STATE_STORAGE.md`.

Recommended schedule/calendar defaults:

```env
SCHEDULE_TIMEZONE=America/Argentina/Buenos_Aires
DAILY_SCHEDULE_ENABLED=true
DAILY_SCHEDULE_TIME=08:00

CALENDAR_CHANNEL_ID=...
TODAY_CHANNEL_ID=...
CALENDAR_ENABLED=true
CALENDAR_BASE_URL=...
CALENDAR_REALM=Regular
CALENDAR_DAYS=30
TODAY_ENABLED=true

AUTO_SYNC_NICKNAME=false
```

The tournament-calendar integration does not require a source login, cookie, SignalR connection,
or personal account token. It uses the public calendar endpoints exposed by the source.
Automatic source traffic is intentionally sparse: four lightweight metadata probes per UTC
day while the source cadence is learned, full content only when metadata changes, and one
Akurier mini-event fetch per day at 18:00 UTC (R+1).
The 30-day calendar lists tournament STARTS; the daily post includes starts, continues,
ends, and the mini-tournament schedule for that day.

Start command:

```text
python bot.py
```

Health path:

```text
/healthz
```

## 8. First controlled test

Test the simplified member flow in this order:

1. Apply the current native onboarding config with `py tools/discord/onboarding.py apply config/discord/onboarding.json --apply`.
2. Join with a spare Discord account and complete language + G/M/S.
3. Confirm only the normal start/setup channels are visible before the game name is linked.
4. Click **Set game name**.
5. Enter a misspelled roster name and confirm the bot offers close active-roster suggestions based on what was typed.
6. Select the correct name and confirm access opens immediately with no Leader/Superior approval.
7. Confirm the chosen language channel and normal clan categories are visible.
8. Confirm ADMIN and LEADERSHIP remain hidden from a normal member.
9. Change G/M/S in Discord **Channels & Roles**, then run `/profile` and confirm the structured values update.
10. Use `/game-name` to test a normal in-game rename with the same stable Total Battle `user_id`.
11. As Leader/Superior, test `/member-name`, `/member-troops`, and `/members-json`.
12. Run `/chests`, `/away`, `/back`, `/schedule`, `/calendar`, `/today`, and `/event-create` as normal regression checks.

## 9. Expected new-member behavior

- Discord Community Onboarding collects language, Guardsmen, Monsters and Specialists.
- OZY Admin mirrors those zero-permission metadata roles into structured profile state.
- Discord cannot ask for a free-text game name natively, so one final bot field collects the Total Battle name.
- An exact active-roster name that is not already linked is accepted immediately.
- A non-exact name produces fuzzy roster suggestions from the text the member entered.
- There is no normal approval queue and no Approve/Reject interaction.
- `Verified` opens normal clan access. ADMIN/LEADERSHIP permissions remain governed by their category/leadership roles.
- Existing stable Total Battle links survive normal leave/rejoin and are canonicalized by `user_id`.
- Members update language/G/M/S through Discord **Channels & Roles**.
- `/game-name` lets members update their own roster name. If they already have a stable Total Battle identity, self-service changes must keep the same `user_id`.
- `/member-name` lets Leader/Superior correct a member's roster link.
- `/member-troops` lets Leader/Superior correct G/M/S metadata.
- `/members-json` exports the active roster with Discord/profile fields.

## Local verification

```bash
py -m pip install -r requirements.txt
py -m pytest -q
py preflight_ozy_admin.py
```
