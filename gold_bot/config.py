"""Bot configuration from environment variables (and an optional .env file)."""
from __future__ import annotations

from datetime import time
from functools import cached_property
from zoneinfo import ZoneInfo

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from gold_bot.models import Timeframe


def _parse_hhmm(value: str) -> time:
    hours, minutes = value.strip().split(":")
    return time(int(hours), int(minutes))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore", env_ignore_empty=True)

    # Discord
    discord_bot_token: SecretStr = Field(default=SecretStr(""))
    discord_daily_channel_id: int = 0
    discord_session_channel_id: int = 0  # 0 = same as the daily channel
    discord_guild_id: int = 0  # optional: instant slash-command sync to one server

    # Market data: "twelvedata" (default), "oanda" (OANDA v20 REST) or "fxcm" (ForexConnect bridge)
    data_provider: str = "twelvedata"
    twelvedata_api_key: SecretStr = Field(default=SecretStr(""))
    twelvedata_symbol: str = "XAU/USD"
    twelvedata_digits: int = 0  # 0 = detect the quote precision from the data
    twelvedata_refresh_seconds: int = 150  # normal refresh (free plan: 800 credits/day)
    twelvedata_fast_refresh_seconds: int = 20  # right after session opens / daily open
    twelvedata_fast_window_minutes: int = 4
    oanda_api_token: SecretStr = Field(default=SecretStr(""))
    oanda_account_id: str = ""  # optional: first account of the token is used
    oanda_environment: str = "practice"  # practice (demo) or live
    oanda_instrument: str = "XAU_USD"
    oanda_smooth: bool = False  # False: candle open = first tick (OANDA default)
    fxcm_bridge_url: str = "http://fxcm-bridge:8765"
    fxcm_symbol: str = "XAU/USD"
    report_brand: str = "GOLD FXCM"  # embed title prefix, e.g. "GOLD FXCM | LONDON SESSION OPEN"
    bridge_timeout_seconds: float = 30.0
    bridge_retries: int = 3

    # Time zones and sessions (session times are New York wall-clock times, so
    # they follow New York daylight-saving changes; display is in DISPLAY_TZ).
    display_tz: str = "Asia/Karachi"
    session_tz: str = "America/New_York"
    session_asia_open: str = "19:00"
    session_london_open: str = "03:00"
    session_newyork_open: str = "08:00"
    session_newyork_close: str = "17:00"

    # Analysis
    dealing_range_mode: str = "previous_day"
    fvg_timeframes: str = "D1,H4,H1"
    fvg_lookback_d1: int = 250
    fvg_lookback_h4: int = 500
    fvg_lookback_h1: int = 500
    fvg_max_per_side: int = 6

    # Scheduling / reliability
    poll_interval_seconds: int = 20
    daily_report_grace_hours: int = 8
    session_report_grace_minutes: int = 60
    database_path: str = "data/gold_bot.sqlite3"
    log_level: str = "INFO"

    @field_validator("dealing_range_mode")
    @classmethod
    def _check_range_mode(cls, value: str) -> str:
        from gold_bot.analysis.premium_discount import RANGE_BUILDERS

        if value not in RANGE_BUILDERS:
            raise ValueError(f"DEALING_RANGE_MODE must be one of {sorted(RANGE_BUILDERS)}")
        return value

    @field_validator("data_provider")
    @classmethod
    def _check_provider(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in ("twelvedata", "oanda", "fxcm"):
            raise ValueError("DATA_PROVIDER must be 'twelvedata', 'oanda' or 'fxcm'")
        return value

    @field_validator("session_asia_open", "session_london_open", "session_newyork_open", "session_newyork_close")
    @classmethod
    def _check_hhmm(cls, value: str) -> str:
        _parse_hhmm(value)
        return value

    @cached_property
    def display_zone(self) -> ZoneInfo:
        return ZoneInfo(self.display_tz)

    @cached_property
    def session_zone(self) -> ZoneInfo:
        return ZoneInfo(self.session_tz)

    @property
    def display_symbol(self) -> str:
        return {
            "twelvedata": self.twelvedata_symbol,
            "oanda": self.oanda_instrument.replace("_", "/"),
        }.get(self.data_provider, self.fxcm_symbol)

    @property
    def source_name(self) -> str:
        return {"twelvedata": "Twelve Data", "oanda": "OANDA"}.get(self.data_provider, "FXCM")

    @property
    def session_channel(self) -> int:
        return self.discord_session_channel_id or self.discord_daily_channel_id

    @property
    def fvg_timeframe_list(self) -> list[Timeframe]:
        return [Timeframe(tf.strip()) for tf in self.fvg_timeframes.split(",") if tf.strip()]

    def fvg_lookback(self, tf: Timeframe) -> int:
        return {
            Timeframe.D1: self.fvg_lookback_d1,
            Timeframe.H4: self.fvg_lookback_h4,
            Timeframe.H1: self.fvg_lookback_h1,
        }.get(tf, 500)

    def session_times(self) -> dict[str, time]:
        return {
            "asia": _parse_hhmm(self.session_asia_open),
            "london": _parse_hhmm(self.session_london_open),
            "newyork": _parse_hhmm(self.session_newyork_open),
            "newyork_close": _parse_hhmm(self.session_newyork_close),
        }
