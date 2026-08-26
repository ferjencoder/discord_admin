# OZY Admin Persistent State

OZY Admin stores Discord-to-Total-Battle identity links, pending verification claims, verification history, troop levels, away records, welcome dedupe, and scheduled-post dedupe in its state database.

## Recommended production setup: PostgreSQL

Set either:

```env
STATE_DATABASE_URL=postgresql://...
```

or the conventional:

```env
DATABASE_URL=postgresql://...
```

`STATE_DATABASE_URL` takes precedence when both are present.

When a PostgreSQL URL is configured, OZY Admin creates and migrates its state tables automatically at startup and uses a small connection pool. The local `STATE_DB` SQLite path becomes only a fallback for local development.

### Render

For a real launch, use a durable PostgreSQL instance and connect it to the OZY Admin service. Do not rely on the free web service filesystem for identity state.

Recommended service variables:

```env
STATE_DATABASE_URL=<Render Postgres internal connection URL>
VERIFICATION_CHANNEL_ID=<private leadership verification queue channel ID>
```

After deployment, `/healthz` reports:

```json
"state_backend": "postgres"
```

The startup log also prints `OZY Admin state storage: PostgreSQL`.

## SQLite fallback

Without `STATE_DATABASE_URL` or `DATABASE_URL`, OZY Admin uses:

```env
STATE_DB=data/ozy_admin.sqlite3
```

This is appropriate for local development. It is also acceptable on a paid Render service only when `STATE_DB` points inside an attached persistent disk mount.

Do not use SQLite on Render's ephemeral filesystem for production identity state.

## Verification queue

Create a private leadership text channel such as:

```text
LEADERSHIP
└─ #verification
```

Set:

```env
VERIFICATION_CHANNEL_ID=<channel ID>
```

Every pending exact-roster claim is posted there with persistent **Approve** and **Reject** buttons. The buttons continue working after bot restarts because OZY Admin re-registers views for all pending claims from the database.

Approval re-checks the live authoritative roster before linking the Discord account. Rejection can include a reason and keeps the member Unverified. Both decisions are written to `verification_history`.

Leadership commands:

```text
/pending-verifications
/verification-history
/member-link
```

`/member-link` remains as the manual override/recovery command and records an approval decision when a pending claim exists.

## Optional SQLite -> PostgreSQL migration

If you already have approved links/state in a local SQLite database, set the PostgreSQL destination URL and run before switching production:

```bash
python migrate_state_sqlite_to_postgres.py
```

The script copies the known OZY Admin state tables and uses `ON CONFLICT DO NOTHING`, so existing destination rows are preserved. Back up the SQLite file first if it contains important production identity links.
