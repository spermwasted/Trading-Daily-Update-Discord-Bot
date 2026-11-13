"""Market-data provider interface used by the analysis/service layers.

The analysis engine depends only on this interface, never on ForexConnect.
"""
from __future__ import annotations

import logging
from dataclasses import replace
from datetime import datetime
from typing import Protocol

from gold_bot.models import Candle, Price, Timeframe

log = logging.getLogger(__name__)


class DataUnavailable(Exception):
    """Raised when real market data cannot be obtained. Never substitute values."""


def normalize_ohlc(candle: Candle, source: str) -> Candle:
    """Enforce low <= open/close <= high using the candle's own prices.

    A data source occasionally reports a bar whose high/low does not include its
    own open or close. Rejecting the whole download would stop the bot for as long
    as that bar stays in the history window, so the range is widened to the bar's
    own open/close (the minimum correction its own prices prove) and logged.
    """
    high = max(candle.high, candle.open, candle.close)
    low = min(candle.low, candle.open, candle.close)
    if high == candle.high and low == candle.low:
        return candle
    log.warning("%s bar %s had inconsistent OHLC %s; range widened to its own open/close",
                source, candle.time.isoformat(), (candle.open, candle.high, candle.low, candle.close))
    return replace(candle, high=high, low=low)


class MarketDataProvider(Protocol):
    async def get_candles(
        self,
        timeframe: Timeframe,
        count: int | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[Candle]: ...

    async def get_price(self) -> Price: ...

    async def health(self) -> dict: ...
