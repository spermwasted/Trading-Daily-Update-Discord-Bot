"""Discord client: posts scheduled reports and serves /gold slash commands.

discord.py reconnects to the Discord gateway automatically; posting uses the
REST API via partial messageables, so it does not depend on the channel cache.
"""
from __future__ import annotations

import asyncio
import logging

import discord
from discord import app_commands

from gold_bot.config import Settings
from gold_bot.data.provider import DataUnavailable
from gold_bot.embeds import developing_embed, session_embed
from gold_bot.service import ReportService

log = logging.getLogger(__name__)


class DiscordPublisher:
    def __init__(self, client: discord.Client) -> None:
        self.client = client

    async def send(self, channel_id: int, embed: discord.Embed) -> str:
        if not channel_id:
            raise RuntimeError("Discord channel id is not configured")
        channel = self.client.get_partial_messageable(channel_id)
        message = await channel.send(embed=embed)
        return str(message.id)


class GoldBot(discord.Client):
    def __init__(self, settings: Settings) -> None:
        super().__init__(intents=discord.Intents.default())
        self.settings = settings
        self.tree = app_commands.CommandTree(self)
        self.service: ReportService | None = None
        self._service_task: asyncio.Task | None = None

    def attach_service(self, service: ReportService) -> None:
        self.service = service
        self.tree.add_command(_gold_commands(self))

    async def setup_hook(self) -> None:
        if self.settings.discord_guild_id:
            guild = discord.Object(id=self.settings.discord_guild_id)
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
        else:
            await self.tree.sync()
        if self.service is not None:
            self._service_task = asyncio.create_task(self.service.run_forever(), name="report-service")

    async def on_ready(self) -> None:
        log.info("Discord connected as %s", self.user)

    async def close(self) -> None:
        if self.service is not None:
            self.service.stop()
        if self._service_task is not None:
            self._service_task.cancel()
        await super().close()


def _gold_commands(bot: GoldBot) -> app_commands.Group:
    group = app_commands.Group(name="gold", description="GOLD market intelligence")

    @group.command(name="analysis", description="Daily bias, premium/discount and active FVGs right now")
    async def analysis(interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        try:
            report = await bot.service.builder.build_daily(bot.service.clock())
        except DataUnavailable as exc:
            await interaction.followup.send(f"⚠️ Market data unavailable: {exc}")
            return
        await interaction.followup.send(
            embed=developing_embed(report, bot.settings.display_zone, bot.settings.fvg_max_per_side)
        )

    @group.command(name="session", description="Current trading session tracking")
    async def session(interaction: discord.Interaction) -> None:
        await interaction.response.defer(thinking=True)
        now = bot.service.clock()
        occurrence = bot.service.schedule.current(now)
        if occurrence is None:
            nxt = bot.service.schedule.next_open(now)
            text = "No trading session is open right now."
            if nxt:
                from gold_bot.timeutils import fmt_local

                text += f" Next: **{nxt.name}** at {fmt_local(nxt.start, bot.settings.display_zone)}."
            await interaction.followup.send(text)
            return
        try:
            report = await bot.service.builder.build_session(occurrence, now)
        except DataUnavailable as exc:
            await interaction.followup.send(f"⚠️ Market data unavailable: {exc}")
            return
        await interaction.followup.send(embed=session_embed(report, bot.settings.display_zone, "TRACKER"))

    @group.command(name="status", description="Market data connection status")
    async def status(interaction: discord.Interaction) -> None:
        health = await bot.service.provider.health()
        ok = health.get("connected")
        lines = [
            f"{bot.settings.source_name} connection: {'🟢 connected' if ok else '🔴 not connected'} ({health.get('status')})",
            f"Account type: {health.get('connection', 'n/a')} · Instrument: {health.get('instrument', 'n/a')}",
            f"Last price update: {health.get('last_price_received_at', 'n/a')}",
            f"Reconnects: {health.get('reconnects', 'n/a')}",
        ]
        if health.get("last_error"):
            lines.append(f"Last error: {health['last_error']}")
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    return group
