import discord

from .campaign_common import (
    GM_ROLE_PREFIX,
    SELECT_OPTIONS_LIMIT,
    build_select_options,
    channel_select_option,
    deliver_result,
    logger,
    move_archive_to_end,
    resurrect_channel,
)

# Поле "Название кампании" всегда занимает один из 5 слотов модалки Discord —
# на переименование отдельных каналов остаётся максимум 4.
MAX_CHANNELS_FOR_RENAME = 4


# --- Выбор кампании для восстановления (если заархивированных кампаний несколько) ---

class ResurrectSelectView(discord.ui.View):
    def __init__(self, campaigns: dict):
        super().__init__(timeout=120)
        self.campaigns = campaigns
        names = list(campaigns.keys())
        options, truncated = build_select_options(names)
        self.select_campaign.options = options
        if truncated:
            self.select_campaign.placeholder = (
                f"Какую кампанию вернуть из архива? (показаны первые {len(options)} из {len(names)})"
            )

    @discord.ui.select(placeholder="Какую кампанию вернуть из архива?")
    async def select_campaign(self, interaction: discord.Interaction, select: discord.ui.Select):
        chosen_name = select.values[0]
        campaign_role, gm_role, channels = self.campaigns[chosen_name]

        if len(channels) > MAX_CHANNELS_FOR_RENAME:
            view = ResurrectChannelPickView(chosen_name, campaign_role, gm_role, channels)
            await interaction.response.edit_message(
                content=(
                    f"У кампании **{chosen_name}** больше {MAX_CHANNELS_FOR_RENAME} заархивированных каналов — "
                    f"переименовать сразу можно не больше {MAX_CHANNELS_FOR_RENAME} за раз (лимит полей в модалке Discord, "
                    f"одно из них уже занято под название кампании). Выбери, какие каналы переименовать сейчас — "
                    f"остальные вернутся с прежним названием, поправить его потом можно через /campaign_edit. "
                    f"Восстановлены при этом будут все каналы, независимо от выбора здесь."
                ),
                view=view,
            )
        else:
            modal = ResurrectModal(chosen_name, campaign_role, gm_role, channels)
            await interaction.response.send_modal(modal)


class ResurrectChannelPickView(discord.ui.View):
    """
    Показывается, только когда у кампании больше MAX_CHANNELS_FOR_RENAME
    заархивированных каналов. Восстанавливаются всегда ВСЕ каналы кампании —
    этот выбор влияет только на то, каким из них дать поле переименования
    в следующей модалке (лимит — 5 полей на модалку, одно уже занято
    под название кампании).
    """
    def __init__(self, campaign_name: str, campaign_role, gm_role, channels: list):
        super().__init__(timeout=120)
        self.campaign_name = campaign_name
        self.campaign_role = campaign_role
        self.gm_role = gm_role
        self.channels = channels
        self.chosen_channels: list[str] = []

        truncated = len(channels) > SELECT_OPTIONS_LIMIT
        shown = channels[:SELECT_OPTIONS_LIMIT]
        options = [channel_select_option(ch) for ch in shown]
        self.select_channels.options = options
        self.select_channels.max_values = min(len(options), MAX_CHANNELS_FOR_RENAME)

        placeholder = f"Какие каналы переименовать? (максимум {MAX_CHANNELS_FOR_RENAME})"
        if truncated:
            placeholder += f" — показаны первые {len(shown)} из {len(channels)}"
        self.select_channels.placeholder = placeholder

    @discord.ui.select(placeholder="Какие каналы переименовать?", min_values=0)
    async def select_channels(self, interaction: discord.Interaction, select: discord.ui.Select):
        self.chosen_channels = select.values
        await interaction.response.defer()

    @discord.ui.button(label="Продолжить", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        chosen_ids = set(self.chosen_channels)
        rename_channels = [ch for ch in self.channels if str(ch.id) in chosen_ids]
        modal = ResurrectModal(
            self.campaign_name, self.campaign_role, self.gm_role, self.channels,
            rename_channels=rename_channels,
        )
        await interaction.response.send_modal(modal)
        self.stop()

    @discord.ui.button(label="Отмена", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="Восстановление отменено.", view=None)
        self.stop()


# --- Название (можно оставить как есть или переименовать) + подтверждение ---

class ResurrectModal(discord.ui.Modal, title="Вернуть кампанию из архива"):
    new_name = discord.ui.TextInput(
        label="Название кампании",
        placeholder="Оставь как есть или впиши новое название",
        max_length=100
    )

    def __init__(self, campaign_name: str, campaign_role, gm_role, channels: list, rename_channels: list | None = None):
        super().__init__()
        self.campaign_name = campaign_name
        self.campaign_role = campaign_role
        self.gm_role = gm_role
        self.channels = channels
        self.new_name.default = campaign_name

        # Поля переименования каналов — по умолчанию текущее (архивное, с префиксом)
        # имя, чтобы префикс можно было сразу тут же убрать. Если rename_channels не
        # передан явно (кампания с небольшим числом каналов, отдельного выбора не
        # потребовалось) — поле получает каждый канал кампании.
        # В подписи поля указан тип канала — иначе одноимённые войс- и текстовый
        # канал в модалке было бы не различить.
        self.channel_name_inputs: dict[int, discord.ui.TextInput] = {}
        for ch in (rename_channels if rename_channels is not None else channels):
            type_label = "🔊" if isinstance(ch, discord.VoiceChannel) else "#️⃣"
            field = discord.ui.TextInput(
                label=f"[{type_label}] {ch.name}"[:45],
                default=ch.name,
                max_length=100,
            )
            self.channel_name_inputs[ch.id] = field
            self.add_item(field)

    async def on_submit(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)
        guild = interaction.guild
        final_name = self.new_name.value.strip() or self.campaign_name

        # Проверяем конфликты названия, если кампанию переименовывают
        if discord.utils.get(guild.categories, name=final_name):
            await interaction.followup.send(
                f"Категория с названием **{final_name}** уже существует. Попробуй другое название.",
                ephemeral=True
            )
            return
        existing_role = discord.utils.get(guild.roles, name=final_name)
        if existing_role and (self.campaign_role is None or existing_role.id != self.campaign_role.id):
            await interaction.followup.send(
                f"Роль с названием **{final_name}** уже занята другой кампанией. Попробуй другое название.",
                ephemeral=True
            )
            return

        try:
            campaign_role = self.campaign_role
            gm_role = self.gm_role

            if campaign_role:
                if campaign_role.name != final_name:
                    await campaign_role.edit(name=final_name)
            else:
                # Роль участника могла быть удалена вручную — на всякий случай создаём заново
                campaign_role = await guild.create_role(name=final_name, mentionable=True)

            gm_role_name = f"{GM_ROLE_PREFIX}{final_name}"
            if gm_role.name != gm_role_name:
                await gm_role.edit(name=gm_role_name)

            overwrites = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                campaign_role: discord.PermissionOverwrite(view_channel=True),
                gm_role: discord.PermissionOverwrite(view_channel=True, manage_channels=True),
            }
            category = await guild.create_category(name=final_name, overwrites=overwrites)
            await move_archive_to_end(guild)

            restored = []
            renamed_from = {}
            for channel in self.channels:
                field = self.channel_name_inputs.get(channel.id)
                new_channel_name = field.value.strip() if field else None
                old_name = channel.name
                await resurrect_channel(guild, channel, category, campaign_role, gm_role, new_name=new_channel_name)
                restored.append(channel)
                if channel.name != old_name:
                    renamed_from[channel.id] = old_name

            channel_lines = [
                f"{renamed_from[ch.id]} → {ch.mention}" if ch.id in renamed_from else ch.mention
                for ch in restored
            ]
            restored_text = ", ".join(channel_lines) or "каналов не было"
            skipped_count = len(self.channels) - len(self.channel_name_inputs)
            note = (
                f"У {skipped_count} канал(ов) название не менялось (не влезло в модалку) — "
                f"поправить его можно через /campaign_edit.\n"
                if skipped_count > 0 else ""
            )
            message = (
                f"Кампания **{final_name}** возвращена из архива!\n"
                f"Роль: {campaign_role.mention}\n"
                f"Каналы: {restored_text}\n"
                f"{note}"
                f"Индивидуальный доступ (например «только ГМ»), если он был у канала до архивации, "
                f"из архива не восстанавливается автоматически — поправить его можно через /campaign_edit."
            )
            await deliver_result(interaction, restored, message)
        except Exception as e:
            logger.error("Ошибка при восстановлении кампании: %s", repr(e))
            await interaction.followup.send(
                f"Что-то пошло не так при восстановлении кампании. Ошибка: {e}",
                ephemeral=True
            )