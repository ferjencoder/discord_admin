# OZY Admin

Discord administration bot for the OZY Total Battle clan.

OZY Admin is separate from OZY Translator. It owns roster verification, access roles, post-verification profiles, chest reporting, schedules, events, tournament calendar output, Away status, and operational audit workflows.

## Current membership flow

```text
Join
-> Unverified
-> roster suggestions based on Discord names
-> member confirms exact Total Battle identity
-> leadership Approve / Reject
-> Verified + Leader/Superior synchronization
-> Complete OZY profile
-> preferred language + G/M/S saved
```

Roster suggestions never auto-verify a member. Approved links use the stable Total Battle `user_id` when available.

G/M/S are profile fields, not Discord roles. Language roles are assigned only after verification.

## Production data

```text
ROSTER_URL=https://ozy.com.ar/api/v1/roster
CHEST_DATA_URL=https://ozy.com.ar/api/v1/chests/current
SCHEDULE_URL=https://ozy.com.ar/api/ozy/schedule
```

The bot consumes normalized OZY website APIs, not Google Sheets or raw PeekABoo files.

## Production state

```text
SQLite working copy
-> authenticated https://ozy.com.ar/api/ozy-admin/state
-> Netlify Blob
```

No PostgreSQL backend is used.

## Repository layout

See [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md).

Useful docs:

- [`docs/SETUP.md`](docs/SETUP.md)
- [`docs/STATE_STORAGE.md`](docs/STATE_STORAGE.md)
- [`docs/DATA_API.md`](docs/DATA_API.md)
- [`docs/CALENDAR.md`](docs/CALENDAR.md)
- [`docs/DISCORD_TOOLS.md`](docs/DISCORD_TOOLS.md)

## Local setup

```bash
py -m pip install -r requirements.txt
copy .env.example .env
py bot.py
```

Tests:

```bash
py -m pytest -q
```

Windows needs the IANA timezone database used by `zoneinfo`; `tzdata` is therefore an explicit dependency in `requirements.txt`.

## Render

Build:

```text
pip install -r requirements.txt
```

Start:

```text
python bot.py
```

Health check:

```text
/healthz
```

## Safe defaults

```env
TRUST_EXACT_DISPLAY_NAME=false
AUTO_SYNC_NICKNAME=false
```

Do not grant language roles from Discord Community Onboarding. Use `config/discord/onboarding.json` and let OZY Admin grant the selected language after roster verification.

Do not grant Administrator to OZY Admin or OZY Translator.
