"""
Вью для /event_cancel: выбор события из списка + подтверждение.
"""

from __future__ import annotations

import discord

from .event_announcements import delete_event_record, get_event_record
from .event_common import SERVER_TZ, can_manage_event


class EventCancelSelect(discord.ui.Select):
    def __init__(self, parent_view: "EventCancelView", events: list[discord.ScheduledEvent]):
        self.parent_view = parent_view
        self._events_by_id = {str(ev.id): ev for ev in events}
        options = [
            discord.SelectOption(
                label=ev.name[:100],
                description=ev.start_time.astimezone(SERVER_TZ).strftime("%d.%m, %H:%M NSK"),
                value=str(ev.id),
            )
            for ev in events[:25]
        ]
        super().__init__(placeholder="Выбери событие для отмены", options=options, min_values=1, max_values=1, row=0)

    async def callback(self, interaction: discord.Interaction):
        event = self._events_by_id[self.values[0]]
        record = get_event_record(event.id)
        creator_id = record["creator_id"] if record else None
        if not can_manage_event(interaction.user, self.parent_view.campaign_name, creator_id):
            await interaction.response.send_message(
                "Отменить это событие может только ГМ кампании или тот, кто его создал.",
                ephemeral=True,
            )
            return

        self.parent_view.selected_event = event
        self.parent_view.rebuild_items()
        when = event.start_time.astimezone(SERVER_TZ).strftime("%d.%m, %H:%M NSK")
        await interaction.response.edit_message(
            content=f"Точно отменить «{event.name}» ({when})?", view=self.parent_view
        )


class ConfirmCancelButton(discord.ui.Button):
    def __init__(self, parent_view: "EventCancelView", row: int):
        super().__init__(label="Да, отменить событие", style=discord.ButtonStyle.danger, row=row)
        self.parent_view = parent_view

    async def callback(self, interaction: discord.Interaction):
        event = self.parent_view.selected_event
        try:
            await event.delete()
        except discord.Forbidden:
            await interaction.response.send_message("У бота нет прав на удаление события.", ephemeral=True)
            return
        except discord.HTTPException as e:
            await interaction.response.send_message(f"Discord отклонил запрос: {e}", ephemeral=True)
            return

        record = get_event_record(event.id)
        if record and record.get("channel_id") and record.get("message_id"):
            channel = interaction.guild.get_channel(record["channel_id"])
            if channel is not None:
                try:
                    message = await channel.fetch_message(record["message_id"])
                    await message.delete()
                except (discord.NotFound, discord.Forbidden):
                    pass
        delete_event_record(event.id)

        await interaction.response.edit_message(content=f"Событие «{event.name}» отменено.", view=None)


class DeclineCancelButton(discord.ui.Button):
    def __init__(self, row: int):
        super().__init__(label="Отмена", style=discord.ButtonStyle.secondary, row=row)

    async def callback(self, interaction: discord.Interaction):
        await interaction.response.edit_message(content="Отмена события отменена.", view=None)


class EventCancelView(discord.ui.View):
    def __init__(self, campaign_name: str, events: list[discord.ScheduledEvent], requester: discord.Member):
        super().__init__(timeout=300)
        self.campaign_name = campaign_name
        self.events = events
        self.requester = requester
        self.selected_event: discord.ScheduledEvent | None = None
        self.rebuild_items()

    def rebuild_items(self) -> None:
        self.clear_items()
        if self.selected_event is None:
            self.add_item(EventCancelSelect(self, self.events))
        else:
            self.add_item(ConfirmCancelButton(self, row=0))
            self.add_item(DeclineCancelButton(row=0))

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester.id:
            await interaction.response.send_message("Эта форма не для тебя.", ephemeral=True)
            return False
        return True