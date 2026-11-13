"""Create the configured market-data provider (DATA_PROVIDER=oanda|fxcm)."""
from __future__ import annotations

from gold_bot.analysis.sessions import SessionSchedule
from gold_bot.config import Settings
from gold_bot.data.bridge_client import BridgeClient
from gold_bot.data.oanda import OandaProvider
from gold_bot.data.twelvedata import TwelveDataProvider, event_window


class ConfigurationError(Exception):
    pass


def validate_provider_settings(settings: Settings) -> None:
    """Raise ConfigurationError for missing credentials (opens no connections)."""
    required = {
        "twelvedata": ("TWELVEDATA_API_KEY", settings.twelvedata_api_key.get_secret_value()),
        "oanda": ("OANDA_API_TOKEN", settings.oanda_api_token.get_secret_value()),
    }.get(settings.data_provider)
    if required and not required[1]:
        raise ConfigurationError(f"{required[0]} is not set")


def make_provider(settings: Settings) -> TwelveDataProvider | OandaProvider | BridgeClient:
    if settings.data_provider == "twelvedata":
        key = settings.twelvedata_api_key.get_secret_value()
        if not key:
            raise ConfigurationError("TWELVEDATA_API_KEY is not set")
        schedule = SessionSchedule(settings.session_times(), settings.session_zone)
        return TwelveDataProvider(
            api_key=key,
            symbol=settings.twelvedata_symbol,
            digits=settings.twelvedata_digits,
            refresh_seconds=settings.twelvedata_refresh_seconds,
            fast_refresh_seconds=settings.twelvedata_fast_refresh_seconds,
            fast_window=event_window(schedule, settings.twelvedata_fast_window_minutes),
            timeout=settings.bridge_timeout_seconds,
            retries=settings.bridge_retries,
        )
    if settings.data_provider == "oanda":
        token = settings.oanda_api_token.get_secret_value()
        if not token:
            raise ConfigurationError("OANDA_API_TOKEN is not set")
        return OandaProvider(
            token=token,
            account_id=settings.oanda_account_id,
            environment=settings.oanda_environment,
            instrument=settings.oanda_instrument,
            smooth=settings.oanda_smooth,
            timeout=settings.bridge_timeout_seconds,
            retries=settings.bridge_retries,
        )
    return BridgeClient(settings.fxcm_bridge_url, settings.bridge_timeout_seconds, settings.bridge_retries)
