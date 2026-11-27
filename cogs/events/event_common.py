"""
Общие хелперы для команд работы с событиями кампаний.
"""

from __future__ import annotations

import logging
from datetime import datetime

# Даты и календарь живут в ядре (core/dates.py). Здесь они импортируются заново,
# чтобы остальные файлы cogs/events/ могли по-прежнему брать их из event_common.
from core.dates import (  # noqa: F401
    MAX_DAYS_AHEAD,
    SERVER_TZ,
    combine_date_and_time,
    format_date_ru,
    generate_calendar_text_for_week,
    generate_month_calendar_text,
    parse_time_input,
    today_server,
    week_dates,
    week_start_for,
)
from db.campaigns_db import get_campaign_id_by_category
from db.events_db import delete_event_record, get_event_record

import discord

logger = logging.getLogger("argus")


async def remove_event_announcement_and_record(guild: discord.Guild, event_id: int) -> bool:
    """
    Убирает «хвосты» отменённого события: сообщение-анонс в канале и запись в БД.
    Само событие в Discord к этому моменту уже должно быть удалено.

    Используется и /event cancel, и обработчиком ручного удаления события
    в Discord. Безопасно вызывать повторно: если анонса или записи уже нет —
    просто ничего не делает. Возвращает True, если запись в БД была.
    """
    record = await get_event_record(event_id)
    if record is None:
        # Обычная ситуация при /event cancel: сначала запись убирает сама
        # команда, потом Discord присылает «событие удалено» — и здесь уже пусто.
        # Но если анонс остался висеть, причина — именно отсутствие записи.
        logger.debug("Событие %s: записи в БД нет — анонс удалять не по чему", event_id)
        return False

    channel_id = record["announcement_channel_id"]
    message_id = record["announcement_message_id"]
    if channel_id and message_id:
        channel = guild.get_channel(channel_id)
        if channel is None:
            logger.warning(
                "Событие %s: канал анонса (id %s) не найден — анонс не удалён", event_id, channel_id
            )
        else:
            try:
                message = await channel.fetch_message(message_id)
                await message.delete()
            except discord.NotFound:
                logger.info("Событие %s: анонс уже был удалён вручную", event_id)
            except discord.Forbidden:
                logger.warning(
                    "Событие %s: у бота нет прав удалить анонс в канале «%s»", event_id, channel.name
                )
            except discord.HTTPException as e:
                logger.warning("Не удалось удалить анонс события %s: %r", event_id, e)

    await delete_event_record(event_id)
    return True


async def rollback_created_event(event, announce_message) -> None:
    """Откат неудавшегося /event create: удаляет уже отправленный анонс
    и уже созданное событие в Discord. Ошибки при откате только пишутся
    в лог — пользователю в любом случае сообщат, что создать не вышло."""
    if announce_message is not None:
        try:
            await announce_message.delete()
        except discord.HTTPException as e:
            logger.warning("Откат события: не удалось удалить анонс %s: %r", announce_message.id, e)
    try:
        await event.delete()
    except discord.HTTPException as e:
        logger.warning("Откат события: не удалось удалить событие %s: %r", event.id, e)


def get_campaign_for_channel(channel: discord.abc.GuildChannel) -> str | None:
    """Определяет кампанию по каналу вызова команды: название категории == название кампании."""
    category = channel.category
    return category.name if category else None


async def get_campaign_id_for_channel(channel: discord.abc.GuildChannel) -> int | None:
    """campaign_id из БД по категории канала — нужен для record_event при создании события."""
    category = channel.category
    if category is None:
        return None
    return await get_campaign_id_by_category(category.id)


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


async def edit_scheduled_event(
    event: discord.ScheduledEvent,
    name: str,
    channel: discord.VoiceChannel,
    start_time: datetime,
    description: str | None,
) -> tuple[discord.ScheduledEvent | None, str | None]:
    """Редактирует существующий Scheduled Event. Возвращает (event, None) либо (None, текст_ошибки)."""
    try:
        updated = await event.edit(
            name=name,
            channel=channel,
            start_time=start_time,
            description=description,
        )
        return updated, None
    except discord.Forbidden:
        logger.exception("Нет прав на редактирование Scheduled Event")
        return None, "У бота нет прав на редактирование событий на этом сервере."
    except discord.HTTPException as e:
        logger.exception("Ошибка Discord API при редактировании события")
        return None, f"Discord отклонил запрос: {e}"


def can_manage_event(member: discord.Member, campaign_name: str, creator_id: int | None) -> bool:
    """ГМ кампании или тот, кто создал это конкретное событие, может им управлять (редактировать/отменять)."""
    is_gm = user_role_in_campaign(member, campaign_name) == "gm"
    is_creator = creator_id is not None and creator_id == member.id
    return is_gm or is_creator
    

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