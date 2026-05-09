# Last Stand Clan — Discord Raid-Test Bot

## What This Project Is

A Discord bot for stress-testing a custom anti-raid system on a private test server.
Built in Python (discord.py 2.x) with a cog-based architecture.
Replit is the **editor only** — the bot runs exclusively on **Railway**.

---

## Stack

- **Language**: Python 3.12
- **Framework**: discord.py 2.3+
- **Concurrency**: asyncio + asyncio.Semaphore
- **Deployment**: Railway via Docker
- **Package manager**: pnpm (Node.js side, not used for the bot)

---

## Project Structure

```
discord-bot/
  bot.py              — entry point, loads cogs, syncs slash commands
  requirements.txt    — discord.py>=2.3.0, python-dotenv>=1.0.0
  cogs/
    raid.py           — /raid command (full destruction engine)
    ban.py            — /banevery1 (mass ban+kick) + /unban (mass unban, password protected)
    spam.py           — /spamchannels (webhook flood)
    control.py        — /stop, /status, /setratelimit, /bypassstats, /nuke, /timeoutall
  utils/
    bypass.py         — BypassEngine: per-route rate limit isolation, adaptive 429 recovery,
                        jitter modes, fingerprint rotation, burst-drain cycles
    state.py          — BotState singleton shared across all cogs (stop_event, tasks, bypass)
    rate_control.py   — intensity 1–10 mapped to delays and burst sizes
src/
  index.ts            — bare TypeScript stub (NOT used, ignore it)
Dockerfile            — Python 3.12-slim, installs requirements, runs discord-bot/bot.py
railway.json          — tells Railway to use the Dockerfile
```

---

## Slash Commands

| Command | What it does |
|---|---|
| `/raid` | Full destruction: nuke channels, create 100 flood channels, 15 webhooks each, ban/kick/timeout all members, role flood, server rename. `chaos=True` adds emoji flood, event flood, voice chaos, wave repeat, etc. |
| `/banevery1` | Mass ban + optional kick of all eligible members |
| `/unban` | Mass unban everyone — requires password `hellonice` |
| `/spamchannels` | Webhook flood across all existing channels |
| `/nuke` | Delete every channel, optionally rebuild with flood channels |
| `/timeoutall` | Timeout all members 28 days |
| `/stop` | Cancel all running operations instantly |
| `/status` | Show active operation + bypass engine stats |
| `/setratelimit` | Change intensity 1–10 live during a run |
| `/bypassstats` | Detailed bypass engine call/success/retry stats |
| `/massdm` | Mass DM all server members — text and/or embed. Params: `message`, `embed_title`, `embed_description`, `embed_color`, `intensity`, `skip_bots` |
| `/listservers` | List all servers the bot is in with member counts and permission info |
| `/leaveallservers` | Leave all servers (optionally including the current one) — requires password `hellonice` |
| `/deleteroles` | Delete all deletable roles in the server. Param: `keep_managed` (default True skips bot/boost roles) |

---

## Deployment Workflow

```
Edit on Replit → Git tab → Commit & Push → Railway auto-redeploys (~2 min)
```

- **Railway logs**: Railway → Deployments → latest build → View logs
- **Successful startup looks like**:
  ```
  [cog] loaded: cogs.raid
  [cog] loaded: cogs.ban
  [cog] loaded: cogs.spam
  [cog] loaded: cogs.control
  [sync] slash commands synced to guild XXXX   ← if TEST_GUILD_ID is set
  [ready] Logged in as BotName#0000
  ```

---

## Railway Environment Variables

| Variable | Required | Notes |
|---|---|---|
| `DISCORD_BOT_TOKEN` | YES | From Discord Developer Portal → Bot tab |
| `TEST_GUILD_ID` | Recommended | Your test server ID. Without it, slash command updates take up to 1 hour to show in Discord. With it, they update instantly on every redeploy. |

---

## Discord Developer Portal Requirements

Both of these MUST be ON in the portal (Bot → Privileged Gateway Intents):
- **Server Members Intent**
- **Message Content Intent**

---

## STRICT RULES — Read Before Touching Anything

1. **NEVER run the bot inside Replit** — no workflows, no `python bot.py`, no local execution of any kind
2. **NEVER create `pnpm-workspace.yaml`** — breaks Railway by spawning phantom workspace services
3. **DO NOT TOUCH these files**:
   - `railway.json`
   - `.dockerignore`
   - `tsconfig.json`
   - `src/index.ts` (deprecated stub, leave it alone)
4. If `package.json` is changed → always run `pnpm install` after to regenerate `pnpm-lock.yaml`
5. If Railway fails with `ERR_PNPM_LOCKFILE_CONFIG_MISMATCH` → delete `pnpm-lock.yaml`, run `pnpm install`
6. Keep responses short — user is on phone

---

## Key Architecture Details

### BotState singleton (`utils/state.py`)
All cogs share one `bot_state` instance. Critical fields:
- `stop_event` — asyncio.Event, set by `/stop`, checked in every coroutine loop
- `active_simulation` — string name of what's running (prevents double-starts)
- `running_tasks` — set of active asyncio.Tasks
- `bypass` — BypassEngine instance
- `rate_controller` — RateController instance

### BypassEngine (`utils/bypass.py`)
Every Discord API call should go through `bot_state.bypass.execute(ROUTE_*, factory, stop_event)`.
- Handles per-route 429 isolation (ban 429s don't pause webhook sends)
- Adaptive retry with full-jitter exponential backoff
- Fingerprint rotation for content variation

### Channel creation rate limit
Discord allows ~2 channel creates per second per guild.
`_phase_nuke_and_build` uses `BATCH=2, BATCH_PAUSE=2.0` — do NOT increase BATCH or decrease BATCH_PAUSE or creates will fail silently.

### Member cache
Always call `await guild.chunk(cache=True)` before using `guild.members`.
Without it, `guild.members` returns an empty or incomplete list.

### Role hierarchy
Bot can only ban/kick/timeout members whose top role is BELOW the bot's role.
Filter targets with `m.top_role < guild.me.top_role` before attempting actions.

### Interaction responses
- For fast commands: use `interaction.response.send_message()` directly
- For commands that do async work first (chunking, permission checks): use `await interaction.response.defer()` first, then `interaction.followup.send()`
- Discord requires a response within 3 seconds or "Application did not respond" appears

---

## Recent Changes Made (in order)

1. Fixed `/banevery1` — added guild.chunk(), upfront permission check, role hierarchy filter, completion report
2. Added `/unban` — mass unban with password `hellonice`, 20 concurrent unbans
3. Rewrote `/raid` — added `chaos` bool to replace 15 individual True/False params, fixed guild.chunk(), fixed 2000-char message limit crash, fixed overwrite_storm/mention_burst reading empty channel cache, fixed audit_flood rate limit (48→12 renames)
4. Fixed channel creation — BATCH 4→2, PAUSE 1.1→2.0 so all requested channels actually get created
5. Fixed webhook spam — replaced sequential burst_drain_execute with flat asyncio.gather across all webhooks × all messages simultaneously
6. Fixed Dockerfile — was running TypeScript stub (no commands). Now runs Python bot correctly.
7. Fixed infinite thinking on ALL deferred commands — added `asyncio.wait_for(..., timeout=10.0)` to every `guild.chunk()` call in raid.py, ban.py, and dm.py. Without this, if chunk hangs the Discord "thinking" spinner never resolves.
8. Fixed `/timeoutall` — was responding instantly without deferring or chunking, risking 3-second Discord timeout and missing members. Now defers first, chunks with timeout, then responds. Also added role hierarchy filter so it only targets members the bot can actually timeout.
9. Hardened `_phase_integration_wipe` in raid.py — added `if not my_app_id: return` guard so if application_id is unset the whole wipe is skipped rather than risking self-deletion (which kicks the bot).
