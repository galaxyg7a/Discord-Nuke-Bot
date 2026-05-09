"""
tools.py — LAST STAND | /massnick, /prune, /shufflechannels, /renamechannels, /renameroles
Features ported from Thallium Nuker feature list (Thallium had no source — reimplemented natively).
"""

import asyncio
import random

import discord
from discord import app_commands
from discord.ext import commands

from utils.state import bot_state

RAID_TAG = "LAST STAND"
RAIDER   = "JEAN(LORENZO)"


class Tools(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ── /massnick ──────────────────────────────────────────────────────────────
    @app_commands.command(
        name="massnick",
        description="✏️ Set every member's nickname simultaneously.",
    )
    @app_commands.describe(
        nickname="Nickname to apply. Leave blank to clear all nicknames.",
        intensity="Concurrency 1–10. Default 10.",
    )
    @app_commands.guild_only()
    async def massnick(
        self,
        interaction: discord.Interaction,
        nickname: str = "",
        intensity: int = 10,
    ) -> None:
        if bot_state.active_simulation:
            await interaction.response.send_message(
                f"⚠️ **{bot_state.active_simulation}** is running. Use `/stop` first.",
                ephemeral=True,
            )
            return

        guild = interaction.guild
        if not guild.me.guild_permissions.manage_nicknames:
            await interaction.response.send_message(
                "❌ Bot is missing **Manage Nicknames** permission.", ephemeral=True
            )
            return

        await interaction.response.defer()

        try:
            await asyncio.wait_for(guild.chunk(cache=True), timeout=10.0)
        except Exception:
            pass

        me = guild.me
        targets = [
            m for m in guild.members
            if not m.bot
            and m.id != interaction.user.id
            and m.id != me.id
            and m.top_role < me.top_role
        ]

        if not targets:
            await interaction.followup.send("⚠️ No eligible members found.", ephemeral=True)
            return

        bot_state.reset()
        bot_state.active_simulation = "massnick"

        display_nick = f"`{nickname}`" if nickname else "`[cleared]`"
        await interaction.followup.send(
            f"✏️ **MASS NICK — {RAID_TAG}**\n"
            f"┣ Targets  : `{len(targets)}`\n"
            f"┣ Nickname : {display_nick}\n"
            f"┗ `/stop` halts immediately."
        )

        task = asyncio.create_task(
            self._run_massnick(interaction, targets, nickname or None)
        )
        bot_state.add_task(task)

    async def _run_massnick(
        self,
        interaction: discord.Interaction,
        members: list[discord.Member],
        nickname: str | None,
    ) -> None:
        sem = asyncio.Semaphore(25)

        async def _nick_one(m: discord.Member) -> bool:
            if bot_state.stop_event.is_set():
                return False
            async with sem:
                if bot_state.stop_event.is_set():
                    return False
                try:
                    await m.edit(nick=nickname)
                    return True
                except discord.HTTPException:
                    return False

        try:
            results = await asyncio.gather(
                *[_nick_one(m) for m in members], return_exceptions=True
            )
            done   = sum(1 for r in results if r is True)
            failed = len(members) - done
        except asyncio.CancelledError:
            done = failed = 0
        finally:
            if bot_state.active_simulation == "massnick":
                bot_state.active_simulation = None

        try:
            label = f"`{nickname}`" if nickname else "`[cleared]`"
            await interaction.followup.send(
                f"✏️ **Mass nick complete — {RAID_TAG}**\n"
                f"┣ Nickname : {label}\n"
                f"┣ Done     : `{done}/{len(members)}`\n"
                f"┗ Failed   : `{failed}` (higher role / missing perm)"
            )
        except Exception:
            pass

    # ── /prune ─────────────────────────────────────────────────────────────────
    @app_commands.command(
        name="prune",
        description="🔪 Kick members who have been inactive for X days (no messages/roles).",
    )
    @app_commands.describe(
        days="Inactivity threshold in days (1–30). Default 1.",
        dry_run="Count how many would be pruned without actually kicking. Default False.",
    )
    @app_commands.guild_only()
    async def prune(
        self,
        interaction: discord.Interaction,
        days: int = 1,
        dry_run: bool = False,
    ) -> None:
        if not 1 <= days <= 30:
            await interaction.response.send_message(
                "❌ Days must be 1–30.", ephemeral=True
            )
            return

        guild = interaction.guild
        if not guild.me.guild_permissions.kick_members:
            await interaction.response.send_message(
                "❌ Bot is missing **Kick Members** permission.", ephemeral=True
            )
            return

        await interaction.response.defer()

        try:
            estimate = await guild.estimate_pruned_members(days=days)
        except discord.HTTPException as exc:
            await interaction.followup.send(f"❌ Failed to estimate prune: `{exc}`", ephemeral=True)
            return

        if dry_run:
            await interaction.followup.send(
                f"🔪 **PRUNE ESTIMATE — {RAID_TAG}**\n"
                f"┣ Days     : `{days}`\n"
                f"┣ Would kick : `{estimate}` members\n"
                f"┗ Run without `dry_run=True` to actually kick."
            )
            return

        try:
            pruned = await guild.prune_members(
                days=days,
                reason=f"Mass prune — {RAID_TAG}",
                compute_prune_count=True,
            )
        except discord.HTTPException as exc:
            await interaction.followup.send(f"❌ Prune failed: `{exc}`", ephemeral=True)
            return

        await interaction.followup.send(
            f"🔪 **PRUNE COMPLETE — {RAID_TAG}**\n"
            f"┣ Days    : `{days}`\n"
            f"┗ Kicked  : `{pruned}` inactive members"
        )

    # ── /shufflechannels ───────────────────────────────────────────────────────
    @app_commands.command(
        name="shufflechannels",
        description="🔀 Randomize the position of every channel to cause maximum confusion.",
    )
    @app_commands.guild_only()
    async def shufflechannels(self, interaction: discord.Interaction) -> None:
        if bot_state.active_simulation:
            await interaction.response.send_message(
                f"⚠️ **{bot_state.active_simulation}** is running. Use `/stop` first.",
                ephemeral=True,
            )
            return

        guild = interaction.guild
        if not guild.me.guild_permissions.manage_channels:
            await interaction.response.send_message(
                "❌ Bot is missing **Manage Channels** permission.", ephemeral=True
            )
            return

        channels = [
            ch for ch in guild.channels
            if isinstance(ch, (discord.TextChannel, discord.VoiceChannel, discord.CategoryChannel))
        ]

        if not channels:
            await interaction.response.send_message("⚠️ No channels to shuffle.", ephemeral=True)
            return

        await interaction.response.defer()

        bot_state.reset()
        bot_state.active_simulation = "shufflechannels"

        await interaction.followup.send(
            f"🔀 **SHUFFLE CHANNELS — {RAID_TAG}**\n"
            f"┣ Channels : `{len(channels)}`\n"
            f"┗ Randomising positions…"
        )

        task = asyncio.create_task(
            self._run_shuffle(interaction, guild, channels)
        )
        bot_state.add_task(task)

    async def _run_shuffle(
        self,
        interaction: discord.Interaction,
        guild: discord.Guild,
        channels: list,
    ) -> None:
        sem = asyncio.Semaphore(10)
        positions = list(range(len(channels)))
        random.shuffle(positions)

        async def _move(ch, pos: int) -> bool:
            if bot_state.stop_event.is_set():
                return False
            async with sem:
                if bot_state.stop_event.is_set():
                    return False
                try:
                    await ch.edit(position=pos)
                    return True
                except discord.HTTPException:
                    return False

        try:
            results = await asyncio.gather(
                *[_move(ch, pos) for ch, pos in zip(channels, positions)],
                return_exceptions=True,
            )
            done = sum(1 for r in results if r is True)
        except asyncio.CancelledError:
            done = 0
        finally:
            if bot_state.active_simulation == "shufflechannels":
                bot_state.active_simulation = None

        try:
            await interaction.followup.send(
                f"🔀 **Shuffle complete — {RAID_TAG}**\n"
                f"┗ Moved : `{done}/{len(channels)}` channels"
            )
        except Exception:
            pass

    # ── /renamechannels ────────────────────────────────────────────────────────
    @app_commands.command(
        name="renamechannels",
        description="📝 Rename every channel to a given name (appends -1, -2 …).",
    )
    @app_commands.describe(
        name="Base name to apply. Default: 'jean-lorenzo-raided'.",
    )
    @app_commands.guild_only()
    async def renamechannels(
        self,
        interaction: discord.Interaction,
        name: str = "jean-lorenzo-raided",
    ) -> None:
        if bot_state.active_simulation:
            await interaction.response.send_message(
                f"⚠️ **{bot_state.active_simulation}** is running. Use `/stop` first.",
                ephemeral=True,
            )
            return

        guild = interaction.guild
        if not guild.me.guild_permissions.manage_channels:
            await interaction.response.send_message(
                "❌ Bot is missing **Manage Channels** permission.", ephemeral=True
            )
            return

        channels = [
            ch for ch in guild.channels
            if isinstance(ch, (discord.TextChannel, discord.VoiceChannel))
        ]

        if not channels:
            await interaction.response.send_message("⚠️ No channels to rename.", ephemeral=True)
            return

        await interaction.response.defer()

        bot_state.reset()
        bot_state.active_simulation = "renamechannels"

        await interaction.followup.send(
            f"📝 **RENAME CHANNELS — {RAID_TAG}**\n"
            f"┣ Channels : `{len(channels)}`\n"
            f"┣ Name     : `{name}-N`\n"
            f"┗ `/stop` halts immediately."
        )

        task = asyncio.create_task(
            self._run_renamechannels(interaction, channels, name)
        )
        bot_state.add_task(task)

    async def _run_renamechannels(
        self,
        interaction: discord.Interaction,
        channels: list,
        base_name: str,
    ) -> None:
        sem = asyncio.Semaphore(10)

        async def _rename(ch, i: int) -> bool:
            if bot_state.stop_event.is_set():
                return False
            async with sem:
                if bot_state.stop_event.is_set():
                    return False
                try:
                    await ch.edit(name=f"{base_name}-{i}")
                    return True
                except discord.HTTPException:
                    return False

        try:
            results = await asyncio.gather(
                *[_rename(ch, i) for i, ch in enumerate(channels, 1)],
                return_exceptions=True,
            )
            done   = sum(1 for r in results if r is True)
            failed = len(channels) - done
        except asyncio.CancelledError:
            done = failed = 0
        finally:
            if bot_state.active_simulation == "renamechannels":
                bot_state.active_simulation = None

        try:
            await interaction.followup.send(
                f"📝 **Rename channels complete — {RAID_TAG}**\n"
                f"┣ Renamed : `{done}/{len(channels)}`\n"
                f"┗ Failed  : `{failed}` (missing perm / rate limited)"
            )
        except Exception:
            pass

    # ── /renameroles ───────────────────────────────────────────────────────────
    @app_commands.command(
        name="renameroles",
        description="📝 Rename every role to a given name (appends -1, -2 …).",
    )
    @app_commands.describe(
        name="Base name to apply. Default: 'LS-RAIDED'.",
        keep_managed="Skip bot/integration roles. Default True.",
    )
    @app_commands.guild_only()
    async def renameroles(
        self,
        interaction: discord.Interaction,
        name: str = "LS-RAIDED",
        keep_managed: bool = True,
    ) -> None:
        if bot_state.active_simulation:
            await interaction.response.send_message(
                f"⚠️ **{bot_state.active_simulation}** is running. Use `/stop` first.",
                ephemeral=True,
            )
            return

        guild = interaction.guild
        me    = guild.me

        if not me.guild_permissions.manage_roles:
            await interaction.response.send_message(
                "❌ Bot is missing **Manage Roles** permission.", ephemeral=True
            )
            return

        targets = [
            r for r in guild.roles
            if r.name != "@everyone"
            and r.position < me.top_role.position
            and not r.is_default()
            and (not keep_managed or not r.managed)
        ]

        if not targets:
            await interaction.response.send_message("⚠️ No renameable roles found.", ephemeral=True)
            return

        await interaction.response.defer()

        bot_state.reset()
        bot_state.active_simulation = "renameroles"

        await interaction.followup.send(
            f"📝 **RENAME ROLES — {RAID_TAG}**\n"
            f"┣ Roles : `{len(targets)}`\n"
            f"┣ Name  : `{name}-N`\n"
            f"┗ `/stop` halts immediately."
        )

        task = asyncio.create_task(
            self._run_renameroles(interaction, targets, name)
        )
        bot_state.add_task(task)

    async def _run_renameroles(
        self,
        interaction: discord.Interaction,
        roles: list[discord.Role],
        base_name: str,
    ) -> None:
        sem = asyncio.Semaphore(15)

        async def _rename(r: discord.Role, i: int) -> bool:
            if bot_state.stop_event.is_set():
                return False
            async with sem:
                if bot_state.stop_event.is_set():
                    return False
                try:
                    await r.edit(name=f"{base_name}-{i}")
                    return True
                except discord.HTTPException:
                    return False

        try:
            results = await asyncio.gather(
                *[_rename(r, i) for i, r in enumerate(roles, 1)],
                return_exceptions=True,
            )
            done   = sum(1 for r in results if r is True)
            failed = len(roles) - done
        except asyncio.CancelledError:
            done = failed = 0
        finally:
            if bot_state.active_simulation == "renameroles":
                bot_state.active_simulation = None

        try:
            await interaction.followup.send(
                f"📝 **Rename roles complete — {RAID_TAG}**\n"
                f"┣ Renamed : `{done}/{len(roles)}`\n"
                f"┗ Failed  : `{failed}` (managed / higher role)"
            )
        except Exception:
            pass

    # ── error handlers ─────────────────────────────────────────────────────────
    @massnick.error
    @prune.error
    @shufflechannels.error
    @renamechannels.error
    @renameroles.error
    async def _missing_perms(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        msg = (
            "❌ You need **Administrator** permission."
            if isinstance(error, app_commands.MissingPermissions)
            else f"❌ {error}"
        )
        try:
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except Exception:
            pass


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Tools(bot))
