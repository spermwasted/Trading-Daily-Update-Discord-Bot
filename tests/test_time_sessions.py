from datetime import time, timedelta
from zoneinfo import ZoneInfo

from gold_bot.analysis.sessions import SessionSchedule, session_stats, sweep_status
from gold_bot.models import Timeframe
from gold_bot.timeutils import candle_end, fmt_local, market_closed, split_completed, trading_date, trading_day_start
from tests.helpers import c, dt, series

NY = ZoneInfo("America/New_York")
PKT = ZoneInfo("Asia/Karachi")
SCHEDULE = SessionSchedule(
    {"asia": time(19), "london": time(3), "newyork": time(8), "newyork_close": time(17)}, NY
)


def test_trading_day_start_follows_new_york_dst():
    # Summer (EDT): 17:00 NY = 21:00 UTC. Winter (EST): 22:00 UTC.
    assert trading_day_start(dt("2026-07-15T12:00:00Z")) == dt("2026-07-14T21:00:00Z")
    assert trading_day_start(dt("2026-12-15T23:00:00Z")) == dt("2026-12-15T22:00:00Z")


def test_d1_candle_end_across_dst_changes():
    # Candle opening Sat 31 Oct 17:00 EDT? (no - weekend) use Sun 1 Nov 2026 17:00 EST (after fall back)
    assert candle_end(Timeframe.D1, dt("2026-11-01T22:00:00Z")) == dt("2026-11-02T22:00:00Z")
    # Friday 30 Oct 2026 candle opened Thu 17:00 EDT = 21:00Z, ends Fri 17:00 EDT
    assert candle_end(Timeframe.D1, dt("2026-10-29T21:00:00Z")) == dt("2026-10-30T21:00:00Z")
    # Spring forward: Sun 8 Mar 2026 17:00 EDT = 21:00Z; previous Fri candle ended 22:00Z
    assert candle_end(Timeframe.D1, dt("2026-03-08T21:00:00Z")) == dt("2026-03-09T21:00:00Z")


def test_h4_aligned_to_trading_day():
    assert candle_end(Timeframe.H4, dt("2026-07-14T21:00:00Z")) == dt("2026-07-15T01:00:00Z")


def test_trading_date_label():
    assert trading_date(dt("2026-11-09T22:00:00Z")).isoformat() == "2026-11-10"  # Mon 17:00 EST -> Tue


def test_split_completed_marks_forming_candle():
    d1 = series(dt("2026-11-08T22:00:00Z"), timedelta(days=1), [(1, 2, 0.5, 1.5), (1.5, 2, 1, 1.8)])
    completed, forming = split_completed(Timeframe.D1, d1, dt("2026-11-10T06:00:00Z"))
    assert completed == d1[:1] and forming == d1[1]
    completed, forming = split_completed(Timeframe.D1, d1, dt("2026-11-10T22:00:00Z"))
    assert completed == d1 and forming is None


def test_weekend_closure():
    assert market_closed(dt("2026-11-07T12:00:00Z"))  # Saturday
    assert market_closed(dt("2026-11-06T22:30:00Z"))  # Fri 17:30 EST
    assert not market_closed(dt("2026-11-08T22:30:00Z"))  # Sun 17:30 EST


def test_session_opens_in_pkt_summer_and_winter():
    summer = SCHEDULE.occurrences(dt("2026-07-14T00:00:00Z"), dt("2026-07-15T00:00:00Z"))
    opens = {o.key: fmt_local(o.start, PKT, with_date=False) for o in summer}
    assert opens == {"london": "12:00 PKT", "newyork": "17:00 PKT", "asia": "04:00 PKT"}
    winter = SCHEDULE.occurrences(dt("2026-12-15T00:00:00Z"), dt("2026-12-16T00:00:00Z"))
    opens = {o.key: fmt_local(o.start, PKT, with_date=False) for o in winter}
    assert opens == {"london": "13:00 PKT", "newyork": "18:00 PKT", "asia": "05:00 PKT"}


def test_dst_transition_day_sessions():
    # US DST starts Sun 8 Mar 2026. Monday London opens 03:00 EDT = 07:00 UTC.
    occ = SCHEDULE.occurrences(dt("2026-03-09T01:00:00Z"), dt("2026-03-09T12:01:00Z"))
    assert [(o.key, o.start) for o in occ] == [
        ("london", dt("2026-03-09T07:00:00Z")),
        ("newyork", dt("2026-03-09T12:00:00Z")),
    ]
    # Sunday's Asia session opened 19:00 EDT = 23:00 UTC (first session after the change)
    assert SCHEDULE.latest_open(dt("2026-03-08T23:30:00Z")).start == dt("2026-03-08T23:00:00Z")
    # Previous Friday (EST) London opened 03:00 EST = 08:00 UTC
    fri = SCHEDULE.occurrences(dt("2026-03-06T01:00:00Z"), dt("2026-03-06T10:00:00Z"))
    assert [(o.key, o.start) for o in fri] == [("london", dt("2026-03-06T08:00:00Z"))]


def test_no_sessions_during_weekend_and_monday_asia_follows_friday_new_york():
    weekend = SCHEDULE.occurrences(dt("2026-11-06T22:00:00Z"), dt("2026-11-08T22:00:00Z"))
    assert weekend == []
    sunday_asia = SCHEDULE.latest_open(dt("2026-11-09T00:30:00Z"))  # Sun 19:30 EST
    assert sunday_asia.key == "asia" and sunday_asia.start == dt("2026-11-09T00:00:00Z")
    prev = SCHEDULE.previous(sunday_asia)
    assert prev.key == "newyork" and prev.start == dt("2026-11-06T13:00:00Z")


def test_session_sequence_previous_mapping():
    london = SCHEDULE.current(dt("2026-11-10T08:30:00Z"))
    assert london.key == "london"
    assert SCHEDULE.previous(london).key == "asia"
    ny = SCHEDULE.current(dt("2026-11-10T14:00:00Z"))
    assert SCHEDULE.previous(ny).key == "london"
    assert SCHEDULE.current(dt("2026-11-10T22:30:00Z")) is None  # 17:30 NY gap before Asia


def test_session_stats_and_sweep():
    london = SCHEDULE.current(dt("2026-11-10T08:30:00Z"))
    asia = SCHEDULE.previous(london)
    m5 = [
        c(asia.start, 4100, 4110, 4095, 4105),
        c(asia.end - timedelta(minutes=5), 4105, 4108, 4101, 4106),
        c(london.start, 4106, 4112, 4104, 4111),
        c(london.start + timedelta(minutes=5), 4111, 4113, 4109, 4110),
    ]
    a, lo = session_stats(asia, m5), session_stats(london, m5)
    assert (a.open, a.high, a.low) == (4100, 4110, 4095)
    assert (lo.open, lo.high, lo.low, lo.last) == (4106, 4113, 4104, 4110)
    s = sweep_status(lo, a)
    assert s.swept_high and not s.swept_low and s.detected
