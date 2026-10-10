# Canonical OZY game calendar

Deploy the website changes first. The bot now reads only the public OZY endpoint:

```env
GAME_EVENTS_API_URL=https://ozy.com.ar/api/ozy/events
CALENDAR_ENABLED=true
CALENDAR_CHANNEL_ID=123456789012345678
TODAY_CHANNEL_ID=234567890123456789
CALENDAR_REFRESH_MINUTES=30
CALENDAR_DAYS=30
TODAY_ENABLED=true
```

GAME_EVENTS_API_URL defaults to the production URL. CALENDAR_BASE_URL and CALENDAR_REALM are legacy settings and no longer choose a provider; existing Render values can be removed. CALENDAR_MIN_ACTIONS no longer rejects short but valid canonical calendars.

The bot converts schema_version=1 events into the existing CalendarSnapshot. Nexus STARTS/CONTINUE/ENDS and event details survive unchanged. Events without provider actions generate starts, known ends and daily continuation actions at the 17:00 UTC reset. Unknown mini ends use start as the internal display sentinel; the API's end remains null.

Calendar and Today rendering, copyable code blocks, persistent message tracking and scheduling remain in place. A game day is [17:00 UTC, next 17:00 UTC), using R+0 notation. Calendar shows starts over the next 30 days; Today groups starts, continuations, ends and mini events. Discord permissions remain View Channel, Send Messages and Read Message History.

Every scheduled refresh, leadership refresh and legacy once-daily mini refresh reads the same canonical API. No bot path fetches Akurier or Nexus directly. Semantic comparison excludes request/freshness timestamps, so unchanged schedules do not cause calendar reposts. The website caches provider downloads for five minutes. The bot retains its last good snapshot on HTTP/schema failures and records last_error; degraded API responses are usable and recorded as degraded.

Clan-created events and Power Hours continue through SCHEDULE_URL and /api/ozy/schedule, with their existing audiences, authorization and persistent storage.

Run calendar checks with:

```text
python -m pytest tests/test_event_calendar.py tests/test_canonical_calendar.py
```


## Free-tier traffic policy

Cloudflare refreshes the canonical sources once daily at 17:00 UTC (14:00
Argentina). The bot reads the cached website calendar at startup and at 17:01
UTC, without probing providers between resets. Explicit leadership refresh
commands remain available. The separate automatic Akurier job is disabled.

The integrated Merc feed makes no website requests unless the cached calendar
has a confirmed Mercenary Exchange start/end window containing the current time.
Missing, stale (>26 hours), or unknown event timing leaves the feed paused.
The local timer does not perform network/database work while paused.

Website event mutations notify the authenticated `/internal/website-changed`
route on the existing Render server. The bot drains pending work once on restart
and after notifications; it then waits without polling. Failed changes use
bounded exponential backoff. Website notifications retry briefly, and durable
pending events remain recoverable using the manager's Retry Discord sync action
or after a bot restart. Do not add public wake-up routes or log their token.
