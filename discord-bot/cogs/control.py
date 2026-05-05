"""
control.py — /stop, /setratelimit, /status
"""

import discord
from discord import app_commands
from discord.ext import commands

from utils.state import bot_state

RAID_TAG = "EoN"


class Control(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

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
            f"🛑 **Stopped**\n"
            f"┣ Operation : `{simulation}`\n"
            f"┗ Cancelled : `{count}` tasks",
        )

    @app_commands.command(name="setratelimit", description="Change attack intensity live (1–10).")
    @app_commands.describe(level="1 = slowest, 10 = fastest.")
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
            f"┣ Intensity : `{level}/10`\n"
            f"┗ Gap       : `{bot_state.rate_controller.get_delay()}s`",
            ephemeral=True,
        )

    @app_commands.command(name="status", description="Show current operation status.")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    async def status(self, interaction: discord.Interaction) -> None:
        rc = bot_state.rate_controller
        if bot_state.active_simulation:
            msg = (
                f"📊 **Running: `{bot_state.active_simulation}`**\n"
                f"┣ Intensity : `{rc.intensity}/10`\n"
                f"┣ Gap       : `{rc.get_delay()}s`\n"
                f"┗ Tasks     : `{len(bot_state.running_tasks)}`"
            )
        else:
            msg = (
                f"📊 **Idle**\n"
                f"┣ Last intensity: `{rc.intensity}/10`\n"
                f"┗ Last gap      : `{rc.get_delay()}s`"
            )
        await interaction.response.send_message(msg, ephemeral=True)

    async def _missing_perms(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message("❌ You need **Administrator** permission.", ephemeral=True)

    stop.error(_missing_perms)          # type: ignore[arg-type]
    setratelimit.error(_missing_perms)  # type: ignore[arg-type]
    status.error(_missing_perms)        # type: ignore[arg-type]


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Control(bot))
