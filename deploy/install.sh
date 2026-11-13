#!/usr/bin/env bash
# One-command setup for an Ubuntu/Debian server:
#   sudo bash deploy/install.sh
# Installs Docker if needed, asks for your keys (stored only in .env on this
# server), checks that live gold data loads, then starts the bot. It restarts
# automatically after crashes and reboots. Safe to run again.
set -euo pipefail
cd "$(dirname "$0")/.."

if [ "$(id -u)" -ne 0 ]; then
  echo "Please run with sudo:  sudo bash deploy/install.sh"
  exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "==> Installing Docker"
  curl -fsSL https://get.docker.com | sh
fi
if ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose plugin is missing. Install it with: apt-get install -y docker-compose-plugin"
  exit 1
fi

set_env() {  # set_env NAME VALUE  (replace the line in .env, or append it)
  local name="$1" value="$2" escaped
  escaped=$(printf '%s' "$value" | sed -e 's/[|&\\]/\\&/g')
  if grep -q "^${name}=" .env; then
    sed -i "s|^${name}=.*|${name}=${escaped}|" .env
  else
    printf '%s=%s\n' "$name" "$value" >> .env
  fi
}

current() { grep -E "^$1=" .env 2>/dev/null | head -1 | cut -d= -f2- || true; }

ask() {  # ask NAME "Prompt" secret(0/1)
  local name="$1" prompt="$2" secret="$3" value old
  old=$(current "$name")
  if [ -n "$old" ]; then
    prompt="$prompt [press Enter to keep the current value]"
  fi
  if [ "$secret" = "1" ]; then
    read -rsp "$prompt: " value; echo
  else
    read -rp "$prompt: " value
  fi
  value=$(printf '%s' "$value" | tr -d '[:space:]')
  if [ -n "$value" ]; then
    set_env "$name" "$value"
  elif [ -z "$old" ] && [ "$name" != "DISCORD_GUILD_ID" ]; then
    echo "  $name is required."; ask "$name" "$2" "$secret"
  fi
}

[ -f .env ] || cp .env.example .env
chmod 600 .env
echo "==> Keys (saved only in $(pwd)/.env, readable by root only)"
ask TWELVEDATA_API_KEY "Twelve Data API key" 1
ask DISCORD_BOT_TOKEN "Discord bot token" 1
ask DISCORD_DAILY_CHANNEL_ID "Discord channel ID for the reports" 0
ask DISCORD_GUILD_ID "Discord server ID (optional, makes /gold commands appear instantly)" 0

echo "==> Building (first time takes a few minutes)"
docker compose build bot

echo "==> Checking live gold data (nothing is posted)"
if docker compose run --rm -T bot python -m gold_bot --dry-run > /tmp/gold-bot-check.txt 2>&1; then
  echo "    OK: gold data loaded. Full preview saved in /tmp/gold-bot-check.txt"
else
  echo "    PROBLEM: the data check failed:"
  grep -E "unavailable|error|Error|not set" /tmp/gold-bot-check.txt | tail -5 | sed 's/^/      /'
  read -rp "Start the bot anyway? [y/N]: " answer
  if [ "${answer,,}" != "y" ]; then
    echo "Not started. Fix the key and run: sudo bash deploy/install.sh"
    exit 1
  fi
fi

echo "==> Starting the bot"
docker compose up -d bot
echo
echo "Done. Useful commands (run in $(pwd)):"
echo "  docker compose logs -f bot        live logs (Ctrl+C to stop watching)"
echo "  docker compose ps                 is it running?"
echo "  docker compose restart bot        restart"
echo "  git pull && docker compose up -d --build bot   update to the latest code"
