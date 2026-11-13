from datetime import timedelta

from gold_bot.analysis import fvg
from gold_bot.analysis.bias import Direction, Signal, classify, confirmed_bias, developing_bias
from gold_bot.analysis.premium_discount import (
    DealingRange,
    Position,
    build_range,
    classify_position,
)
from gold_bot.models import Timeframe
from tests.helpers import c, dt, series

D0 = dt("2026-11-02T22:00:00Z")
PREV = c(D0, 4100, 4120, 4090, 4110)  # previous day: H 4120, L 4090


def day(o, h, low, cl):
    return c(D0 + timedelta(days=1), o, h, low, cl)


# ----------------------------- daily bias ---------------------------------- #
def test_bullish_breakout_requires_close_above_previous_high():
    r = classify(day(4110, 4135, 4105, 4125), PREV, confirmed=True)
    assert (r.direction, r.signal, r.confirmed) == (Direction.BULLISH, Signal.BULLISH_BREAKOUT, True)


def test_bearish_breakout_requires_close_below_previous_low():
    r = classify(day(4100, 4105, 4070, 4080), PREV, confirmed=True)
    assert (r.direction, r.signal) == (Direction.BEARISH, Signal.BEARISH_BREAKOUT)


def test_wick_above_high_closing_back_below_is_buy_side_sweep_bearish():
    r = classify(day(4110, 4130, 4100, 4115), PREV, confirmed=True)
    assert (r.direction, r.signal) == (Direction.BEARISH, Signal.BUY_SIDE_SWEEP)


def test_wick_below_low_closing_back_above_is_sell_side_sweep_bullish():
    r = classify(day(4100, 4112, 4080, 4095), PREV, confirmed=True)
    assert (r.direction, r.signal) == (Direction.BULLISH, Signal.SELL_SIDE_SWEEP)


def test_close_exactly_at_previous_high_is_not_a_breakout():
    r = classify(day(4110, 4125, 4100, 4120), PREV, confirmed=True)
    assert r.signal is Signal.BUY_SIDE_SWEEP  # wick above, no close above


def test_inside_day_is_neutral():
    r = classify(day(4100, 4115, 4095, 4105), PREV, confirmed=True)
    assert (r.direction, r.signal) == (Direction.NEUTRAL, Signal.INSIDE)


def test_outside_day_closing_inside_is_neutral_both_sides():
    r = classify(day(4100, 4130, 4080, 4105), PREV, confirmed=True)
    assert (r.direction, r.signal) == (Direction.NEUTRAL, Signal.BOTH_SIDES_SWEPT)


def test_breakout_close_wins_over_opposite_sweep():
    r = classify(day(4100, 4140, 4080, 4130), PREV, confirmed=True)
    assert r.signal is Signal.BULLISH_BREAKOUT


def test_confirmed_bias_uses_last_two_completed_and_developing_is_unconfirmed():
    completed = [PREV, day(4110, 4135, 4105, 4125)]
    forming = c(D0 + timedelta(days=2), 4125, 4126, 4100, 4101)
    assert confirmed_bias(completed).confirmed is True
    assert confirmed_bias(completed).candle == completed[-1]
    dev = developing_bias(completed, forming)
    assert dev.confirmed is False and dev.reference == completed[-1]
    assert confirmed_bias([PREV]) is None


# --------------------------- premium / discount ----------------------------- #
def test_premium_discount_equilibrium():
    rng = build_range("previous_day", [PREV])
    assert (rng.high, rng.low, rng.equilibrium) == (4120, 4090, 4105)
    assert classify_position(4110.01, rng).position is Position.PREMIUM
    assert classify_position(4104.99, rng).position is Position.DISCOUNT
    assert classify_position(4105.0, rng).position is Position.EQUILIBRIUM
    assert classify_position(4105.0, rng).percent_of_range == 50


def test_equilibrium_with_half_point():
    rng = DealingRange(4120.11, 4090.00, "test")
    assert rng.equilibrium == (4120.11 + 4090.00) / 2
    assert classify_position(4105.06, rng).position is Position.PREMIUM
    assert classify_position(4105.05, rng).position is Position.DISCOUNT


# ------------------------------- FVGs --------------------------------------- #
H1 = timedelta(hours=1)
T0 = dt("2026-11-09T10:00:00Z")


def test_bullish_fvg_detected_with_exact_boundaries():
    candles = series(T0, H1, [(4110, 4119, 4105, 4117), (4117, 4135, 4116, 4133), (4133, 4140, 4129, 4138)])
    gaps = fvg.detect(Timeframe.H1, candles)
    assert len(gaps) == 1
    g = gaps[0]
    assert (g.direction, g.lower, g.upper) == (fvg.FVGDirection.BULLISH, 4119, 4129)
    assert g.status is fvg.FVGStatus.UNFILLED and g.timeframe is Timeframe.H1


def test_bearish_fvg_detected_with_exact_boundaries():
    candles = series(T0, H1, [(4140, 4142, 4130, 4131), (4131, 4132, 4110, 4112), (4112, 4120, 4105, 4108)])
    g = fvg.detect(Timeframe.H4, candles)[0]
    assert (g.direction, g.lower, g.upper) == (fvg.FVGDirection.BEARISH, 4120, 4130)


def test_touching_wicks_are_not_a_gap():
    candles = series(T0, H1, [(4110, 4119, 4105, 4117), (4117, 4135, 4116, 4133), (4133, 4140, 4119, 4138)])
    assert fvg.detect(Timeframe.H1, candles) == []


def _bullish_with_followups(followups):
    base = [(4110, 4119, 4105, 4117), (4117, 4135, 4116, 4133), (4133, 4140, 4129, 4138)]
    candles = series(T0, H1, base + followups)
    gaps = fvg.analyse(Timeframe.H1, candles, None)
    return [g for g in gaps if g.candle1_time == T0][0]


def test_unfilled_partial_and_full_mitigation():
    untouched = _bullish_with_followups([(4138, 4145, 4131, 4140)])
    assert untouched.status is fvg.FVGStatus.UNFILLED

    tapped = _bullish_with_followups([(4138, 4145, 4129, 4140)])
    assert tapped.status is fvg.FVGStatus.PARTIALLY_FILLED and tapped.fill_percent == 0

    partial = _bullish_with_followups([(4138, 4145, 4124, 4140)])
    assert partial.status is fvg.FVGStatus.PARTIALLY_FILLED and partial.fill_percent == 50

    filled = _bullish_with_followups([(4138, 4145, 4119, 4140)])
    assert filled.status is fvg.FVGStatus.FILLED and not filled.is_active
    assert fvg.active_sorted([filled, partial], 4140) == [partial]


def test_bearish_mitigation_uses_highs():
    base = [(4140, 4142, 4130, 4131), (4131, 4132, 4110, 4112), (4112, 4120, 4105, 4108)]
    partial = fvg.analyse(Timeframe.H1, series(T0, H1, base + [(4108, 4126, 4100, 4110)]), None)[0]
    assert partial.status is fvg.FVGStatus.PARTIALLY_FILLED and partial.fill_percent == 60
    full = fvg.analyse(Timeframe.H1, series(T0, H1, base + [(4108, 4131, 4100, 4110)]), None)[0]
    assert full.status is fvg.FVGStatus.FILLED


def test_forming_candle_counts_toward_mitigation():
    base = series(T0, H1, [(4110, 4119, 4105, 4117), (4117, 4135, 4116, 4133), (4133, 4140, 4129, 4138)])
    forming = c(T0 + 3 * H1, 4138, 4139, 4118, 4120)
    assert fvg.analyse(Timeframe.H1, base, forming)[0].status is fvg.FVGStatus.FILLED


def test_distance_and_sorting():
    a = fvg.FVG(Timeframe.H1, fvg.FVGDirection.BULLISH, 4119, 4129, T0, T0 + 2 * H1)
    b = fvg.FVG(Timeframe.H4, fvg.FVGDirection.BEARISH, 4150, 4160, T0, T0 + 2 * H1)
    inside = fvg.FVG(Timeframe.D1, fvg.FVGDirection.BULLISH, 4135, 4145, T0, T0 + 2 * H1)
    assert a.distance(4140) == 11 and b.distance(4140) == 10 and inside.distance(4140) == 0
    assert fvg.active_sorted([a, b, inside], 4140) == [inside, b, a]
