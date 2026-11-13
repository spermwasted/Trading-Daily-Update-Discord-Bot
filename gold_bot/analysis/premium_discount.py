"""Premium / Discount / Equilibrium relative to a dealing range.

Equilibrium = (Range High + Range Low) / 2
price > EQ -> PREMIUM, price < EQ -> DISCOUNT, price == EQ -> EQUILIBRIUM.

The dealing range is pluggable (DEALING_RANGE_MODE). Only ``previous_day`` (the
last completed FXCM daily candle) is implemented; a swing-based builder can be
added to RANGE_BUILDERS later without touching callers.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable

from gold_bot.models import Candle


class Position(str, Enum):
    PREMIUM = "PREMIUM"
    DISCOUNT = "DISCOUNT"
    EQUILIBRIUM = "EQUILIBRIUM"


@dataclass(frozen=True)
class DealingRange:
    high: float
    low: float
    source: str

    @property
    def equilibrium(self) -> float:
        return (self.high + self.low) / 2


@dataclass(frozen=True)
class PDResult:
    range: DealingRange
    price: float
    position: Position

    @property
    def percent_of_range(self) -> float | None:
        span = self.range.high - self.range.low
        if span <= 0:
            return None
        return (self.price - self.range.low) / span * 100


def previous_day_range(completed_daily: list[Candle]) -> DealingRange | None:
    if not completed_daily:
        return None
    last = completed_daily[-1]
    return DealingRange(high=last.high, low=last.low, source="Previous completed FXCM daily candle")


RANGE_BUILDERS: dict[str, Callable[[list[Candle]], DealingRange | None]] = {
    "previous_day": previous_day_range,
}


def build_range(mode: str, completed_daily: list[Candle]) -> DealingRange | None:
    return RANGE_BUILDERS[mode](completed_daily)


def classify_position(price: float, dealing_range: DealingRange, tolerance: float = 1e-9) -> PDResult:
    """``tolerance`` absorbs float noise only; it is not a price band."""
    eq = dealing_range.equilibrium
    if abs(price - eq) <= tolerance:
        position = Position.EQUILIBRIUM
    elif price > eq:
        position = Position.PREMIUM
    else:
        position = Position.DISCOUNT
    return PDResult(range=dealing_range, price=price, position=position)
