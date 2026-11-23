"""
Cog с командами по событиям кампаний, сгруппированными под /event.
"""

import discord
from discord import app_commands
from discord.ext import commands, tasks

from db.events_db import delete_event_record, get_all_event_records
from .event_common import (
    build_event_preview_embed,
    generate_calendar_text_for_week,
    get_campaign_for_channel,
    get_campaign_id_for_channel,
    logger,
    remove_event_announcement_and_record,
    user_role_in_campaign,
)
from .event_create import EventCreateView
from .event_cancel import EventCancelView
from .event_edit import EventEditSelectView


class EventGroup(app_commands.Group):
    def __init__(self):
        super().__init__(name="event", description="Команды для событий (сессий) кампаний")

    @app_commands.command(name="create", description="Создать событие (сессию) для текущей кампании")
    async def create(self, interaction: discord.Interaction):
        try:
            campaign_name = get_campaign_for_channel(interaction.channel)
            if campaign_name is None:
                await interaction.response.send_message(
                    "Эта команда вызывается внутри канала кампании.", ephemeral=True
                )
                return

            if user_role_in_campaign(interaction.user, campaign_name) is None:
                await interaction.response.send_message(
                    "Ты не состоишь в этой кампании.", ephemeral=True
                )
                return

            category = interaction.channel.category
            all_channels = category.channels if category else []
            voice_channels = [ch for ch in all_channels if isinstance(ch, discord.VoiceChannel)]
            text_channels = [ch for ch in all_channels if isinstance(ch, discord.TextChannel)]
            if not voice_channels:
                await interaction.response.send_message(
                    "У этой кампании нет войс-канала — событие не к чему привязать.",
                    ephemeral=True,
                )
                return

            campaign_id = await get_campaign_id_for_channel(interaction.channel)
            view = EventCreateView(campaign_name, campaign_id, voice_channels, text_channels, interaction.user)
            embed = build_event_preview_embed(
                campaign_name, None, None, None,
                voice_channels[0] if len(voice_channels) == 1 else None, None,
                generate_calendar_text_for_week(view.week_start),
            )
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        except Exception:
            logger.exception("Ошибка в /event create")
            msg = "Что-то пошло не так."
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)


    @app_commands.command(name="edit", description="Редактировать событие (сессию) кампании")
    async def edit(self, interaction: discord.Interaction):
        try:
            campaign_name = get_campaign_for_channel(interaction.channel)
            if campaign_name is None:
                await interaction.response.send_message(
                    "Эта команда вызывается внутри канала кампании.", ephemeral=True
                )
                return

            if user_role_in_campaign(interaction.user, campaign_name) is None:
                await interaction.response.send_message(
                    "Ты не состоишь в этой кампании.", ephemeral=True
                )
                return

            category = interaction.channel.category
            all_channels = category.channels if category else []
            voice_channel_ids = {ch.id for ch in all_channels if isinstance(ch, discord.VoiceChannel)}
            text_channels = [ch for ch in all_channels if isinstance(ch, discord.TextChannel)]

            now = discord.utils.utcnow()
            events = [
                ev for ev in interaction.guild.scheduled_events
                if ev.channel_id in voice_channel_ids and ev.start_time > now
            ]
            if not events:
                await interaction.response.send_message(
                    "У этой кампании нет запланированных событий.", ephemeral=True
                )
                return
            events.sort(key=lambda e: e.start_time)

            view = EventEditSelectView(campaign_name, text_channels, events, interaction.user)
            await interaction.response.send_message(
                "Выбери событие, которое нужно отредактировать:", view=view, ephemeral=True
            )
        except Exception:
            logger.exception("Ошибка в /event edit")
            msg = "Что-то пошло не так."
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)


    @app_commands.command(name="cancel", description="Отменить событие (сессию) кампании")
    async def cancel(self, interaction: discord.Interaction):
        try:
            campaign_name = get_campaign_for_channel(interaction.channel)
            if campaign_name is None:
                await interaction.response.send_message(
                    "Эта команда вызывается внутри канала кампании.", ephemeral=True
                )
                return

            if user_role_in_campaign(interaction.user, campaign_name) is None:
                await interaction.response.send_message(
                    "Ты не состоишь в этой кампании.", ephemeral=True
                )
                return

            category = interaction.channel.category
            voice_channel_ids = {
                ch.id for ch in (category.channels if category else [])
                if isinstance(ch, discord.VoiceChannel)
            }
            now = discord.utils.utcnow()
            events = [
                ev for ev in interaction.guild.scheduled_events
                if ev.channel_id in voice_channel_ids and ev.start_time > now
            ]
            if not events:
                await interaction.response.send_message(
                    "У этой кампании нет запланированных событий.", ephemeral=True
                )
                return
            events.sort(key=lambda e: e.start_time)

            view = EventCancelView(campaign_name, events, interaction.user)
            await interaction.response.send_message(
                "Выбери событие, которое нужно отменить:", view=view, ephemeral=True
            )
        except Exception:
            logger.exception("Ошибка в /event cancel")
            msg = "Что-то пошло не так."
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)


FINISHED_EVENT_STATUSES = (discord.EventStatus.completed, discord.EventStatus.canceled)


async def event_is_really_gone(bot: commands.Bot, guild_id: int | None, event_id: int) -> bool:
    """
    True — только если мы ТОЧНО знаем, что события больше нет (удалено,
    отменено или уже завершилось). При любой неясности — False: лучше
    оставить лишнюю запись в БД, чем стереть живое событие.
    """
    if guild_id is None:
        logger.warning("Очистка event_announcements: у события %s нет кампании в БД — пропускаю", event_id)
        return False

    guild = bot.get_guild(guild_id)
    if guild is None or guild.unavailable:
        # Сервер не загружен или «временно недоступен» — его события
        # в памяти бота пустые, верить им нельзя.
        logger.warning("Очистка event_announcements: сервер %s недоступен — пропускаю событие %s", guild_id, event_id)
        return False

    if guild.get_scheduled_event(event_id) is not None:
        return False  # событие на месте

    # В памяти события нет — переспрашиваем Discord напрямую.
    try:
        event = await guild.fetch_scheduled_event(event_id)
    except discord.NotFound:
        return True  # Discord подтвердил: события нет
    except discord.HTTPException as e:
        logger.warning("Очистка event_announcements: не удалось проверить событие %s (%r) — пропускаю", event_id, e)
        return False
    return event.status in FINISHED_EVENT_STATUSES


async def cleanup_event_records(bot: commands.Bot) -> int:
    """Удаляет из БД записи о событиях, которых больше нет в Discord.
    Возвращает, сколько записей удалено."""
    records = await get_all_event_records()
    removed = 0
    for rec in records:
        event_id = rec["scheduled_event_id"]
        if await event_is_really_gone(bot, rec["guild_id"], event_id):
            await delete_event_record(event_id)
            removed += 1

    if removed:
        logger.info("Очистка event_announcements: удалено %d устаревших записей", removed)
    return removed


async def handle_scheduled_event_deleted(event: discord.ScheduledEvent) -> bool:
    """
    Discord сообщил, что событие удалено (вручную в интерфейсе Discord или
    через /event cancel). Убираем анонс и запись в БД — так же, как /event cancel.
    Если запись уже убрана (например, это сам /event cancel) — ничего не делаем.

    Завершённые (сыгранные) события не трогаем: их анонсы остаются как история.
    Возвращает True, если что-то было убрано.
    """
    if event.status == discord.EventStatus.completed:
        return False

    removed = await remove_event_announcement_and_record(event.guild, event.id)
    if removed:
        logger.info("Событие %s удалено в Discord — анонс и запись убраны", event.id)
    return removed


class EventsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.bot.tree.add_command(EventGroup())
        self.cleanup_stale_event_records.start()

    def cog_unload(self) -> None:
        self.cleanup_stale_event_records.cancel()

    @commands.Cog.listener()
    async def on_scheduled_event_delete(self, event: discord.ScheduledEvent) -> None:
        try:
            await handle_scheduled_event_deleted(event)
        except Exception:
            logger.exception("Ошибка при обработке удаления события %s", event.id)

    @tasks.loop(hours=24 * 7)
    async def cleanup_stale_event_records(self) -> None:
        try:
            await cleanup_event_records(self.bot)
        except Exception:
            logger.exception("Очистка event_announcements: непредвиденная ошибка")

    @cleanup_stale_event_records.before_loop
    async def before_cleanup(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(EventsCog(bot))