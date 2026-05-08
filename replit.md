# Last Stand Clan — Discord Nuke/Raid-Test Bot

## Overview

Hybrid Discord bot project. Python (discord.py) is the primary implementation with cog-based modular commands. TypeScript (discord.js) is a lightweight mirror entry point.

**Replit is the editor only. The bot runs exclusively on Railway.**

## Stack

- **Python**: discord.py 2.3+, Python 3.12, asyncio, cog-based architecture
- **Node.js**: discord.js v14, TypeScript, tsx (entry: src/index.ts)
- **Package manager**: pnpm

## Project Structure

- `discord-bot/bot.py` — Python bot entry point
- `discord-bot/cogs/` — Slash command cogs (raid.py, ban.py, spam.py, control.py)
- `discord-bot/utils/` — Shared utilities (bypass.py, state.py, rate_control.py)
- `src/index.ts` — TypeScript entry point

## Hosting

- **Platform**: Railway (NOT Replit)
- **Deploy**: Auto-redeploys on push to GitHub `main` branch
- **Build**: Root `Dockerfile` (node:22-bookworm-slim + pnpm@10)
- **Logs**: Railway → Deployments tab → latest build
- **Env var**: `DISCORD_BOT_TOKEN` set in Railway dashboard

## Workflow

Edit on Replit → push via Git tab → Railway auto-redeploys (~2 min)

## User Preferences

- NEVER run the bot inside Replit — no workflows, no `python bot.py`, no `pnpm dev`
- NEVER create `pnpm-workspace.yaml` — breaks Railway
- After any `package.json` change, run `pnpm install` to regenerate `pnpm-lock.yaml`
- If Railway fails with `ERR_PNPM_LOCKFILE_CONFIG_MISMATCH`: delete `pnpm-lock.yaml` and run `pnpm install`
- Keep instructions short (user is on phone)

## DO NOT TOUCH

- `Dockerfile`
- `railway.json`
- `.dockerignore`
- `tsconfig.json`

## Discord Developer Portal Requirements

- Server Members Intent: ON
- Message Content Intent: ON
