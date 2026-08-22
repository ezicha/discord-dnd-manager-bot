"""
Вью и модалка для /event_create.
"""

from __future__ import annotations

import discord

from .event_common import (
    SERVER_TZ,
    build_event_preview_embed,
    combine_date_and_time,
    create_scheduled_event,
    generate_date_options,
    parse_time_input,
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
            label="Время (MSK, формат ЧЧ:ММ)", max_length=5, required=True,
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


class DateSelect(discord.ui.Select):
    def __init__(self, parent_view: "EventCreateView"):
        self.parent_view = parent_view
        super().__init__(
            placeholder="Выбери день",
            options=generate_date_options(),
            min_values=1, max_values=1,
        )

    async def callback(self, interaction: discord.Interaction):
        self.parent_view.selected_date = self.values[0]
        await interaction.response.edit_message(
            embed=self.parent_view.build_embed(), view=self.parent_view
        )


class ChannelSelect(discord.ui.Select):
    def __init__(self, parent_view: "EventCreateView", voice_channels: list[discord.VoiceChannel]):
        self.parent_view = parent_view
        options = [
            discord.SelectOption(label=ch.name, value=str(ch.id))
            for ch in voice_channels[:25]
        ]
        super().__init__(placeholder="Выбери войс-канал", options=options, min_values=1, max_values=1)
        self._channels_by_id = {str(ch.id): ch for ch in voice_channels}

    async def callback(self, interaction: discord.Interaction):
        self.parent_view.selected_channel = self._channels_by_id[self.values[0]]
        await interaction.response.edit_message(
            embed=self.parent_view.build_embed(), view=self.parent_view
        )


class FillDetailsButton(discord.ui.Button):
    def __init__(self, parent_view: "EventCreateView"):
        super().__init__(label="Название и время", style=discord.ButtonStyle.secondary)
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.send_modal(TitleTimeModal(self.parent_view))


class CreateEventButton(discord.ui.Button):
    def __init__(self, parent_view: "EventCreateView"):
        super().__init__(label="Создать", style=discord.ButtonStyle.success)
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        v = self.parent_view
        missing = []
        if not v.selected_date:
            missing.append("день")
        if not v.selected_channel:
            missing.append("канал")
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

        for item in v.children:
            item.disabled = True
        await interaction.response.edit_message(
            content=f"Готово! Событие «{v.title}» создано: {event.url}",
            embed=v.build_embed(), view=v,
        )


class EventCreateView(discord.ui.View):
    def __init__(self, campaign_name: str, voice_channels: list[discord.VoiceChannel], requester: discord.Member):
        super().__init__(timeout=600)
        self.campaign_name = campaign_name
        self.requester = requester
        self.selected_date: str | None = None
        self.selected_channel: discord.VoiceChannel | None = (
            voice_channels[0] if len(voice_channels) == 1 else None
        )
        self.title: str | None = None
        self.time_str: str | None = None
        self.description: str | None = None

        self.add_item(DateSelect(self))
        if len(voice_channels) > 1:
            self.add_item(ChannelSelect(self, voice_channels))
        self.add_item(FillDetailsButton(self))
        self.add_item(CreateEventButton(self))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester.id:
            await interaction.response.send_message(
                "Эта форма не для тебя.", ephemeral=True
            )
            return False
        return True

    def build_embed(self) -> discord.Embed:
        return build_event_preview_embed(
            self.campaign_name, self.title, self.selected_date,
            self.time_str, self.selected_channel, self.description,
        )