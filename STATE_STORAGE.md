# OZY Admin Persistent State

OZY Admin stores Discord-to-Total-Battle identity links, pending verification claims, verification history, troop levels, away records, welcome dedupe, and scheduled-post dedupe.

## Recommended production setup: ozy.com.ar + Netlify Blobs

The bot keeps a small SQLite database as its local working state and mirrors a consistent snapshot to an authenticated endpoint hosted by the OZY website. On every bot startup, the website snapshot is restored before OZY Admin starts using the database.

This gives us durable state without a separate hosted database.

Render variables:

```env
STATE_REMOTE_URL=https://ozy.com.ar/api/ozy-admin/state
STATE_REMOTE_TOKEN=<same strong secret configured on Netlify>
STATE_REMOTE_TIMEOUT_SECONDS=10
STATE_DB=data/ozy_admin.sqlite3
```

Do **not** set `STATE_DATABASE_URL` or `DATABASE_URL` when `STATE_REMOTE_URL` is configured.

Netlify variable, available to Functions only:

```env
OZY_ADMIN_STATE_TOKEN=<same strong secret>
```

The OZY website repository must contain the protected `/api/ozy-admin/state` Netlify Function and `@netlify/blobs` dependency.

After deployment, `/healthz` reports:

```json
"state_backend": "web-snapshot"
```

The startup log reports `OZY Web snapshot + SQLite cache`.

### Failure behavior

Startup is fail-closed. If the configured website state endpoint is unreachable or returns an authentication/server error, OZY Admin refuses to replace it with an empty local database. A genuine first run is represented by HTTP 404, after which the bot creates and uploads the initial state snapshot.

Every successful SQLite state mutation uploads a fresh consistent snapshot. The uploaded object is private and cannot be fetched without the shared server-side token.

## What stays public vs private

Public/read-only normalized data may be hosted separately by ozy.com.ar:

- active roster data intended for the bot/member portal
- chest ranking data
- event/calendar data

Private bot state must **not** be written to public `/data/*.json` files:

- Discord user IDs
- Discord -> Total Battle identity links
- verification claims and decisions
- troop profile metadata
- Away records
- internal dedupe/message IDs

These stay in the authenticated Netlify Blob snapshot.

## Local development

Without `STATE_REMOTE_URL`, `STATE_DATABASE_URL`, or `DATABASE_URL`, OZY Admin uses local SQLite only:

```env
STATE_DB=data/ozy_admin.sqlite3
```

This is suitable for local development but not durable on Render's ephemeral filesystem.

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

Every pending exact-roster claim is posted there with persistent **Approve** and **Reject** buttons. Approval re-checks the authoritative roster before linking the Discord account. Rejection can include a reason and keeps the member Unverified.

Leadership commands:

```text
/pending-verifications
/verification-history
/member-link
```
