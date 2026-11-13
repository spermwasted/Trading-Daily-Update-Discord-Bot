"""Configuration for the FXCM ForexConnect bridge.

Everything comes from environment variables. Credentials are never hardcoded.
This module must stay compatible with Python 3.7 (ForexConnect's newest Linux
build only supports CPython 3.5-3.7).
"""
import os
from urllib.parse import urlparse

SUPPORTED_TIMEFRAMES = ("m1", "m5", "m15", "m30", "H1", "H4", "D1", "W1")


class ConfigError(Exception):
    pass


def _get(name, default=None):
    value = os.environ.get(name)
    if value is None or value.strip() == "":
        return default
    return value.strip()


def _get_int(name, default):
    raw = _get(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ConfigError("{} must be an integer, got {!r}".format(name, raw))


class BridgeConfig(object):
    def __init__(self):
        self.username = _get("FXCM_USERNAME")
        self.password = _get("FXCM_PASSWORD")
        # ForexConnect logs in against a host descriptor URL plus a connection
        # name ("Demo" or "Real"). It does NOT use the REST API token.
        self.url = _get("FXCM_URL", "https://www.fxcorporate.com/Hosts.jsp")
        self.connection = _normalise_connection(_get("FXCM_CONNECTION", _get("FXCM_ENV", "Demo")))
        self.session_id = _get("FXCM_SESSION_ID")  # only for multi-database accounts
        self.pin = _get("FXCM_PIN")  # only for accounts with a PIN
        self.instrument = _get("FXCM_SYMBOL", "XAU/USD")
        self.price_side = (_get("FXCM_PRICE_SIDE", "bid") or "bid").lower()
        self.candle_open_mode = (_get("FXCM_CANDLE_OPEN_MODE", "previous_close") or "").lower()

        # HTTPS trust store for ForexConnect on Linux (required for https URLs).
        self.ca_file = _get("FXCM_CA_FILE") or _default_ca_file()
        # Optional outbound proxy (FXCM_PROXY_URL, else HTTPS_PROXY if present).
        self.proxy_host, self.proxy_port = _parse_proxy(_get("FXCM_PROXY_URL") or _get("HTTPS_PROXY"))

        self.history_dir = _get("FXCM_HISTORY_CACHE_DIR", "/tmp/fxcm-history")
        self.http_host = _get("BRIDGE_HOST", "0.0.0.0")
        self.http_port = _get_int("BRIDGE_PORT", 8765)

        # Reconnection behaviour.
        self.sdk_reconnect_attempts = _get_int("FXCM_SDK_RECONNECT_ATTEMPTS", 5)
        self.reconnect_backoff_initial = _get_int("FXCM_RECONNECT_BACKOFF_INITIAL", 5)
        self.reconnect_backoff_max = _get_int("FXCM_RECONNECT_BACKOFF_MAX", 300)
        # Force a fresh login if no price update arrives for this long while the
        # market should be open (0 disables the watchdog).
        self.stale_price_seconds = _get_int("FXCM_STALE_PRICE_SECONDS", 600)
        self.history_ready_timeout = _get_int("FXCM_HISTORY_READY_TIMEOUT", 60)
        self.max_quotes_per_request = _get_int("FXCM_MAX_QUOTES", 5000)

    def validate(self):
        missing = [n for n, v in (("FXCM_USERNAME", self.username), ("FXCM_PASSWORD", self.password)) if not v]
        if missing:
            raise ConfigError("Missing required environment variable(s): " + ", ".join(missing))
        if self.price_side not in ("bid", "ask"):
            raise ConfigError("FXCM_PRICE_SIDE must be 'bid' or 'ask'")
        if self.candle_open_mode not in ("previous_close", "first_tick"):
            raise ConfigError("FXCM_CANDLE_OPEN_MODE must be 'previous_close' or 'first_tick'")
        return self

    def describe(self):
        """Safe summary for logs (never includes the password)."""
        return {
            "username": _mask(self.username),
            "url": self.url,
            "connection": self.connection,
            "instrument": self.instrument,
            "price_side": self.price_side,
            "candle_open_mode": self.candle_open_mode,
            "ca_file": self.ca_file,
            "proxy": "{}:{}".format(self.proxy_host, self.proxy_port) if self.proxy_host else None,
        }


def _normalise_connection(value):
    """FXCM connection names are "Demo" and "Real"; accept any letter case."""
    return {"demo": "Demo", "real": "Real"}.get(value.lower(), value)


def _mask(value):
    if not value:
        return None
    return value[:2] + "***" if len(value) > 3 else "***"


def _default_ca_file():
    try:
        import certifi  # noqa: WPS433

        return certifi.where()
    except Exception:  # pragma: no cover - certifi missing
        for path in ("/etc/ssl/certs/ca-certificates.crt", "/etc/pki/tls/certs/ca-bundle.crt"):
            if os.path.exists(path):
                return path
    return None


def _parse_proxy(url):
    if not url:
        return None, None
    parsed = urlparse(url if "://" in url else "http://" + url)
    if not parsed.hostname:
        return None, None
    return parsed.hostname, parsed.port or 8080
