"""
Общие хелперы для команд работы с событиями кампаний.
"""

from __future__ import annotations

import calendar as calendar_module
import logging
from datetime import date, datetime, timedelta, timezone

import discord

logger = logging.getLogger("argus")

# Фиксированный часовой пояс сервера для ввода времени событий: NSK, UTC+7, без перехода на летнее.
SERVER_TZ = timezone(timedelta(hours=7), name="NSK")

# Насколько далеко вперёд разрешена навигация по неделям в календаре.
MAX_DAYS_AHEAD = 60

_WEEKDAY_NAMES_RU = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
_MONTH_NAMES_RU = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]
_MONTH_NAMES_RU_NOM = [
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
]


def today_server() -> date:
    """Сегодняшняя дата в часовом поясе сервера (SERVER_TZ)."""
    return datetime.now(SERVER_TZ).date()


def week_start_for(d: date) -> date:
    """Понедельник недели, в которую входит дата d."""
    return d - timedelta(days=d.weekday())


def week_dates(week_start: date) -> list[date]:
    """7 дат недели, начиная с понедельника week_start."""
    return [week_start + timedelta(days=i) for i in range(7)]


def format_date_ru(d: date) -> str:
    return f"{d.day} {_MONTH_NAMES_RU[d.month - 1]}"


def generate_month_calendar_text(month_first_day: date, selected_date_iso: str | None = None) -> str:
    """
    Текстовая сетка-календарь месяца, в котором лежит month_first_day.
    Каждая ячейка ровно 4 символа шириной — и обычная (" 25 "), и выбранная
    ("[25]") — поэтому колонки не съезжают при подсветке.
    """
    weeks = calendar_module.monthcalendar(month_first_day.year, month_first_day.month)
    title = f"{_MONTH_NAMES_RU_NOM[month_first_day.month - 1]} {month_first_day.year}"

    selected_day = None
    if selected_date_iso:
        sd = datetime.strptime(selected_date_iso, "%Y-%m-%d").date()
        if sd.year == month_first_day.year and sd.month == month_first_day.month:
            selected_day = sd.day

    header = "".join(f" {name} " for name in _WEEKDAY_NAMES_RU)
    lines = [title.center(len(header)), header]
    for week in weeks:
        cells = []
        for day in week:
            if day == 0:
                cells.append("    ")
            elif day == selected_day:
                cells.append(f"[{day:2d}]")
            else:
                cells.append(f" {day:2d} ")
        lines.append("".join(cells))
    return "```\n" + "\n".join(lines) + "\n```"

def generate_calendar_text_for_week(week_start: date, selected_date_iso: str | None = None) -> str:
    """
    Сетка(-и) месяца/месяцев, которые пересекает отображаемая неделя.
    Если неделя целиком в одном месяце — одна сетка, как раньше. Если неделя
    переходит границу месяца (например Пн 31 августа — Вс 6 сентября) — две
    отдельные сетки друг под другом, каждая со своим заголовком месяца, чтобы
    даты соседних месяцев визуально не путались.
    """
    seen: list[tuple[int, int]] = []
    for d in week_dates(week_start):
        key = (d.year, d.month)
        if key not in seen:
            seen.append(key)
    blocks = [
        generate_month_calendar_text(date(year, month, 1), selected_date_iso)
        for year, month in seen
    ]
    return "\n".join(blocks)

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


def build_event_preview_embed(
    campaign_name: str,
    title: str | None,
    date_iso: str | None,
    time_str: str | None,
    channel: discord.VoiceChannel | None,
    description: str | None,
    calendar_text: str,
) -> discord.Embed:
    embed = discord.Embed(
        title=title or "Новое событие (название не указано)",
        description=description or None,
        color=discord.Color.blurple(),
    )
    embed.add_field(name="Кампания", value=campaign_name, inline=True)
    embed.add_field(name="Канал", value=channel.mention if channel else "не выбран", inline=True)
    embed.add_field(name="Время (NSK)", value=time_str or "не указано", inline=True)

    selected_line = f"Выбрано: {format_date_ru(datetime.strptime(date_iso, '%Y-%m-%d').date())}" if date_iso else "Выбрано: дата ещё не выбрана"
    embed.add_field(name="Календарь", value=f"{calendar_text}\n{selected_line}", inline=False)
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

def get_campaign_role(guild: discord.Guild, campaign_name: str) -> discord.Role | None:
    """Роль игроков-участников кампании (название совпадает с названием кампании)."""
    return discord.utils.get(guild.roles, name=campaign_name)


def build_event_announcement_embed(campaign_name: str, event: discord.ScheduledEvent) -> discord.Embed:
    """Embed анонса события в текстовом канале кампании. <t:...:F> — Discord сам покажет
    время в часовом поясе того, кто читает сообщение."""
    embed = discord.Embed(
        title=f"📅 {event.name}",
        description=event.description or None,
        color=discord.Color.blurple(),
    )
    embed.add_field(name="Кампания", value=campaign_name, inline=True)
    embed.add_field(name="Когда", value=discord.utils.format_dt(event.start_time, style="F"), inline=True)
    if event.channel is not None:
        embed.add_field(name="Канал", value=event.channel.mention, inline=True)
    embed.add_field(name="Ссылка на событие", value=event.url, inline=False)
    return embed