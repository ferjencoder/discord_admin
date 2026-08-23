from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from event_calendar import (
    build_calendar_chunks,
    build_today_chunks,
    parse_voltron_calendar_html,
    reset_label,
    build_today_local_chunks,
    parse_akurier_mini_events_html,
)


HTML = r'''
<html><body>
<h2>Mini Tournaments</h2>
<div>20 showing</div>
<div>Aug 21 · 21:30 UTC running · ends 01:00 UTC</div>
<div>in — ends in —</div>
<div>Silver Rush</div>
<div>Aug 22 · 01:00 UTC running · ends 03:00 UTC</div>
<div>in — ends in —</div>
<div>Hammer and Anvil</div>
<h2>Now Running</h2>
<div id="vl-cal-timeline">
  <div class="vl-cal__row" data-utc="2026-08-22T17:00:00Z">
    <img src="x"><span>17:00 UTC</span><strong>STARTS</strong><div>Ragnarök</div>
  </div>
  <div class="vl-cal__row" data-utc="2026-08-23T17:00:00+00:00">
    <span>17:00 UTC CONTINUE</span><div>Ragnarök</div>
  </div>
  <div class="vl-cal__row" data-utc="2026-08-24T17:00:00Z">
    <span>17:00 UTC</span><span>ENDS</span><div>Ragnarök</div>
  </div>
  <div class="vl-cal__row" data-utc="2026-08-22T17:00:00Z">
    <span>17:00 UTC STARTS</span><div>Summon Mastery</div><small>Aurora Fragments</small>
  </div>
</div>
</body></html>
'''


def snapshot():
    return parse_voltron_calendar_html(
        HTML,
        reference=datetime(2026, 8, 21, 22, 0, tzinfo=timezone.utc),
    )


def test_parse_full_calendar_rows_and_details():
    snap = snapshot()
    assert len(snap.actions) == 4
    assert snap.actions[0].title in {"Ragnarök", "Summon Mastery"}
    summon = next(x for x in snap.actions if x.title == "Summon Mastery")
    assert summon.action == "STARTS"
    assert summon.details == "Aurora Fragments"


def test_void_img_does_not_break_row_capture():
    snap = snapshot()
    rag = [x for x in snap.actions if x.title == "Ragnarök"]
    assert [x.action for x in rag] == ["STARTS", "CONTINUE", "ENDS"]


def test_parse_mini_tournaments_and_midnight_rollover():
    snap = snapshot()
    assert len(snap.mini_tournaments) == 2
    silver = snap.mini_tournaments[0]
    assert silver.title == "Silver Rush"
    assert silver.start_utc.isoformat() == "2026-08-21T21:30:00+00:00"
    assert silver.end_utc.isoformat() == "2026-08-22T01:00:00+00:00"


def test_calendar_only_lists_starts():
    snap = snapshot()
    chunks = build_calendar_chunks(
        snap,
        start_date=date(2026, 8, 21),
        days=30,
        timezone_info=ZoneInfo("UTC"),
    )
    joined = "\n".join(chunks)
    assert "Ragnarök" in joined
    assert "Summon Mastery" in joined
    assert "Active / continues" not in joined
    assert "Ends today" not in joined
    assert "<t:" not in joined
    assert joined.startswith("OZY Tournament Calendar - Next 30 Days")
    assert "```\nFri 21 Aug" in joined
    assert "```\nSat 22 Aug" in joined
    assert "Times display in each member's Discord timezone" not in joined
    assert "nexusportal.voltron.me" not in joined
    assert "R+0 Ragnarök" in joined
    assert "ends" not in joined
    assert "Mini Events" in joined
    assert "Silver Rush" in joined
    assert "Hammer and Anvil" in joined


def test_today_includes_major_actions_and_minis():
    snap = snapshot()
    chunks = build_today_chunks(
        snap,
        target_date=date(2026, 8, 22),
        timezone_info=ZoneInfo("UTC"),
    )
    joined = "\n".join(chunks)
    assert "Starts today" in joined
    assert "Ragnarök" in joined
    assert "Summon Mastery" in joined
    assert "Mini Events" in joined
    assert "Hammer and Anvil" in joined
    assert joined.startswith("```\nOZY Today")
    assert "Times display in each member's Discord timezone" not in joined
    assert "nexusportal.voltron.me" not in joined


def test_semantic_hash_is_stable_for_same_html():
    a = snapshot()
    b = snapshot()
    assert a.semantic_hash == b.semantic_hash


def test_calendar_chunking_stays_under_discord_limit():
    rows = []
    for i in range(60):
        rows.append(
            f'<div class="vl-cal__row" data-utc="2026-08-22T{(i % 24):02d}:00:00Z">'
            f'<span>{(i % 24):02d}:00 UTC STARTS</span><div>Very Long Tournament Name {i} ' + ('X' * 40) + '</div></div>'
        )
    snap = parse_voltron_calendar_html('<div id="vl-cal-timeline">' + ''.join(rows) + '</div>')
    chunks = build_calendar_chunks(
        snap,
        start_date=date(2026, 8, 21),
        days=30,
        timezone_info=ZoneInfo("UTC"),
    )
    assert len(chunks) > 1
    assert all(len(chunk) <= 2000 for chunk in chunks)


def test_reset_clock_notation_uses_1700_utc_and_fractional_hours():
    assert reset_label(datetime(2026, 8, 22, 17, 0, tzinfo=timezone.utc)) == "R+0"
    assert reset_label(datetime(2026, 8, 22, 18, 0, tzinfo=timezone.utc)) == "R+1"
    assert reset_label(datetime(2026, 8, 22, 18, 30, tzinfo=timezone.utc)) == "R+1.5"
    assert reset_label(datetime(2026, 8, 22, 14, 0, tzinfo=timezone.utc)) == "R-3"
    assert reset_label(datetime(2026, 8, 23, 3, 0, tzinfo=timezone.utc)) == "R+10"
    assert reset_label(datetime(2026, 8, 23, 6, 0, tzinfo=timezone.utc)) == "R-11"


def test_local_time_view_is_timezone_specific_and_start_only():
    snap = snapshot()
    chunks = build_today_local_chunks(
        snap,
        target_date=date(2026, 8, 22),
        timezone_info=ZoneInfo("America/Argentina/Buenos_Aires"),
        timezone_label="Argentina",
    )
    joined = "\n".join(chunks)
    assert "OZY Today - Argentina" in joined
    assert "14:00 Ragnarök" in joined
    assert "22:00 Hammer and Anvil" in joined
    assert "01:00" not in joined
    assert "ends" not in joined


AKURIER_HTML = r'''<html><body>
<table>
<tr><th>Start date:</th><th>Time</th><th>Event</th><th>Time till start</th><th>Bonus</th></tr>
<tr><td>22.08.2026</td><td>10:00</td><td><i>Tar Mastery</i></td><td>started</td><td>+10% crypt efficiency</td></tr>
<tr><td>22.08.2026</td><td>17:00</td><td><b>Gold Rush</b></td><td>02:00</td><td>+25% food, silver prod.</td></tr>
<tr><td>23.08.2026</td><td>01:30</td><td>Battle Training</td><td>10:30</td><td>+5% dominance</td></tr>
</table>
<h3>for SK below:</h3>
<table>
<tr><td>22.08.2026</td><td>21:30</td><td>SK Gold Rush</td><td>06:57</td><td>bonus</td></tr>
</table>
</body></html>'''


def test_akurier_regular_events_only_and_warsaw_to_utc_conversion():
    events = parse_akurier_mini_events_html(AKURIER_HTML)
    assert [e.title for e in events] == ["Tar Mastery", "Gold Rush", "Battle Training"]
    # Warsaw is UTC+2 on 22 Aug 2026.
    assert events[0].start_utc.isoformat() == "2026-08-22T08:00:00+00:00"
    assert events[1].start_utc.isoformat() == "2026-08-22T15:00:00+00:00"
    assert events[2].start_utc.isoformat() == "2026-08-22T23:30:00+00:00"
    assert all("SK" not in e.title for e in events)
    assert reset_label(events[1].start_utc) == "R-2"
    assert reset_label(events[2].start_utc) == "R+6.5"
