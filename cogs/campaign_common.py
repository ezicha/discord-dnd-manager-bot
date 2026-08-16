import logging
logger = logging.getLogger("argus")

import discord

# --- Константы ---
# Вынесены сюда, чтобы при необходимости переименовать архивную категорию
# или изменить формат имени ГМ-роли не пришлось искать текст по всему проекту.
ARCHIVE_CATEGORY_NAME = "Архив"
GM_ROLE_PREFIX = "ГМ: "


def slugify(name: str) -> str:
    return name.lower().replace(" ", "-")


def channel_select_option(ch: discord.abc.GuildChannel) -> discord.SelectOption:
    """
    Собирает SelectOption для одного канала с явным различением войса и
    текста (эмодзи + подпись) — нужно, чтобы каналы с одинаковым названием
    не путались в select-меню.
    """
    return discord.SelectOption(
        label=ch.name,
        value=str(ch.id),
        description="Голосовой канал" if isinstance(ch, discord.VoiceChannel) else "Текстовый канал",
        emoji="🔊" if isinstance(ch, discord.VoiceChannel) else "#️⃣",
    )


# Discord ограничивает select-меню 25 опциями — это жёсткий лимит компонента,
# превышение которого падает с ошибкой при создании View, а не мягким предупреждением.
SELECT_OPTIONS_LIMIT = 25
 
 
def channel_options(category: discord.CategoryChannel, limit: int = SELECT_OPTIONS_LIMIT) -> tuple[list[discord.SelectOption], bool]:
    """
    Собирает список SelectOption из каналов категории (через channel_select_option,
    чтобы у каждого канала было различение войс/текст), обрезая до limit.
    Используется во всех select-меню "выбери канал из категории".
    Возвращает (options, truncated) — truncated=True, если каналов больше лимита
    и часть из них не попала в список.
    """
    channels = category.channels
    truncated = len(channels) > limit
    options = [channel_select_option(ch) for ch in channels[:limit]]
    return options, truncated
 
 
def build_select_options(labels: list[str], limit: int = SELECT_OPTIONS_LIMIT) -> tuple[list[discord.SelectOption], bool]:
    """
    Собирает список SelectOption из строковых меток (например, названий кампаний),
    обрезая до limit. Возвращает (options, truncated) — как и channel_options.
    """
    truncated = len(labels) > limit
    options = [discord.SelectOption(label=label) for label in labels[:limit]]
    return options, truncated
 

def build_access_overwrites(
    guild: discord.Guild,
    campaign_role: discord.Role | None,
    gm_role: discord.Role | None,
    gm_only: bool,
    is_voice: bool,
    base_overwrites: dict | None = None,
) -> dict:
    """
    Собирает overwrites для канала в зависимости от того, кто может в нём писать/говорить.

    gm_only=True  -> участники кампании видят канал, но не могут писать
                      (а если это голосовой канал — не могут и подключаться),
                      писать/говорить может только ГМ.
    gm_only=False -> пишут и говорят все участники кампании.

    base_overwrites можно передать, если нужно сохранить уже существующие
    overwrites канала (например, при редактировании доступа).
    """
    overwrites = dict(base_overwrites) if base_overwrites else {}
    overwrites[guild.default_role] = discord.PermissionOverwrite(view_channel=False)

    if campaign_role:
        if gm_only:
            overwrites[campaign_role] = discord.PermissionOverwrite(
                view_channel=True,
                send_messages=False,
                connect=(False if is_voice else None),
            )
        else:
            overwrites[campaign_role] = discord.PermissionOverwrite(view_channel=True)

    if gm_role:
        overwrites[gm_role] = discord.PermissionOverwrite(
            view_channel=True,
            send_messages=True if gm_only else None,
        )

    return overwrites


async def get_or_create_archive_category(guild: discord.Guild) -> discord.CategoryChannel:
    """
    Возвращает категорию "Архив", создавая её при необходимости,
    и следит, чтобы она всегда была в самом низу списка каналов.
    """
    archive_category = discord.utils.get(guild.categories, name=ARCHIVE_CATEGORY_NAME)
    if archive_category is None:
        archive_category = await guild.create_category(name=ARCHIVE_CATEGORY_NAME)
    await archive_category.move(end=True)
    return archive_category


async def move_archive_to_end(guild: discord.Guild):
    """
    Если категория "Архив" уже существует — двигает её в конец списка.
    В отличие от get_or_create_archive_category, ничего не создаёт:
    используется после создания новой кампании, когда архива может ещё не быть.
    """
    archive_category = discord.utils.get(guild.categories, name=ARCHIVE_CATEGORY_NAME)
    if archive_category:
        await archive_category.move(end=True)


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

async def resurrect_channel(guild, channel, category, campaign_role, gm_role):
    """
    Возвращает один заархивированный канал обратно в категорию кампании
    и восстанавливает обычный доступ (писать могут все участники кампании).
 
    Индивидуальные настройки доступа канала (например «только ГМ»), если они
    были у него до архивации, не сохраняются — при необходимости их можно
    заново включить через /campaign_edit → «Изменить доступ». Название канала
    (вместе с префиксом архивации) тоже не меняется — переименовать его можно
    там же, через «Редактировать канал».
    """
    overwrites = channel.overwrites
    overwrites[guild.default_role] = discord.PermissionOverwrite(view_channel=False)
    if campaign_role:
        overwrites[campaign_role] = discord.PermissionOverwrite(view_channel=True)
    if gm_role:
        overwrites[gm_role] = discord.PermissionOverwrite(view_channel=True, manage_channels=True)
    await channel.edit(category=category, overwrites=overwrites, sync_permissions=False)
    return channel

async def deliver_result(interaction: discord.Interaction, channels: list, message: str, short_ack: str = "Готово.") -> None:
    """
    Отправляет итоговое сообщение о выполнении команды не в канал вызова команды,
    а в затронутые текстовые каналы — в каждый из них, если их несколько
    (голосовые каналы из списка просто игнорируются, в них нельзя писать текстом).
    Взаимодействие при этом закрывается коротким приватным (ephemeral) подтверждением,
    чтобы у вызвавшего не осталось висящего "бот думает...".
 
    Если среди channels не нашлось ни одного текстового канала (например, у кампании
    есть только голосовой канал) — само итоговое сообщение уходит вызвавшему как
    ephemeral-ответ, короткое подтверждение в этом случае не отправляется.
 
    Перед вызовом взаимодействие должно быть подтверждено через
    interaction.response.defer(ephemeral=True).
    """
    text_channels = [ch for ch in channels if isinstance(ch, discord.TextChannel)]
    if text_channels:
        for ch in text_channels:
            await ch.send(message)
        await interaction.followup.send(short_ack, ephemeral=True)
    else:
        await interaction.followup.send(message, ephemeral=True)
 

def get_gm_campaigns(guild: discord.Guild, member: discord.Member) -> dict:
    """
    Возвращает словарь кампаний, где member является ГМом:
    { "Название кампании": (category, campaign_role, gm_role) }
    """
    gm_roles = [r for r in member.roles if r.name.startswith(GM_ROLE_PREFIX)]
    campaigns = {}
    for role in gm_roles:
        campaign_name = role.name.removeprefix(GM_ROLE_PREFIX)
        category = discord.utils.get(guild.categories, name=campaign_name)
        if category:
            campaign_role = discord.utils.get(guild.roles, name=campaign_name)
            campaigns[campaign_name] = (category, campaign_role, role)
    return campaigns

def get_gm_archived_campaigns(guild: discord.Guild, member: discord.Member) -> dict:
    """
    Возвращает словарь заархивированных кампаний, где member является ГМом:
    { "Название кампании": (campaign_role, gm_role, [заархивированные каналы]) }
 
    Кампания считается заархивированной, если у ГМа есть роль "ГМ: <название>",
    активной категории с таким названием сейчас не существует, а в категории
    "Архив" есть хотя бы один канал с правами доступа, выставленными именно
    для роли участника или роли ГМа этой кампании. Поиск идёт по правам
    доступа, а не по названию канала — префикс архивации вводится вручную
    и не обязан совпадать с названием кампании, так что имени канала
    доверять нельзя.
    """
    archive_category = discord.utils.get(guild.categories, name=ARCHIVE_CATEGORY_NAME)
    gm_roles = [r for r in member.roles if r.name.startswith(GM_ROLE_PREFIX)]
    campaigns = {}
    for role in gm_roles:
        campaign_name = role.name.removeprefix(GM_ROLE_PREFIX)
        if discord.utils.get(guild.categories, name=campaign_name):
            continue  # кампания активна, а не заархивирована
 
        campaign_role = discord.utils.get(guild.roles, name=campaign_name)
 
        archived_channels = []
        if archive_category:
            for ch in archive_category.channels:
                if role in ch.overwrites or (campaign_role and campaign_role in ch.overwrites):
                    archived_channels.append(ch)
 
        if archived_channels:
            campaigns[campaign_name] = (campaign_role, role, archived_channels)
    return campaigns