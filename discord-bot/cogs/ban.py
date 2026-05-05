import asyncio

import discord
from discord import app_commands
from discord.ext import commands

from utils.state import bot_state


class Ban(commands.Cog):
    """Simulates a mass-ban wave to test server ban-rate protections."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="banevery1",
        description="[TEST ONLY] Ban all non-bot members to test anti-raid ban protections.",
    )
    @app_commands.describe(
        intensity="Ban rate intensity 1 (slow) – 10 (fastest). Default: 3.",
        skip_admins="Skip members with Administrator permission. Default: True.",
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    async def banevery1(
        self,
        interaction: discord.Interaction,
        intensity: int = 3,
        skip_admins: bool = True,
    ) -> None:
        if bot_state.active_simulation:
            await interaction.response.send_message(
                f"⚠️ Simulation **{bot_state.active_simulation}** is already running. "
                "Use `/stop` to halt it first.",
                ephemeral=True,
            )
            return

        if not 1 <= intensity <= 10:
            await interaction.response.send_message(
                "❌ Intensity must be between **1** and **10**.", ephemeral=True
            )
            return

        guild = interaction.guild
        assert guild is not None

        targets = [
            m
            for m in guild.members
            if not m.bot
            and m.id != interaction.user.id
            and (not skip_admins or not m.guild_permissions.administrator)
        ]

        if not targets:
            await interaction.response.send_message(
                "No eligible members found to simulate banning.", ephemeral=True
            )
            return

        bot_state.reset()
        bot_state.rate_controller.set_intensity(intensity)
        bot_state.active_simulation = "banevery1"

        await interaction.response.send_message(
            f"🔨 **BAN SIMULATION STARTED**\n"
            f"┣ Targets   : `{len(targets)} members`\n"
            f"┣ Intensity : `{intensity}/10`\n"
            f"┣ Skip admins: `{skip_admins}`\n"
            f"┣ Ban gap   : `{bot_state.rate_controller.get_delay()}s`\n"
            f"┗ Use `/stop` to halt immediately.",
        )

        task = asyncio.create_task(self._run_bans(guild, targets))
        bot_state.add_task(task)

    async def _run_bans(
        self, guild: discord.Guild, members: list[discord.Member]
    ) -> None:
        """Ban each member one-by-one at the configured rate."""
        try:
            for member in members:
                if bot_state.stop_event.is_set():
                    break
                try:
                    await guild.ban(
                        member,
                        reason="[RAID TEST] BanEvery1 simulation",
                        delete_message_days=0,
                    )
                except discord.HTTPException:
                    pass
                await asyncio.sleep(bot_state.rate_controller.get_delay())
        except asyncio.CancelledError:
            pass
        finally:
            if bot_state.active_simulation == "banevery1":
                bot_state.active_simulation = None

    @banevery1.error
    async def banevery1_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "❌ You need **Administrator** permission to run simulations.",
                ephemeral=True,
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Ban(bot))
