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

        # Check bot has the permissions it actually needs before doing anything
        me = guild.me
        missing: list[str] = []
        if not me.guild_permissions.ban_members:
            missing.append("Ban Members")
        if kick_first and not me.guild_permissions.kick_members:
            missing.append("Kick Members")
        if missing:
            await interaction.response.send_message(
                f"❌ Bot is missing permissions: **{', '.join(missing)}**\n"
                "Grant these in Server Settings → Roles.",
                ephemeral=True,
            )
            return

        # Defer so we have time to chunk members (can take a second on large servers)
        await interaction.response.defer()

        # Force-fetch all members — guild.members is often empty without this
        try:
            await guild.chunk(cache=True)
        except Exception:
            pass  # Best-effort; carry on with whatever is cached

        targets = [
            m for m in guild.members
            if not m.bot
            and m.id != interaction.user.id
            and m.id != me.id
            and (not skip_admins or not m.guild_permissions.administrator)
            # Can't ban/kick members with equal or higher top role
            and m.top_role < me.top_role
        ]

        if not targets:
            await interaction.followup.send(
                "⚠️ No eligible targets found.\n"
                "Possible reasons:\n"
                "• All members have a role equal to or higher than the bot's role\n"
                "• Members Intent is not enabled in the Discord Developer Portal\n"
                "• Server has no non-bot members other than you",
                ephemeral=True,
            )
            return

        bot_state.reset()
        bot_state.rate_controller.set_intensity(intensity)
        bot_state.active_simulation = "banevery1"

        await interaction.followup.send(
            f"☠️ **MASS BAN+KICK — {RAID_TAG}**\n"
            f"┣ Targets    : `{len(targets)}`\n"
            f"┣ Intensity  : `{intensity}/10`\n"
            f"┣ Kick first : `{'✅' if kick_first else '❌'}`\n"
            f"┣ Skip admins: `{'✅' if skip_admins else '❌'}`\n"
            f"┗ `/stop` halts immediately.",
        )

        task = asyncio.create_task(self._run(interaction, guild, targets, kick_first))
        bot_state.add_task(task)

    async def _run(
        self,
        interaction: discord.Interaction,
        guild: discord.Guild,
        members: list[discord.Member],
        kick_first: bool,
    ) -> None:
        sem_ban  = asyncio.Semaphore(SEM_BAN)
        sem_kick = asyncio.Semaphore(SEM_KICK)
        kicked = 0
        banned = 0
        failed = 0

        try:
            if kick_first:
                kick_results = await asyncio.gather(
                    *[self._kick_one(guild, m, sem_kick) for m in members],
                    return_exceptions=True,
                )
                kicked = sum(1 for r in kick_results if r is True)

            ban_results = await asyncio.gather(
                *[self._ban_one(guild, m, sem_ban) for m in members],
                return_exceptions=True,
            )
            banned = sum(1 for r in ban_results if r is True)
            failed = len(members) - banned

        except asyncio.CancelledError:
            pass
        finally:
            if bot_state.active_simulation == "banevery1":
                bot_state.active_simulation = None

            # Send a completion report
            try:
                summary = (
                    f"☠️ **Ban complete — {RAID_TAG}**\n"
                    f"┣ Banned  : `{banned}/{len(members)}`\n"
                )
                if kick_first:
                    summary += f"┣ Kicked  : `{kicked}/{len(members)}`\n"
                summary += f"┗ Failed  : `{failed}` (higher role / already gone)"
                await interaction.followup.send(summary)
            except Exception:
                pass

    async def _kick_one(self, guild: discord.Guild, member: discord.Member, sem: asyncio.Semaphore) -> bool:
        if bot_state.stop_event.is_set():
            return False
        async with sem:
            if bot_state.stop_event.is_set():
                return False
            try:
                await guild.kick(member, reason=f"Stress-test by {RAID_TAG}")
                return True
            except discord.HTTPException:
                return False

    async def _ban_one(self, guild: discord.Guild, member: discord.Member, sem: asyncio.Semaphore) -> bool:
        if bot_state.stop_event.is_set():
            return False
        async with sem:
            if bot_state.stop_event.is_set():
                return False
            try:
                await guild.ban(member, reason=f"Stress-test by {RAID_TAG}", delete_message_days=0)
                return True
            except discord.HTTPException:
                return False

    @banevery1.error
    async def banevery1_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message("❌ You need **Administrator** permission.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Ban(bot))
