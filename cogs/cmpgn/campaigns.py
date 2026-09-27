import discord
from discord.ext import commands, tasks

from .campaign_archive import ArchiveSelectView
from .campaign_common import logger, resolve_gm_archived_campaigns_from_db, resolve_gm_campaigns_from_db
from .campaign_create import CampaignModal
from .campaign_edit import CampaignEditMenuView, CampaignEditSelectView
from .campaign_resurrect import MAX_CHANNELS_FOR_RENAME, ResurrectChannelPickView, ResurrectModal, ResurrectSelectView

from db.campaigns_db import get_all_campaign_channels, remove_deleted_channel

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
            campaigns = await resolve_gm_campaigns_from_db(guild, member)

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
            campaigns = await resolve_gm_campaigns_from_db(guild, member)

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
            campaigns = await resolve_gm_archived_campaigns_from_db(guild, member)

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
        self.cleanup_stale_campaign_channels.start()

    async def cog_unload(self):
        self.bot.tree.remove_command(self.campaign_group.name)
        self.cleanup_stale_campaign_channels.cancel()

    @tasks.loop(hours=24 * 7)
    async def cleanup_stale_campaign_channels(self) -> None:
        rows = await get_all_campaign_channels()
        if not rows:
            return

        removed = 0
        for row in rows:
            if self.bot.get_channel(row["discord_channel_id"]) is None:
                # Канал удалили в Discord мимо бота (не через /campaign archive
                # и не через /dev wipe_archive) — remove_deleted_channel сам решит,
                # надо ли заодно снести и саму кампанию, если каналов не осталось.
                await remove_deleted_channel(row["discord_channel_id"], self.bot.user.id)
                removed += 1

        if removed:
            logger.info("Очистка campaign_channels: удалено %d устаревших записей", removed)

    @cleanup_stale_campaign_channels.before_loop
    async def before_cleanup(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(Campaigns(bot))