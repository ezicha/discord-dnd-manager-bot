import discord
from discord.ext import commands, tasks

from .campaign_archive import ArchiveSelectView
from .campaign_common import logger, resolve_gm_archived_campaigns_from_db, resolve_gm_campaigns_from_db
from .campaign_create import CampaignModal
from .campaign_edit import CampaignEditMenuView, CampaignEditSelectView
from .campaign_resurrect import MAX_CHANNELS_FOR_RENAME, ResurrectChannelPickView, ResurrectModal, ResurrectSelectView

from db.campaigns_db import (
    get_active_campaigns_with_category,
    get_all_campaign_channels,
    remove_deleted_channel,
    wipe_campaign,
)

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


async def channel_is_really_deleted(bot: commands.Bot, guild_id: int | None, channel_id: int) -> bool:
    """
    True — только если мы ТОЧНО знаем, что канала (или категории — для Discord
    это тоже канал) больше нет. При любой неясности (сервер недоступен, сбой
    сети, нет прав) — False: лучше оставить лишнюю запись в БД, чем стереть
    живую кампанию.
    """
    if guild_id is None:
        logger.warning("Автоочистка кампаний: у канала %s нет кампании в БД — пропускаю", channel_id)
        return False

    guild = bot.get_guild(guild_id)
    if guild is None or guild.unavailable:
        # Сервер не загружен в память бота или Discord отдал его как
        # «временно недоступен» — его каналы в памяти пустые, верить им нельзя.
        logger.warning("Автоочистка кампаний: сервер %s недоступен — пропускаю канал %s", guild_id, channel_id)
        return False

    if guild.get_channel(channel_id) is not None:
        return False  # канал на месте

    # В памяти канала нет — переспрашиваем Discord напрямую.
    try:
        await bot.fetch_channel(channel_id)
    except discord.NotFound:
        return True  # Discord подтвердил: канала нет
    except discord.HTTPException as e:
        logger.warning("Автоочистка кампаний: не удалось проверить канал %s (%r) — пропускаю", channel_id, e)
        return False
    return False  # Discord канал нашёл — просто в памяти его не было


async def cleanup_deleted_categories(bot: commands.Bot) -> int:
    """Стирает из БД активные кампании, чью категорию удалили в Discord мимо
    бота. В самом Discord ничего не трогает (каналы, роли, события остаются).
    Возвращает, сколько кампаний стёрто."""
    wiped = 0
    for campaign in await get_active_campaigns_with_category():
        if await channel_is_really_deleted(bot, campaign["guild_id"], campaign["category_id"]):
            await wipe_campaign(campaign["id"], bot.user.id)
            logger.info(
                "Автоочистка кампаний: категория кампании «%s» (id=%s) удалена в Discord — кампания стёрта из БД",
                campaign["name"], campaign["id"],
            )
            wiped += 1
    return wiped


async def cleanup_campaign_channels(bot: commands.Bot) -> int:
    """Удаляет из БД записи о каналах, которые удалили в Discord мимо бота.
    Возвращает, сколько записей удалено."""
    rows = await get_all_campaign_channels()
    removed = 0
    for row in rows:
        channel_id = row["discord_channel_id"]
        if await channel_is_really_deleted(bot, row["guild_id"], channel_id):
            # Канал удалили в Discord мимо бота (не через /campaign archive
            # и не через /dev wipe_archive) — remove_deleted_channel сам решит,
            # надо ли заодно стереть заархивированную кампанию без каналов.
            await remove_deleted_channel(channel_id, bot.user.id)
            removed += 1

    if removed:
        logger.info("Очистка campaign_channels: удалено %d устаревших записей", removed)
    return removed


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
        try:
            # Сначала кампании с удалённой категорией (их каналы уйдут вместе
            # с ними), потом — отдельные удалённые каналы.
            await cleanup_deleted_categories(self.bot)
            await cleanup_campaign_channels(self.bot)
        except Exception:
            logger.exception("Очистка campaign_channels: непредвиденная ошибка")

    @cleanup_stale_campaign_channels.before_loop
    async def before_cleanup(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(Campaigns(bot))