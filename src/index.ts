import { Client, GatewayIntentBits, Events } from "discord.js";

const token = process.env.DISCORD_BOT_TOKEN;
if (!token) {
  console.error("DISCORD_BOT_TOKEN is not set. Exiting.");
  process.exit(1);
}

const client = new Client({
  intents: [
    GatewayIntentBits.Guilds,
    GatewayIntentBits.GuildMembers,
    GatewayIntentBits.GuildMessages,
    GatewayIntentBits.MessageContent,
  ],
});

client.once(Events.ClientReady, (readyClient) => {
  console.log(`[ready] Logged in as ${readyClient.user.tag} (ID: ${readyClient.user.id})`);
  console.log("[ready] Bot is online and ready for raid simulation testing.");
});

client.login(token);
