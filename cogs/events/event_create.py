"""
Вью и модалка для /event_create — выбор дня через календарь (кнопки), а не select.
"""

from __future__ import annotations

from datetime import timedelta

import discord

from .event_announcements import get_event_record, record_event
from .event_common import (
    MAX_DAYS_AHEAD,
    SERVER_TZ,
    build_event_announcement_embed,
    build_event_preview_embed,
    combine_date_and_time,
    create_scheduled_event,
    edit_scheduled_event,
    generate_calendar_text_for_week,
    get_campaign_role,
    parse_time_input,
    today_server,
    week_dates,
    week_start_for,
)


class TitleTimeModal(discord.ui.Modal, title="Название и время события"):
    def __init__(self, parent_view: "EventCreateView"):
        super().__init__()
        self.parent_view = parent_view
        self.title_input = discord.ui.TextInput(
            label="Название", max_length=100, required=True,
            default=parent_view.title or "",
        )
        self.time_input = discord.ui.TextInput(
            label="Время (NSK, формат ЧЧ:ММ)", max_length=5, required=True,
            placeholder="19:00", default=parent_view.time_str or "",
        )
        self.description_input = discord.ui.TextInput(
            label="Описание (необязательно)", style=discord.TextStyle.paragraph,
            max_length=1000, required=False, default=parent_view.description or "",
        )
        self.add_item(self.title_input)
        self.add_item(self.time_input)
        self.add_item(self.description_input)

    async def on_submit(self, interaction: discord.Interaction):
        try:
            parse_time_input(str(self.time_input.value))
        except ValueError as e:
            await interaction.response.send_message(str(e), ephemeral=True)
            return

        self.parent_view.title = str(self.title_input.value)
        self.parent_view.time_str = str(self.time_input.value).strip()
        self.parent_view.description = str(self.description_input.value) or None

        await interaction.response.edit_message(
            embed=self.parent_view.build_embed(), view=self.parent_view
        )


class DayButton(discord.ui.Button):
    def __init__(self, parent_view: "EventCreateView", date_iso: str, label: str, row: int, disabled: bool):
        super().__init__(label=label, style=discord.ButtonStyle.secondary, row=row, disabled=disabled)
        self.parent_view = parent_view
        self.date_iso = date_iso

    async def callback(self, interaction: discord.Interaction):
        self.parent_view.selected_date = self.date_iso
        self.parent_view.rebuild_items()
        await interaction.response.edit_message(
            embed=self.parent_view.build_embed(), view=self.parent_view
        )


class WeekNavButton(discord.ui.Button):
    def __init__(self, parent_view: "EventCreateView", direction: int, row: int, disabled: bool):
        label = "◀" if direction < 0 else "▶"
        super().__init__(label=label, style=discord.ButtonStyle.primary, row=row, disabled=disabled)
        self.parent_view = parent_view
        self.direction = direction

    async def callback(self, interaction: discord.Interaction):
        self.parent_view.week_start += timedelta(days=7 * self.direction)
        self.parent_view.rebuild_items()
        await interaction.response.edit_message(
            embed=self.parent_view.build_embed(), view=self.parent_view
        )


class ChannelSelect(discord.ui.Select):
    def __init__(self, parent_view: "EventCreateView", voice_channels: list[discord.VoiceChannel], row: int):
        self.parent_view = parent_view
        options = [
            discord.SelectOption(label=ch.name, value=str(ch.id))
            for ch in voice_channels[:25]
        ]
        super().__init__(placeholder="Выбери войс-канал", options=options, min_values=1, max_values=1, row=row)
        self._channels_by_id = {str(ch.id): ch for ch in voice_channels}

    async def callback(self, interaction: discord.Interaction):
        self.parent_view.selected_channel = self._channels_by_id[self.values[0]]
        await interaction.response.edit_message(
            embed=self.parent_view.build_embed(), view=self.parent_view
        )


class AnnounceChannelSelect(discord.ui.Select):
    def __init__(self, parent_view: "EventCreateView", text_channels: list[discord.TextChannel], row: int):
        self.parent_view = parent_view
        options = [
            discord.SelectOption(label=f"#{ch.name}", value=str(ch.id))
            for ch in text_channels[:25]
        ]
        super().__init__(placeholder="Куда постить анонс", options=options, min_values=1, max_values=1, row=row)
        self._channels_by_id = {str(ch.id): ch for ch in text_channels}

    async def callback(self, interaction: discord.Interaction):
        self.parent_view.selected_announce_channel = self._channels_by_id[self.values[0]]
        await interaction.response.edit_message(
            embed=self.parent_view.build_embed(), view=self.parent_view
        )


class FillDetailsButton(discord.ui.Button):
    def __init__(self, parent_view: "EventCreateView", row: int):
        super().__init__(label="Название и время", style=discord.ButtonStyle.secondary, row=row)
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(TitleTimeModal(self.parent_view))


class CreateEventButton(discord.ui.Button):
    def __init__(self, parent_view: "EventCreateView", row: int):
        label = "Сохранить" if parent_view.existing_event else "Создать"
        super().__init__(label=label, style=discord.ButtonStyle.success, row=row)
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        v = self.parent_view
        missing = []
        if not v.selected_date:
            missing.append("день")
        if not v.selected_channel:
            missing.append("войс-канал")
        if v.text_channels and not v.selected_announce_channel:
            missing.append("канал для анонса")
        if not v.title:
            missing.append("название")
        if not v.time_str:
            missing.append("время")
        if missing:
            await interaction.response.send_message(
                f"Сначала заполни: {', '.join(missing)}.", ephemeral=True
            )
            return

        hour, minute = parse_time_input(v.time_str)
        start_dt = combine_date_and_time(v.selected_date, hour, minute)

        if start_dt < discord.utils.utcnow().astimezone(SERVER_TZ):
            await interaction.response.send_message(
                "Указанное время уже в прошлом.", ephemeral=True
            )
            return

        if v.existing_event is None:
            await self._create(interaction, v, start_dt)
        else:
            await self._save_edit(interaction, v, start_dt)

    async def _create(self, interaction: discord.Interaction, v: "EventCreateView", start_dt) -> None:
        event, error = await create_scheduled_event(
            guild=interaction.guild,
            channel=v.selected_channel,
            name=v.title,
            start_time=start_dt,
            description=v.description,
        )
        if error:
            await interaction.response.send_message(error, ephemeral=True)
            return

        announce_note = ""
        announce_channel_id = None
        announce_message_id = None
        if v.selected_announce_channel is not None:
            role = get_campaign_role(interaction.guild, v.campaign_name)
            content = role.mention if role else None
            announce_embed = build_event_announcement_embed(v.campaign_name, event)
            announce_message = await v.selected_announce_channel.send(content=content, embed=announce_embed)
            announce_channel_id = v.selected_announce_channel.id
            announce_message_id = announce_message.id
        else:
            announce_note = " (анонс не отправлен — нет текстового канала кампании)"

        record_event(event.id, interaction.user.id, announce_channel_id, announce_message_id)

        for item in v.children:
            item.disabled = True
        await interaction.response.edit_message(
            content=f"Готово! Событие «{v.title}» создано: {event.url}{announce_note}",
            embed=v.build_embed(), view=v,
        )

    async def _save_edit(self, interaction: discord.Interaction, v: "EventCreateView", start_dt) -> None:
        event = v.existing_event
        updated_event, error = await edit_scheduled_event(
            event=event,
            name=v.title,
            channel=v.selected_channel,
            start_time=start_dt,
            description=v.description,
        )
        if error:
            await interaction.response.send_message(error, ephemeral=True)
            return

        old_record = get_event_record(event.id)
        creator_id = old_record["creator_id"] if old_record else interaction.user.id
        old_channel_id = old_record["channel_id"] if old_record else None
        old_message_id = old_record["message_id"] if old_record else None

        new_channel_id = v.selected_announce_channel.id if v.selected_announce_channel else None
        announce_note = ""

        if old_channel_id and old_message_id and old_channel_id == new_channel_id:
            channel = interaction.guild.get_channel(old_channel_id)
            new_message_id = old_message_id
            if channel is not None:
                try:
                    message = await channel.fetch_message(old_message_id)
                    await message.edit(embed=build_event_announcement_embed(v.campaign_name, updated_event))
                except (discord.NotFound, discord.Forbidden):
                    announce_note = " (не удалось обновить старое сообщение-анонс)"
        else:
            if old_channel_id and old_message_id:
                old_channel = interaction.guild.get_channel(old_channel_id)
                if old_channel is not None:
                    try:
                        old_message = await old_channel.fetch_message(old_message_id)
                        await old_message.delete()
                    except (discord.NotFound, discord.Forbidden):
                        pass

            new_message_id = None
            if v.selected_announce_channel is not None:
                role = get_campaign_role(interaction.guild, v.campaign_name)
                content = role.mention if role else None
                announce_embed = build_event_announcement_embed(v.campaign_name, updated_event)
                new_message = await v.selected_announce_channel.send(content=content, embed=announce_embed)
                new_message_id = new_message.id
            else:
                announce_note = " (анонс не отправлен — нет текстового канала кампании)"

        record_event(event.id, creator_id, new_channel_id, new_message_id)

        for item in v.children:
            item.disabled = True
        await interaction.response.edit_message(
            content=f"Готово! Событие «{v.title}» обновлено: {updated_event.url}{announce_note}",
            embed=v.build_embed(), view=v,
        )


class CancelButton(discord.ui.Button):
    def __init__(self, parent_view: "EventCreateView", row: int):
        super().__init__(label="Отмена", style=discord.ButtonStyle.secondary, row=row)
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        msg = "Редактирование отменено." if self.parent_view.existing_event else "Создание события отменено."
        await interaction.response.edit_message(content=msg, view=None)
        self.parent_view.stop()


class EventCreateView(discord.ui.View):
    # Ряды: 0 — Пн–Пт (5 кнопок), 1 — ◀/Сб/Вс/▶ (4 кнопки),
    # 2..N — селекты каналов (если их больше одного), последний ряд — кнопки действий.
    def __init__(
        self,
        campaign_name: str,
        voice_channels: list[discord.VoiceChannel],
        text_channels: list[discord.TextChannel],
        requester: discord.Member,
        existing_event: discord.ScheduledEvent | None = None,
    ):
        super().__init__(timeout=600)
        self.campaign_name = campaign_name
        self.voice_channels = voice_channels
        self.text_channels = text_channels
        self.requester = requester
        self.existing_event = existing_event

        if existing_event is not None:
            start_local = existing_event.start_time.astimezone(SERVER_TZ)
            self.week_start = week_start_for(start_local.date())
            self.selected_date = start_local.date().isoformat()
            self.time_str = start_local.strftime("%H:%M")
            self.title = existing_event.name
            self.description = existing_event.description or None
            self.selected_channel = (
                existing_event.channel
                if isinstance(existing_event.channel, discord.VoiceChannel)
                and existing_event.channel in voice_channels
                else (voice_channels[0] if len(voice_channels) == 1 else None)
            )
            record = get_event_record(existing_event.id)
            record_channel_id = record["channel_id"] if record else None
            preselected_announce = next((ch for ch in text_channels if ch.id == record_channel_id), None)
            self.selected_announce_channel = (
                preselected_announce or (text_channels[0] if len(text_channels) == 1 else None)
            )
        else:
            self.week_start = week_start_for(today_server())
            self.selected_date = None
            self.selected_channel = voice_channels[0] if len(voice_channels) == 1 else None
            self.selected_announce_channel = text_channels[0] if len(text_channels) == 1 else None
            self.title = None
            self.time_str = None
            self.description = None

        self.rebuild_items()

    def rebuild_items(self) -> None:
        self.clear_items()
        today = today_server()
        max_date = today + timedelta(days=MAX_DAYS_AHEAD)
        dates = week_dates(self.week_start)

        for d in dates[:5]:  # Пн–Пт
            disabled = d < today or d > max_date
            self.add_item(DayButton(self, d.isoformat(), str(d.day), row=0, disabled=disabled))

        can_go_prev = self.week_start > week_start_for(today)
        can_go_next = self.week_start + timedelta(days=7) <= max_date
        if can_go_prev:
            self.add_item(WeekNavButton(self, -1, row=1, disabled=False))
        for d in dates[5:]:  # Сб, Вс
            disabled = d < today or d > max_date
            self.add_item(DayButton(self, d.isoformat(), str(d.day), row=1, disabled=disabled))
        if can_go_next:
            self.add_item(WeekNavButton(self, 1, row=1, disabled=False))

        row = 2
        if len(self.voice_channels) > 1:
            self.add_item(ChannelSelect(self, self.voice_channels, row=row))
            row += 1
        if len(self.text_channels) > 1:
            self.add_item(AnnounceChannelSelect(self, self.text_channels, row=row))
            row += 1

        self.add_item(FillDetailsButton(self, row=row))
        self.add_item(CreateEventButton(self, row=row))
        self.add_item(CancelButton(self, row=row))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester.id:
            await interaction.response.send_message(
                "Эта форма не для тебя.", ephemeral=True
            )
            return False
        return True

    def build_embed(self) -> discord.Embed:
        calendar_text = generate_calendar_text_for_week(self.week_start, self.selected_date)
        return build_event_preview_embed(
            self.campaign_name, self.title, self.selected_date,
            self.time_str, self.selected_channel, self.description,
            calendar_text,
        )