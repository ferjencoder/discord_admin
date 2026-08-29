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
│   ├── onboarding_profile.py native Discord profile-role parser
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
Discord native Onboarding
-> language + G/M/S metadata roles
-> OZY Admin mirrors them into structured member_profiles
-> roster-name suggestion / exact name fallback
-> leadership approval
-> Verified + Leader/Superior sync
```

Discord owns the member-facing language/G/M/S choices. OZY Admin owns roster
identity and authorization. Roster suggestions are never proof of identity.
Stable Total Battle `user_id` is the durable identity after approval.

Language and G/M/S Discord roles are metadata only and grant no clan access.
`Verified` / `Special Access` are the access gate. The database mirror is used
for reports, verification cards and APIs.

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
