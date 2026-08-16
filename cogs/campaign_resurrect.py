import discord

from .campaign_common import GM_ROLE_PREFIX, build_select_options, deliver_result, logger, move_archive_to_end, resurrect_channel


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
        modal = ResurrectModal(chosen_name, campaign_role, gm_role, channels)
        await interaction.response.send_modal(modal)


# --- Название (можно оставить как есть или переименовать) + подтверждение ---

class ResurrectModal(discord.ui.Modal, title="Вернуть кампанию из архива"):
    new_name = discord.ui.TextInput(
        label="Название кампании",
        placeholder="Оставь как есть или впиши новое название",
        max_length=100
    )

    def __init__(self, campaign_name: str, campaign_role, gm_role, channels: list):
        super().__init__()
        self.campaign_name = campaign_name
        self.campaign_role = campaign_role
        self.gm_role = gm_role
        self.channels = channels
        self.new_name.default = campaign_name

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
            for channel in self.channels:
                await resurrect_channel(guild, channel, category, campaign_role, gm_role)
                restored.append(channel)

            restored_text = ", ".join(ch.mention for ch in restored) or "каналов не было"
            message = (
                f"Кампания **{final_name}** возвращена из архива!\n"
                f"Роль: {campaign_role.mention}\n"
                f"Каналы: {restored_text}\n"
                f"Названия каналов и индивидуальный доступ (например «только ГМ») из архива не восстанавливаются "
                f"автоматически — поправить их можно через /campaign_edit."
            )
            await deliver_result(interaction, restored, message)
        except Exception as e:
            logger.error("Ошибка при восстановлении кампании: %s", repr(e))
            await interaction.followup.send(
                f"Что-то пошло не так при восстановлении кампании. Ошибка: {e}",
                ephemeral=True
            )