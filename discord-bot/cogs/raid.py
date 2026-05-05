import asyncio
import time

import discord
from discord import app_commands
from discord.ext import commands

from utils.state import bot_state

SPAM_MESSAGES = [
    "@everyone FREE NITRO CLICK HERE",
    "JOIN NOW: discord.gg/fake-raid-test",
    "YOU HAVE BEEN SELECTED FOR A GIVEAWAY",
    "RAID RAID RAID RAID RAID",
    "SERVER IS COMPROMISED - LEAVE NOW",
    "FREE DISCORD NITRO FOR EVERYONE!!!",
    "discord.gg/test discord.gg/test discord.gg/test",
    "ADMIN ABUSE ADMIN ABUSE ADMIN ABUSE",
    "BAN EVERYONE BAN EVERYONE BAN EVERYONE",
    "SERVER IS BEING TAKEN OVER",
]


class Raid(commands.Cog):
    """
    Full raid simulation — runs all destructive actions simultaneously:
      • Creates & deletes channels in a loop
      • Creates & deletes roles in a loop
      • Floods every text channel with spam messages
      • Mass-bans all non-bot, non-admin members
    """

    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot

    # ------------------------------------------------------------------
    # /raid
    # ------------------------------------------------------------------
    @app_commands.command(
        name="raid",
        description="[TEST ONLY] Full raid: spam + channel flood + role flood + mass ban — all at once.",
    )
    @app_commands.describe(
        intensity="Attack intensity 1 (slow) – 10 (max). Default: 5.",
        duration="Seconds to run channel/role/spam loops. Default: 30.",
        ban_members="Also mass-ban all non-admin, non-bot members. Default: True.",
        skip_admins="Skip administrators when banning. Default: True.",
        messages_per_channel="Spam messages to send per channel. Default: 20.",
    )
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.guild_only()
    async def raid(
        self,
        interaction: discord.Interaction,
        intensity: int = 5,
        duration: int = 30,
        ban_members: bool = True,
        skip_admins: bool = True,
        messages_per_channel: int = 20,
    ) -> None:
        if bot_state.active_simulation:
            await interaction.response.send_message(
                f"⚠️ Simulation **{bot_state.active_simulation}** is already running. "
                "Use `/stop` to halt it first.",
                ephemeral=True,
            )
            return

        if not 1 <= intensity <= 10:
            await interaction.response.send_message(
                "❌ Intensity must be between **1** and **10**.", ephemeral=True
            )
            return

        guild = interaction.guild
        assert guild is not None

        bot_state.reset()
        bot_state.rate_controller.set_intensity(intensity)
        bot_state.active_simulation = "raid"

        # Collect ban targets before responding (guild.members may need to be fetched)
        ban_targets: list[discord.Member] = []
        if ban_members:
            ban_targets = [
                m for m in guild.members
                if not m.bot
                and m.id != interaction.user.id
                and (not skip_admins or not m.guild_permissions.administrator)
            ]

        spam_channels = [
            ch for ch in guild.text_channels
            if ch.permissions_for(guild.me).send_messages
        ]

        await interaction.response.send_message(
            f"🚨 **FULL RAID SIMULATION STARTED**\n"
            f"┣ Intensity      : `{intensity}/10`  ({bot_state.rate_controller.get_delay()}s gap)\n"
            f"┣ Duration       : `{duration}s` (channel/role/spam loops)\n"
            f"┣ Channel flood  : ✅ create → delete loop\n"
            f"┣ Role flood     : ✅ create → delete loop\n"
            f"┣ Spam channels  : ✅ `{len(spam_channels)} channels` × `{messages_per_channel} msgs`\n"
            f"┣ Mass ban       : {'✅ `' + str(len(ban_targets)) + ' members`' if ban_members else '❌ disabled'}\n"
            f"┗ Use `/stop` to halt everything immediately.",
        )

        # Launch all attack vectors concurrently
        tasks: list[asyncio.Task] = [
            asyncio.create_task(self._channel_flood(guild, duration)),
            asyncio.create_task(self._role_flood(guild, duration)),
            asyncio.create_task(self._spam_all_channels(spam_channels, messages_per_channel)),
        ]
        if ban_members and ban_targets:
            tasks.append(asyncio.create_task(self._mass_ban(guild, ban_targets)))

        for task in tasks:
            bot_state.add_task(task)

    # ------------------------------------------------------------------
    # Attack vectors
    # ------------------------------------------------------------------
    async def _channel_flood(self, guild: discord.Guild, duration: int) -> None:
        """Rapidly create and delete text channels for `duration` seconds."""
        end_time = time.monotonic() + duration
        created: list[discord.TextChannel] = []
        counter = 0
        try:
            while not bot_state.stop_event.is_set() and time.monotonic() < end_time:
                delay = bot_state.rate_controller.get_delay()
                counter += 1
                tag = counter % 9999

                try:
                    ch = await guild.create_text_channel(f"raid-test-{tag}")
                    created.append(ch)
                except discord.HTTPException:
                    pass

                await asyncio.sleep(delay)
                if bot_state.stop_event.is_set():
                    break

                if created:
                    ch = created.pop(0)
                    try:
                        await ch.delete(reason="[RAID TEST] channel flood")
                    except discord.HTTPException:
                        pass

                await asyncio.sleep(delay)

        except asyncio.CancelledError:
            pass
        finally:
            for ch in created:
                try:
                    await ch.delete(reason="[RAID TEST] cleanup")
                except discord.HTTPException:
                    pass
            if bot_state.active_simulation == "raid" and not bot_state.is_running():
                bot_state.active_simulation = None

    async def _role_flood(self, guild: discord.Guild, duration: int) -> None:
        """Rapidly create and delete roles for `duration` seconds."""
        end_time = time.monotonic() + duration
        created: list[discord.Role] = []
        counter = 0
        try:
            while not bot_state.stop_event.is_set() and time.monotonic() < end_time:
                delay = bot_state.rate_controller.get_delay()
                counter += 1
                tag = counter % 9999

                try:
                    role = await guild.create_role(name=f"raid-role-{tag}")
                    created.append(role)
                except discord.HTTPException:
                    pass

                await asyncio.sleep(delay)
                if bot_state.stop_event.is_set():
                    break

                if created:
                    role = created.pop(0)
                    try:
                        await role.delete(reason="[RAID TEST] role flood")
                    except discord.HTTPException:
                        pass

                await asyncio.sleep(delay)

        except asyncio.CancelledError:
            pass
        finally:
            for role in created:
                try:
                    await role.delete(reason="[RAID TEST] cleanup")
                except discord.HTTPException:
                    pass

    async def _spam_all_channels(
        self, channels: list[discord.TextChannel], count: int
    ) -> None:
        """Flood every channel concurrently with `count` messages each."""
        try:
            await asyncio.gather(
                *[self._spam_one_channel(ch, count) for ch in channels],
                return_exceptions=True,
            )
        except asyncio.CancelledError:
            pass

    async def _spam_one_channel(self, channel: discord.TextChannel, count: int) -> None:
        try:
            for i in range(count):
                if bot_state.stop_event.is_set():
                    break
                try:
                    await channel.send(SPAM_MESSAGES[i % len(SPAM_MESSAGES)])
                except discord.HTTPException:
                    pass
                await asyncio.sleep(bot_state.rate_controller.get_delay())
        except asyncio.CancelledError:
            pass

    async def _mass_ban(
        self, guild: discord.Guild, members: list[discord.Member]
    ) -> None:
        """Ban each target member one by one at the configured rate."""
        try:
            for member in members:
                if bot_state.stop_event.is_set():
                    break
                try:
                    await guild.ban(
                        member,
                        reason="[RAID TEST] mass ban simulation",
                        delete_message_days=0,
                    )
                except discord.HTTPException:
                    pass
                await asyncio.sleep(bot_state.rate_controller.get_delay())
        except asyncio.CancelledError:
            pass

    # ------------------------------------------------------------------
    # Error handler
    # ------------------------------------------------------------------
    @raid.error
    async def raid_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        if isinstance(error, app_commands.MissingPermissions):
            await interaction.response.send_message(
                "❌ You need **Administrator** permission to run simulations.",
                ephemeral=True,
            )


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Raid(bot))
