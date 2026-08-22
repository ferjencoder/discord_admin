# OZY Admin - Voltron Calendar Setup

## What this integration does

OZY Admin reads Voltron's public Total Battle tournament calendar and maintains two Discord outputs:

- `CALENDAR_CHANNEL_ID`: rolling next-30-days tournament calendar.
- `TODAY_CHANNEL_ID`: one daily "OZY Today" post with today's tournament activity.

The bot does **not** need a Voltron login, browser cookie, account token, or SignalR/WebSocket connection.

Source endpoints:

```text
https://nexusportal.voltron.me/api/calendar/snapshot-meta?realm=Regular
https://nexusportal.voltron.me/api/calendar/content?realm=Regular
```

The lightweight `snapshot-meta` request is attempted first. When unavailable, the bot safely falls back to the public content endpoint.

## Discord channels

Create or choose two normal text channels, for example:

```text
#calendar
#today
```

Copy each channel ID and add it to Render:

```env
CALENDAR_CHANNEL_ID=123456789012345678
TODAY_CHANNEL_ID=234567890123456789
```

OZY Admin needs these permissions in both channels:

- View Channel
- Send Messages
- Read Message History

`Read Message History` is important because the bot recovers its canonical posts after a restart and edits them instead of creating duplicates.

It does not need Manage Messages for this feature.

## Render variables

Recommended initial configuration:

```env
VOLTRON_CALENDAR_ENABLED=true
VOLTRON_BASE_URL=https://nexusportal.voltron.me
VOLTRON_REALM=Regular
VOLTRON_REFRESH_MINUTES=30
VOLTRON_CALENDAR_DAYS=30
VOLTRON_TODAY_ENABLED=true
VOLTRON_TODAY_TIME=08:00
VOLTRON_MIN_ACTIONS=10
```

`SCHEDULE_TIMEZONE` controls which civil date is considered "today" and which date heading an event belongs under:

```env
SCHEDULE_TIMEZONE=America/Argentina/Buenos_Aires
```

The actual event clock times use Discord timestamp tags, so each member sees the time in their own Discord-local timezone.

If you prefer the daily post shortly after Total Battle reset, set `VOLTRON_TODAY_TIME` to the desired local time in `SCHEDULE_TIMEZONE`.

## Calendar channel behavior

The calendar channel is intentionally low-noise.

- Shows the next 30 days.
- Lists tournament `STARTS` grouped by date.
- Includes the matched ending date/time when Voltron exposes one.
- Uses a small message series only when needed to stay below Discord's 2,000-character message limit.
- Existing messages are edited in place.
- Old extra chunks are removed when the calendar becomes shorter.
- The rolling window advances automatically when the configured local date changes, even if Voltron's underlying snapshot did not change.

## Today channel behavior

One canonical post is created per day and contains:

- tournaments starting today;
- multi-day tournaments continuing today;
- tournaments ending today;
- mini tournaments scheduled today.

If Voltron changes the schedule later the same day, the existing daily post is edited rather than duplicated.

## Failure behavior

The bot is deliberately fail-safe:

- A Voltron HTTP failure does not clear Discord messages.
- A suspicious parse returning fewer than `VOLTRON_MIN_ACTIONS` is rejected.
- The last in-memory good snapshot is retained when a refresh fails.
- Calendar messages are recoverable from Discord history even when Render loses the ephemeral SQLite file.
- The source is polled at a controlled interval rather than maintaining a permanent SignalR socket.

## Commands

Everyone:

```text
/calendar
/today
```

Leadership:

```text
/calendar-refresh
/calendar-status
```

Use `/calendar-status` first after deployment. It should show cached actions and a recent successful fetch.

## First deployment test

1. Deploy OZY Admin with the two channel IDs and Voltron variables.
2. Run `/calendar-status`.
3. Run `/calendar-refresh`.
4. Confirm `#calendar` receives the rolling 30-day calendar.
5. Run `/today` and compare it with Voltron's website.
6. Run `/calendar-refresh` again and confirm it edits the existing calendar messages rather than creating duplicates.
7. Restart the Render service.
8. Run `/calendar-refresh` again and confirm the bot recovers the old calendar messages from Discord history.
