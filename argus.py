import os
import discord
from discord.ext import commands
from dotenv import load_dotenv
import asyncio

import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("argus")

load_dotenv()
TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.presences = True

bot = commands.Bot(command_prefix="!", intents=intents)


@bot.event
async def on_ready():
    print(f"Бот запущен как {bot.user} (ID: {bot.user.id})")
    try:
        synced = await bot.tree.sync()
        print(f"Синхронизировано {len(synced)} слэш-команд")
    except Exception as e:
        print(f"Ошибка синхронизации команд: {e}")


@bot.tree.command(name="тык", description="Проверка, что бот жив")
async def ping(interaction: discord.Interaction):
    await interaction.response.send_message("Не распускайте руки.")


async def main():
    async with bot:
        await bot.load_extension("cogs.campaigns")
        await bot.start(TOKEN)

asyncio.run(main())