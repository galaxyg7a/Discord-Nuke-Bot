"""
dm.py — LAST STAND | /massdm: mass DM all server members with text and/or embed.
"""

import asyncio
import random

import discord
from discord import app_commands
from discord.ext import commands

from utils.bypass import BypassEngine, ROUTE_MEMBER_EDIT
from utils.state import bot_state

RAID_TAG  = "LAST STAND"
RAID_LINK = "https://discord.gg/s59zWvzK6c"

SEM_DM = 8  # Discord aggressively rate-limits DMs — keep concurrency low


class DM(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ── /massdm ────────────────────────────────────────────────────────────────
    @app_commands.command(
        name="massdm",
        description="📨 Mass DM every member in this server (text and/or embed).",
    )
    @app_commands.describe(
        message="Text content to send. Leave blank to send embed only.",
        embed_title="Embed title (leave blank for no embed).",
        embed_description="Embed description text.",
        embed_color="Embed color as hex (e.g. FF0000 for red). Default: red.",
        intensity="DM send rate 1–10. Keep low (1–3) to avoid account flags. Default 3.",
        skip_bots="Skip bot accounts. Default True.",
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    async def massdm(
        self,
        interaction: discord.Interaction,
        message: str = "",
        embed_title: str = "",
        embed_description: str = "",
        embed_color: str = "FF0000",
        intensity: int = 3,
        skip_bots: bool = True,
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

        if not message and not embed_title and not embed_description:
            await interaction.response.send_message(
                "❌ Provide at least a `message` or `embed_title`/`embed_description`.",
                ephemeral=True,
            )
            return

        guild = interaction.guild
        assert guild is not None

        await interaction.response.defer()

        try:
            try:
                await asyncio.wait_for(guild.chunk(cache=True), timeout=10.0)
            except Exception:
                pass

            targets = [
                m for m in guild.members
                if m.id != interaction.user.id
                and m.id != self.bot.user.id
                and (not skip_bots or not m.bot)
            ]

            if not targets:
                await interaction.followup.send("⚠️ No eligible members found.", ephemeral=True)
                return

            embed: discord.Embed | None = None
            if embed_title or embed_description:
                try:
                    color_int = int(embed_color.lstrip("#"), 16)
                except ValueError:
                    color_int = 0xFF0000
                embed = discord.Embed(
                    title=embed_title or discord.utils.MISSING,
                    description=embed_description or discord.utils.MISSING,
                    colour=discord.Colour(color_int),
                )
                embed.set_footer(text=f"{RAID_TAG} | {RAID_LINK}")

            bot_state.reset()
            bot_state.rate_controller.set_intensity(intensity)
            bot_state.active_simulation = "massdm"

            has_embed = embed is not None
            await interaction.followup.send(
                f"📨 **MASS DM — {RAID_TAG}**\n"
                f"┣ Targets   : `{len(targets)}`\n"
                f"┣ Text msg  : `{'✅' if message else '❌'}`\n"
                f"┣ Embed     : `{'✅' if has_embed else '❌'}`\n"
                f"┣ Intensity : `{intensity}/10`\n"
                f"┗ `/stop` halts immediately."
            )

            task = asyncio.create_task(
                self._run(interaction, targets, message or None, embed)
            )
            bot_state.add_task(task)

        except Exception as exc:
            bot_state.active_simulation = None
            try:
                await interaction.followup.send(f"❌ Error: `{exc}`", ephemeral=True)
            except Exception:
                pass

    async def _run(
        self,
        interaction: discord.Interaction,
        members: list[discord.Member],
        text: str | None,
        embed: discord.Embed | None,
    ) -> None:
        sem = asyncio.Semaphore(SEM_DM)
        results = await asyncio.gather(
            *[self._dm_one(m, text, embed, sem) for m in members],
            return_exceptions=True,
        )
        sent   = sum(1 for r in results if r is True)
        failed = len(members) - sent

        try:
            if bot_state.active_simulation == "massdm":
                bot_state.active_simulation = None
            await interaction.followup.send(
                f"📨 **Mass DM complete — {RAID_TAG}**\n"
                f"┣ Sent   : `{sent}/{len(members)}`\n"
                f"┗ Failed : `{failed}` (DMs closed / blocked)"
            )
        except Exception:
            pass

    async def _dm_one(
        self,
        member: discord.Member,
        text: str | None,
        embed: discord.Embed | None,
        sem: asyncio.Semaphore,
    ) -> bool:
        if bot_state.stop_event.is_set():
            return False
        async with sem:
            if bot_state.stop_event.is_set():
                return False
            # Small random delay between DMs to avoid instant account flags
            delay = bot_state.rate_controller.get_delay()
            if delay > 0:
                await asyncio.sleep(delay + random.uniform(0.1, 0.5))
            try:
                kwargs: dict = {}
                if text:
                    kwargs["content"] = text
                if embed:
                    kwargs["embed"] = embed
                await member.send(**kwargs)
                return True
            except discord.HTTPException:
                return False

    @massdm.error
    async def massdm_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message("❌ You need **Administrator** permission.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(DM(bot))
