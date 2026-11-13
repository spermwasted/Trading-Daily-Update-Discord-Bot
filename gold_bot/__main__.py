"""Entry point.

    python -m gold_bot                 run the Discord bot: automated reports + /gold commands
    python -m gold_bot --no-gateway    automated reports only, posted via Discord's REST API
                                       (no gateway connection, no slash commands)
    python -m gold_bot --dry-run       build both reports from live market data and
                                       print the embeds as JSON (no Discord)
    python -m gold_bot --send-now      build both reports and post them once to the
                                       configured channel(s), then exit
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys

from gold_bot.analysis.sessions import SessionSchedule
from gold_bot.config import Settings
from gold_bot.data.factory import ConfigurationError, make_provider, validate_provider_settings
from gold_bot.data.provider import DataUnavailable
from gold_bot.storage import Storage


def _setup_logging(level: str) -> None:
    logging.basicConfig(level=level.upper(), format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("discord").setLevel(logging.INFO)
    # httpx logs every request URL at INFO; keep logs readable.
    logging.getLogger("httpx").setLevel(logging.WARNING)


async def _dry_run(settings: Settings, send: bool = False) -> int:
    from gold_bot.embeds import developing_embed, session_embed
    from gold_bot.reports import ReportBuilder
    from gold_bot.service import utc_now

    provider = make_provider(settings)
    storage = Storage(":memory:")
    schedule = SessionSchedule(settings.session_times(), settings.session_zone)
    builder = ReportBuilder(settings, provider, storage, schedule)
    now = utc_now()
    try:
        print("Data source health:", json.dumps(await provider.health(), indent=2))
        embeds = []
        daily = await builder.build_daily(now)
        embeds.append((settings.discord_daily_channel_id, developing_embed(daily, settings.display_zone)))
        occurrence = schedule.current(now) or schedule.latest_open(now)
        if occurrence:
            session = await builder.build_session(occurrence, now)
            embeds.append((settings.session_channel, session_embed(session, settings.display_zone, "TRACKER")))
        for _, embed in embeds:
            print(json.dumps(embed.to_dict(), indent=2, ensure_ascii=False))
        if send:
            from gold_bot.rest_publisher import RestPublisher

            publisher = RestPublisher(settings.discord_bot_token.get_secret_value())
            try:
                for channel, embed in embeds:
                    print("Posted message", await publisher.send(channel, embed), "to channel", channel)
            except Exception as exc:  # noqa: BLE001
                print(f"Discord rejected the message: {exc}")
                return 1
            finally:
                await publisher.aclose()
    except DataUnavailable as exc:
        print(f"{settings.source_name} data unavailable: {exc}")
        return 1
    finally:
        await provider.aclose()
    return 0


async def _run_without_gateway(settings: Settings) -> int:
    from gold_bot.rest_publisher import RestPublisher
    from gold_bot.service import ReportService

    provider = make_provider(settings)
    storage = Storage(settings.database_path)
    schedule = SessionSchedule(settings.session_times(), settings.session_zone)
    publisher = RestPublisher(settings.discord_bot_token.get_secret_value())
    service = ReportService(settings, provider, storage, publisher, schedule)
    try:
        await service.run_forever()
    finally:
        await publisher.aclose()
        await provider.aclose()
        storage.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="GOLD market intelligence Discord bot")
    parser.add_argument("--dry-run", action="store_true", help="print reports from live market data, no Discord")
    parser.add_argument("--send-now", action="store_true", help="post the current reports once and exit")
    parser.add_argument("--no-gateway", action="store_true", help="automated reports via REST only (no slash commands)")
    args = parser.parse_args()

    settings = Settings()
    _setup_logging(settings.log_level)
    try:
        validate_provider_settings(settings)
    except ConfigurationError as exc:
        logging.error("%s", exc)
        return 2
    if args.dry_run:
        return asyncio.run(_dry_run(settings))

    token = settings.discord_bot_token.get_secret_value()
    if not token:
        logging.error("DISCORD_BOT_TOKEN is not set")
        return 2
    if not settings.discord_daily_channel_id:
        logging.error("DISCORD_DAILY_CHANNEL_ID is not set")
        return 2
    if args.send_now:
        return asyncio.run(_dry_run(settings, send=True))
    if args.no_gateway:
        try:
            return asyncio.run(_run_without_gateway(settings))
        except KeyboardInterrupt:
            return 0

    from gold_bot.bot import DiscordPublisher, GoldBot
    from gold_bot.service import ReportService

    bot = GoldBot(settings)
    provider = make_provider(settings)
    storage = Storage(settings.database_path)
    schedule = SessionSchedule(settings.session_times(), settings.session_zone)
    bot.attach_service(ReportService(settings, provider, storage, DiscordPublisher(bot), schedule))
    bot.run(token, log_handler=None, reconnect=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
