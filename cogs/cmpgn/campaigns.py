import discord
from discord.ext import commands, tasks

from .campaign_archive import ArchiveSelectView
from .campaign_common import logger, resolve_gm_archived_campaigns_from_db, resolve_gm_campaigns_from_db
from .campaign_create import CampaignModal
from .campaign_edit import CampaignEditMenuView, CampaignEditSelectView
from .campaign_resurrect import MAX_CHANNELS_FOR_RENAME, ResurrectChannelPickView, ResurrectModal, ResurrectSelectView

from db.campaigns_db import (
    adopt_campaign_channel,
    get_active_campaign_by_category,
    get_active_campaigns_with_category,
    get_all_campaign_channels,
    move_campaign_channel,
    remove_deleted_channel,
    set_campaign_channel_gm_only,
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


def describe_channel(channel) -> str:
    """«название» (id …) — чтобы в логах было понятно человеку, а по id можно было найти точно."""
    return f"«{channel.name}» (id {channel.id})"


async def channel_is_really_deleted(bot: commands.Bot, guild_id: int | None, channel_id: int, what: str) -> bool:
    """
    True — только если мы ТОЧНО знаем, что канала (или категории — для Discord
    это тоже канал) больше нет. При любой неясности (сервер недоступен, сбой
    сети, нет прав) — False: лучше оставить лишнюю запись в БД, чем стереть
    живую кампанию.

    what — человекопонятное описание для логов, например
    "канал id 123 кампании «Лекторий»".
    """
    if guild_id is None:
        logger.warning("Автоочистка кампаний: %s — кампания не найдена в БД, пропускаю", what)
        return False

    guild = bot.get_guild(guild_id)
    if guild is None or guild.unavailable:
        # Сервер не загружен в память бота или Discord отдал его как
        # «временно недоступен» — его каналы в памяти пустые, верить им нельзя.
        logger.warning("Автоочистка кампаний: сервер (id %s) недоступен — пропускаю %s", guild_id, what)
        return False

    if guild.get_channel(channel_id) is not None:
        return False  # канал на месте

    # В памяти канала нет — переспрашиваем Discord напрямую.
    try:
        await bot.fetch_channel(channel_id)
    except discord.NotFound:
        return True  # Discord подтвердил: канала нет
    except discord.HTTPException as e:
        logger.warning("Автоочистка кампаний: не удалось проверить %s (%r) — пропускаю", what, e)
        return False
    return False  # Discord канал нашёл — просто в памяти его не было


async def cleanup_deleted_categories(bot: commands.Bot) -> int:
    """Стирает из БД активные кампании, чью категорию удалили в Discord мимо
    бота. В самом Discord ничего не трогает (каналы, роли, события остаются).
    Возвращает, сколько кампаний стёрто."""
    wiped = 0
    for campaign in await get_active_campaigns_with_category():
        what = f"категорию кампании «{campaign['name']}»"
        if await channel_is_really_deleted(bot, campaign["guild_id"], campaign["category_id"], what):
            await wipe_campaign(campaign["id"], bot.user.id)
            logger.info(
                "Категория кампании «%s» удалена в Discord — кампания стёрта из БД "
                "(каналы, роли и события в Discord не тронуты)",
                campaign["name"],
            )
            wiped += 1
    return wiped


def channel_type_of(channel) -> str:
    """Тип канала в том виде, в каком он хранится в БД."""
    return "voice" if isinstance(channel, (discord.VoiceChannel, discord.StageChannel)) else "text"


def infer_gm_only(channel, player_role_id: int) -> bool | None:
    """Определяет по правам канала, «только ГМ пишет/говорит» ли он: так бот
    выставляет права в build_access_overwrites — роли игроков запрещено писать
    (а в голосовом — подключаться). None — если роли игроков нет и понять нельзя."""
    role = channel.guild.get_role(player_role_id)
    if role is None:
        return None
    overwrite = channel.overwrites_for(role)
    return overwrite.send_messages is False or overwrite.connect is False


async def handle_channel_created(channel) -> bool:
    """Discord сообщил о новом канале. Если он в категории активной кампании —
    записываем его в БД (если его там ещё нет). True — если записали."""
    if isinstance(channel, discord.CategoryChannel) or channel.category_id is None:
        return False
    campaign = await get_active_campaign_by_category(channel.category_id)
    if campaign is None:
        return False

    adopted = await adopt_campaign_channel(
        campaign["id"], channel.id, channel_type_of(channel),
        bool(infer_gm_only(channel, campaign["player_role_id"])),
    )
    if adopted:
        logger.info("Новый канал %s в кампании «%s» записан в БД", describe_channel(channel), campaign["name"])
    return adopted


async def adopt_manual_channels(bot: commands.Bot) -> tuple[int, int, int]:
    """Еженедельная сверка каналов в категориях активных кампаний с БД:
    - канала нет в БД (создан вручную, пока бот был выключен) — записываем;
    - канал записан за другой кампанией (его перенесли сюда вручную) — перепривязываем;
    - режим «только ГМ» в БД приводим к текущим правам канала (их могли
      поменять руками в Discord).
    Если сервер недоступен или категории нет в памяти — кампанию пропускаем.
    Возвращает (сколько записано, сколько перепривязано, у скольких обновлён режим «только ГМ»)."""
    rows = await get_all_campaign_channels()
    known = {row["discord_channel_id"]: row["campaign_id"] for row in rows}
    known_gm_only = {row["discord_channel_id"]: bool(row["gm_only"]) for row in rows}
    campaign_names = {row["campaign_id"]: row["campaign_name"] for row in rows}
    adopted = moved = gm_updated = 0

    for campaign in await get_active_campaigns_with_category():
        guild = bot.get_guild(campaign["guild_id"])
        if guild is None or guild.unavailable:
            continue
        category = guild.get_channel(campaign["category_id"])
        if category is None:
            continue

        for channel in category.channels:
            gm_only = infer_gm_only(channel, campaign["player_role_id"])
            owner_id = known.get(channel.id)
            if owner_id is None:
                await adopt_campaign_channel(campaign["id"], channel.id, channel_type_of(channel), bool(gm_only))
                logger.info(
                    "Канал %s, которого не было в БД, записан в кампанию «%s» (режим «только ГМ»: %s)",
                    describe_channel(channel), campaign["name"], "да" if gm_only else "нет",
                )
                adopted += 1
                known[channel.id] = campaign["id"]
                continue

            if owner_id != campaign["id"]:
                await move_campaign_channel(channel.id, campaign["id"])
                logger.info(
                    "Канал %s перенесён вручную из кампании «%s» в «%s» — привязка в БД обновлена",
                    describe_channel(channel), campaign_names.get(owner_id, "?"), campaign["name"],
                )
                moved += 1
                known[channel.id] = campaign["id"]

            if gm_only is not None and gm_only != known_gm_only.get(channel.id):
                await set_campaign_channel_gm_only(channel.id, gm_only)
                logger.info(
                    "Канал %s кампании «%s»: режим «только ГМ» в БД обновлён по правам в Discord → %s",
                    describe_channel(channel), campaign["name"], "да" if gm_only else "нет",
                )
                gm_updated += 1

    return adopted, moved, gm_updated


async def cleanup_campaign_channels(bot: commands.Bot) -> int:
    """Удаляет из БД записи о каналах, которые удалили в Discord мимо бота.
    Возвращает, сколько записей удалено."""
    rows = await get_all_campaign_channels()
    removed = 0
    for row in rows:
        channel_id = row["discord_channel_id"]
        # Название удалённого канала уже не узнать — только id и кампания.
        what = f"канал (id {channel_id}) кампании «{row['campaign_name'] or '?'}»"
        if await channel_is_really_deleted(bot, row["guild_id"], channel_id, what):
            # Канал удалили в Discord мимо бота (не через /campaign archive
            # и не через /dev wipe_archive) — remove_deleted_channel сам решит,
            # надо ли заодно стереть заархивированную кампанию без каналов.
            await remove_deleted_channel(channel_id, bot.user.id)
            logger.info("Удалён в Discord %s — запись убрана из БД", what)
            removed += 1

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

    @commands.Cog.listener()
    async def on_guild_channel_create(self, channel: discord.abc.GuildChannel) -> None:
        try:
            await handle_channel_created(channel)
        except Exception:
            logger.exception("Ошибка при записи нового канала %s в БД", channel.id)

    @tasks.loop(hours=24 * 7)
    async def cleanup_stale_campaign_channels(self) -> None:
        try:
            # 1) кампании с удалённой категорией (их каналы уйдут вместе с ними);
            # 2) каналы, созданные или перенесённые вручную;
            # 3) отдельные удалённые каналы.
            wiped = await cleanup_deleted_categories(self.bot)
            adopted, moved, gm_updated = await adopt_manual_channels(self.bot)
            removed = await cleanup_campaign_channels(self.bot)
            # Итог пишется всегда — даже если ничего не нашлось, чтобы по логам
            # было видно, что сверка вообще прошла.
            logger.info(
                "Сверка кампаний завершена: стёрто кампаний — %d, записано новых каналов — %d, "
                "перепривязано — %d, обновлён режим «только ГМ» — %d, убрано удалённых каналов — %d",
                wiped, adopted, moved, gm_updated, removed,
            )
        except Exception:
            logger.exception("Очистка campaign_channels: непредвиденная ошибка")

    @cleanup_stale_campaign_channels.before_loop
    async def before_cleanup(self) -> None:
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(Campaigns(bot))