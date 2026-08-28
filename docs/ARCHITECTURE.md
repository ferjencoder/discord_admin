# OZY Admin Architecture

The repository is intentionally split into runtime code, configuration, maintenance tools, documentation, and tests.

```text
discord_admin/
├── bot.py                 Discord client, commands, UI wiring and background loops
├── settings.py            environment parsing and validation
├── ozy/
│   ├── data_provider.py   authoritative roster/chest/schedule reads
│   ├── event_calendar.py  tournament/mini-event parsing and formatting logic
│   ├── state.py           SQLite + OZY web snapshot persistence
│   └── utils.py           presentation helpers
├── tools/discord/         manual server maintenance tools, never imported at runtime
├── config/discord/        onboarding/role/profile configuration artifacts
├── docs/                  operational documentation
├── data/                  local examples/fallback development data
└── tests/                 unit tests
```

## Boundaries

- `bot.py` owns Discord-specific orchestration. It may call runtime modules but runtime modules must not depend on the Discord bot instance.
- `ozy/data_provider.py` is the only runtime layer that reads authoritative OZY roster/chest/schedule datasets.
- `ozy/state.py` owns persistence only. Production uses SQLite plus the authenticated OZY web snapshot endpoint.
- `ozy/event_calendar.py` owns calendar parsing and reset-time calculations.
- `tools/discord/` contains one-off operator utilities. Those scripts are not production dependencies.
- `config/discord/` stores safe declarative Discord configuration. Secrets never belong there.

## Membership flow

```text
Join
-> Unverified
-> roster-name suggestions
-> exact roster claim
-> leadership approval
-> Verified + Leader/Superior sync
-> post-verification profile
-> preferred language + G/M/S
```

Roster suggestions are never proof of identity. Stable Total Battle `user_id` is the durable identity after approval.

G/M/S are profile data, not Discord roles. Language roles are granted only after verification.

## Data flow

```text
PeekABoo / website publisher
        v
ozy.com.ar normalized APIs
        v
DataProvider
        v
OZY Admin Discord workflows
```

The bot does not read Google Sheets or raw Total Battle data directly.

## State flow

```text
Discord workflow
-> SQLite transaction
-> consistent SQLite backup
-> authenticated PUT to ozy.com.ar
-> Netlify Blob
```

On restart the direction reverses before the bot begins serving Discord workflows.
