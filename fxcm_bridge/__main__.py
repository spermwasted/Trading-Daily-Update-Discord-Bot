"""Entry point: python -m fxcm_bridge"""
import logging
import os
import signal
import sys
import threading

from fxcm_bridge.config import BridgeConfig, ConfigError
from fxcm_bridge.feed import ForexConnectFeed
from fxcm_bridge.server import serve


def main():
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    try:
        config = BridgeConfig().validate()
    except ConfigError as exc:
        logging.error("Configuration error: %s", exc)
        return 2

    feed = ForexConnectFeed(config)
    feed.start()
    httpd = serve(feed, config.http_host, config.http_port)

    stopping = threading.Event()

    def shutdown(signum, frame):
        if stopping.is_set():
            return
        stopping.set()
        logging.info("Shutting down (signal %s)", signum)
        threading.Thread(target=httpd.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)
    try:
        httpd.serve_forever()
    finally:
        feed.stop()
        httpd.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
