import discord
from discord import app_commands
from discord.ext import commands

from utils.state import bot_state


class Control(commands.Cog):
    """Control commands: stop, setratelimit, status."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ------------------------------------------------------------------
    # /stop
    # ------------------------------------------------------------------
    @app_commands.command(
        name="stop",
        description="Immediately stop all running simulations.",
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    async def stop(self, interaction: discord.Interaction) -> None:
        if not bot_state.active_simulation and not bot_state.is_running():
            await interaction.response.send_message(
                "ℹ️ No simulation is currently running.", ephemeral=True
            )
            return

        simulation = bot_state.active_simulation or "unknown"
        task_count = len(bot_state.running_tasks)
        bot_state.stop_all()

        await interaction.response.send_message(
            f"🛑 **SIMULATION STOPPED**\n"
            f"┣ Halted    : `{simulation}`\n"
            f"┣ Tasks cancelled: `{task_count}`\n"
            f"┗ Leftover test resources are being cleaned up in the background.",
        )

    # ------------------------------------------------------------------
    # /setratelimit
    # ------------------------------------------------------------------
    @app_commands.command(
        name="setratelimit",
        description="Adjust attack intensity for the current or next simulation.",
    )
    @app_commands.describe(
        level="New intensity level: 1 = slowest, 10 = fastest."
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    async def setratelimit(
        self, interaction: discord.Interaction, level: int
    ) -> None:
        if not 1 <= level <= 10:
            await interaction.response.send_message(
                "❌ Level must be between **1** and **10**.", ephemeral=True
            )
            return

        bot_state.rate_controller.set_intensity(level)
        delay = bot_state.rate_controller.get_delay()
        context = (
            "(applied to running simulation)"
            if bot_state.active_simulation
            else "(will apply to the next simulation)"
        )

        await interaction.response.send_message(
            f"⚙️ **Rate limit updated** {context}\n"
            f"┣ Intensity : `{level}/10`\n"
            f"┗ Action gap: `{delay}s`",
            ephemeral=True,
        )

    # ------------------------------------------------------------------
    # /status
    # ------------------------------------------------------------------
    @app_commands.command(
        name="status",
        description="Show the current simulation status and rate settings.",
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    async def status(self, interaction: discord.Interaction) -> None:
        rc = bot_state.rate_controller
        if bot_state.active_simulation:
            msg = (
                f"📊 **Simulation running**\n"
                f"┣ Active    : `{bot_state.active_simulation}`\n"
                f"┣ Intensity : `{rc.intensity}/10`\n"
                f"┣ Action gap: `{rc.get_delay()}s`\n"
                f"┗ Background tasks: `{len(bot_state.running_tasks)}`"
            )
        else:
            msg = (
                f"📊 **No simulation running**\n"
                f"┣ Last intensity: `{rc.intensity}/10`\n"
                f"┗ Last action gap: `{rc.get_delay()}s`"
            )
        await interaction.response.send_message(msg, ephemeral=True)

    # ------------------------------------------------------------------
    # Error handlers
    # ------------------------------------------------------------------
    async def _missing_perms(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "❌ You need **Administrator** permission for this command.",
                ephemeral=True,
            )

    stop.error(_missing_perms)  # type: ignore[arg-type]
    setratelimit.error(_missing_perms)  # type: ignore[arg-type]
    status.error(_missing_perms)  # type: ignore[arg-type]


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Control(bot))
