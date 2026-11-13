"""Build higher-timeframe candles from lower-timeframe candles.

Used when a data source does not offer FXCM-style candles directly (e.g. Twelve
Data's daily bars run midnight to midnight). Buckets follow FXCM's boundaries:
  m5 / m15 / H1  aligned to the hour (UTC)
  H4             aligned to the 17:00 New York trading-day open
                 (17:00, 21:00, 01:00, 05:00, 09:00, 13:00 New York wall-clock)
  D1             17:00 New York -> 17:00 New York
Aggregation is exact: open = first open, high = max high, low = min low,
close = last close. A leading bucket that the source data only partly covers is
dropped, so no candle is ever built from incomplete history.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

from gold_bot.models import Candle, Timeframe
from gold_bot.timeutils import NEW_YORK, trading_day_start

UTC = timezone.utc
_MINUTES = {Timeframe.M5: 5, Timeframe.M15: 15, Timeframe.H1: 60}


def bucket_start(timeframe: Timeframe, moment: datetime) -> datetime:
    if timeframe in _MINUTES:
        step = _MINUTES[timeframe]
        utc = moment.astimezone(UTC)
        return utc.replace(minute=utc.minute - utc.minute % step, second=0, microsecond=0)
    if timeframe is Timeframe.D1:
        return trading_day_start(moment)
    if timeframe is Timeframe.H4:
        local = moment.astimezone(NEW_YORK)
        index = ((local.hour - 17) % 24) // 4
        hour = (17 + 4 * index) % 24
        day = local.date() if hour <= local.hour else local.date() - timedelta(days=1)
        return datetime.combine(day, time(hour)).replace(tzinfo=NEW_YORK).astimezone(UTC)
    raise ValueError(timeframe)


def aggregate(candles: list[Candle], timeframe: Timeframe) -> list[Candle]:
    """Aggregate time-sorted candles into ``timeframe`` buckets."""
    if not candles:
        return []
    buckets: dict[datetime, list[Candle]] = {}
    for candle in candles:
        buckets.setdefault(bucket_start(timeframe, candle.time), []).append(candle)
    result = []
    for start in sorted(buckets):
        members = buckets[start]
        result.append(
            Candle(
                time=start,
                open=members[0].open,
                high=max(c.high for c in members),
                low=min(c.low for c in members),
                close=members[-1].close,
                volume=sum(c.volume for c in members),
            )
        )
    if result and candles[0].time > result[0].time:
        result = result[1:]  # history starts mid-bucket: drop the partial bucket
    return result
