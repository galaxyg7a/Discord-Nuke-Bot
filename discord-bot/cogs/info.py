"""
info.py — LAST STAND | /listservers, /leaveallservers, /deleteroles
"""

import asyncio

import discord
from discord import app_commands
from discord.ext import commands

from utils.bypass import ROUTE_CHANNEL_DELETE, ROUTE_ROLE_DELETE
from utils.state import bot_state

RAID_TAG       = "LAST STAND"
LEAVE_PASSWORD = "hellonice"


class Info(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ── /listservers ───────────────────────────────────────────────────────────
    @app_commands.command(
        name="listservers",
        description="📋 List all servers this bot is in with permission info.",
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    async def listservers(self, interaction: discord.Interaction) -> None:
        guilds = self.bot.guilds
        if not guilds:
            await interaction.response.send_message("Bot is not in any servers.", ephemeral=True)
            return

        lines: list[str] = [f"**Bot is in {len(guilds)} server(s):**\n"]
        for guild in guilds:
            me = guild.get_member(self.bot.user.id)
            if not me:
                continue

            gp = me.guild_permissions
            perms: list[str] = []
            if gp.administrator:        perms.append("Admin")
            if gp.ban_members:          perms.append("Ban")
            if gp.kick_members:         perms.append("Kick")
            if gp.manage_channels:      perms.append("Channels")
            if gp.manage_roles:         perms.append("Roles")
            if gp.manage_nicknames:     perms.append("Nicks")
            if gp.manage_emojis:        perms.append("Emojis")
            if gp.manage_guild:         perms.append("ManageServer")
            if gp.moderate_members:     perms.append("Timeout")

            perm_str  = ", ".join(perms) if perms else "No key perms"
            marker    = "◀ **HERE**" if guild.id == (interaction.guild_id or 0) else ""
            lines.append(
                f"**{guild.name}** {marker}\n"
                f"  ID: `{guild.id}` | Members: `{guild.member_count}`\n"
                f"  Perms: `{perm_str}`"
            )

        # Discord message limit: 2000 chars — split if needed
        chunks: list[str] = []
        current = ""
        for line in lines:
            if len(current) + len(line) + 1 > 1900:
                chunks.append(current)
                current = line
            else:
                current += "\n" + line if current else line
        if current:
            chunks.append(current)

        await interaction.response.send_message(chunks[0], ephemeral=True)
        for chunk in chunks[1:]:
            await interaction.followup.send(chunk, ephemeral=True)

    # ── /leaveallservers ───────────────────────────────────────────────────────
    @app_commands.command(
        name="leaveallservers",
        description="🚪 Leave every server the bot is in except the current one. Requires password.",
    )
    @app_commands.describe(
        password="Required password to run this command.",
        leave_current="Also leave the current server. Default False.",
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    async def leaveallservers(
        self,
        interaction: discord.Interaction,
        password: str,
        leave_current: bool = False,
    ) -> None:
        if password != LEAVE_PASSWORD:
            await interaction.response.send_message("❌ Wrong password.", ephemeral=True)
            return

        current_guild_id = interaction.guild_id
        guilds_to_leave = [
            g for g in self.bot.guilds
            if leave_current or g.id != current_guild_id
        ]

        if not guilds_to_leave:
            await interaction.response.send_message(
                "No servers to leave (only in this one).", ephemeral=True
            )
            return

        await interaction.response.send_message(
            f"🚪 **Leaving {len(guilds_to_leave)} server(s)…**",
            ephemeral=True,
        )

        left = 0
        for guild in guilds_to_leave:
            try:
                await guild.leave()
                left += 1
            except Exception:
                pass
            await asyncio.sleep(1)

        try:
            await interaction.followup.send(
                f"🚪 Done. Left `{left}/{len(guilds_to_leave)}` servers.",
                ephemeral=True,
            )
        except Exception:
            pass

    # ── /deleteroles ───────────────────────────────────────────────────────────
    @app_commands.command(
        name="deleteroles",
        description="🗑️ Delete all deletable roles in this server.",
    )
    @app_commands.describe(
        keep_managed="Keep integration-managed roles (bots, boosts). Default True.",
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    async def deleteroles(
        self,
        interaction: discord.Interaction,
        keep_managed: bool = True,
    ) -> None:
        if bot_state.active_simulation:
            await interaction.response.send_message(
                f"⚠️ **{bot_state.active_simulation}** running. Use `/stop` first.",
                ephemeral=True,
            )
            return

        guild = interaction.guild
        assert guild is not None
        me    = guild.me

        targets = [
            r for r in guild.roles
            if r.name != "@everyone"
            and r.position < me.top_role.position
            and not r.is_default()
            and (not keep_managed or not r.managed)
        ]

        if not targets:
            await interaction.response.send_message(
                "⚠️ No deletable roles found.", ephemeral=True
            )
            return

        bot_state.reset()
        bot_state.bypass.configure(10)
        bot_state.active_simulation = "deleteroles"

        await interaction.response.send_message(
            f"🗑️ **DELETE ROLES — {RAID_TAG}**\n"
            f"┣ Roles to delete : `{len(targets)}`\n"
            f"┣ Keep managed    : `{'✅' if keep_managed else '❌'}`\n"
            f"┗ `/stop` cancels."
        )

        task = asyncio.create_task(self._run_deleteroles(interaction, targets))
        bot_state.add_task(task)

    async def _run_deleteroles(
        self,
        interaction: discord.Interaction,
        roles: list[discord.Role],
    ) -> None:
        sem = asyncio.Semaphore(20)
        results = await asyncio.gather(
            *[self._delete_role(r, sem) for r in roles],
            return_exceptions=True,
        )
        deleted = sum(1 for r in results if r is True)
        failed  = len(roles) - deleted

        try:
            if bot_state.active_simulation == "deleteroles":
                bot_state.active_simulation = None
            await interaction.followup.send(
                f"🗑️ **Delete roles complete — {RAID_TAG}**\n"
                f"┣ Deleted : `{deleted}/{len(roles)}`\n"
                f"┗ Failed  : `{failed}` (managed / higher role)"
            )
        except Exception:
            pass

    async def _delete_role(self, role: discord.Role, sem: asyncio.Semaphore) -> bool:
        if bot_state.stop_event.is_set():
            return False
        async with sem:
            if bot_state.stop_event.is_set():
                return False
            return await bot_state.bypass.execute(
                ROUTE_ROLE_DELETE,
                lambda r=role: r.delete(reason=f"Role wipe — {RAID_TAG}"),
                bot_state.stop_event,
            ) is not None

    # ── Error handlers ─────────────────────────────────────────────────────────
    @listservers.error
    @leaveallservers.error
    @deleteroles.error
    async def _missing_perms(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "❌ You need **Administrator** permission.", ephemeral=True
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Info(bot))
