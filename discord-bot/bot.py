"""
Discord Raid-Test Bot
---------------------
For testing anti-spam / anti-raid moderation systems on a dedicated TEST server only.

Environment variables (set via Replit Secrets):
  DISCORD_BOT_TOKEN   - Your bot token from https://discord.com/developers/applications
  TEST_GUILD_ID       - (optional) Guild ID to sync slash commands to immediately on startup
"""

import asyncio
import os
import sys

import discord
from discord.ext import commands
from dotenv import load_dotenv

load_dotenv()

COGS = [
    "cogs.raid",
    "cogs.ban",
    "cogs.spam",
    "cogs.control",
]


class RaidTestBot(commands.Bot):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        intents.members = True          # Server Members Intent (privileged — must be ON in portal)
        intents.message_content = True  # Message Content Intent (privileged — must be ON in portal)
        super().__init__(command_prefix="!", intents=intents)

    async def setup_hook(self) -> None:
        for cog in COGS:
            try:
                await self.load_extension(cog)
                print(f"[cog] loaded: {cog}")
            except Exception as exc:
                print(f"[cog] FAILED to load {cog}: {exc}", file=sys.stderr)

        # Sync to a specific guild immediately (instant update) when TEST_GUILD_ID is set.
        # Without it, global sync can take up to an hour.
        guild_id = os.getenv("TEST_GUILD_ID")
        if guild_id:
            guild = discord.Object(id=int(guild_id))
            self.tree.copy_global_to(guild=guild)
            await self.tree.sync(guild=guild)
            print(f"[sync] slash commands synced to guild {guild_id}")
        else:
            await self.tree.sync()
            print("[sync] slash commands synced globally (may take up to 1h)")

    async def on_ready(self) -> None:
        assert self.user is not None
        print(f"[ready] Logged in as {self.user} (ID: {self.user.id})")
        print("[ready] Bot is online and ready for raid simulation testing.")
        await self.change_presence(
            activity=discord.Activity(
                type=discord.ActivityType.watching,
                name="for /raid | TEST MODE",
            )
        )


def main() -> None:
    token = os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        print(
            "ERROR: DISCORD_BOT_TOKEN is not set.\n"
            "Add it as a Replit Secret named DISCORD_BOT_TOKEN.",
            file=sys.stderr,
        )
        sys.exit(1)

    bot = RaidTestBot()
    asyncio.run(bot.start(token))


if __name__ == "__main__":
    main()
