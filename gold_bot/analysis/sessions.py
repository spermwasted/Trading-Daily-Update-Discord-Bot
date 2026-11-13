"""Asia / London / New York session schedule and per-session statistics.

Session open/close times are New York wall-clock times (configurable), so they
shift with New York daylight saving. Defaults:
  Asia      19:00 -> 03:00 NY   (summer 04:00-12:00 PKT, winter 05:00-13:00 PKT)
  London    03:00 -> 08:00 NY   (summer 12:00-17:00 PKT, winter 13:00-18:00 PKT)
  New York  08:00 -> 17:00 NY   (summer 17:00-02:00 PKT, winter 18:00-03:00 PKT)
Each session ends when the next one opens; New York ends at the FXCM daily
close (17:00 NY). No sessions run during the weekend closure (Fri 17:00 -
Sun 17:00 NY), so Monday's Asia session follows Friday's New York session.

Session statistics are computed from FXCM m5 candles, which align exactly with
these boundaries (all session times are multiples of 5 minutes).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from gold_bot.models import Candle
from gold_bot.timeutils import market_closed

UTC = timezone.utc
SESSION_ORDER = ("asia", "london", "newyork")
SESSION_NAMES = {"asia": "Asian", "london": "London", "newyork": "New York"}


@dataclass(frozen=True)
class SessionOccurrence:
    key: str
    start: datetime  # UTC
    end: datetime  # UTC

    @property
    def name(self) -> str:
        return SESSION_NAMES[self.key]

    @property
    def id(self) -> str:
        return f"{self.key}:{self.start.strftime('%Y-%m-%dT%H:%MZ')}"

    def contains(self, moment: datetime) -> bool:
        return self.start <= moment < self.end


@dataclass(frozen=True)
class SessionStats:
    open: float
    high: float
    low: float
    last: float
    high_time: datetime
    low_time: datetime


class SessionSchedule:
    def __init__(self, times: dict[str, time], zone: ZoneInfo) -> None:
        self.times = times
        self.zone = zone

    def _at(self, day: date, t: time) -> datetime:
        return datetime.combine(day, t).replace(tzinfo=self.zone).astimezone(UTC)

    def _occurrences_for_date(self, day: date) -> list[SessionOccurrence]:
        """Sessions that OPEN on New York calendar date ``day``."""
        asia, london, ny, ny_close = (self.times[k] for k in ("asia", "london", "newyork", "newyork_close"))
        result = []
        # Asia opens in the evening and runs into the next calendar day.
        asia_end_day = day + timedelta(days=1) if asia > london else day
        result.append(SessionOccurrence("asia", self._at(day, asia), self._at(asia_end_day, london)))
        result.append(SessionOccurrence("london", self._at(day, london), self._at(day, ny)))
        result.append(SessionOccurrence("newyork", self._at(day, ny), self._at(day, ny_close)))
        return [o for o in result if not market_closed(o.start)]

    def occurrences(self, start: datetime, end: datetime) -> list[SessionOccurrence]:
        """All sessions opening in [start, end), sorted."""
        first = start.astimezone(self.zone).date() - timedelta(days=1)
        last = end.astimezone(self.zone).date() + timedelta(days=1)
        found: list[SessionOccurrence] = []
        day = first
        while day <= last:
            found.extend(o for o in self._occurrences_for_date(day) if start <= o.start < end)
            day += timedelta(days=1)
        return sorted(found, key=lambda o: o.start)

    def latest_open(self, now: datetime) -> SessionOccurrence | None:
        """Most recent session that opened at or before ``now``."""
        found = self.occurrences(now - timedelta(days=4), now + timedelta(seconds=1))
        return found[-1] if found else None

    def current(self, now: datetime) -> SessionOccurrence | None:
        occ = self.latest_open(now)
        return occ if occ and occ.contains(now) else None

    def previous(self, occurrence: SessionOccurrence) -> SessionOccurrence | None:
        found = self.occurrences(occurrence.start - timedelta(days=4), occurrence.start)
        return found[-1] if found else None

    def next_open(self, now: datetime) -> SessionOccurrence | None:
        found = self.occurrences(now + timedelta(seconds=1), now + timedelta(days=4))
        return found[0] if found else None


def session_stats(occurrence: SessionOccurrence, m5_candles: list[Candle]) -> SessionStats | None:
    """OHLC statistics of the session from m5 candles inside it (None if no data)."""
    inside = [c for c in m5_candles if occurrence.start <= c.time < occurrence.end]
    if not inside:
        return None
    high_candle = max(inside, key=lambda c: c.high)
    low_candle = min(inside, key=lambda c: c.low)
    return SessionStats(
        open=inside[0].open,
        high=high_candle.high,
        low=low_candle.low,
        last=inside[-1].close,
        high_time=high_candle.time,
        low_time=low_candle.time,
    )


@dataclass(frozen=True)
class SweepStatus:
    swept_high: bool
    swept_low: bool

    @property
    def detected(self) -> bool:
        return self.swept_high or self.swept_low

    def describe(self, previous_name: str) -> str:
        if self.swept_high and self.swept_low:
            return f"DETECTED - {previous_name} high and low swept"
        if self.swept_high:
            return f"DETECTED - {previous_name} high swept"
        if self.swept_low:
            return f"DETECTED - {previous_name} low swept"
        return "NOT DETECTED"


def sweep_status(current: SessionStats | None, previous: SessionStats | None) -> SweepStatus | None:
    if current is None or previous is None:
        return None
    return SweepStatus(swept_high=current.high > previous.high, swept_low=current.low < previous.low)
