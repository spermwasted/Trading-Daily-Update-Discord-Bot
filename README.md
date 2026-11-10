# GOLD FXCM – Automated Daily Market Analysis & Session Tracking Discord Bot

Posts professional Discord embeds for **XAU/USD on FXCM** using real FXCM candle
data from FXCM's official **ForexConnect API**:

* **Daily Market Analysis** – once per new FXCM daily candle: confirmed daily bias
  (breakout vs liquidity sweep), Premium / Discount / Equilibrium, and all active
  (unfilled or partially filled) Daily / 4H / 1H Fair Value Gaps, nearest first.
* **Session Opens** – once per Asian, London and New York session open: opening
  price, current price, previous-session high/low, daily bias, premium/discount and
  liquidity-sweep status.
* Slash commands: `/gold analysis` (adds the intraday *developing* indication),
  `/gold session` (live session tracker), `/gold status` (FXCM connection health).

It is **market data and analysis only** – no orders are ever created.

## Architecture

```
            FXCM servers
                 │  ForexConnect (login: user ID + password + Hosts.jsp + "Demo"/"Real")
┌────────────────▼─────────────────┐        ┌────────────────────────────────────────────┐
│ fxcm-bridge  (Python 3.7)        │  HTTP  │ bot  (Python 3.12)                          │
│  fxcm_bridge/feed.py             │◄──────►│  gold_bot/data/bridge_client.py  data layer │
│   persistent session, reconnect, │  JSON  │  gold_bot/analysis/*  bias, P/D, FVG, sess. │
│   live OFFERS prices, get_history│        │  gold_bot/service.py  scheduling, de-dup    │
│  fxcm_bridge/server.py  GET-only │        │  gold_bot/bot.py      Discord + /commands   │
└──────────────────────────────────┘        │  SQLite: sent reports, FVGs, session state  │
                                            └────────────────────────────────────────────┘
```

**Why two processes?** The newest ForexConnect Python package (`forexconnect`
1.6.43) ships Linux wheels **only for CPython 3.5–3.7**, while discord.py needs
Python ≥ 3.8. The bridge isolates the SDK in a Python 3.7 container and exposes a
small read-only API; the analysis engine never imports ForexConnect.

| Bridge endpoint | Returns |
|---|---|
| `GET /health` | session status, last price time, reconnect count, last error (503 when not connected) |
| `GET /price` | live bid/ask, digits, server time |
| `GET /candles?timeframe=D1\|H4\|H1\|m15\|m5&count=N` or `&from=ISO&to=ISO` | OHLC (bid) oldest → newest |

### Reliability
* **FXCM:** ForexConnect's own quick reconnects (`FXCM_SDK_RECONNECT_ATTEMPTS`), plus a
  supervisor thread that logs in again with exponential backoff (5 s → 5 min) after
  `SESSION_LOST` / `DISCONNECTED`, failed logins, or no price for 10 min while the
  market is open. Data is never served from a dead session (HTTP 503).
* **Bot:** bridge calls retry with backoff; if data is still unavailable the cycle is
  skipped and logged – values are never invented. discord.py reconnects to Discord
  automatically. Each report key (`daily:<trading date>`, `session:<name>:<open time>`)
  is claimed in SQLite before sending, so it is sent at most once, including across
  restarts; a failed Discord send releases the claim and is retried.

## Interpretation of the specification

| Topic | Rule implemented |
|---|---|
| FXCM daily candle | 17:00 → 17:00 New York (FXCM "What is Timeframe?"); boundaries come from FXCM's D1 timestamps. A candle is "completed" only after its period ends. |
| Daily report trigger | The first poll after FXCM's feed shows the new D1 candle (first tick after 17:00 NY; gold reopens ~18:00 NY). |
| Bullish / bearish bias | Completed daily **close** strictly above previous high / below previous low. |
| Liquidity sweeps | Wick beyond the level with the close back inside → buy-side sweep = BEARISH, sell-side sweep = BULLISH. Close exactly at the level is not a breakout. |
| Both sides swept, close inside | NEUTRAL, labelled "Both Sides Swept". |
| Developing indication | Same rules on the forming candle, shown only in `/gold analysis`, labelled "not a confirmed close". |
| Dealing range | Previous completed FXCM daily candle (`DEALING_RANGE_MODE=previous_day`; pluggable). |
| FVG status | **Unfilled** = not tapped yet; **Partially Filled** = price touched/entered the gap (fill %); **Filled** = price reached the far edge → removed. Detection uses completed candles; mitigation counts candle 4 onward including the forming candle. |
| Sessions (New York time, DST-aware) | Asia 19:00–03:00, London 03:00–08:00, New York 08:00–17:00 NY. In PKT: London 12:00 (summer) / 13:00 (winter), New York 17:00 / 18:00, Asia 04:00 / 05:00. No sessions Fri 17:00 – Sun 17:00 NY. |
| Session opening price | Open of the first FXCM m5 candle of the session (the report waits for it). |
| Previous session | Asia → previous New York (Friday's on Mondays), London → Asia, New York → London. |
| Prices | FXCM **bid** (as on FXCM charts), shown with the instrument's own precision. |
| Candle open price | ForexConnect default `PREVIOUS_CLOSE`; set `FXCM_CANDLE_OPEN_MODE=first_tick` if your chart's opens differ. |

## Credentials and configuration

All secrets live in environment variables (a `.env` file on the server, never committed).
See [`.env.example`](.env.example) for every setting.

1. **FXCM demo account** – open one at FXCM (Trading Station "TSII" account). ForexConnect
   uses your **Trading Station user ID and password** with
   `FXCM_URL=https://www.fxcorporate.com/Hosts.jsp` and `FXCM_CONNECTION=Demo`.
   This is *not* the FXCM REST API token. Use `FXCM_SESSION_ID` / `FXCM_PIN` only if FXCM
   gave you one. Switch to `FXCM_CONNECTION=Real` only after the demo checks pass.
2. **Discord bot token** – Discord Developer Portal → your application → **Bot** →
   **Reset Token** → copy. (Not the Client Secret, Application ID or Public Key.)
   Invite the bot with the `bot` and `applications.commands` scopes and the
   *Send Messages* + *Embed Links* permissions.
3. **Channel IDs** – Discord → Settings → Advanced → Developer Mode, then right-click
   the channel → *Copy Channel ID* → `DISCORD_DAILY_CHANNEL_ID` (and optionally
   `DISCORD_SESSION_CHANNEL_ID`).

## Deploying on an always-on server

Any small Linux VPS (1 vCPU / 1 GB RAM is enough) with outbound HTTPS.

```bash
# 1. Install Docker (Ubuntu/Debian)
curl -fsSL https://get.docker.com | sudo sh
sudo usermod -aG docker $USER && newgrp docker

# 2. Get the code
git clone https://github.com/spermwasted/Trading-Daily-Update-Discord-Bot.git
cd Trading-Daily-Update-Discord-Bot

# 3. Configure secrets
cp .env.example .env
nano .env            # FXCM_USERNAME, FXCM_PASSWORD, DISCORD_BOT_TOKEN, channel IDs
chmod 600 .env

# 4. Build, then verify the FXCM demo connection BEFORE starting the bot
docker compose build
docker compose run --rm fxcm-bridge python -m fxcm_bridge.verify --seconds 30
#   -> PASS live price, PASS D1/H4/H1/m15/m5 history, D1 candles start 17:00 New York

# 5. Preview the reports from live FXCM data without posting to Discord
docker compose up -d fxcm-bridge
docker compose run --rm bot python -m gold_bot --dry-run

# 6. Start everything (restarts automatically after crashes and reboots)
docker compose up -d
docker compose logs -f
```

Operations:
* `docker compose ps` – health (`fxcm-bridge` is *healthy* only while logged in to FXCM).
* `docker compose logs -f fxcm-bridge` – login/reconnect events.
* `/gold status` in Discord – connection status, last price time, reconnect count.
* Update: `git pull && docker compose up -d --build`.
* The SQLite database (sent reports, FVGs, session state) lives in the `bot-data` volume.

## Development

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest
```

The tests cover daily bias, both liquidity sweeps, premium/discount, bullish/bearish FVG
detection, partial and full mitigation, all three session triggers, DST transitions,
duplicate-report prevention (including restarts), data-provider failures and FXCM
reconnection (with a stand-in SDK). Live verification against FXCM is
`python -m fxcm_bridge.verify` as shown above.
