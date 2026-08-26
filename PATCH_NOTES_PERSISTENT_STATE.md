# OZY Admin - Persistent State + Verification Review

This cumulative patch includes the prior roster/onboarding/troop-profile work plus the launch-state improvements below.

## Added

- PostgreSQL-backed persistent state using `STATE_DATABASE_URL` or `DATABASE_URL`.
- SQLite fallback remains for local development.
- PostgreSQL connection pool.
- `VERIFICATION_CHANNEL_ID` private leadership review queue.
- Persistent Approve / Reject buttons on pending roster claims.
- Reject modal with leadership reason.
- Verification decision history.
- `/verification-history` leadership command.
- `/pending-verifications` now points leadership to the review queue.
- Pending-button views are re-registered after restarts/redeploys.
- Approval re-checks the current website/API roster and stable Total Battle `user_id` before linking.
- Queue messages are updated to Approved / Rejected after review.
- Member DMs on approval/rejection when Discord allows DMs.
- `/healthz` exposes `state_backend` (`postgres` or `sqlite`).
- Optional `migrate_state_sqlite_to_postgres.py` migration helper.
- `.env.example` and `STATE_STORAGE.md`.

## Production recommendation

For Render launch, use PostgreSQL. Do not rely on the Free web service's local SQLite filesystem for identity state.

Recommended new Render variables:

```env
STATE_DATABASE_URL=<PostgreSQL internal URL>
VERIFICATION_CHANNEL_ID=<private leadership channel ID>
```

After deployment, verify `/healthz` contains:

```json
"state_backend": "postgres"
```
