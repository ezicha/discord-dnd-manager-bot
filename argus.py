import os
import discord
from discord.ext import commands
from dotenv import load_dotenv
import asyncio

import logging

logging.basicConfig(level=logging.INFO) # DEBUG, INFO, WARNING, ERROR
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

@bot.tree.error
async def on_app_command_error(interaction: discord.Interaction, error: discord.app_commands.AppCommandError):
    # discord.py заворачивает "настоящее" исключение из команды в CommandInvokeError —
    # error.original содержит его; если обёртки нет, логируем error как есть.
    original = getattr(error, "original", error)
    logger.error(
        "Необработанная ошибка в команде /%s: %s",
        interaction.command.name if interaction.command else "?",
        repr(original),
    )

    message = f"Что-то пошло не так. Ошибка: {original}"
    try:
        if interaction.response.is_done():
            await interaction.followup.send(message, ephemeral=True)
        else:
            await interaction.response.send_message(message, ephemeral=True)
    except discord.HTTPException:
        # Если и это не отправится (например, взаимодействие уже протухло
        # по таймауту) — тут уже ничего не поделать, просто не роняем бота.
        pass

async def main():
    async with bot:
        await bot.load_extension("cogs.cmpgn.campaigns")
        await bot.load_extension("cogs.events.events")
        await bot.load_extension("cogs.dice")
        await bot.load_extension("cogs.housekeeping")
        await bot.load_extension("cogs.campaign_devtool")
        await bot.start(TOKEN)

asyncio.run(main())