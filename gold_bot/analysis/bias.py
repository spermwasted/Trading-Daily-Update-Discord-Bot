"""Daily directional bias from a daily candle vs the previous day's high/low.

Rules (strict comparisons, closes only - a wick never confirms a breakout):
  close >  previous high                      -> BULLISH breakout (confirmed)
  close <  previous low                       -> BEARISH breakout (confirmed)
  high  >  previous high, close <= prev high   -> buy-side liquidity sweep -> BEARISH
  low   <  previous low,  close >= prev low    -> sell-side liquidity sweep -> BULLISH
  both sides swept, close back inside          -> both sides swept -> NEUTRAL
  otherwise (inside day)                       -> no confirmed bias -> NEUTRAL

``confirmed`` is True only when the evaluated candle is a COMPLETED FXCM daily
candle. Applying the same rules to the forming candle gives an intraday
"developing" indication that must never be announced as a confirmed close.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from gold_bot.models import Candle


class Direction(str, Enum):
    BULLISH = "BULLISH"
    BEARISH = "BEARISH"
    NEUTRAL = "NEUTRAL"


class Signal(str, Enum):
    BULLISH_BREAKOUT = "Bullish breakout (close above previous day high)"
    BEARISH_BREAKOUT = "Bearish breakout (close below previous day low)"
    BUY_SIDE_SWEEP = "Buy-side liquidity sweep (wick above previous day high, close back below)"
    SELL_SIDE_SWEEP = "Sell-side liquidity sweep (wick below previous day low, close back above)"
    BOTH_SIDES_SWEPT = "Both sides swept (outside day, closed back inside the range)"
    INSIDE = "No break of previous day high or low"

    @property
    def short(self) -> str:
        return {
            Signal.BULLISH_BREAKOUT: "Breakout",
            Signal.BEARISH_BREAKOUT: "Breakout",
            Signal.BUY_SIDE_SWEEP: "Liquidity Sweep (Buy-side)",
            Signal.SELL_SIDE_SWEEP: "Liquidity Sweep (Sell-side)",
            Signal.BOTH_SIDES_SWEPT: "Both Sides Swept",
            Signal.INSIDE: "Unconfirmed",
        }[self]


@dataclass(frozen=True)
class BiasResult:
    direction: Direction
    signal: Signal
    candle: Candle
    reference: Candle
    confirmed: bool

    @property
    def reference_high(self) -> float:
        return self.reference.high

    @property
    def reference_low(self) -> float:
        return self.reference.low


def classify(candle: Candle, previous: Candle, confirmed: bool) -> BiasResult:
    pdh, pdl = previous.high, previous.low
    if candle.close > pdh:
        direction, signal = Direction.BULLISH, Signal.BULLISH_BREAKOUT
    elif candle.close < pdl:
        direction, signal = Direction.BEARISH, Signal.BEARISH_BREAKOUT
    else:
        swept_high = candle.high > pdh
        swept_low = candle.low < pdl
        if swept_high and swept_low:
            direction, signal = Direction.NEUTRAL, Signal.BOTH_SIDES_SWEPT
        elif swept_high:
            direction, signal = Direction.BEARISH, Signal.BUY_SIDE_SWEEP
        elif swept_low:
            direction, signal = Direction.BULLISH, Signal.SELL_SIDE_SWEEP
        else:
            direction, signal = Direction.NEUTRAL, Signal.INSIDE
    return BiasResult(direction, signal, candle, previous, confirmed)


def confirmed_bias(completed_daily: list[Candle]) -> BiasResult | None:
    """Bias from the last two COMPLETED daily candles (None if not enough data)."""
    if len(completed_daily) < 2:
        return None
    return classify(completed_daily[-1], completed_daily[-2], confirmed=True)


def developing_bias(completed_daily: list[Candle], forming: Candle | None) -> BiasResult | None:
    """Intraday indication: the forming daily candle vs the last completed one."""
    if forming is None or not completed_daily:
        return None
    return classify(forming, completed_daily[-1], confirmed=False)
