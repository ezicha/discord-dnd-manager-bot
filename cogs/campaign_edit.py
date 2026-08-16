import discord

from .campaign_archive import ArchiveSingleChannelView
from .campaign_common import build_access_overwrites, build_select_options, channel_options, deliver_result, logger


# --- Выбор кампании для редактирования (если их несколько) ---

class CampaignEditSelectView(discord.ui.View):
    def __init__(self, campaigns: dict):
        super().__init__(timeout=120)
        self.campaigns = campaigns
        names = list(campaigns.keys())
        options, truncated = build_select_options(names)
        self.select_campaign.options = options
        if truncated:
            self.select_campaign.placeholder = (
                f"Какую кампанию редактировать? (показаны первые {len(options)} из {len(names)})"
            )

    @discord.ui.select(placeholder="Какую кампанию редактировать?")
    async def select_campaign(self, interaction: discord.Interaction, select: discord.ui.Select):
        chosen_name = select.values[0]
        category, campaign_role, gm_role = self.campaigns[chosen_name]
        menu = CampaignEditMenuView(chosen_name, category, campaign_role, gm_role)
        await interaction.response.edit_message(
            content=f"Редактирование кампании **{chosen_name}**:", view=menu
        )


# --- Главное меню редактирования ---

class CampaignEditMenuView(discord.ui.View):
    def __init__(self, campaign_name, category, campaign_role, gm_role):
        super().__init__(timeout=180)
        self.campaign_name = campaign_name
        self.category = category
        self.campaign_role = campaign_role
        self.gm_role = gm_role

    @discord.ui.button(label="Добавить канал", style=discord.ButtonStyle.success)
    async def add_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        view = AddChannelTypeView(self.category, self.campaign_role, self.gm_role)
        await interaction.response.edit_message(content="Настрой новый канал:", view=view)

    @discord.ui.button(label="Архивировать канал", style=discord.ButtonStyle.danger)
    async def archive_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        channels = self.category.channels
        if not channels:
            await interaction.response.send_message("В категории пока нет каналов.", ephemeral=True)
            return
        view = ArchiveSingleChannelView(self.campaign_name, self.category, self.campaign_role, self.gm_role)
        await interaction.response.edit_message(content="Выбери каналы для архивации:", view=view)

    @discord.ui.button(label="Редактировать канал", style=discord.ButtonStyle.primary)
    async def rename_channel(self, interaction: discord.Interaction, button: discord.ui.Button):
        channels = self.category.channels
        if not channels:
            await interaction.response.send_message("В категории пока нет каналов.", ephemeral=True)
            return
        view = RenameChannelSelectView(self.category)
        await interaction.response.edit_message(content="Какой канал изменить?", view=view)

    @discord.ui.button(label="Изменить доступ", style=discord.ButtonStyle.primary)
    async def change_access(self, interaction: discord.Interaction, button: discord.ui.Button):
        channels = self.category.channels
        if not channels:
            await interaction.response.send_message("В категории пока нет каналов.", ephemeral=True)
            return
        view = ChannelAccessSelectView(self.category, self.campaign_role, self.gm_role)
        await interaction.response.edit_message(content="Какому каналу изменить доступ?", view=view)

    @discord.ui.button(label="Закрыть", style=discord.ButtonStyle.secondary)
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Меню редактирования закрыто.", view=None)
        self.stop()


# --- Добавление канала: тип + доступ ---

class AddChannelTypeView(discord.ui.View):
    def __init__(self, category, campaign_role, gm_role):
        super().__init__(timeout=180)
        self.category = category
        self.campaign_role = campaign_role
        self.gm_role = gm_role
        self.channel_type = "text"
        self.gm_only = False

    @discord.ui.select(
        placeholder="Тип канала",
        options=[
            discord.SelectOption(label="Текстовый", value="text"),
            discord.SelectOption(label="Голосовой", value="voice"),
        ]
    )
    async def select_type(self, interaction: discord.Interaction, select: discord.ui.Select):
        self.channel_type = select.values[0]
        await interaction.response.defer()

    @discord.ui.select(
        placeholder="Кто может писать",
        options=[
            discord.SelectOption(label="Все участники кампании", value="all"),
            discord.SelectOption(label="Только ГМ (остальные — только просмотр)", value="gm_only"),
        ]
    )
    async def select_access(self, interaction: discord.Interaction, select: discord.ui.Select):
        self.gm_only = select.values[0] == "gm_only"
        await interaction.response.defer()

    @discord.ui.button(label="Далее", style=discord.ButtonStyle.success)
    async def next_step(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = AddChannelModal(self.category, self.campaign_role, self.gm_role, self.channel_type, self.gm_only)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Отмена", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Добавление канала отменено.", view=None)
        self.stop()


class AddChannelModal(discord.ui.Modal, title="Новый канал"):
    channel_name = discord.ui.TextInput(label="Название канала", max_length=100)
    channel_topic = discord.ui.TextInput(
        label="Описание (только для текстового)",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=1024
    )

    def __init__(self, category, campaign_role, gm_role, channel_type, gm_only):
        super().__init__()
        self.category = category
        self.campaign_role = campaign_role
        self.gm_role = gm_role
        self.channel_type = channel_type
        self.gm_only = gm_only

    async def on_submit(self, interaction: discord.Interaction):
        logger.debug("on_submit вызван, название: %s", self.channel_name.value)
        await interaction.response.defer(ephemeral=True)
        logger.debug("defer прошёл успешно")
        guild = interaction.guild

        is_voice = self.channel_type == "voice"
        overwrites = build_access_overwrites(
            guild, self.campaign_role, self.gm_role, self.gm_only, is_voice
        )
        logger.debug("тип канала: %s, overwrites: %s", self.channel_type, overwrites)

        try:
            if not is_voice:
                channel = await guild.create_text_channel(
                    name=self.channel_name.value,
                    category=self.category,
                    topic=self.channel_topic.value.strip() or None,
                    overwrites=overwrites
                )
            else:
                channel = await guild.create_voice_channel(
                    name=self.channel_name.value,
                    category=self.category,
                    overwrites=overwrites
                )

            logger.debug("канал создан: %s", channel)
            await deliver_result(interaction, [channel], f"Канал {channel.mention} создан.")
            logger.debug("сообщение отправлено")
        except Exception as e:
            # Раньше здесь ошибка только логировалась, а пользователь не получал
            # никакого ответа — взаимодействие просто зависало. Теперь сообщаем и ему.
            logger.error("Ошибка при создании канала: %s", repr(e))
            await interaction.followup.send(f"Не удалось создать канал. Ошибка: {e}", ephemeral=True)


# --- Переименование / изменение описания канала ---

class RenameChannelSelectView(discord.ui.View):
    def __init__(self, category):
        super().__init__(timeout=120)
        self.category = category
        options, truncated = channel_options(category)
        self.select_channel.options = options
        if truncated:
            self.select_channel.placeholder = (
                f"Какой канал изменить? (показаны первые {len(options)} из {len(category.channels)})"
            )

    @discord.ui.select(placeholder="Какой канал изменить?")
    async def select_channel(self, interaction: discord.Interaction, select: discord.ui.Select):
        channel = interaction.guild.get_channel(int(select.values[0]))
        modal = RenameChannelModal(channel)
        await interaction.response.send_modal(modal)


class RenameChannelModal(discord.ui.Modal, title="Изменить канал"):
    new_name = discord.ui.TextInput(label="Новое название", max_length=100)
    new_topic = discord.ui.TextInput(
        label="Новое описание (только для текстового)",
        style=discord.TextStyle.paragraph,
        required=False,
        max_length=1024
    )

    def __init__(self, channel):
        super().__init__()
        self.channel = channel
        self.new_name.default = channel.name
        if isinstance(channel, discord.TextChannel):
            self.new_topic.default = channel.topic or ""

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        try:
            if isinstance(self.channel, discord.TextChannel):
                await self.channel.edit(name=self.new_name.value, topic=self.new_topic.value.strip() or None)
            else:
                await self.channel.edit(name=self.new_name.value)
            await deliver_result(interaction, [self.channel], f"Канал {self.channel.mention} обновлён.")
        except Exception as e:
            logger.error("Ошибка при редактировании канала: %s", repr(e))
            await interaction.followup.send(f"Не удалось изменить канал. Ошибка: {e}", ephemeral=True)


# --- Изменение доступа существующего канала ---

class ChannelAccessSelectView(discord.ui.View):
    def __init__(self, category, campaign_role, gm_role):
        super().__init__(timeout=120)
        self.category = category
        self.campaign_role = campaign_role
        self.gm_role = gm_role
        options, truncated = channel_options(category)
        self.select_channel.options = options
        if truncated:
            self.select_channel.placeholder = (
                f"Какому каналу изменить доступ? (показаны первые {len(options)} из {len(category.channels)})"
            )

    @discord.ui.select(placeholder="Какому каналу изменить доступ?")
    async def select_channel(self, interaction: discord.Interaction, select: discord.ui.Select):
        channel = interaction.guild.get_channel(int(select.values[0]))
        view = ChangeAccessApplyView(channel, self.campaign_role, self.gm_role)
        await interaction.response.edit_message(content=f"Настрой доступ для {channel.mention}:", view=view)


class ChangeAccessApplyView(discord.ui.View):
    def __init__(self, channel, campaign_role, gm_role):
        super().__init__(timeout=120)
        self.channel = channel
        self.campaign_role = campaign_role
        self.gm_role = gm_role
        self.gm_only = False

    @discord.ui.select(
        placeholder="Кто может писать",
        options=[
            discord.SelectOption(label="Все участники кампании", value="all"),
            discord.SelectOption(label="Только ГМ (остальные — только просмотр)", value="gm_only"),
        ]
    )
    async def select_access(self, interaction: discord.Interaction, select: discord.ui.Select):
        self.gm_only = select.values[0] == "gm_only"
        await interaction.response.defer()

    @discord.ui.button(label="Применить", style=discord.ButtonStyle.success)
    async def apply(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild

        try:
            is_voice = isinstance(self.channel, discord.VoiceChannel)
            overwrites = build_access_overwrites(
                guild, self.campaign_role, self.gm_role, self.gm_only, is_voice,
                base_overwrites=self.channel.overwrites,
            )
            await self.channel.edit(overwrites=overwrites)

            access_text = "только ГМ может писать, остальные — только просмотр" if self.gm_only else "могут писать все участники"
            await deliver_result(interaction, [self.channel], f"Доступ для {self.channel.mention} обновлён: {access_text}.")
        except Exception as e:
            logger.error("Ошибка при изменении доступа: %s", repr(e))
            await interaction.followup.send(f"Не удалось изменить доступ. Ошибка: {e}", ephemeral=True)
        self.stop()

    @discord.ui.button(label="Отмена", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Изменение доступа отменено.", view=None)
        self.stop()