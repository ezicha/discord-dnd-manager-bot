from datetime import datetime, timezone

from db.connection import get_connection


async def create_campaign(
    guild_id: int,
    name: str,
    gm_role_id: int,
    player_role_id: int,
    category_id: int,
    actor_id: int,
) -> int:
    """Создаёт запись новой кампании и строку 'created' в истории.

    actor_id — Discord ID того, кто создал кампанию (для campaign_history).
    Возвращает id созданной записи в campaigns.
    """
    db = await get_connection()
    now = datetime.now(timezone.utc).isoformat()

    cursor = await db.execute(
        """
        INSERT INTO campaigns (guild_id, name, gm_role_id, player_role_id, category_id, status, created_at)
        VALUES (?, ?, ?, ?, ?, 'active', ?)
        """,
        (guild_id, name, gm_role_id, player_role_id, category_id, now),
    )
    campaign_id = cursor.lastrowid

    await db.execute(
        """
        INSERT INTO campaign_history (campaign_id, action, actor_id, created_at)
        VALUES (?, 'created', ?, ?)
        """,
        (campaign_id, actor_id, now),
    )

    await db.commit()
    return campaign_id


async def get_campaign_id_by_category(category_id: int) -> int | None:
    """Находит id активной кампании по её текущей category_id — нужно там, где
    на руках есть только Discord-объект категории, а не запись кампании из БД
    (например, при архивации всей кампании целиком, пока get_gm_campaigns ещё
    не переведён на поиск через БД). Возвращает None, если совпадения нет —
    например, кампания была создана ещё до перехода на БД.
    """
    db = await get_connection()
    cursor = await db.execute(
        "SELECT id FROM campaigns WHERE category_id = ? AND status = 'active'",
        (category_id,),
    )
    row = await cursor.fetchone()
    return row["id"] if row else None


async def get_campaign_id_by_gm_role(gm_role_id: int, status: str = "archived") -> int | None:
    """Находит id кампании по её gm_role_id, с фильтром по статусу (по умолчанию
    'archived' — нужно при resurrect, где на руках есть только Discord-объект
    роли ГМа, а campaign_id ещё не известен, пока get_gm_archived_campaigns
    не переведён на поиск через БД). Роль ГМа не меняется при архивации, в
    отличие от category_id — поэтому здесь ищем по роли, а не по категории.

    Переиспользует уже существующий _get_campaigns_by_gm_roles — просто со
    списком из одной роли, без дублирования SQL-запроса.
    """
    rows = await _get_campaigns_by_gm_roles([gm_role_id], status)
    return rows[0]["id"] if rows else None


async def _get_campaigns_by_gm_roles(gm_role_ids: list[int], status: str):
    if not gm_role_ids:
        return []

    db = await get_connection()
    placeholders = ", ".join("?" for _ in gm_role_ids)
    cursor = await db.execute(
        f"""
        SELECT * FROM campaigns
        WHERE status = ? AND gm_role_id IN ({placeholders})
        """,
        (status, *gm_role_ids),
    )
    return await cursor.fetchall()


async def get_gm_campaigns(gm_role_ids: list[int]):
    """Активные кампании, где пользователь — ГМ (по списку ID его ролей на сервере)."""
    return await _get_campaigns_by_gm_roles(gm_role_ids, "active")


async def get_gm_archived_campaigns(gm_role_ids: list[int]):
    """Заархивированные кампании, где пользователь — ГМ."""
    return await _get_campaigns_by_gm_roles(gm_role_ids, "archived")


async def archive_campaign(campaign_id: int, actor_id: int) -> None:
    """Помечает кампанию и все её каналы как заархивированные, обнуляет category_id
    (категория удаляется в Discord при архивации) и пишет строку 'archived' в историю.
    """
    db = await get_connection()
    now = datetime.now(timezone.utc).isoformat()

    await db.execute(
        """
        UPDATE campaigns
        SET status = 'archived', category_id = NULL
        WHERE id = ?
        """,
        (campaign_id,),
    )

    await db.execute(
        """
        UPDATE campaign_channels
        SET status = 'archived'
        WHERE campaign_id = ?
        """,
        (campaign_id,),
    )

    await db.execute(
        """
        INSERT INTO campaign_history (campaign_id, action, actor_id, created_at)
        VALUES (?, 'archived', ?, ?)
        """,
        (campaign_id, actor_id, now),
    )

    await db.commit()


async def resurrect_campaign(campaign_id: int, category_id: int, actor_id: int) -> None:
    """Возвращает кампанию и все её каналы в статус active, привязывает новую
    категорию (создаётся заново в Discord при resurrect) и пишет строку
    'resurrected' в историю.
    """
    db = await get_connection()
    now = datetime.now(timezone.utc).isoformat()

    await db.execute(
        """
        UPDATE campaigns
        SET status = 'active', category_id = ?
        WHERE id = ?
        """,
        (category_id, campaign_id),
    )

    await db.execute(
        """
        UPDATE campaign_channels
        SET status = 'active'
        WHERE campaign_id = ?
        """,
        (campaign_id,),
    )

    await db.execute(
        """
        INSERT INTO campaign_history (campaign_id, action, actor_id, created_at)
        VALUES (?, 'resurrected', ?, ?)
        """,
        (campaign_id, actor_id, now),
    )

    await db.commit()


async def get_campaign_channel_gm_only(discord_channel_id: int) -> bool | None:
    """Возвращает текущее значение gm_only для канала по его Discord ID, или
    None, если записи в campaign_channels нет (например, канал заведён ещё
    до перехода на БД). Используется при resurrect, чтобы восстановить канал
    с тем же режимом доступа, что был у него до архивации, а не всегда как
    "пишут все".
    """
    db = await get_connection()
    cursor = await db.execute(
        "SELECT gm_only FROM campaign_channels WHERE discord_channel_id = ?",
        (discord_channel_id,),
    )
    row = await cursor.fetchone()
    return bool(row["gm_only"]) if row else None


async def add_campaign_channel(
campaign_id: int,
discord_channel_id: int,
channel_type: str,
gm_only: bool = False,
) -> None:
    """Добавляет новый канал к существующей кампании (status = 'active')."""
    db = await get_connection()
    await db.execute(
         """
        INSERT INTO campaign_channels (campaign_id, discord_channel_id, channel_type, status, gm_only)
        VALUES (?, ?, ?, 'active', ?)
        """,
    (campaign_id, discord_channel_id, channel_type, int(gm_only)),
    )

    await db.commit()


async def archive_campaign_channel(discord_channel_id: int) -> None:
    """Архивирует один конкретный канал, не трогая остальные каналы кампании."""
    db = await get_connection()
    await db.execute(
        "UPDATE campaign_channels SET status = 'archived' WHERE discord_channel_id = ?",
        (discord_channel_id,),
    )
    await db.commit()


async def unarchive_campaign_channel(discord_channel_id: int) -> None:
    """Возвращает один ранее заархивированный канал в active.

    Важно: сама функция не проверяет статус родительской кампании — это
    решение о том, разрешено ли восстановление (кампания должна быть
    active, а не archived целиком), остаётся на стороне вызывающего кода.
    """
    db = await get_connection()
    await db.execute(
        "UPDATE campaign_channels SET status = 'active' WHERE discord_channel_id = ?",
        (discord_channel_id,),
    )
    await db.commit()


async def remove_deleted_channel(discord_channel_id: int, actor_id: int) -> None:
    """Вызывается после того, как канал уже физически удалён в Discord.

    Убирает его из campaign_channels; если после этого у родительской
    кампании не осталось ни одного канала — удаляет и саму кампанию,
    предварительно записав 'wiped' в историю.
    """
    db = await get_connection()
    now = datetime.now(timezone.utc).isoformat()

    cursor = await db.execute(
        "SELECT campaign_id FROM campaign_channels WHERE discord_channel_id = ?",
        (discord_channel_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        # Канал не был известен БД (например, создан ещё до перехода на БД) — нечего чистить.
        return
    campaign_id = row["campaign_id"]

    await db.execute(
        "DELETE FROM campaign_channels WHERE discord_channel_id = ?",
        (discord_channel_id,),
    )

    cursor = await db.execute(
        "SELECT COUNT(*) AS cnt FROM campaign_channels WHERE campaign_id = ?",
        (campaign_id,),
    )
    remaining = (await cursor.fetchone())["cnt"]

    if remaining == 0:
        await db.execute(
            """
            INSERT INTO campaign_history (campaign_id, action, actor_id, created_at)
            VALUES (?, 'wiped', ?, ?)
            """,
            (campaign_id, actor_id, now),
        )
        await db.execute("DELETE FROM campaigns WHERE id = ?", (campaign_id,))

    await db.commit()


async def set_campaign_channel_gm_only(discord_channel_id: int, gm_only: bool) -> None:
    """Обновляет флаг gm_only у канала — используется в /campaign edit → «Изменить
    доступ», когда меняют, кто может писать в уже существующем канале кампании.
    """
    db = await get_connection()
    await db.execute(
        "UPDATE campaign_channels SET gm_only = ? WHERE discord_channel_id = ?",
        (int(gm_only), discord_channel_id),
    )
    await db.commit()