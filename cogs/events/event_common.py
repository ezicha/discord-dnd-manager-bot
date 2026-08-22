"""
Общие хелперы для команд работы с событиями кампаний.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import discord

logger = logging.getLogger("argus")

# Фиксированный часовой пояс сервера для ввода времени событий: MSK, UTC+3, без перехода на летнее.
SERVER_TZ = timezone(timedelta(hours=3), name="MSK")

_WEEKDAY_NAMES_RU = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
_MONTH_NAMES_RU = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]


def generate_date_options(days_ahead: int = 14) -> list[discord.SelectOption]:
    """Ближайшие days_ahead дней от сегодня (в SERVER_TZ). value — ISO-дата YYYY-MM-DD."""
    today = datetime.now(SERVER_TZ).date()
    options: list[discord.SelectOption] = []
    for offset in range(days_ahead):
        day = today + timedelta(days=offset)
        label = f"{_WEEKDAY_NAMES_RU[day.weekday()]}, {day.day} {_MONTH_NAMES_RU[day.month - 1]}"
        if offset == 0:
            label += " (сегодня)"
        elif offset == 1:
            label += " (завтра)"
        options.append(discord.SelectOption(label=label, value=day.isoformat()))
    return options


def parse_time_input(raw: str) -> tuple[int, int]:
    """Строго парсит 'ЧЧ:ММ'. Бросает ValueError с понятным текстом при неверном формате."""
    raw = raw.strip()
    parts = raw.split(":")
    if len(parts) != 2 or not (parts[0].isdigit() and parts[1].isdigit()):
        raise ValueError("Введите время в формате ЧЧ:ММ, например 19:00")
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("Час должен быть 0–23, минуты 0–59")
    return hour, minute


def combine_date_and_time(date_iso: str, hour: int, minute: int) -> datetime:
    """Дата (ISO YYYY-MM-DD) + час/минута -> aware datetime в SERVER_TZ (discord.py сам сконвертирует в UTC)."""
    d = datetime.strptime(date_iso, "%Y-%m-%d").date()
    return datetime(d.year, d.month, d.day, hour, minute, tzinfo=SERVER_TZ)


def get_campaign_for_channel(channel: discord.abc.GuildChannel) -> str | None:
    """Определяет кампанию по каналу вызова команды: название категории == название кампании."""
    category = channel.category
    return category.name if category else None


def user_role_in_campaign(member: discord.Member, campaign_name: str) -> str | None:
    """Возвращает "gm", "player" или None. Роль ГМа — "ГМ: <campaign_name>", роль игрока — "<campaign_name>"."""
    gm_role_name = f"ГМ: {campaign_name}"
    player_role_name = campaign_name
    role_names = {role.name for role in member.roles}
    if gm_role_name in role_names:
        return "gm"
    if player_role_name in role_names:
        return "player"
    return None


def build_event_preview_embed(
    campaign_name: str,
    title: str | None,
    date_iso: str | None,
    time_str: str | None,
    channel: discord.VoiceChannel | None,
    description: str | None,
) -> discord.Embed:
    embed = discord.Embed(
        title=title or "Новое событие (название не указано)",
        description=description or None,
        color=discord.Color.blurple(),
    )
    embed.add_field(name="Кампания", value=campaign_name, inline=True)
    embed.add_field(name="Канал", value=channel.mention if channel else "не выбран", inline=True)
    embed.add_field(name="Дата", value=date_iso or "не выбрана", inline=True)
    embed.add_field(name="Время (MSK)", value=time_str or "не указано", inline=True)
    return embed


async def create_scheduled_event(
    guild: discord.Guild,
    channel: discord.VoiceChannel,
    name: str,
    start_time: datetime,
    description: str | None,
) -> tuple[discord.ScheduledEvent | None, str | None]:
    """Создаёт нативный Scheduled Event. Возвращает (event, None) либо (None, текст_ошибки).
    Переиспользуемо для будущих команд редактирования/отмены событий."""
    try:
        event = await guild.create_scheduled_event(
            name=name,
            start_time=start_time,
            channel=channel,
            description=description or discord.utils.MISSING,
            entity_type=discord.EntityType.voice,
            privacy_level=discord.PrivacyLevel.guild_only,
        )
        return event, None
    except discord.Forbidden:
        logger.exception("Нет прав на создание Scheduled Event")
        return None, "У бота нет прав на создание событий на этом сервере."
    except discord.HTTPException as e:
        logger.exception("Ошибка Discord API при создании события")
        return None, f"Discord отклонил запрос: {e}"