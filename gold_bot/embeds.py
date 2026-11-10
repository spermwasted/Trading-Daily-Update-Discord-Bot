"""Discord embed rendering for the daily analysis and session reports."""
from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import discord

from gold_bot.analysis.bias import BiasResult, Direction
from gold_bot.analysis.fvg import FVG, FVGDirection
from gold_bot.analysis.premium_discount import PDResult, Position
from gold_bot.reports import DailyReport, SessionReport
from gold_bot.timeutils import fmt_local, trading_date

GREEN = 0x2ECC71
RED = 0xE74C3C
ORANGE = 0xF39C12
BLUE = 0x3498DB
GRAY = 0x95A5A6
SEPARATOR = "━━━━━━━━━━━━━━━━━━━━"
FIELD_LIMIT = 1024

DIRECTION_ICON = {Direction.BULLISH: "🟢", Direction.BEARISH: "🔴", Direction.NEUTRAL: "⚪"}
POSITION_ICON = {Position.PREMIUM: "🟠", Position.DISCOUNT: "🔵", Position.EQUILIBRIUM: "⚪"}


def bias_color(bias: BiasResult | None) -> int:
    if bias is None:
        return GRAY
    return {Direction.BULLISH: GREEN, Direction.BEARISH: RED}.get(bias.direction, GRAY)


def position_color(pd: PDResult | None) -> int:
    if pd is None:
        return GRAY
    return {Position.PREMIUM: ORANGE, Position.DISCOUNT: BLUE}.get(pd.position, GRAY)


def fmt(value: float | None, digits: int) -> str:
    return "n/a" if value is None else f"{value:,.{digits}f}"


def _direction_text(bias: BiasResult | None) -> str:
    if bias is None:
        return "⚪ NEUTRAL (insufficient daily data)"
    return f"{DIRECTION_ICON[bias.direction]} **{bias.direction.value}**"


def _fvg_line(gap: FVG, price: float, digits: int) -> str:
    distance = gap.distance(price)
    if distance == 0:
        where = "price inside zone"
    else:
        where = f"{fmt(distance, digits)} {'above' if gap.lower > price else 'below'} price"
    status = gap.status.value
    if gap.fill_percent > 0:
        status += f" ({gap.fill_percent:.0f}%)"
    return (
        f"**{fmt(gap.lower, digits)} – {fmt(gap.upper, digits)}** · {gap.timeframe.label}\n"
        f"> Status: {status} · Distance: {where}"
    )


def _fvg_block(gaps: list[FVG], price: float, digits: int, limit: int) -> str:
    if not gaps:
        return "No active gaps"
    lines: list[str] = []
    for gap in gaps[:limit]:
        line = _fvg_line(gap, price, digits)
        if len("\n".join(lines + [line])) > FIELD_LIMIT - 40:
            break
        lines.append(line)
    hidden = len(gaps) - len(lines)
    if hidden:
        lines.append(f"… +{hidden} more further away")
    return "\n".join(lines)


def _footer(embed: discord.Embed, text: str, source: str, now: datetime, tz: ZoneInfo) -> None:
    embed.set_footer(text=f"{text} • Data: {source} bid • {fmt_local(now, tz)}")
    embed.timestamp = now


def daily_embed(report: DailyReport, tz: ZoneInfo, max_per_side: int = 6) -> discord.Embed:
    d = report.digits
    bias = report.bias
    trade_day = trading_date(report.trading_day_start)
    embed = discord.Embed(
        title=f"{report.brand} | DAILY MARKET ANALYSIS",
        description=(
            f"**{report.instrument}** · trading day **{trade_day:%a %d %b %Y}** "
            f"(opened {fmt_local(report.trading_day_start, tz)})"
        ),
        color=bias_color(bias),
    )

    if bias is not None:
        confirmation = f"{bias.signal.short}\n> {bias.signal.value}"
        ref = f"vs {trading_date(bias.reference.time):%d %b} H {fmt(bias.reference_high, d)} / L {fmt(bias.reference_low, d)}"
    else:
        confirmation, ref = "Unconfirmed", "—"
    prev = report.previous_day
    embed.add_field(
        name="📊 DAILY MARKET BIAS",
        value=(
            f"• Direction: {_direction_text(bias)}\n"
            f"• Previous Day High: `{fmt(prev.high, d)}`\n"
            f"• Previous Day Low: `{fmt(prev.low, d)}`\n"
            f"• Previous Day Close: `{fmt(prev.close, d)}`\n"
            f"• Daily Open: `{fmt(report.daily_open, d)}`\n"
            f"• Confirmation: {confirmation}\n"
            f"• Based on completed candle {trading_date(prev.time):%a %d %b} {ref}\n{SEPARATOR}"
        )[:FIELD_LIMIT],
        inline=False,
    )

    pd = report.premium_discount
    if pd is not None:
        pct = pd.percent_of_range
        pct_text = f" ({pct:.1f}% of range)" if pct is not None else ""
        pd_value = (
            f"• Range High: `{fmt(pd.range.high, d)}`\n"
            f"• Range Low: `{fmt(pd.range.low, d)}`\n"
            f"• Equilibrium: `{fmt(pd.range.equilibrium, d + 1)}`\n"
            f"• Current Price: `{fmt(report.current_price, d)}`\n"
            f"• Market Position: {POSITION_ICON[pd.position]} **{pd.position.value}**{pct_text}\n{SEPARATOR}"
        )
    else:
        pd_value = f"Dealing range unavailable\n{SEPARATOR}"
    embed.add_field(name="⚖️ PREMIUM & DISCOUNT", value=pd_value[:FIELD_LIMIT], inline=False)

    bullish = [g for g in report.fvgs if g.direction is FVGDirection.BULLISH]
    bearish = [g for g in report.fvgs if g.direction is FVGDirection.BEARISH]
    tfs = ", ".join(tf.label for tf in report.fvg_timeframes)
    embed.add_field(
        name="📍 ACTIVE FAIR VALUE GAPS",
        value=f"Timeframes: {tfs} · nearest first · fully filled gaps removed",
        inline=False,
    )
    embed.add_field(
        name=f"🟢 BULLISH FVG ({len(bullish)})",
        value=_fvg_block(bullish, report.current_price, d, max_per_side),
        inline=False,
    )
    embed.add_field(
        name=f"🔴 BEARISH FVG ({len(bearish)})",
        value=_fvg_block(bearish, report.current_price, d, max_per_side),
        inline=False,
    )
    if report.fvg_errors:
        embed.add_field(
            name="⚠️ Data notice",
            value="\n".join(f"{tf}: {report.source} data unavailable" for tf in report.fvg_errors)[:FIELD_LIMIT],
            inline=False,
        )
    _footer(embed, f"{report.brand} | Automated Market Intelligence", report.source, report.generated_at, tz)
    return embed


def developing_embed(report: DailyReport, tz: ZoneInfo, max_per_side: int = 6) -> discord.Embed:
    """On-demand variant that adds the intraday (unconfirmed) indication."""
    embed = daily_embed(report, tz, max_per_side)
    dev = report.developing
    if dev is not None:
        embed.insert_field_at(
            1,
            name="⏳ INTRADAY DEVELOPING INDICATION (not a confirmed close)",
            value=(
                f"• Current daily candle so far: {DIRECTION_ICON[dev.direction]} {dev.direction.value}\n"
                f"• {dev.signal.value}\n"
                f"• Becomes confirmed only after the daily close (17:00 New York)\n{SEPARATOR}"
            )[:FIELD_LIMIT],
            inline=False,
        )
    return embed


def session_embed(report: SessionReport, tz: ZoneInfo, title_suffix: str = "OPEN") -> discord.Embed:
    d = report.digits
    s = report.session
    embed = discord.Embed(
        title=f"{report.brand} | {s.name.upper()} SESSION {title_suffix}",
        description=f"**{report.instrument}** · {fmt_local(s.start, tz)} → {fmt_local(s.end, tz, with_date=False)}",
        color=position_color(report.premium_discount),
    )
    embed.add_field(
        name="🌍 SESSION INFORMATION",
        value=(
            f"• Session: **{s.name}**\n"
            f"• Opening Time: {fmt_local(s.start, tz, with_date=False)}\n"
            f"• Opening Price: `{fmt(report.opening_price, d)}`\n"
            f"• Current Price: `{fmt(report.current_price, d)}`"
            + (
                f"\n• Session High / Low so far: `{fmt(report.current_stats.high, d)}` / `{fmt(report.current_stats.low, d)}`"
                if report.current_stats
                else ""
            )
        ),
        inline=False,
    )
    prev_name = report.previous_session.name if report.previous_session else "Previous"
    ps = report.previous_stats
    embed.add_field(
        name="📈 PREVIOUS SESSION LEVELS",
        value=(
            f"• Previous Session: {prev_name}\n"
            f"• Previous Session High: `{fmt(ps.high if ps else None, d)}`\n"
            f"• Previous Session Low: `{fmt(ps.low if ps else None, d)}`"
        ),
        inline=False,
    )
    bias = report.bias
    pd = report.premium_discount
    sweep_text = report.current_sweep.describe(prev_name) if report.current_sweep else "NOT DETECTED"
    if report.previous_sweep is not None and report.previous_sweep.detected:
        sweep_text += f"\n> Last session ({prev_name}) swept the session before it: " + (
            "high and low" if report.previous_sweep.swept_high and report.previous_sweep.swept_low
            else "high" if report.previous_sweep.swept_high else "low"
        )
    embed.add_field(
        name="📊 MARKET POSITION",
        value=(
            f"• Daily Bias: {_direction_text(bias)}"
            + (f" ({bias.signal.short})" if bias else "")
            + f"\n• Market Position: "
            + (f"{POSITION_ICON[pd.position]} **{pd.position.value}** (EQ `{fmt(pd.range.equilibrium, d + 1)}`)" if pd else "n/a")
            + f"\n• Liquidity Sweep: {sweep_text}"
        )[:FIELD_LIMIT],
        inline=False,
    )
    _footer(embed, f"{report.brand} | Session Intelligence", report.source, report.generated_at, tz)
    return embed
