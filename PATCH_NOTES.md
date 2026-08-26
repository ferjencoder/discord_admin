# OZY Admin - OZY website state persistence

## Production architecture

- Local working state: `data/ozy_admin.sqlite3`
- Durable state: authenticated snapshot stored by `ozy.com.ar` in Netlify Blobs
- No separate hosted database required
- PostgreSQL support remains only as an unused compatibility option

## New Render variables

```env
STATE_REMOTE_URL=https://ozy.com.ar/api/ozy-admin/state
STATE_REMOTE_TOKEN=<same secret configured on Netlify>
STATE_REMOTE_TIMEOUT_SECONDS=10
STATE_DB=data/ozy_admin.sqlite3
```

Do not configure `STATE_DATABASE_URL` or `DATABASE_URL` when `STATE_REMOTE_URL` is used.

## Safety behavior

- Startup GET 404 means first run and initializes new state.
- Authentication errors, network failures, or server errors fail startup rather than overwriting durable state with an empty DB.
- Remote payloads must have a valid SQLite file header.
- Local database snapshots are generated with SQLite's backup API before upload.
- State endpoint token is separate from public/browser credentials.

## Verification/onboarding state persisted

The snapshot includes all existing OZY Admin state tables, including member identity links, stable TB IDs, troop profiles, pending verification claims/history, Away state, welcome state, and calendar/chest/schedule dedupe state.
