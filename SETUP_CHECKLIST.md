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
RANK_ROLE_MAP=Leader:555555555555555555,Superior:666666666666666666,Officer:777777777777777777,Veteran:888888888888888888,Soldier:999999999999999999
```

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

Recommended schedule/calendar defaults:

```env
SCHEDULE_TIMEZONE=America/Argentina/Buenos_Aires
DAILY_SCHEDULE_ENABLED=true
DAILY_SCHEDULE_TIME=08:00

CALENDAR_CHANNEL_ID=...
TODAY_CHANNEL_ID=...
VOLTRON_CALENDAR_ENABLED=true
VOLTRON_REALM=Regular
VOLTRON_CALENDAR_DAYS=30
VOLTRON_TODAY_ENABLED=true
VOLTRON_TODAY_TIME=08:00

TRUST_EXACT_DISPLAY_NAME=false
AUTO_SYNC_NICKNAME=false
```

The Voltron integration does not require a Voltron login, cookie, SignalR connection,
or personal account token. It uses the public calendar endpoints exposed by the site.
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
5. `/member-link` to approve the test member
6. `/member`
7. `/chests`
8. `/away days:1 reason:Test`
9. `/back`
10. `/schedule`
11. `/calendar-status`
12. `/calendar`
13. `/today`
14. `/calendar-refresh`
15. confirm the calendar channel is edited in place rather than spammed
16. `/event-create` - select a restricted category + voice channel and create a test event
17. confirm only members who can view that voice channel can access the scheduled event
18. `/sync-roles apply:false`
19. inspect the preview
20. `/sync-roles apply:true`
21. `/announce ping:false`
22. only after that, test `/announce ping:true`

## 9. Test a new member

Use a test account if possible.

Expected behavior:

- if the Discord server display name exactly matches an active roster name, the bot identifies it but keeps the link pending by default;
- leadership approves the identity with `/member-link` before a roster rank role is assigned;
- otherwise the welcome message asks them to use `/verify` and can suggest close roster names;
- if Membership Screening leaves the member pending, the welcome workflow waits until screening is complete;
- language roles assigned by Discord Onboarding are left untouched.
