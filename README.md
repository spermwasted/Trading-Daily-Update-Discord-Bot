# GOLD – Automated Daily Market Analysis & Session Tracking Discord Bot

Posts professional Discord embeds for **XAU/USD** using real broker candle data:

* **Daily Market Analysis** – once per new daily candle: confirmed daily bias
  (breakout vs liquidity sweep), Premium / Discount / Equilibrium, and all active
  (unfilled or partially filled) Daily / 4H / 1H Fair Value Gaps, nearest first.
* **Session Opens** – once per Asian, London and New York session open: opening
  price, current price, previous-session high/low, daily bias, premium/discount and
  liquidity-sweep status.
* Slash commands: `/gold analysis` (adds the intraday *developing* indication),
  `/gold session` (live session tracker), `/gold status` (data connection health).

It is **market data and analysis only** – no orders are ever created.

## Data source

| `DATA_PROVIDER` | Source | Account needed |
|---|---|---|
| `twelvedata` (default) | Twelve Data `time_series` API, XAU/USD | Free API key (email sign-up only) |
| `oanda` | OANDA v20 REST API, XAU_USD **bid** candles | OANDA practice (demo) account + API token |
| `fxcm` | FXCM ForexConnect via the `fxcm_bridge` service | FXCM Trading Station login |

**Twelve Data** gives 5-minute and 1-hour candles; the bot builds 15M from 5M and
4H / Daily from 1H itself, on FXCM's boundaries (daily 17:00 → 17:00 New York, 4H at
17:00, 21:00, 01:00, 05:00, 09:00, 13:00 NY), so candle *timing* matches the FXCM chart.
Prices are Twelve Data's aggregated XAU/USD quotes, so levels can differ from FXCM by a
few cents. To stay inside the free plan (800 credits/day, 8/minute) the bot loads history
once at start-up and then fetches only the newest 5-minute candles every 150 s (every
20 s for 4 minutes after each session open and the daily open; every 30 min at weekends),
about 650 credits per trading day. "Current price" is the latest 5-minute close.

**How close is OANDA to the FXCM chart?** The candle *structure* is the same: OANDA
candles are requested with `dailyAlignment=17` and `alignmentTimezone=America/New_York`,
so daily candles run 17:00 → 17:00 New York exactly like FXCM's, and 4H candles are
aligned to that daily open (17:00, 21:00, 01:00, 05:00, 09:00, 13:00 NY) as on FXCM.
The *prices* come from a different broker, so highs/lows/closes can differ from your
FXCM chart by a few cents (occasionally more around news), and OANDA quotes gold with
3 decimals. Levels, bias and FVGs are therefore OANDA's view of gold, not FXCM's. Every
embed footer states the data source.

## Architecture

```
gold_bot/data/          data layer (only place that talks to a data source)
  twelvedata.py         Twelve Data provider with credit-saving cache (default)
  aggregate.py          builds 15M / 4H / Daily candles on FXCM boundaries
  oanda.py              OANDA v20 REST provider
  bridge_client.py      client for fxcm_bridge/ (DATA_PROVIDER=fxcm)
gold_bot/analysis/      pure functions: bias, premium/discount, FVGs, sessions
gold_bot/reports.py     builds report data from candles
gold_bot/service.py     scheduling, duplicate prevention, session tracking
gold_bot/embeds.py      Discord embeds
gold_bot/bot.py         discord.py client + /gold commands
fxcm_bridge/            optional Python 3.7 ForexConnect service (FXCM only)
```

### Reliability
* **Data:** every request retries with backoff on network/server errors (a bad token
  fails fast). If data is still unavailable the cycle is skipped and logged – values are
  never invented. With OANDA each poll is an independent HTTPS request, so a dropped
  connection recovers on the next poll (every 20 s) without restarts. The FXCM bridge has
  its own re-login supervisor.
* **Discord:** discord.py reconnects automatically.
* **No duplicates:** each report key (`daily:<trading date>`, `session:<name>:<open time>`)
  is claimed in SQLite before sending – at most once, including across restarts; a failed
  send is released and retried.

## Interpretation of the specification

| Topic | Rule implemented |
|---|---|
| Daily candle | 17:00 → 17:00 New York. "Completed" only after its period ends. |
| Daily report trigger | First poll after the feed shows the new daily candle (gold reopens ~18:00 NY). |
| Bullish / bearish bias | Completed daily **close** strictly above previous high / below previous low. |
| Liquidity sweeps | Wick beyond the level, close back inside → buy-side sweep = BEARISH, sell-side sweep = BULLISH. Close exactly at the level is not a breakout. |
| Both sides swept, close inside | NEUTRAL, "Both Sides Swept". |
| Developing indication | Same rules on the forming candle, only in `/gold analysis`, labelled "not a confirmed close". |
| Dealing range | Previous completed daily candle (`DEALING_RANGE_MODE=previous_day`; pluggable). |
| FVG status | **Unfilled** = not tapped yet; **Partially Filled** = price touched/entered the gap (fill %); **Filled** = price reached the far edge → removed. Detection on completed candles; mitigation from candle 4 on, including the forming candle. |
| Sessions (New York time, DST-aware) | Asia 19:00–03:00, London 03:00–08:00, New York 08:00–17:00 NY. In PKT: London 12:00 (summer) / 13:00 (winter), New York 17:00 / 18:00, Asia 04:00 / 05:00. None Fri 17:00 – Sun 17:00 NY. |
| Session opening price | Open of the first m5 candle of the session (the report waits for it). |
| Previous session | Asia → previous New York (Friday's on Mondays), London → Asia, New York → London. |
| Prices | **Bid**, as on broker charts, with the instrument's own precision. |

## Credentials

All secrets are environment variables in a `.env` file on the server (never committed);
see [`.env.example`](.env.example).

1. **Twelve Data API key** – sign up with your email at twelvedata.com (confirm the
   email), then Dashboard → **API Keys** → copy the key → `.env`: `TWELVEDATA_API_KEY=<key>`.
   (For OANDA instead: `DATA_PROVIDER=oanda`, `OANDA_API_TOKEN=<token>` from the practice
   account's *Manage API Access* page.)
2. **Discord bot token** – Discord Developer Portal → your application → **Bot** →
   **Reset Token** → copy (not the Client Secret, Application ID or Public Key). Invite the
   bot with the `bot` and `applications.commands` scopes and *Send Messages* + *Embed Links*.
3. **Channel IDs** – Discord → Settings → Advanced → Developer Mode, then right-click the
   channel → *Copy Channel ID* → `DISCORD_DAILY_CHANNEL_ID` (and optionally
   `DISCORD_SESSION_CHANNEL_ID`).

## Deploying on an always-on server

Any small Linux VPS (1 vCPU / 1 GB RAM) with outbound HTTPS.

```bash
# 1. Install Docker (Ubuntu/Debian)
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER && newgrp docker

# 2. Get the code
git clone https://github.com/spermwasted/Trading-Daily-Update-Discord-Bot.git
cd Trading-Daily-Update-Discord-Bot

# 3. Configure secrets
cp .env.example .env
nano .env            # TWELVEDATA_API_KEY, DISCORD_BOT_TOKEN, DISCORD_DAILY_CHANNEL_ID
chmod 600 .env

# 4. Build and preview the reports from live data without posting to Discord
docker compose build
docker compose run --rm bot python -m gold_bot --dry-run
#    ...or post one test report to your channel right now:
docker compose run --rm bot python -m gold_bot --send-now

# 5. Start (restarts automatically after crashes and reboots)
docker compose up -d
docker compose logs -f
```

Without slash commands (lighter, REST-only posting), change the bot's command to
`python -m gold_bot --no-gateway`; the automated reports are identical.

Operations: `docker compose ps`, `docker compose logs -f bot`, `/gold status` in Discord.
Update with `git pull && docker compose up -d --build`. The SQLite database lives in the
`bot-data` volume.

### Using FXCM instead
Set `DATA_PROVIDER=fxcm`, fill in `FXCM_USERNAME` / `FXCM_PASSWORD` (Trading Station login,
`FXCM_CONNECTION=Demo` first), then:

```bash
docker compose --profile fxcm build
docker compose --profile fxcm run --rm fxcm-bridge python -m fxcm_bridge.verify
docker compose --profile fxcm up -d
```

The bridge exists because FXCM's ForexConnect Python package (1.6.43) only supports
CPython 3.5–3.7 on Linux, while discord.py needs Python ≥ 3.8.

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```

Tests cover daily bias, both liquidity sweeps, premium/discount, bullish/bearish FVG
detection, partial and full mitigation, all three session triggers, DST transitions,
duplicate-report prevention (including restarts), data-provider failures, 4H/Daily candle
building on FXCM boundaries, the Twelve Data cache (credit use, back-off), the OANDA
client, FXCM reconnection, Discord REST posting and the `/gold` slash commands.
