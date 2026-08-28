# OZY Admin - Tournament Calendar Setup

## What this integration does

OZY Admin maintains two Discord outputs from a configured read-only tournament calendar source:

- `CALENDAR_CHANNEL_ID`: rolling next-30-days tournament calendar.
- `TODAY_CHANNEL_ID`: one canonical `OZY Today` post for the current Total Battle game day.

The bot does not need a source-site login, browser cookie, personal account token, browser automation, or websocket client.

The source base URL is supplied through `CALENDAR_BASE_URL`. The bot reads:

```text
/api/calendar/snapshot-meta?realm=Regular
/api/calendar/content?realm=Regular
```

The lightweight metadata request is used for automatic change detection. After a good snapshot exists, a temporary metadata failure keeps the cached calendar and does not trigger an unnecessary full download.

## Discord channels

Create or choose two normal text channels, for example:

```text
#calendar
#today
```

Add the IDs to Render:

```env
CALENDAR_CHANNEL_ID=123456789012345678
TODAY_CHANNEL_ID=234567890123456789
```

OZY Admin needs in both channels:

- View Channel
- Send Messages
- Read Message History

`Read Message History` is required so the bot can recover its canonical posts after a restart and edit them instead of creating duplicates.

## Render variables

```env
CALENDAR_ENABLED=true
CALENDAR_BASE_URL=<calendar source base URL>
CALENDAR_REALM=Regular
CALENDAR_DAYS=30
TODAY_ENABLED=true
CALENDAR_MIN_ACTIONS=10
```

`OZY Today` is not scheduled by civil midnight. It rolls automatically at the Total Battle reset:

```text
17:00 UTC = R+0
```

The current game day is the half-open interval:

```text
[today R+0, tomorrow R+0)
```

So an event at exactly tomorrow's R+0 belongs to the next game day.

## Calendar channel behavior

- Shows the next 30 days.
- Lists tournament starts and regular mini events.
- Uses Total Battle reset-clock notation.
- **Every day is its own triple-backtick code block** for clean copy/paste.
- Uses multiple Discord messages only when necessary to stay below the 2,000-character limit.
- Existing messages are edited in place.
- Old extra chunks are removed when the calendar becomes shorter.

Example structure:

````text
OZY Tournament Calendar - Next 30 Days

```
Sun 23 Aug
- R+0 Ancients' Treasure
- R+0 Ruthless Slaughter
```

```
Mon 24 Aug
- R+0 Clash for the Throne
- R+0 Conquerors' Revival
```
````

## Today channel behavior

One canonical post is created for each Total Battle game day and contains:

- tournament starts in the reset-to-reset window;
- multi-day tournament continue markers in the window;
- tournament end markers in the window;
- **all regular mini events from the current R+0 until the next R+0**.

Mini-event source times are already UTC and are parsed as UTC exactly as published. The parser ignores the dynamic `Time till start` column and keeps the `Bonus` column when present. The separate `for SK below` section remains ignored.

If source data changes later in the same game day, the existing Today message is edited rather than duplicated.

## Commands

Everyone:

```text
/calendar
/today
/time
```

Leadership:

```text
/calendar-refresh
/calendar-status
/event-create
```

## First deployment test

1. Deploy OZY Admin with `CALENDAR_BASE_URL`, `CALENDAR_CHANNEL_ID`, and `TODAY_CHANNEL_ID`.
2. Confirm Render startup logs say `event-create` was included in the synchronized command list.
3. Run `/calendar-status`.
4. Run `/calendar-refresh`.
5. Confirm `#calendar` uses one copyable code block per day.
6. Run `/today`.
7. Confirm mini events include post-midnight UTC events until the next R+0.
8. Run `/event-create` and create a test Discord scheduled event.
9. Restart the Render service and confirm the canonical calendar/Today messages are recovered and edited rather than duplicated.
