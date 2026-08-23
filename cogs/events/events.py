"""
Тонкий Cog с командами по событиям кампаний.
"""

import discord
from discord import app_commands
from discord.ext import commands

from .event_common import (
    build_event_preview_embed, 
    generate_calendar_text_for_week,
    get_campaign_for_channel, 
    logger,
    user_role_in_campaign
)
from .event_create import EventCreateView


class EventsCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="event_create",
        description="Создать событие (сессию) для текущей кампании",
    )
    async def event_create(self, interaction: discord.Interaction):
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

            view = EventCreateView(campaign_name, voice_channels, text_channels, interaction.user)
            embed = build_event_preview_embed(
                campaign_name, None, None, None,
                voice_channels[0] if len(voice_channels) == 1 else None, None,
                generate_calendar_text_for_week(view.week_start),
                )
            await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
        except Exception:
            logger.exception("Ошибка в /event_create")
            msg = "Что-то пошло не так."
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(EventsCog(bot))