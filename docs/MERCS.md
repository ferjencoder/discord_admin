# Mercenary Exchange feed

Set `MERCS_CHANNEL_ID` to the destination Discord text channel or thread ID in
the configured `SERVER_ID`, then restart the existing bot. A blank value disables
the feed. The bot needs View Channel and Send Messages (Send Messages in Threads
for a thread). It sends plain messages without role/user mentions.

```env
MERCS_CHANNEL_ID=your_channel_id
MERCS_POLL_SECONDS=15
```

Reuse the existing `OZY_DATA_API_TOKEN`; no additional secret is needed.
The bot reads `https://ozy.com.ar/api/v1/mercs/current` with the existing
`X-OZY-Admin-Token` header. Redirects are rejected. Reads bypass the general
dataset cache. The poll interval accepts 5–60 seconds because the API only
returns sightings from the last 90 seconds. Old queued records are not posted.

Example message:

```text
Mercenary Exchange | Level 10
K:35 X:843 Y:603
Seen [relative Discord time] ([local Discord time])
```

Coordinates appear inside a triple-backtick code block for easy copying.
They are Total Battle-style text, not a game deep link.
Level is omitted when unavailable. `seen_at` must be an ISO timestamp with a
timezone; invalid records are skipped and reflected in health logs.

## Deduplication and delivery

The feed tracks both kingdom/object ID and kingdom/X/Y, scoped to the destination
channel. Repeated polls, overlapping scanner reports and continuously advancing
`seen_at` timestamps do not create repeated messages. A coordinate/level change
or a gap of more than 90 seconds between observations permits a new post.
Older observations cannot replace newer ones. Entries expire after 24 hours.
API failures and empty results do not clear posting history.

History uses the existing `AdminState` SQLite database and its configured remote
snapshot mechanism, so normal restarts retain deduplication. Keep one active bot
instance per state database. Production on ephemeral storage needs the existing
remote state configuration. No website/API changes are required.

Successful sends are saved immediately. Failed sends remain eligible while
fresh. A process crash between Discord accepting a message and the state write,
or an ambiguous network failure after Discord accepted it, can still duplicate
a message; Discord and SQLite cannot form one transaction. A failed state write
keeps in-memory history and is retried before further sends.

## Health and lifecycle

The task starts once in `setup_hook`, waits for Discord readiness, and is canceled
and awaited during normal shutdown alongside existing background tasks. It uses
the bot's shared HTTP session. API 401, 5xx, malformed responses, network and
Discord failures are contained with exponential retry delays capped at 300s.
Health transitions and five-minute summaries are logged without secrets or
response bodies. An empty result means no fresh Mercs, not proof that a scanner
is offline; this task does not query scanner status. Existing schedule and
announcement integrations are unaffected.

Run tests with `python -m pytest tests` from the project directory.
