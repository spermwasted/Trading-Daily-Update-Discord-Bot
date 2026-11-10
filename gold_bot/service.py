"""Automated reporting: daily-candle report and session-open reports.

Two independent jobs run on every poll:

* Daily report - fires when FXCM's feed shows a NEW daily candle (the newest D1
  candle is still forming and no report exists for its trading date). Confirmed
  bias uses only completed daily candles.
* Session reports - fire once when the Asian / London / New York session opens
  and the first FXCM m5 candle of the session exists (so the opening price is a
  real FXCM price).

Each report key is claimed atomically in SQLite before sending, so a report is
sent at most once even across restarts. A failed send releases the claim so the
next poll retries (within the grace window).
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Callable, Protocol

import discord

from gold_bot.analysis.sessions import SessionSchedule, session_stats, sweep_status
from gold_bot.config import Settings
from gold_bot.data.provider import DataUnavailable, MarketDataProvider
from gold_bot.embeds import daily_embed, session_embed
from gold_bot.models import Timeframe
from gold_bot.reports import ReportBuilder
from gold_bot.storage import Storage
from gold_bot.timeutils import split_completed, trading_date

log = logging.getLogger(__name__)
UTC = timezone.utc


class Publisher(Protocol):
    async def send(self, channel_id: int, embed: discord.Embed) -> str: ...


def utc_now() -> datetime:
    return datetime.now(UTC)


class ReportService:
    def __init__(
        self,
        settings: Settings,
        provider: MarketDataProvider,
        storage: Storage,
        publisher: Publisher,
        schedule: SessionSchedule,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        self.settings = settings
        self.provider = provider
        self.storage = storage
        self.publisher = publisher
        self.schedule = schedule
        self.clock = clock
        self.builder = ReportBuilder(settings, provider, storage, schedule)
        self._last_tracked: datetime | None = None
        self._stopped = asyncio.Event()

    # ------------------------------------------------------------------ #
    async def run_forever(self) -> None:
        log.info("Report service started (poll every %ss)", self.settings.poll_interval_seconds)
        while not self._stopped.is_set():
            await self.tick()
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=self.settings.poll_interval_seconds)
            except asyncio.TimeoutError:
                pass

    def stop(self) -> None:
        self._stopped.set()

    async def tick(self) -> None:
        now = self.clock()
        # The jobs are isolated: a failure in one never blocks the other.
        for job in (self.check_daily, self.check_session, self.track_session):
            try:
                await job(now)
            except DataUnavailable as exc:
                log.warning("%s skipped this cycle - FXCM data unavailable: %s", job.__name__, exc)
            except Exception:  # noqa: BLE001
                log.exception("%s failed", job.__name__)

    # ------------------------------------------------------------------ #
    async def check_daily(self, now: datetime) -> str | None:
        daily = await self.provider.get_candles(Timeframe.D1, count=3)
        _, forming = split_completed(Timeframe.D1, daily, now)
        if forming is None:
            return None  # no new FXCM daily candle has opened (e.g. weekend)
        key = f"daily:{trading_date(forming.time).isoformat()}"
        if self.storage.report_status(key):
            return None
        if now - forming.time > timedelta(hours=self.settings.daily_report_grace_hours):
            self.storage.claim_report(key, "daily")
            self.storage.finish_report(key, "skipped", "bot was not running when the daily candle opened")
            log.warning("Daily report %s skipped: candle opened at %s, outside grace window", key, forming.time)
            return None
        if not self.storage.claim_report(key, "daily"):
            return None
        try:
            report = await self.builder.build_daily(now)
            embed = daily_embed(report, self.settings.display_zone, self.settings.fvg_max_per_side)
            message_id = await self.publisher.send(self.settings.discord_daily_channel_id, embed)
        except BaseException:
            self.storage.release_report(key)
            raise
        self.storage.finish_report(key, "sent", message_id)
        log.info("Daily report %s sent (message %s)", key, message_id)
        return key

    async def check_session(self, now: datetime) -> str | None:
        occurrence = self.schedule.current(now)
        if occurrence is None:
            return None
        key = f"session:{occurrence.id}"
        if self.storage.report_status(key):
            return None
        if now - occurrence.start > timedelta(minutes=self.settings.session_report_grace_minutes):
            self.storage.claim_report(key, "session")
            self.storage.finish_report(key, "skipped", "outside grace window")
            log.warning("Session report %s skipped: outside grace window", key)
            return None
        report = await self.builder.build_session(occurrence, now)
        if report.opening_price is None:
            log.info("%s session open: waiting for the first FXCM tick", occurrence.name)
            return None
        if not self.storage.claim_report(key, "session"):
            return None
        try:
            embed = session_embed(report, self.settings.display_zone)
            message_id = await self.publisher.send(self.settings.session_channel, embed)
        except BaseException:
            self.storage.release_report(key)
            raise
        self.storage.finish_report(key, "sent", message_id)
        log.info("Session report %s sent (message %s)", key, message_id)
        return key

    async def track_session(self, now: datetime) -> dict | None:
        """Keep session open/high/low/sweep state current (about once a minute)."""
        if self._last_tracked and now - self._last_tracked < timedelta(seconds=60):
            return None
        occurrence = self.schedule.current(now)
        if occurrence is None:
            return None
        self._last_tracked = now
        previous = self.schedule.previous(occurrence)
        start = (previous or occurrence).start
        m5 = await self.provider.get_candles(Timeframe.M5, start=start - timedelta(minutes=5), end=now)
        current = session_stats(occurrence, m5)
        if current is None:
            return None
        prev_stats = session_stats(previous, m5) if previous else None
        sweep = sweep_status(current, prev_stats)
        state = {
            "session": occurrence.key,
            "start": occurrence.start.isoformat(),
            "open": current.open,
            "high": current.high,
            "low": current.low,
            "last": current.last,
            "previous_high": prev_stats.high if prev_stats else None,
            "previous_low": prev_stats.low if prev_stats else None,
            "swept_previous_high": sweep.swept_high if sweep else None,
            "swept_previous_low": sweep.swept_low if sweep else None,
            "updated_at": now.isoformat(),
        }
        old = self.storage.load_session_state(occurrence.id) or {}
        for side in ("high", "low"):
            if state[f"swept_previous_{side}"] and not old.get(f"swept_previous_{side}"):
                log.info("%s session swept the previous session %s", occurrence.name, side)
        self.storage.save_session_state(occurrence.id, state)
        return state
