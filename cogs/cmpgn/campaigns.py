import discord
from discord.ext import commands

from .campaign_archive import ArchiveSelectView
from .campaign_common import get_gm_archived_campaigns, get_gm_campaigns, logger
from .campaign_create import CampaignModal
from .campaign_edit import CampaignEditMenuView, CampaignEditSelectView
from .campaign_resurrect import MAX_CHANNELS_FOR_RENAME, ResurrectChannelPickView, ResurrectModal, ResurrectSelectView


class CampaignGroup(discord.app_commands.Group):
    """
    Группа /campaign — все команды кампаний под одним неймспейсом.
    Сама бизнес-логика не менялась, изменилась только регистрация:
    имена команд теперь без префикса "campaign_" (его даёт сама группа),
    и это не Cog, а discord.app_commands.Group — подключается через
    bot.tree.add_command в Campaigns.__init__, а не через @app_commands.command
    на методах Cog'а.
    """

    def __init__(self):
        super().__init__(name="campaign", description="Управление кампаниями")

    @discord.app_commands.command(name="create", description="Создать новую кампанию через интерактивное окошко")
    async def create(self, interaction: discord.Interaction):
        try:
            await interaction.response.send_modal(CampaignModal())
        except Exception as e:
            logger.error("Ошибка при открытии формы /campaign create: %s", repr(e))
            if not interaction.response.is_done():
                await interaction.response.send_message(f"Не удалось открыть форму. Ошибка: {e}", ephemeral=True)

    @discord.app_commands.command(name="archive", description="Архивировать одну из своих кампаний (только для ГМа)")
    async def archive(self, interaction: discord.Interaction):
        guild = interaction.guild
        member = interaction.user

        try:
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
        except Exception as e:
            logger.error("Ошибка в /campaign archive: %s", repr(e))
            if not interaction.response.is_done():
                await interaction.response.send_message(f"Что-то пошло не так. Ошибка: {e}", ephemeral=True)

    @discord.app_commands.command(name="edit", description="Редактировать одну из своих кампаний (только для ГМа)")
    async def edit(self, interaction: discord.Interaction):
        guild = interaction.guild
        member = interaction.user

        try:
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
        except Exception as e:
            logger.error("Ошибка в /campaign edit: %s", repr(e))
            if not interaction.response.is_done():
                await interaction.response.send_message(f"Что-то пошло не так. Ошибка: {e}", ephemeral=True)

    @discord.app_commands.command(name="resurrect", description="Вернуть заархивированную кампанию из архива (только для ГМа)")
    async def resurrect(self, interaction: discord.Interaction):
        guild = interaction.guild
        member = interaction.user

        try:
            campaigns = get_gm_archived_campaigns(guild, member)

            if not campaigns:
                await interaction.response.send_message(
                    "У тебя нет заархивированных кампаний.", ephemeral=True
                )
                return

            if len(campaigns) == 1:
                name, (campaign_role, gm_role, channels) = next(iter(campaigns.items()))
                if len(channels) > MAX_CHANNELS_FOR_RENAME:
                    view = ResurrectChannelPickView(name, campaign_role, gm_role, channels)
                    await interaction.response.send_message(
                        f"У кампании **{name}** больше {MAX_CHANNELS_FOR_RENAME} заархивированных каналов — "
                        f"переименовать сразу можно не больше {MAX_CHANNELS_FOR_RENAME} за раз (лимит полей в модалке Discord, "
                        f"одно из них уже занято под название кампании). Выбери, какие каналы переименовать сейчас — "
                        f"остальные вернутся с прежним названием, поправить его потом можно через /campaign edit. "
                        f"Восстановлены при этом будут все каналы, независимо от выбора здесь.",
                        view=view, ephemeral=True
                    )
                else:
                    modal = ResurrectModal(name, campaign_role, gm_role, channels)
                    await interaction.response.send_modal(modal)
            else:
                view = ResurrectSelectView(campaigns)
                await interaction.response.send_message(
                    "Какую кампанию вернуть из архива?", view=view, ephemeral=True
                )
        except Exception as e:
            logger.error("Ошибка в /campaign resurrect: %s", repr(e))
            if not interaction.response.is_done():
                await interaction.response.send_message(f"Что-то пошло не так. Ошибка: {e}", ephemeral=True)


class Campaigns(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.campaign_group = CampaignGroup()
        self.bot.tree.add_command(self.campaign_group)

    async def cog_unload(self):
        # Симметрично add_command в __init__ — иначе при перезагрузке кога (reload)
        # группа останется висеть в дереве команд задвоенной.
        self.bot.tree.remove_command(self.campaign_group.name)


async def setup(bot: commands.Bot):
    await bot.add_cog(Campaigns(bot))