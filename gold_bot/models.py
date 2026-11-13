"""Core data types shared by the data layer, analysis engine and Discord layer."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import Enum


class Timeframe(str, Enum):
    """FXCM ForexConnect timeframe identifiers."""

    M5 = "m5"
    M15 = "m15"
    H1 = "H1"
    H4 = "H4"
    D1 = "D1"

    @property
    def label(self) -> str:
        return {"m5": "5M", "m15": "15M", "H1": "1H", "H4": "4H", "D1": "Daily"}[self.value]


@dataclass(frozen=True)
class Candle:
    """One FXCM OHLC candle. ``time`` is the candle open time in aware UTC."""

    time: datetime
    open: float
    high: float
    low: float
    close: float
    volume: int = 0


@dataclass(frozen=True)
class Price:
    """Latest live FXCM quote."""

    instrument: str
    bid: float
    ask: float
    time: datetime | None
    digits: int
    point_size: float

    @property
    def value(self) -> float:
        """Price used for analysis (bid, matching FXCM charts)."""
        return self.bid
