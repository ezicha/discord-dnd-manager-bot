import discord

from .campaign_common import GM_ROLE_PREFIX, deliver_result, logger, move_archive_to_end


class CampaignModal(discord.ui.Modal, title="Новая кампания"):
    campaign_name = discord.ui.TextInput(
        label="Название кампании",
        placeholder="Например: Проклятие Страда",
        max_length=100
    )

    text_channel_name = discord.ui.TextInput(
        label="Название текстового канала",
        placeholder="Оставить это поле пустым, если канал не нужен",
        required=False,
        max_length=100
    )

    voice_channel_name = discord.ui.TextInput(
        label="Название голосового канала",
        placeholder="Оставить это поле пустым, если канал не нужен",
        required=False,
        max_length=100
    )

    text_channel_topic = discord.ui.TextInput(
        label="Описание текстового канала",
        default="D&D 5e (2014г.)",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=1024
    )

    async def on_submit(self, interaction: discord.Interaction):
        view = CampaignPlayersView(
            campaign_name=self.campaign_name.value,
            text_channel_name=self.text_channel_name.value.strip(),
            voice_channel_name=self.voice_channel_name.value.strip(),
            text_channel_topic=self.text_channel_topic.value.strip()
        )
        await interaction.response.send_message(
            f"Название: **{self.campaign_name.value}**\nВыбери себя и игроков, которых нужно добавить:",
            view=view,
            ephemeral=True
        )


class CampaignPlayersView(discord.ui.View):
    def __init__(self, campaign_name: str, text_channel_name: str, voice_channel_name: str, text_channel_topic: str):
        super().__init__(timeout=300)
        self.campaign_name = campaign_name
        self.text_channel_name = text_channel_name
        self.voice_channel_name = voice_channel_name
        self.text_channel_topic = text_channel_topic
        self.selected_players: list[discord.Member] = []

    @discord.ui.select(
        cls=discord.ui.UserSelect,
        placeholder="Выбери игроков",
        min_values=0,
        max_values=25
    )
    async def select_players(self, interaction: discord.Interaction, select: discord.ui.UserSelect):
        self.selected_players = select.values
        await interaction.response.defer()

    @discord.ui.button(label="Создать кампанию", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        name = self.campaign_name

        campaign_role = None
        gm_role = None
        category = None

        try:
            campaign_role = await guild.create_role(name=name, mentionable=True)

            gm_role = await guild.create_role(
                name=f"{GM_ROLE_PREFIX}{name}",
                mentionable=True
            )
            await interaction.user.add_roles(gm_role)

            for player in self.selected_players:
                await player.add_roles(campaign_role)

            overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                campaign_role: discord.PermissionOverwrite(view_channel=True),
                gm_role: discord.PermissionOverwrite(
                    view_channel=True,
                    manage_channels=True,
                ),
            }

            category = await guild.create_category(name=name, overwrites=overwrites)
            await move_archive_to_end(guild)

            text_channel = None
            if self.text_channel_name:
                text_channel = await guild.create_text_channel(
                    name=self.text_channel_name,
                    category=category,
                    topic=self.text_channel_topic or None
                )

            voice_channel = None
            if self.voice_channel_name:
                voice_channel = await guild.create_voice_channel(name=self.voice_channel_name, category=category)

            players_list = ", ".join(p.mention for p in self.selected_players) or "никого пока не добавлено"

            channels_list = []
            if text_channel:
                channels_list.append(text_channel.mention)
            if voice_channel:
                channels_list.append(voice_channel.mention)
            channels_text = ", ".join(channels_list) if channels_list else "не создано ни одного канала"

            summary = (
                f"Кампания **{name}** создана!\n"
                f"Роль: {campaign_role.mention}\n"
                f"Игроки: {players_list}\n"
                f"Каналы: {channels_text}"
            )
            await deliver_result(interaction, [text_channel, voice_channel], summary)
        except Exception as e:
            logger.error("Ошибка при создании кампании «%s»: %s", name, repr(e))
            # Что-то из роли/категории/каналов могло уже успеть создаться до сбоя —
            # сообщаем об этом явно, чтобы не плодить дубли при повторной попытке.
            created = []
            if campaign_role:
                created.append(f"роль {campaign_role.mention}")
            if gm_role:
                created.append(f"роль {gm_role.mention}")
            if category:
                created.append(f"категория «{category.name}»")
            created_text = f"\nУспело создаться: {', '.join(created)}." if created else ""
            await interaction.followup.send(
                f"Не удалось создать кампанию «{name}» до конца. Ошибка: {e}{created_text}",
                ephemeral=True
            )
        self.stop()

    @discord.ui.button(label="Отмена", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Создание кампании отменено.", view=None)
        self.stop()