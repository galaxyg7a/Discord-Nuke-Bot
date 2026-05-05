import asyncio
import time

import discord
from discord import app_commands
from discord.ext import commands

from utils.state import bot_state

SPAM_MESSAGES = [
    "@everyone FREE NITRO CLICK HERE",
    "JOIN NOW: discord.gg/fake-raid-test",
    "YOU HAVE BEEN SELECTED FOR A GIVEAWAY",
    "RAID RAID RAID RAID RAID",
    "SERVER IS COMPROMISED - LEAVE NOW",
    "FREE DISCORD NITRO FOR EVERYONE!!!",
    "discord.gg/test discord.gg/test discord.gg/test",
    "ADMIN ABUSE ADMIN ABUSE ADMIN ABUSE",
    "BAN EVERYONE BAN EVERYONE BAN EVERYONE",
    "SERVER IS BEING TAKEN OVER",
]


class Spam(commands.Cog):
    """Floods every text channel with rapid-fire messages to test anti-spam filters."""

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="spamchannels",
        description="[TEST ONLY] Flood every text channel with messages to stress-test anti-spam.",
    )
    @app_commands.describe(
        intensity="Message rate intensity 1 (slow) – 10 (fastest). Default: 5.",
        messages_per_channel="Number of messages to send per channel. Default: 20.",
        target_channel="Spam a single channel instead of all channels (optional).",
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    async def spamchannels(
        self,
        interaction: discord.Interaction,
        intensity: int = 5,
        messages_per_channel: int = 20,
        target_channel: discord.TextChannel | None = None,
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

        if messages_per_channel < 1:
            await interaction.response.send_message(
                "❌ Messages per channel must be at least 1.", ephemeral=True
            )
            return

        guild = interaction.guild
        assert guild is not None

        if target_channel:
            channels = [target_channel]
        else:
            channels = [
                ch
                for ch in guild.text_channels
                if ch.permissions_for(guild.me).send_messages
            ]

        if not channels:
            await interaction.response.send_message(
                "❌ No text channels found where the bot has permission to send messages.",
                ephemeral=True,
            )
            return

        bot_state.reset()
        bot_state.rate_controller.set_intensity(intensity)
        bot_state.active_simulation = "spamchannels"

        total = len(channels) * messages_per_channel

        await interaction.response.send_message(
            f"💬 **SPAM SIMULATION STARTED**\n"
            f"┣ Channels  : `{len(channels)}`\n"
            f"┣ Msgs/chan  : `{messages_per_channel}`\n"
            f"┣ Total msgs: `{total}`\n"
            f"┣ Intensity : `{intensity}/10`\n"
            f"┣ Msg gap   : `{bot_state.rate_controller.get_delay()}s`\n"
            f"┗ Use `/stop` to halt immediately.",
        )

        task = asyncio.create_task(
            self._run_spam(channels, messages_per_channel)
        )
        bot_state.add_task(task)

    async def _run_spam(
        self,
        channels: list[discord.TextChannel],
        messages_per_channel: int,
    ) -> None:
        """Send spam messages across all channels concurrently."""
        try:
            tasks = [
                asyncio.create_task(self._spam_channel(ch, messages_per_channel))
                for ch in channels
            ]
            for task in tasks:
                bot_state.add_task(task)
            await asyncio.gather(*tasks, return_exceptions=True)
        except asyncio.CancelledError:
            pass
        finally:
            if bot_state.active_simulation == "spamchannels":
                bot_state.active_simulation = None

    async def _spam_channel(
        self, channel: discord.TextChannel, count: int
    ) -> None:
        """Flood a single channel with `count` messages."""
        try:
            for i in range(count):
                if bot_state.stop_event.is_set():
                    break
                msg = SPAM_MESSAGES[i % len(SPAM_MESSAGES)]
                try:
                    await channel.send(msg)
                except discord.HTTPException:
                    pass
                await asyncio.sleep(bot_state.rate_controller.get_delay())
        except asyncio.CancelledError:
            pass

    @spamchannels.error
    async def spamchannels_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "❌ You need **Administrator** permission to run simulations.",
                ephemeral=True,
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Spam(bot))
