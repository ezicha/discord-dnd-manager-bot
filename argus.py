import os
import discord
from discord.ext import commands
from dotenv import load_dotenv
import asyncio

import logging
from db.connection import close_db, init_db

load_dotenv()


def _log_level_from_env(name: str, default: int) -> tuple[int, str | None]:
    """Уровень логов из переменной окружения (DEBUG, INFO, WARNING, ERROR).
    Не задана — default. Не распознана — default и текст предупреждения."""
    raw = os.getenv(name, "").strip().upper()
    if not raw:
        return default, None
    level = logging.getLevelNamesMapping().get(raw)
    if level is None:
        return default, f"{name}={raw!r} не распознан, использую {logging.getLevelName(default)}"
    return level, None


# LOG_LEVEL — общий уровень (включая discord.py), ARGUS_LOG_LEVEL — только код бота.
# На боевом сервере не заданы: всё работает на INFO.
root_level, root_warning = _log_level_from_env("LOG_LEVEL", logging.INFO)
argus_level, argus_warning = _log_level_from_env("ARGUS_LOG_LEVEL", root_level)

logging.basicConfig(
    level=root_level,
    format="%(asctime)s %(levelname)s:%(name)s:%(message)s"
)
logger = logging.getLogger("argus")
logger.setLevel(argus_level)
for warning in (root_warning, argus_warning):
    if warning:
        logger.warning(warning)
logger.info(
    "Уровень логов: общий %s, Аргус %s",
    logging.getLevelName(root_level), logging.getLevelName(argus_level),
)

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
        print(f"Синхронизировано {len(synced)} слэш-команд:")
        names = [c.name for c in synced]
        print(names)
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
    try:
        async with bot:
            await init_db()
            await bot.load_extension("cogs.cmpgn.campaigns")
            await bot.load_extension("cogs.events.events")
            await bot.load_extension("cogs.dice")
            await bot.load_extension("cogs.housekeeping")
            await bot.load_extension("cogs.campaign_devtool")
            await bot.load_extension("cogs.threads")
            await bot.start(TOKEN)
    finally:
        # Сначала бот отключается от Discord (выход из async with bot),
        # потом закрывается база — когда её уже никто не использует.
        await close_db()
        logger.info("Соединение с базой закрыто")

asyncio.run(main())