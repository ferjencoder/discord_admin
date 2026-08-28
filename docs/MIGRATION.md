# Migration from the previous repository layout

This cleanup is intentionally a source-layout refactor, not a feature rewrite.

## Removed from production source

The following are no longer part of the production architecture:

- PostgreSQL state backend and SQLite-to-PostgreSQL migration helper
- one-off Discord permission repair scripts used during server setup
- generated Discord exports
- patch-note/install fragments accumulated during development
- root-level runtime helper modules that are now grouped under `ozy/`

## Moved

```text
data_provider.py  -> ozy/data_provider.py
event_calendar.py -> ozy/event_calendar.py
state.py          -> ozy/state.py
utils.py          -> ozy/utils.py

Discord UI classes -> ozy/discord_ui.py
profile constants  -> ozy/constants.py

export/apply/onboarding tools -> tools/discord/
Discord JSON configuration    -> config/discord/
operational documents         -> docs/
```

## Production state

Only this production state model remains:

```text
SQLite working database
<-> authenticated OZY web snapshot
<-> Netlify Blob
```

Remove `DATABASE_URL` and `STATE_DATABASE_URL` from Render if they still exist.

## Windows local update

```bash
py -m pip install -r requirements.txt
py -m pytest -q
```

`tzdata` is now explicit in `requirements.txt`, so `ZoneInfo("UTC")` and
`ZoneInfo("America/Argentina/Buenos_Aires")` work on Windows.

## Deploy

No Render start-command change is required:

```text
python bot.py
```

The refactor preserves the external environment variable names currently used
by OZY Admin, except obsolete PostgreSQL variables are removed.
