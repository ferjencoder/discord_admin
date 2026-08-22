from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

from voltron_calendar import (
    build_calendar_chunks,
    build_today_chunks,
    parse_voltron_calendar_html,
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
    assert "<t:" in joined


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
    assert "Mini tournaments" in joined
    assert "Hammer and Anvil" in joined


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
