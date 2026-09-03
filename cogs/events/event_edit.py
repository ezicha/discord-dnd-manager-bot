"""
Вью для /event edit: выбор события из списка + переход в форму редактирования
(переиспользует EventCreateView в режиме "редактирование").
"""

from __future__ import annotations

import discord

from .event_announcements import get_event_record
from .event_common import SERVER_TZ, can_manage_event
from .event_create import EventCreateView


class EventEditSelect(discord.ui.Select):
    def __init__(
        self,
        parent_view: "EventEditSelectView",
        campaign_name: str,
        text_channels: list[discord.TextChannel],
        events: list[discord.ScheduledEvent],
    ):
        self.parent_view = parent_view
        self.campaign_name = campaign_name
        self.text_channels = text_channels
        self._events_by_id = {str(ev.id): ev for ev in events}
        options = [
            discord.SelectOption(
                label=ev.name[:100],
                description=ev.start_time.astimezone(SERVER_TZ).strftime("%d.%m, %H:%M MSK"),
                value=str(ev.id),
            )
            for ev in events[:25]
        ]
        super().__init__(placeholder="Выбери событие для редактирования", options=options, min_values=1, max_values=1, row=0)

    async def callback(self, interaction: discord.Interaction):
        event = self._events_by_id[self.values[0]]
        record = get_event_record(event.id)
        creator_id = record["creator_id"] if record else None
        if not can_manage_event(interaction.user, self.campaign_name, creator_id):
            await interaction.response.send_message(
                "Редактировать это событие может только ГМ кампании или тот, кто его создал.",
                ephemeral=True,
            )
            return

        category = interaction.channel.category
        all_channels = category.channels if category else []
        voice_channels = [ch for ch in all_channels if isinstance(ch, discord.VoiceChannel)]

        view = EventCreateView(
            self.campaign_name, voice_channels, self.text_channels, interaction.user,
            existing_event=event,
        )
        await interaction.response.edit_message(content=None, embed=view.build_embed(), view=view)


class EventEditSelectView(discord.ui.View):
    def __init__(
        self,
        campaign_name: str,
        text_channels: list[discord.TextChannel],
        events: list[discord.ScheduledEvent],
        requester: discord.Member,
    ):
        super().__init__(timeout=300)
        self.requester = requester
        self.add_item(EventEditSelect(self, campaign_name, text_channels, events))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester.id:
            await interaction.response.send_message("Эта форма не для тебя.", ephemeral=True)
            return False
        return True