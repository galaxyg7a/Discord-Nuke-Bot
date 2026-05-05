import asyncio
import time

import discord
from discord import app_commands
from discord.ext import commands

from utils.state import bot_state


class Raid(commands.Cog):
    """Simulates a raid by rapidly creating and deleting channels and roles."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="raid",
        description="[TEST ONLY] Simulate a raid: creates/deletes channels & roles rapidly.",
    )
    @app_commands.describe(
        intensity="Attack intensity 1 (slow) – 10 (fastest). Default: 5.",
        duration="How long to run the simulation in seconds. Default: 30.",
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    async def raid(
        self,
        interaction: discord.Interaction,
        intensity: int = 5,
        duration: int = 30,
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

        if duration < 1:
            await interaction.response.send_message(
                "❌ Duration must be at least 1 second.", ephemeral=True
            )
            return

        bot_state.reset()
        bot_state.rate_controller.set_intensity(intensity)
        bot_state.active_simulation = "raid"

        await interaction.response.send_message(
            f"🚨 **RAID SIMULATION STARTED**\n"
            f"┣ Intensity : `{intensity}/10`\n"
            f"┣ Duration  : `{duration}s`\n"
            f"┣ Action gap: `{bot_state.rate_controller.get_delay()}s`\n"
            f"┗ Use `/stop` to halt immediately.",
        )

        task = asyncio.create_task(
            self._run_raid(interaction.guild, duration)  # type: ignore[arg-type]
        )
        bot_state.add_task(task)

    async def _run_raid(self, guild: discord.Guild, duration: int) -> None:
        """Create / delete channels and roles in a tight loop until stopped or timed out."""
        end_time = time.monotonic() + duration
        created_channels: list[discord.TextChannel] = []
        created_roles: list[discord.Role] = []
        counter = 0

        try:
            while not bot_state.stop_event.is_set() and time.monotonic() < end_time:
                delay = bot_state.rate_controller.get_delay()
                counter += 1
                tag = counter % 9999

                # --- Create channel ---
                try:
                    ch = await guild.create_text_channel(f"raid-test-{tag}")
                    created_channels.append(ch)
                except discord.HTTPException:
                    pass

                await asyncio.sleep(delay)
                if bot_state.stop_event.is_set():
                    break

                # --- Delete oldest created channel ---
                if created_channels:
                    ch = created_channels.pop(0)
                    try:
                        await ch.delete(reason="[RAID TEST] cleanup")
                    except discord.HTTPException:
                        pass

                await asyncio.sleep(delay)
                if bot_state.stop_event.is_set():
                    break

                # --- Create role ---
                try:
                    role = await guild.create_role(name=f"raid-role-{tag}")
                    created_roles.append(role)
                except discord.HTTPException:
                    pass

                await asyncio.sleep(delay)
                if bot_state.stop_event.is_set():
                    break

                # --- Delete oldest created role ---
                if created_roles:
                    role = created_roles.pop(0)
                    try:
                        await role.delete(reason="[RAID TEST] cleanup")
                    except discord.HTTPException:
                        pass

                await asyncio.sleep(delay)

        except asyncio.CancelledError:
            pass
        finally:
            # Best-effort cleanup of any leftover test resources
            for ch in created_channels:
                try:
                    await ch.delete(reason="[RAID TEST] final cleanup")
                except discord.HTTPException:
                    pass
            for role in created_roles:
                try:
                    await role.delete(reason="[RAID TEST] final cleanup")
                except discord.HTTPException:
                    pass

            if bot_state.active_simulation == "raid":
                bot_state.active_simulation = None

    @raid.error
    async def raid_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "❌ You need **Administrator** permission to run simulations.",
                ephemeral=True,
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Raid(bot))
