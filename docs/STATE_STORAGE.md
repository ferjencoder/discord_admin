# OZY Admin State Storage

Production state has one architecture:

```text
OZY Admin on Render
        |
        | SQLite working copy
        v
data/ozy_admin.sqlite3
        |
        | authenticated snapshot PUT/GET
        v
https://ozy.com.ar/api/ozy-admin/state
        |
        v
Netlify Blob
```

The local SQLite file is the working database. Render's filesystem is ephemeral, so the bot restores the remote snapshot before opening the schema and uploads a consistent SQLite backup after state changes.

Required Render variables:

```env
STATE_DB=data/ozy_admin.sqlite3
STATE_REMOTE_URL=https://ozy.com.ar/api/ozy-admin/state
STATE_REMOTE_TOKEN=<secret>
STATE_REMOTE_TIMEOUT_SECONDS=10
```

The same secret value is stored on Netlify as:

```env
OZY_ADMIN_STATE_TOKEN=<same secret>
```

The names are intentionally different. The bot sends `STATE_REMOTE_TOKEN` in the `X-OZY-State-Token` request header and the Netlify Function compares it with `OZY_ADMIN_STATE_TOKEN`.

There is no PostgreSQL backend in the current architecture.

## Startup behavior

- HTTP 200 + valid SQLite snapshot: restore and continue.
- HTTP 404: first run - initialize local SQLite and upload it.
- HTTP 401/403: fail closed because the state token is wrong.
- HTTP 200 containing HTML or non-SQLite content: fail closed because the API route is wrong.
- Network/5xx failure: fail closed rather than starting with empty state.

## Persistence test

1. Link a test member with the game-name flow.
2. Confirm the member link/profile state exists.
3. Redeploy Render.
4. Confirm the same state is restored after restart.

`/healthz` reports the state backend as `web-snapshot` in production.
