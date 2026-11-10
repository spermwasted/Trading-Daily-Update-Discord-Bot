"""Build daily-analysis and session reports from market data (no Discord code here)."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from gold_bot.analysis import fvg as fvg_mod
from gold_bot.analysis.bias import BiasResult, confirmed_bias, developing_bias
from gold_bot.analysis.premium_discount import PDResult, build_range, classify_position
from gold_bot.analysis.sessions import (
    SessionOccurrence,
    SessionSchedule,
    SessionStats,
    SweepStatus,
    session_stats,
    sweep_status,
)
from gold_bot.config import Settings
from gold_bot.data.provider import DataUnavailable, MarketDataProvider
from gold_bot.models import Candle, Price, Timeframe
from gold_bot.storage import Storage
from gold_bot.timeutils import split_completed

log = logging.getLogger(__name__)


@dataclass
class DailyReport:
    generated_at: datetime
    source: str  # data provider name shown in the embeds, e.g. "OANDA"
    brand: str
    instrument: str
    digits: int
    trading_day_start: datetime  # open time of the newly opened daily candle
    daily_open: float | None  # None if the new candle is not in the feed yet
    previous_day: Candle
    bias: BiasResult | None
    developing: BiasResult | None
    premium_discount: PDResult | None
    current_price: float
    price_source: str
    fvgs: list[fvg_mod.FVG]
    fvg_timeframes: list[Timeframe]
    fvg_errors: dict[str, str]


@dataclass
class SessionReport:
    generated_at: datetime
    source: str
    brand: str
    instrument: str
    digits: int
    session: SessionOccurrence
    previous_session: SessionOccurrence | None
    opening_price: float | None
    current_price: float
    current_stats: SessionStats | None
    previous_stats: SessionStats | None
    previous_sweep: SweepStatus | None  # did the previous session sweep the one before it
    current_sweep: SweepStatus | None  # has the current session swept the previous one (so far)
    bias: BiasResult | None
    premium_discount: PDResult | None


class ReportBuilder:
    def __init__(self, settings: Settings, provider: MarketDataProvider, storage: Storage, schedule: SessionSchedule):
        self.settings = settings
        self.provider = provider
        self.storage = storage
        self.schedule = schedule

    # ------------------------------------------------------------------ #
    async def daily_context(self, now: datetime) -> tuple[list[Candle], Candle | None]:
        daily = await self.provider.get_candles(Timeframe.D1, count=10)
        return split_completed(Timeframe.D1, daily, now)

    async def current_price(self, fallback: Candle | None) -> tuple[Price | None, float, str]:
        try:
            price = await self.provider.get_price()
            return price, price.value, f"{self.settings.source_name} live bid"
        except DataUnavailable:
            if fallback is None:
                raise
            log.warning("Live price unavailable; using last candle close")
            return None, fallback.close, "last candle close (live quote unavailable)"

    async def active_fvgs(self, now: datetime, price: float) -> tuple[list[fvg_mod.FVG], dict[str, str]]:
        gaps: list[fvg_mod.FVG] = []
        errors: dict[str, str] = {}
        for tf in self.settings.fvg_timeframe_list:
            try:
                candles = await self.provider.get_candles(tf, count=self.settings.fvg_lookback(tf))
            except DataUnavailable as exc:
                errors[tf.label] = str(exc)
                continue
            completed, forming = split_completed(tf, candles, now)
            fresh = fvg_mod.analyse(tf, completed, forming)
            action = completed + ([forming] if forming else [])
            # Persisted gaps older than the fetched window keep being tracked.
            merged = self.storage.merge_fvgs(tf, fresh, action)
            gaps.extend(merged)
        return fvg_mod.active_sorted(gaps, price), errors

    # ------------------------------------------------------------------ #
    async def build_daily(self, now: datetime) -> DailyReport:
        completed, forming = await self.daily_context(now)
        if not completed:
            raise DataUnavailable("no completed daily candles available")
        price_obj, price, source = await self.current_price(forming or completed[-1])
        digits = price_obj.digits if price_obj else 2
        rng = build_range(self.settings.dealing_range_mode, completed)
        fvgs, errors = await self.active_fvgs(now, price)
        return DailyReport(
            generated_at=now,
            source=self.settings.source_name,
            brand=self.settings.report_brand,
            instrument=self.settings.display_symbol,
            digits=digits,
            trading_day_start=forming.time if forming else completed[-1].time,
            daily_open=forming.open if forming else None,
            previous_day=completed[-1],
            bias=confirmed_bias(completed),
            developing=developing_bias(completed, forming),
            premium_discount=classify_position(price, rng) if rng else None,
            current_price=price,
            price_source=source,
            fvgs=fvgs,
            fvg_timeframes=self.settings.fvg_timeframe_list,
            fvg_errors=errors,
        )

    async def build_session(self, occurrence: SessionOccurrence, now: datetime) -> SessionReport:
        previous = self.schedule.previous(occurrence)
        before_previous = self.schedule.previous(previous) if previous else None
        start = (before_previous or previous or occurrence).start
        m5 = await self.provider.get_candles(Timeframe.M5, start=start - timedelta(minutes=5), end=now)
        current_stats = session_stats(occurrence, m5)
        previous_stats = session_stats(previous, m5) if previous else None
        before_stats = session_stats(before_previous, m5) if before_previous else None

        completed, forming = await self.daily_context(now)
        price_obj, price, _ = await self.current_price(m5[-1] if m5 else None)
        rng = build_range(self.settings.dealing_range_mode, completed)
        return SessionReport(
            generated_at=now,
            source=self.settings.source_name,
            brand=self.settings.report_brand,
            instrument=self.settings.display_symbol,
            digits=price_obj.digits if price_obj else 2,
            session=occurrence,
            previous_session=previous,
            opening_price=current_stats.open if current_stats else None,
            current_price=price,
            current_stats=current_stats,
            previous_stats=previous_stats,
            previous_sweep=sweep_status(previous_stats, before_stats),
            current_sweep=sweep_status(current_stats, previous_stats),
            bias=confirmed_bias(completed),
            premium_discount=classify_position(price, rng) if rng else None,
        )
