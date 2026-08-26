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
- Private leadership roster-verification queue -> `VERIFICATION_CHANNEL_ID`

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
RANK_ROLE_MAP=Leader:555555555555555555,Superior:666666666666666666,Officer:777777777777777777,Veteran:888888888888888888,Soldier:999999999999999999
TROOP_LEVEL_ROLE_MAP=G1:111111111111111111,G2:222222222222222222,G3:333333333333333333,G4:444444444444444444,G5:555555555555555555,G6:666666666666666666,G7:777777777777777777,G8:888888888888888888,G9:999999999999999999
```

### Discord Onboarding troop question

Use Community Onboarding for troop level only, because its questions use predefined answer options that can assign roles/channels.

Create one required, single-select question:

`What is your highest troop level?`

Answers: `G1`, `G2`, `G3`, `G4`, `G5`, `G6`, `G7`, `G8`, `G9`.

Each answer should assign exactly one matching troop-level Discord role. Put those role IDs in `TROOP_LEVEL_ROLE_MAP`.

Do not try to collect the exact Total Battle player name through Community Onboarding. OZY Admin's **Verify OZY membership** button/modal is the authoritative name-entry workflow.

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

For persistent production state, create/connect a PostgreSQL database and set its internal connection URL as `STATE_DATABASE_URL`. Create a private leadership text channel such as `#verification` and set `VERIFICATION_CHANNEL_ID` to that channel ID. See `STATE_STORAGE.md`.

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

TRUST_EXACT_DISPLAY_NAME=false
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

Test in this order:

1. `/chats`
2. `/chat`
3. `/verify` - confirm it creates a pending request rather than granting a rank role
4. `/pending-verifications` as leadership
5. Confirm the claim appears in the private verification queue with **Approve / Reject** buttons
6. Approve one test claim from the button and reject another with a reason
7. `/verification-history` and confirm both decisions are recorded
8. `/member-link` as the manual fallback/override
9. `/member`
10. `/chests`
11. `/away days:1 reason:Test`
12. `/back`
13. `/schedule`
14. `/calendar-status`
15. `/calendar`
16. `/today`
17. `/calendar-refresh`
18. confirm the calendar channel is edited in place rather than spammed
19. `/event-create` - the modal should open immediately; select category, event channel/location, and publish channel
20. finish the second date/time step and confirm the Discord Scheduled Event is created and the event card is posted in the selected publish channel
21. confirm a verified normal member can create an event, while an unverified outsider cannot target hidden channels
22. Join with a test account and confirm it initially receives Unverified access.
23. Click **Verify OZY membership** and deliberately enter a misspelled game name; confirm the bot rejects it and asks for the precise roster name.
24. Enter an exact active-roster name; with `TRUST_EXACT_DISPLAY_NAME=false`, confirm it becomes a pending verification instead of instantly granting rank access.
25. Confirm the troop level selected in Discord Onboarding appears in `/member` after `TROOP_LEVEL_ROLE_MAP` is configured.
26. `/sync-roles apply:false`
27. inspect the preview
28. `/sync-roles apply:true`
29. `/announce ping:false`
30. only after that, test `/announce ping:true`

## 9. Test a new member

Use a test account if possible.

Expected behavior:

- the member receives Unverified access first;
- the welcome post includes **Verify OZY membership**;
- if the Discord server display name exactly matches an active roster name, the bot identifies it but keeps the link pending by default;
- leadership approves the identity from the private verification queue; `/member-link` remains the manual fallback;
- if the name is not an exact active-roster match, the verification form rejects it and asks for the precise Total Battle name, with close roster suggestions when available;
- if `TROOP_LEVEL_ROLE_MAP` is configured, the required Onboarding troop-level role is captured into the member profile and follows later role changes;
- an already-approved Discord account that leaves and rejoins keeps its stable Total Battle identity link; access is restored only if that identity is still in the active roster;
- if Membership Screening leaves the member pending, the welcome workflow waits until screening is complete;
- language roles assigned by Discord Onboarding are left untouched.
