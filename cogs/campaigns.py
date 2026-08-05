import logging
logger = logging.getLogger("argus")

import discord
from discord.ext import commands

def slugify(name: str) -> str:
    return name.lower().replace(" ", "-")


async def archive_channel(guild, channel, archive_category, prefix, campaign_role, gm_role):
    """
    Переносит один канал в архивную категорию, переименовывает с префиксом
    и выставляет права "только просмотр" (без записи, без подключения для голоса).
    """
    is_voice = isinstance(channel, discord.VoiceChannel)
    new_name = f"{prefix}-{channel.name}"
    await channel.edit(category=archive_category, name=new_name, sync_permissions=False)

    overwrites = channel.overwrites
    overwrites[guild.default_role] = discord.PermissionOverwrite(view_channel=False)
    if campaign_role:
        overwrites[campaign_role] = discord.PermissionOverwrite(
            view_channel=True, send_messages=False, connect=(False if is_voice else None)
        )
    if gm_role:
        overwrites[gm_role] = discord.PermissionOverwrite(
            view_channel=True, send_messages=False, connect=(False if is_voice else None)
        )
    await channel.edit(overwrites=overwrites)
    return channel

def get_gm_campaigns(guild: discord.Guild, member: discord.Member) -> dict:
    """
    Возвращает словарь кампаний, где member является ГМом:
    { "Название кампании": (category, campaign_role, gm_role) }
    """
    gm_roles = [r for r in member.roles if r.name.startswith("ГМ: ")]
    campaigns = {}
    for role in gm_roles:
        campaign_name = role.name.removeprefix("ГМ: ")
        category = discord.utils.get(guild.categories, name=campaign_name)
        if category:
            campaign_role = discord.utils.get(guild.roles, name=campaign_name)
            campaigns[campaign_name] = (category, campaign_role, role)
    return campaigns

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

        campaign_role = await guild.create_role(name=name, mentionable=True)

        gm_role = await guild.create_role(
            name=f"ГМ: {name}",
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

        await interaction.followup.send(
            f"Кампания **{name}** создана!\n"
            f"Роль: {campaign_role.mention}\n"
            f"Игроки: {players_list}\n"
            f"Каналы: {channels_text}"
        )
        self.stop()

    @discord.ui.button(label="Отмена", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Создание кампании отменено.", view=None)
        self.stop()

class ArchiveSelectView(discord.ui.View):
    def __init__(self, campaigns: dict):
        super().__init__(timeout=120)
        self.campaigns = campaigns
        self.chosen_names: list[str] = []

        options = [discord.SelectOption(label=name) for name in campaigns.keys()]
        self.select_campaigns.options = options
        self.select_campaigns.max_values = len(options)

    @discord.ui.select(placeholder="Выбери кампании для архивации", min_values=1)
    async def select_campaigns(self, interaction: discord.Interaction, select: discord.ui.Select):
        self.chosen_names = select.values
        await interaction.response.defer()

    @discord.ui.button(label="Архивировать выбранное", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.chosen_names:
            await interaction.response.send_message("Сначала выбери хотя бы одну кампанию.", ephemeral=True)
            return

        if len(self.chosen_names) > 5:
            await interaction.response.send_message(
                "За один раз можно ввести префиксы максимум для 5 кампаний "
                "(ограничение формы Discord). Выбери 5 или меньше.", ephemeral=True
            )
            return

        modal = ArchivePrefixModal(self.campaigns, self.chosen_names)
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Отмена", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Архивация отменена.", view=None)
        self.stop()


class ArchivePrefixModal(discord.ui.Modal, title="Префиксы для архива"):
    def __init__(self, campaigns: dict, chosen_names: list):
        super().__init__()
        self.campaigns = campaigns
        self.chosen_names = chosen_names
        self.prefix_inputs = {}

        for name in chosen_names:
            default_prefix = name.lower().replace(" ", "-")
            field = discord.ui.TextInput(
                label=f"Префикс для «{name}»"[:45],
                default=default_prefix,
                max_length=50
            )
            self.prefix_inputs[name] = field
            self.add_item(field)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild

        archive_category = discord.utils.get(guild.categories, name="Архив")
        if archive_category is None:
            archive_category = await guild.create_category(name="Архив")
        await archive_category.move(end=True)

        archived_summary = []

        try:
            for chosen_name in self.chosen_names:
                category, campaign_role, gm_role = self.campaigns[chosen_name]
                prefix = self.prefix_inputs[chosen_name].value.strip() or slugify(chosen_name)

                moved_channels = []
                for channel in list(category.channels):
                    await archive_channel(guild, channel, archive_category, prefix, campaign_role, gm_role)
                    moved_channels.append(channel)

                await category.delete()

                channels_text = ", ".join(ch.mention for ch in moved_channels) or "каналов не было"
                archived_summary.append(f"**{chosen_name}** (префикс `{prefix}`): {channels_text}")

            await interaction.followup.send("Заархивировано:\n" + "\n".join(archived_summary))
        except Exception as e:
            logger.error("Ошибка при архивации: %s", repr(e))
            done_text = "\n".join(archived_summary) if archived_summary else "ничего не успело обработаться"
            await interaction.followup.send(
                f"Что-то пошло не так во время архивации.\nУспело обработаться:\n{done_text}\nОшибка: {e}"
            )

# --- Выбор кампании для редактирования (если их несколько) ---

class CampaignEditSelectView(discord.ui.View):
    def __init__(self, campaigns: dict):
        super().__init__(timeout=120)
        self.campaigns = campaigns
        options = [discord.SelectOption(label=name) for name in campaigns.keys()]
        self.select_campaign.options = options

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
        try:
            await interaction.response.defer(ephemeral=True)
            logger.debug("defer прошёл успешно")
            guild = interaction.guild

            overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
        }
            if self.gm_only:
                if self.campaign_role:
                    overwrites[self.campaign_role] = discord.PermissionOverwrite(view_channel=True, send_messages=False)
                if self.gm_role:
                    overwrites[self.gm_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
            else:
                if self.campaign_role:
                    overwrites[self.campaign_role] = discord.PermissionOverwrite(view_channel=True)
                if self.gm_role:
                    overwrites[self.gm_role] = discord.PermissionOverwrite(view_channel=True)

            logger.debug("тип канала: %s, overwrites: %s", self.channel_type, overwrites)

            if self.channel_type == "text":
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
            await interaction.followup.send(f"Канал {channel.mention} создан.")
            logger.debug("сообщение отправлено")
        except Exception as e:
            logger.error("Ошибка при создании канала: %s", repr(e))


# --- Архивация одного/нескольких каналов внутри категории ---

class ArchiveSingleChannelView(discord.ui.View):
    def __init__(self, campaign_name, category, campaign_role, gm_role):
        super().__init__(timeout=120)
        self.campaign_name = campaign_name
        self.category = category
        self.campaign_role = campaign_role
        self.gm_role = gm_role
        self.chosen_channels = []

        options = [discord.SelectOption(label=ch.name, value=str(ch.id)) for ch in category.channels]
        self.select_channels.options = options
        self.select_channels.max_values = len(options)

    @discord.ui.select(placeholder="Какие каналы архивировать?", min_values=1)
    async def select_channels(self, interaction: discord.Interaction, select: discord.ui.Select):
        self.chosen_channels = select.values
        await interaction.response.defer()

    @discord.ui.button(label="Архивировать выбранное", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.chosen_channels:
            await interaction.response.send_message("Сначала выбери хотя бы один канал.", ephemeral=True)
            return

        modal = SingleArchivePrefixModal(
            self.campaign_name, self.category, self.campaign_role, self.gm_role, self.chosen_channels
        )
        await interaction.response.send_modal(modal)


class SingleArchivePrefixModal(discord.ui.Modal, title="Префикс для архива"):
    prefix = discord.ui.TextInput(label="Префикс для названий каналов", max_length=50)

    def __init__(self, campaign_name, category, campaign_role, gm_role, chosen_channel_ids):
        super().__init__()
        self.campaign_name = campaign_name
        self.category = category
        self.campaign_role = campaign_role
        self.gm_role = gm_role
        self.chosen_channel_ids = chosen_channel_ids
        self.prefix.default = slugify(campaign_name)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild

        archive_category = discord.utils.get(guild.categories, name="Архив")
        if archive_category is None:
            archive_category = await guild.create_category(name="Архив")
        await archive_category.move(end=True)

        prefix = self.prefix.value.strip() or slugify(self.campaign_name)
        moved = []

        for channel_id in self.chosen_channel_ids:
            channel = guild.get_channel(int(channel_id))
            if channel is None:
                continue
            await archive_channel(guild, channel, archive_category, prefix, self.campaign_role, self.gm_role)
            moved.append(channel)

        moved_text = ", ".join(ch.mention for ch in moved) or "ничего не перенесено"
        await interaction.followup.send(f"Каналы перенесены в **Архив** с префиксом `{prefix}`: {moved_text}")

class RenameChannelSelectView(discord.ui.View):
    def __init__(self, category):
        super().__init__(timeout=120)
        self.category = category
        options = [discord.SelectOption(label=ch.name, value=str(ch.id)) for ch in category.channels]
        self.select_channel.options = options

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
        if isinstance(self.channel, discord.TextChannel):
            await self.channel.edit(name=self.new_name.value, topic=self.new_topic.value.strip() or None)
        else:
            await self.channel.edit(name=self.new_name.value)
        await interaction.followup.send(f"Канал {self.channel.mention} обновлён.")

# --- Изменение доступа существующего канала ---

class ChannelAccessSelectView(discord.ui.View):
    def __init__(self, category, campaign_role, gm_role):
        super().__init__(timeout=120)
        self.category = category
        self.campaign_role = campaign_role
        self.gm_role = gm_role

        options = [discord.SelectOption(label=ch.name, value=str(ch.id)) for ch in category.channels]
        self.select_channel.options = options

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

        overwrites = self.channel.overwrites
        overwrites[guild.default_role] = discord.PermissionOverwrite(view_channel=False)
        if self.gm_only:
            if self.campaign_role:
                overwrites[self.campaign_role] = discord.PermissionOverwrite(
                    view_channel=True, send_messages=False, connect=False
                )
            if self.gm_role:
                overwrites[self.gm_role] = discord.PermissionOverwrite(view_channel=True, send_messages=True)
        else:
            if self.campaign_role:
                overwrites[self.campaign_role] = discord.PermissionOverwrite(view_channel=True)
            if self.gm_role:
                overwrites[self.gm_role] = discord.PermissionOverwrite(view_channel=True)

        await self.channel.edit(overwrites=overwrites)

        access_text = "только ГМ может писать, остальные — только просмотр" if self.gm_only else "могут писать все участники"
        await interaction.followup.send(f"Доступ для {self.channel.mention} обновлён: {access_text}.")
        self.stop()

    @discord.ui.button(label="Отмена", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Изменение доступа отменено.", view=None)
        self.stop()

class Campaigns(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @discord.app_commands.command(name="campaign_create", description="Создать новую кампанию через интерактивное окошко")
    async def campaign_create(self, interaction: discord.Interaction):
        await interaction.response.send_modal(CampaignModal())

    @discord.app_commands.command(name="campaign_archive", description="Архивировать одну из своих кампаний (только для ГМа)")
    async def campaign_archive(self, interaction: discord.Interaction):
        guild = interaction.guild
        member = interaction.user

        campaigns = get_gm_campaigns(guild, member)

        if not campaigns:
            await interaction.response.send_message(
                "Ты не являешься ГМом ни одной активной кампании.", ephemeral=True
            )
            return

        view = ArchiveSelectView(campaigns)
        await interaction.response.send_message(
            "Выбери кампанию для архивирования:", view=view, ephemeral=True
        )

    @discord.app_commands.command(name="campaign_edit", description="Редактировать одну из своих кампаний (только для ГМа)")
    async def campaign_edit(self, interaction: discord.Interaction):
        guild = interaction.guild
        member = interaction.user

        campaigns = get_gm_campaigns(guild, member)

        if not campaigns:
            await interaction.response.send_message(
                "Ты не являешься ГМом ни одной активной кампании.", ephemeral=True
            )
            return

        if len(campaigns) == 1:
            name, (category, campaign_role, gm_role) = next(iter(campaigns.items()))
            menu = CampaignEditMenuView(name, category, campaign_role, gm_role)
            await interaction.response.send_message(f"Редактирование кампании **{name}**:", view=menu, ephemeral=True)
        else:
            view = CampaignEditSelectView(campaigns)
            await interaction.response.send_message("Какую кампанию редактировать?", view=view, ephemeral=True)

async def setup(bot: commands.Bot):
    await bot.add_cog(Campaigns(bot))