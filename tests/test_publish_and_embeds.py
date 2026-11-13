import asyncio
import json
from datetime import timedelta

import discord
import httpx
import pytest

from gold_bot.rest_publisher import RestPublisher
from tests.helpers import dt
from tests.test_service import build, load_market


def test_rest_publisher_posts_embed_with_bot_token():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"id": "123"})

    publisher = RestPublisher("tok", transport=httpx.MockTransport(handler))
    embed = discord.Embed(title="GOLD FXCM | TEST", description="x")
    assert asyncio.run(publisher.send(42, embed)) == "123"
    request = seen[0]
    assert request.url.path == "/api/v10/channels/42/messages"
    assert request.headers["Authorization"] == "Bot tok"
    assert json.loads(request.content)["embeds"][0]["title"] == "GOLD FXCM | TEST"


def test_rest_publisher_raises_on_discord_error():
    publisher = RestPublisher("tok", transport=httpx.MockTransport(
        lambda r: httpx.Response(403, json={"message": "Missing Permissions", "code": 50013})))
    with pytest.raises(RuntimeError, match="403"):
        asyncio.run(publisher.send(42, discord.Embed(title="t")))


def test_daily_embed_says_market_closed_when_no_new_day_has_opened(tmp_path):
    from gold_bot.embeds import daily_embed

    now = dt("2026-11-07T12:00:00Z")  # Saturday
    service, provider, _, _ = build(tmp_path, now)
    load_market(provider, dt("2026-11-09T23:00:00Z"))
    provider.candles["D1"] = provider.candles["D1"][:2]  # Fri + Mon only, both completed by Tue
    report = asyncio.run(service.builder.build_daily(dt("2026-11-10T23:30:00Z") - timedelta(hours=1)))
    embed = daily_embed(report, service.settings.display_zone).to_dict()
    assert "market closed" in embed["description"]
    assert "not open yet" in embed["fields"][0]["value"]


def test_embeds_stay_within_discord_limits(tmp_path):
    from gold_bot.embeds import developing_embed

    now = dt("2026-11-09T23:05:00Z")
    service, provider, _, _ = build(tmp_path, now)
    load_market(provider, now)
    report = asyncio.run(service.builder.build_daily(now))
    embed = developing_embed(report, service.settings.display_zone)
    assert len(embed) <= 6000
    assert all(len(f.value) <= 1024 and len(f.name) <= 256 for f in embed.fields)
    assert len(embed.fields) <= 25
