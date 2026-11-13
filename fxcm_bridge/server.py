"""Read-only JSON HTTP API in front of the ForexConnect feed.

Endpoints (GET only, no trading endpoints exist):
  /health                              session status, reconnect count, last error
  /price                               latest live price for the configured instrument
  /candles?timeframe=H1&count=300      OHLC history (oldest -> newest)
  /candles?timeframe=m5&from=ISO[&to=ISO]

Python 3.7 compatible; uses only the standard library.
"""
import datetime
import json
import logging
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from fxcm_bridge.feed import BadRequestError, FeedError, NotConnectedError

log = logging.getLogger("fxcm_bridge.server")


def parse_iso_utc(value):
    text = value.strip().replace("Z", "+00:00")
    try:
        parsed = datetime.datetime.fromisoformat(text)
    except ValueError:
        raise BadRequestError("invalid ISO datetime: {!r}".format(value))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=datetime.timezone.utc)
    # ForexConnect expects naive-or-aware UTC datetimes; pass aware UTC.
    return parsed.astimezone(datetime.timezone.utc)


def make_handler(feed):
    class Handler(BaseHTTPRequestHandler):
        server_version = "fxcm-bridge/1.0"

        def log_message(self, fmt, *args):  # route to logging instead of stderr
            log.debug("%s - %s", self.address_string(), fmt % args)

        def _send(self, status, payload):
            body = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):  # noqa: N802 - stdlib naming
            url = urlparse(self.path)
            query = {k: v[-1] for k, v in parse_qs(url.query).items()}
            try:
                if url.path == "/health":
                    health = feed.health()
                    self._send(200 if health["connected"] else 503, health)
                elif url.path == "/price":
                    self._send(200, feed.latest_price())
                elif url.path == "/candles":
                    self._send(200, self._candles(query))
                else:
                    self._send(404, {"error": "not found"})
            except FeedError as exc:
                self._send(exc.http_status, {"error": str(exc), "type": type(exc).__name__})
            except Exception as exc:  # noqa: B902
                log.exception("unhandled error for %s", self.path)
                self._send(500, {"error": "internal error: {}".format(exc)})

        def _candles(self, query):
            timeframe = query.get("timeframe")
            if not timeframe:
                raise BadRequestError("timeframe is required")
            count = None
            if "count" in query:
                try:
                    count = int(query["count"])
                except ValueError:
                    raise BadRequestError("count must be an integer")
            date_from = parse_iso_utc(query["from"]) if "from" in query else None
            date_to = parse_iso_utc(query["to"]) if "to" in query else None
            candles = feed.get_candles(timeframe, count=count, date_from=date_from, date_to=date_to)
            health = feed.health()
            return {
                "instrument": health["instrument"],
                "timeframe": timeframe,
                "price_side": health["price_side"],
                "candle_open_mode": health["candle_open_mode"],
                "candles": candles,
            }

        def _reject(self):
            self._send(405, {"error": "read-only API"})

        do_POST = do_PUT = do_DELETE = do_PATCH = _reject  # noqa: N815

    return Handler


def serve(feed, host, port):
    httpd = ThreadingHTTPServer((host, port), make_handler(feed))
    httpd.daemon_threads = True
    log.info("FXCM bridge listening on http://%s:%s", host, port)
    return httpd


__all__ = ["serve", "make_handler", "parse_iso_utc", "NotConnectedError"]
