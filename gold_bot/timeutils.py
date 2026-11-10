"""FXCM trading-day and candle-boundary helpers.

FXCM's documentation ("What is Timeframe?") states:
  * a D1 candle runs from 17:00 New York to 17:00 New York the next day,
  * H-timeframes are aligned to the trading-day start (so H4 candles open at
    17:00, 21:00, 01:00, 05:00, 09:00, 13:00 New York),
  * minute timeframes are aligned to the hour.
The analysis uses the candle timestamps FXCM actually returns; these helpers are
only used to decide whether the newest (still forming) candle has closed.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from gold_bot.models import Candle, Timeframe

UTC = timezone.utc
NEW_YORK = ZoneInfo("America/New_York")
TRADING_DAY_START = time(17, 0)

_FIXED = {
    Timeframe.M5: timedelta(minutes=5),
    Timeframe.M15: timedelta(minutes=15),
    Timeframe.H1: timedelta(hours=1),
}


def _wall_clock_add(start_utc: datetime, delta: timedelta) -> datetime:
    """Add ``delta`` in New York wall-clock time (handles DST changes)."""
    local = start_utc.astimezone(NEW_YORK)
    naive_end = local.replace(tzinfo=None) + delta
    return naive_end.replace(tzinfo=NEW_YORK).astimezone(UTC)


def candle_end(timeframe: Timeframe, start_utc: datetime) -> datetime:
    if timeframe in _FIXED:
        return start_utc + _FIXED[timeframe]
    if timeframe is Timeframe.H4:
        return _wall_clock_add(start_utc, timedelta(hours=4))
    if timeframe is Timeframe.D1:
        return _wall_clock_add(start_utc, timedelta(days=1))
    raise ValueError(timeframe)


def split_completed(timeframe: Timeframe, candles: list[Candle], now: datetime) -> tuple[list[Candle], Candle | None]:
    """Return (completed candles, forming candle or None).

    Every candle followed by another one is complete. The newest candle is
    complete only once its FXCM period has ended.
    """
    if not candles:
        return [], None
    last = candles[-1]
    if now >= candle_end(timeframe, last.time):
        return list(candles), None
    return list(candles[:-1]), last


def trading_day_start(now: datetime) -> datetime:
    """Most recent 17:00 New York at or before ``now`` (as UTC)."""
    local = now.astimezone(NEW_YORK)
    start_local = datetime.combine(local.date(), TRADING_DAY_START)
    if local.time() < TRADING_DAY_START:
        start_local -= timedelta(days=1)
    return start_local.replace(tzinfo=NEW_YORK).astimezone(UTC)


def trading_date(candle_start_utc: datetime) -> date:
    """Calendar date a trading day is named after (the day it closes on).

    A D1 candle opening Monday 17:00 New York is Tuesday's candle.
    """
    local = candle_start_utc.astimezone(NEW_YORK)
    return (local + timedelta(hours=7)).date()


def market_closed(now: datetime) -> bool:
    """Weekend closure: Friday 17:00 to Sunday 17:00 New York."""
    local = now.astimezone(NEW_YORK)
    wd, t = local.weekday(), local.time()
    if wd == 5:
        return True
    if wd == 4 and t >= TRADING_DAY_START:
        return True
    if wd == 6 and t < TRADING_DAY_START:
        return True
    return False


def fmt_local(dt: datetime, tz: ZoneInfo, with_date: bool = True) -> str:
    local = dt.astimezone(tz)
    abbr = local.tzname() or ""
    if abbr.startswith(("+", "-")) and tz.key == "Asia/Karachi":
        abbr = "PKT"
    return local.strftime(("%a %d %b %Y %H:%M" if with_date else "%H:%M")) + f" {abbr}".rstrip()
