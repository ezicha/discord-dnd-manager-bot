import discord

from .campaign_common import (
    ARCHIVE_CATEGORY_NAME,
    archive_channel,
    build_select_options,
    channel_options,
    deliver_result,
    get_or_create_archive_category,
    logger,
    slugify,
)


# --- Архивация одной/нескольких кампаний целиком ---

class ArchiveSelectView(discord.ui.View):
    # Модальное окно с префиксами (ArchivePrefixModal) даёт по одному текстовому полю
    # на кампанию, а Discord не позволяет больше 5 полей в одной модалке — поэтому
    # за раз нельзя архивировать больше 5 кампаний.
    MAX_CAMPAIGNS_PER_BATCH = 5

    def __init__(self, campaigns: dict):
        super().__init__(timeout=120)
        self.campaigns = campaigns
        self.chosen_names: list[str] = []

        names = list(campaigns.keys())
        options, truncated = build_select_options(names)
        self.select_campaigns.options = options
        self.select_campaigns.max_values = min(len(options), self.MAX_CAMPAIGNS_PER_BATCH)

        placeholder = f"Выбери кампании для архивации (максимум {self.MAX_CAMPAIGNS_PER_BATCH} за раз)"
        if truncated:
            placeholder += f" — показаны первые {len(options)} из {len(names)}"
        self.select_campaigns.placeholder = placeholder

    @discord.ui.select(placeholder="Выбери кампании для архивации", min_values=1)
    async def select_campaigns(self, interaction: discord.Interaction, select: discord.ui.Select):
        self.chosen_names = select.values
        await interaction.response.defer()

    @discord.ui.button(label="Архивировать выбранное", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.chosen_names:
            await interaction.response.send_message("Сначала выбери хотя бы одну кампанию.", ephemeral=True)
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
            default_prefix = slugify(name)
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

        archive_category = await get_or_create_archive_category(guild)

        archived_summary = []
        archived_per_campaign = []

        try:
            for chosen_name in self.chosen_names:
                category, campaign_role, gm_role = self.campaigns[chosen_name]
                prefix = self.prefix_inputs[chosen_name].value.strip() or slugify(chosen_name)

                moved_channels = []
                for channel in list(category.channels):
                    await archive_channel(guild, channel, archive_category, prefix, campaign_role, gm_role)
                    moved_channels.append(channel)

                await category.delete()
                archived_per_campaign.append((chosen_name, prefix, moved_channels))

                channels_text = ", ".join(ch.mention for ch in moved_channels) or "каналов не было"
                archived_summary.append(f"**{chosen_name}** (префикс `{prefix}`): {channels_text}")

            # Каждой кампании — своё сообщение в её собственные каналы, а не общая
            # сводка по всем сразу (иначе в канал одной кампании прилетал бы список
            # и других заархивированных заодно с ней).
            no_text_channel_summaries = []
            any_text_channel_found = False
            for chosen_name, prefix, moved_channels in archived_per_campaign:
                channels_text = ", ".join(ch.mention for ch in moved_channels) or "каналов не было"
                campaign_message = f"Кампания **{chosen_name}** заархивирована (префикс `{prefix}`): {channels_text}"
                text_channels = [ch for ch in moved_channels if isinstance(ch, discord.TextChannel)]
                if text_channels:
                    any_text_channel_found = True
                    for ch in text_channels:
                        await ch.send(campaign_message)
                else:
                    no_text_channel_summaries.append(campaign_message)

            if no_text_channel_summaries:
                await interaction.followup.send(
                    "Нет текстового канала, чтобы отправить туда итог, для:\n" + "\n".join(no_text_channel_summaries),
                    ephemeral=True
                )
            if any_text_channel_found:
                await interaction.followup.send("Готово.", ephemeral=True)
        except Exception as e:
            logger.error("Ошибка при архивации: %s", repr(e))
            done_text = "\n".join(archived_summary) if archived_summary else "ничего не успело обработаться"
            await interaction.followup.send(
                f"Что-то пошло не так во время архивации.\nУспело обработаться:\n{done_text}\nОшибка: {e}",
                ephemeral=True
            )


# --- Архивация одного/нескольких каналов внутри одной кампании (вызывается из меню редактирования) ---

class ArchiveSingleChannelView(discord.ui.View):
    def __init__(self, campaign_name, category, campaign_role, gm_role):
        super().__init__(timeout=120)
        self.campaign_name = campaign_name
        self.category = category
        self.campaign_role = campaign_role
        self.gm_role = gm_role
        self.chosen_channels = []

        options, truncated = channel_options(category)
        self.select_channels.options = options
        self.select_channels.max_values = len(options)
        if truncated:
            self.select_channels.placeholder = (
                f"Какие каналы архивировать? (показаны первые {len(options)} из {len(category.channels)})"
            )

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

        archive_category = await get_or_create_archive_category(guild)

        prefix = self.prefix.value.strip() or slugify(self.campaign_name)
        moved = []

        try:
            for channel_id in self.chosen_channel_ids:
                channel = guild.get_channel(int(channel_id))
                if channel is None:
                    continue
                await archive_channel(guild, channel, archive_category, prefix, self.campaign_role, self.gm_role)
                moved.append(channel)

            moved_text = ", ".join(ch.mention for ch in moved) or "ничего не перенесено"
            message = f"Каналы перенесены в **{ARCHIVE_CATEGORY_NAME}** с префиксом `{prefix}`: {moved_text}"
            await deliver_result(interaction, moved, message)
        except Exception as e:
            logger.error("Ошибка при архивации каналов: %s", repr(e))
            done_text = ", ".join(ch.mention for ch in moved) or "ничего не успело перенестись"
            await interaction.followup.send(
                f"Что-то пошло не так при архивации.\nУспело обработаться: {done_text}\nОшибка: {e}",
                ephemeral=True
            )