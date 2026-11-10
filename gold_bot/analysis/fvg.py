"""ICT three-candle Fair Value Gap detection and mitigation tracking.

Bullish FVG: candle1.high < candle3.low  -> zone [candle1.high, candle3.low]
Bearish FVG: candle1.low  > candle3.high -> zone [candle3.high, candle1.low]

Detection uses COMPLETED candles only (candle 3 must have closed). Mitigation is
measured from candle 4 onward, including the forming candle's high/low so far:

  bullish: deepest low after formation  (price trades down into the gap)
  bearish: highest high after formation (price trades up into the gap)

  UNFILLED          price has not reached the gap's near edge yet
  PARTIALLY_FILLED  price has traded into the gap (or tapped its edge) but not
                    through the far edge
  FILLED            price reached the far edge -> no longer an FVG, dropped
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum

from gold_bot.models import Candle, Timeframe


class FVGDirection(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"


class FVGStatus(str, Enum):
    UNFILLED = "Unfilled"
    PARTIALLY_FILLED = "Partially Filled"
    FILLED = "Filled"


@dataclass(frozen=True)
class FVG:
    timeframe: Timeframe
    direction: FVGDirection
    lower: float
    upper: float
    candle1_time: datetime
    candle3_time: datetime
    # Most extreme price reached into the gap since formation (None = never
    # traded after candle 3): lowest low for bullish, highest high for bearish.
    extreme: float | None = None

    @property
    def key(self) -> str:
        return f"{self.timeframe.value}|{self.direction.value}|{self.candle1_time.isoformat()}"

    @property
    def size(self) -> float:
        return self.upper - self.lower

    @property
    def status(self) -> FVGStatus:
        if self.extreme is None:
            return FVGStatus.UNFILLED
        if self.direction is FVGDirection.BULLISH:
            if self.extreme <= self.lower:
                return FVGStatus.FILLED
            if self.extreme <= self.upper:
                return FVGStatus.PARTIALLY_FILLED
            return FVGStatus.UNFILLED
        if self.extreme >= self.upper:
            return FVGStatus.FILLED
        if self.extreme >= self.lower:
            return FVGStatus.PARTIALLY_FILLED
        return FVGStatus.UNFILLED

    @property
    def fill_percent(self) -> float:
        status = self.status
        if status is FVGStatus.UNFILLED:
            return 0.0
        if status is FVGStatus.FILLED:
            return 100.0
        if self.direction is FVGDirection.BULLISH:
            filled = self.upper - self.extreme
        else:
            filled = self.extreme - self.lower
        return max(0.0, min(100.0, filled / self.size * 100))

    @property
    def is_active(self) -> bool:
        return self.status is not FVGStatus.FILLED

    def with_price_action(self, candles: list[Candle]) -> "FVG":
        """Fold in candles that came after candle 3 (others are ignored)."""
        later = [c for c in candles if c.time > self.candle3_time]
        if not later:
            return self
        if self.direction is FVGDirection.BULLISH:
            low = min(c.low for c in later)
            extreme = low if self.extreme is None else min(self.extreme, low)
        else:
            high = max(c.high for c in later)
            extreme = high if self.extreme is None else max(self.extreme, high)
        return replace(self, extreme=extreme)

    def distance(self, price: float) -> float:
        """0 if price is inside the zone, else distance to the nearest edge."""
        if self.lower <= price <= self.upper:
            return 0.0
        return self.lower - price if price < self.lower else price - self.upper


def detect(timeframe: Timeframe, completed: list[Candle]) -> list[FVG]:
    """All three-candle FVGs in a list of completed candles (oldest first)."""
    gaps: list[FVG] = []
    for c1, _c2, c3 in zip(completed, completed[1:], completed[2:]):
        if c1.high < c3.low:
            gaps.append(FVG(timeframe, FVGDirection.BULLISH, c1.high, c3.low, c1.time, c3.time))
        elif c1.low > c3.high:
            gaps.append(FVG(timeframe, FVGDirection.BEARISH, c3.high, c1.low, c1.time, c3.time))
    return gaps


def analyse(timeframe: Timeframe, completed: list[Candle], forming: Candle | None) -> list[FVG]:
    """Detect FVGs and apply all later price action (including the forming candle)."""
    action = completed + ([forming] if forming is not None else [])
    return [gap.with_price_action(action) for gap in detect(timeframe, completed)]


def active_sorted(gaps: list[FVG], price: float) -> list[FVG]:
    """Unfilled / partially filled gaps, nearest to ``price`` first."""
    return sorted((g for g in gaps if g.is_active), key=lambda g: (g.distance(price), g.timeframe.value))
