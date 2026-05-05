"""
spam.py — /spamchannels: floods every channel with multiple webhooks simultaneously.
"""

import asyncio

import discord
from discord import app_commands
from discord.ext import commands

from utils.state import bot_state

RAID_TAG  = "EoN"
RAID_LINK = "https://discord.gg/h9UuKHYmfj"

SPAM_MSGS = [
    f"@everyone 💀 **{RAID_TAG}** 💀 {RAID_LINK}",
    f"@everyone Server owned by {RAID_TAG} {RAID_LINK}",
    f"@everyone 🔥 {RAID_TAG} was here 🔥 {RAID_LINK}",
    f"@everyone Your anti-raid failed. {RAID_LINK}",
    f"@everyone ⚔️ {RAID_TAG} RAID ⚔️ {RAID_LINK}",
    f"@everyone Join the real server: {RAID_LINK}",
    f"@everyone {RAID_TAG} {RAID_TAG} {RAID_TAG} {RAID_LINK}",
]

WEBHOOKS_PER_CHANNEL = 5
MSGS_PER_WEBHOOK     = 25
SEM_WEBHOOK          = 10


class Spam(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="spamchannels",
        description="Flood every text channel with multiple webhooks simultaneously.",
    )
    @app_commands.describe(
        intensity="Speed 1–10. Default 8.",
        webhooks_per_channel="Webhooks per channel. Default 5.",
        msgs_per_webhook="Messages each webhook sends. Default 25.",
        target_channel="Spam one specific channel only (optional).",
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    async def spamchannels(
        self,
        interaction: discord.Interaction,
        intensity: int = 8,
        webhooks_per_channel: int = WEBHOOKS_PER_CHANNEL,
        msgs_per_webhook: int = MSGS_PER_WEBHOOK,
        target_channel: discord.TextChannel | None = None,
    ) -> None:
        if bot_state.active_simulation:
            await interaction.response.send_message(
                f"⚠️ **{bot_state.active_simulation}** is running. Use `/stop` first.",
                ephemeral=True,
            )
            return

        if not 1 <= intensity <= 10:
            await interaction.response.send_message("❌ Intensity must be 1–10.", ephemeral=True)
            return

        guild = interaction.guild
        assert guild is not None

        channels = (
            [target_channel]
            if target_channel
            else [ch for ch in guild.text_channels if ch.permissions_for(guild.me).send_messages]
        )

        if not channels:
            await interaction.response.send_message("❌ No accessible text channels found.", ephemeral=True)
            return

        bot_state.reset()
        bot_state.rate_controller.set_intensity(intensity)
        bot_state.active_simulation = "spamchannels"

        total_streams  = len(channels) * webhooks_per_channel
        total_messages = total_streams * msgs_per_webhook

        await interaction.response.send_message(
            f"💬 **SPAM INITIATED — {RAID_TAG}**\n"
            f"┣ Channels       : `{len(channels)}`\n"
            f"┣ Webhooks/chan   : `{webhooks_per_channel}`\n"
            f"┣ Total streams  : `{total_streams}`\n"
            f"┣ Total messages : `~{total_messages}`\n"
            f"┣ Intensity      : `{intensity}/10`\n"
            f"┗ `/stop` halts immediately.",
        )

        task = asyncio.create_task(self._run(channels, webhooks_per_channel, msgs_per_webhook))
        bot_state.add_task(task)

    async def _run(
        self,
        channels: list[discord.TextChannel],
        webhooks_per: int,
        msgs_per: int,
    ) -> None:
        try:
            await asyncio.gather(
                *[self._flood_channel(ch, webhooks_per, msgs_per) for ch in channels],
                return_exceptions=True,
            )
        except asyncio.CancelledError:
            pass
        finally:
            if bot_state.active_simulation == "spamchannels":
                bot_state.active_simulation = None

    async def _flood_channel(
        self, channel: discord.TextChannel, webhooks_per: int, msgs_per: int
    ) -> None:
        """Create multiple webhooks in the channel and fire them all at once."""
        sem = asyncio.Semaphore(SEM_WEBHOOK)

        # Create webhooks concurrently
        results = await asyncio.gather(
            *[self._create_webhook(channel, i, sem) for i in range(webhooks_per)],
            return_exceptions=True,
        )
        webhooks = [w for w in results if isinstance(w, discord.Webhook)]

        if not webhooks:
            await self._direct_spam(channel, msgs_per)
            return

        # All webhooks fire concurrently
        await asyncio.gather(
            *[self._pump_webhook(wh, msgs_per) for wh in webhooks],
            return_exceptions=True,
        )

        # Cleanup
        await asyncio.gather(
            *[self._delete_webhook(wh) for wh in webhooks],
            return_exceptions=True,
        )

    async def _create_webhook(
        self, channel: discord.TextChannel, idx: int, sem: asyncio.Semaphore
    ):
        async with sem:
            try:
                return await channel.create_webhook(name=f"{RAID_TAG}-spam-{idx}")
            except discord.HTTPException as e:
                return e

    async def _delete_webhook(self, webhook: discord.Webhook) -> None:
        try:
            await webhook.delete()
        except discord.HTTPException:
            pass

    async def _pump_webhook(self, webhook: discord.Webhook, count: int) -> None:
        for i in range(count):
            if bot_state.stop_event.is_set():
                break
            try:
                await webhook.send(
                    SPAM_MSGS[i % len(SPAM_MSGS)],
                    username=f"{RAID_TAG} Raider",
                    allowed_mentions=discord.AllowedMentions(everyone=True),
                )
            except discord.HTTPException:
                pass
            await asyncio.sleep(bot_state.rate_controller.get_delay())

    async def _direct_spam(self, channel: discord.TextChannel, count: int) -> None:
        for i in range(count):
            if bot_state.stop_event.is_set():
                break
            try:
                await channel.send(
                    SPAM_MSGS[i % len(SPAM_MSGS)],
                    allowed_mentions=discord.AllowedMentions(everyone=True),
                )
            except discord.HTTPException:
                pass
            await asyncio.sleep(bot_state.rate_controller.get_delay())

    @spamchannels.error
    async def spamchannels_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message("❌ You need **Administrator** permission.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Spam(bot))
