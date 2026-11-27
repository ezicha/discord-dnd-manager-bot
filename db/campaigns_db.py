from datetime import datetime, timezone

from db.connection import get_connection, transaction


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
    async with transaction() as db:
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
    async with transaction() as db:
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


async def resurrect_campaign(campaign_id: int, category_id: int, actor_id: int) -> None:
    """Возвращает кампанию и все её каналы в статус active, привязывает новую
    категорию (создаётся заново в Discord при resurrect) и пишет строку
    'resurrected' в историю.
    """
    async with transaction() as db:
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
    """Добавляет новый канал к существующей кампании (status = 'active').

    Вызывается командами бота (/campaign create, /campaign edit). Канал к этому
    моменту уже мог быть записан обработчиком «создан канал» (adopt_campaign_channel) —
    тогда новую строку не добавляем, а только выставляем gm_only, выбранный
    в команде: он точнее, чем угаданный по правам канала.
    """
    async with transaction() as db:
        await _insert_channel_if_missing(db, campaign_id, discord_channel_id, channel_type, gm_only)
        await db.execute(
            "UPDATE campaign_channels SET gm_only = ? WHERE discord_channel_id = ?",
            (int(gm_only), discord_channel_id),
        )


async def _insert_channel_if_missing(db, campaign_id, discord_channel_id, channel_type, gm_only) -> bool:
    """Одним запросом: вставить строку канала, только если такого канала ещё нет.
    Одним — чтобы два одновременных вызова не создали дубль. True — если вставили."""
    cursor = await db.execute(
        """
        INSERT INTO campaign_channels (campaign_id, discord_channel_id, channel_type, status, gm_only)
        SELECT ?, ?, ?, 'active', ?
        WHERE NOT EXISTS (SELECT 1 FROM campaign_channels WHERE discord_channel_id = ?)
        """,
        (campaign_id, discord_channel_id, channel_type, int(gm_only), discord_channel_id),
    )
    return cursor.rowcount > 0


async def adopt_campaign_channel(
    campaign_id: int,
    discord_channel_id: int,
    channel_type: str,
    gm_only: bool,
) -> bool:
    """Записывает в БД канал, созданный в категории кампании вручную (мимо бота).
    Если канал уже известен — ничего не делает. True — если канал добавлен."""
    async with transaction() as db:
        inserted = await _insert_channel_if_missing(db, campaign_id, discord_channel_id, channel_type, gm_only)
        return inserted


async def register_existing_campaign(
    guild_id: int,
    name: str,
    gm_role_id: int,
    player_role_id: int,
    category_id: int,
    channels: list[tuple[int, str, bool]],
    actor_id: int,
) -> int | None:
    """Записывает в БД кампанию, которая уже есть в Discord, но о которой БД
    не знает (создана до перехода на БД или потеряна). Вызывается сверкой.

    channels — каналы её категории: [(discord_channel_id, 'text'/'voice', gm_only)].
    Каналы, уже известные БД, не трогаются.

    Ничего не записывает (возвращает None), если в БД уже есть кампания
    с этой категорией или с этой ролью ГМа (в любом статусе) — чтобы не
    получить две записи об одной кампании. Иначе возвращает id новой записи.
    Всё делается одной транзакцией: либо кампания записана целиком, либо никак.
    """
    async with transaction() as db:
        cursor = await db.execute(
            "SELECT 1 FROM campaigns WHERE category_id = ? OR gm_role_id = ?",
            (category_id, gm_role_id),
        )
        if await cursor.fetchone() is not None:
            return None

        now = datetime.now(timezone.utc).isoformat()
        cursor = await db.execute(
            """
            INSERT INTO campaigns (guild_id, name, gm_role_id, player_role_id, category_id, status, created_at)
            VALUES (?, ?, ?, ?, ?, 'active', ?)
            """,
            (guild_id, name, gm_role_id, player_role_id, category_id, now),
        )
        campaign_id = cursor.lastrowid

        for channel_id, channel_type, gm_only in channels:
            await _insert_channel_if_missing(db, campaign_id, channel_id, channel_type, gm_only)

        # 'registered' — запись появилась не через /campaign create, а сверкой:
        # по истории будет видно, откуда кампания взялась в БД.
        await db.execute(
            """
            INSERT INTO campaign_history (campaign_id, action, actor_id, created_at)
            VALUES (?, 'registered', ?, ?)
            """,
            (campaign_id, actor_id, now),
        )
        return campaign_id


async def get_all_campaign_category_ids() -> set[int]:
    """id категорий всех кампаний в БД (у заархивированных категории нет) —
    сверке нужно отличать уже известные категории от новых."""
    db = await get_connection()
    cursor = await db.execute("SELECT category_id FROM campaigns WHERE category_id IS NOT NULL")
    return {row["category_id"] for row in await cursor.fetchall()}


async def move_campaign_channel(discord_channel_id: int, new_campaign_id: int) -> None:
    """Перепривязывает канал к другой кампании — когда его вручную перенесли
    в категорию другой активной кампании."""
    async with transaction() as db:
        await db.execute(
            "UPDATE campaign_channels SET campaign_id = ?, status = 'active' WHERE discord_channel_id = ?",
            (new_campaign_id, discord_channel_id),
        )


async def get_active_campaign_by_category(category_id: int) -> dict | None:
    """Активная кампания по id её категории (с player_role_id — он нужен,
    чтобы понять по правам канала, «только ГМ» ли он). None — если нет."""
    db = await get_connection()
    cursor = await db.execute(
        "SELECT id, guild_id, name, category_id, player_role_id FROM campaigns "
        "WHERE category_id = ? AND status = 'active'",
        (category_id,),
    )
    row = await cursor.fetchone()
    return dict(row) if row else None


async def archive_campaign_channel(discord_channel_id: int) -> None:
    """Архивирует один конкретный канал, не трогая остальные каналы кампании."""
    async with transaction() as db:
        await db.execute(
            "UPDATE campaign_channels SET status = 'archived' WHERE discord_channel_id = ?",
            (discord_channel_id,),
        )


async def unarchive_campaign_channel(discord_channel_id: int) -> None:
    """Возвращает один ранее заархивированный канал в active.

    Важно: сама функция не проверяет статус родительской кампании — это
    решение о том, разрешено ли восстановление (кампания должна быть
    active, а не archived целиком), остаётся на стороне вызывающего кода.
    """
    async with transaction() as db:
        await db.execute(
            "UPDATE campaign_channels SET status = 'active' WHERE discord_channel_id = ?",
            (discord_channel_id,),
        )


async def remove_deleted_channel(discord_channel_id: int, actor_id: int) -> None:
    """Вызывается после того, как канал уже физически удалён в Discord.

    Убирает его из campaign_channels. Если после этого у ЗААРХИВИРОВАННОЙ
    кампании не осталось ни одного канала — стирает и саму кампанию
    (с записью 'wiped' в историю).

    Активную кампанию здесь не стираем, даже если известных БД каналов
    не осталось: в её категории могут быть каналы, созданные вручную.
    Активная кампания считается удалённой только когда удалена её
    категория — это решает автоочистка через wipe_campaign.
    """
    async with transaction() as db:
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
            cursor = await db.execute("SELECT status FROM campaigns WHERE id = ?", (campaign_id,))
            campaign = await cursor.fetchone()
            if campaign is not None and campaign["status"] == "archived":
                await _wipe_campaign_rows(db, campaign_id, actor_id)


async def _wipe_campaign_rows(db, campaign_id: int, actor_id: int) -> None:
    """Стирает кампанию и всё, что на неё ссылается (каналы, записи событий),
    записав 'wiped' в историю. Без commit — его делает вызывающая функция.
    Сама история кампании (campaign_history) остаётся.
    """
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        """
        INSERT INTO campaign_history (campaign_id, action, actor_id, created_at)
        VALUES (?, 'wiped', ?, ?)
        """,
        (campaign_id, actor_id, now),
    )
    await db.execute("DELETE FROM event_announcements WHERE campaign_id = ?", (campaign_id,))
    await db.execute("DELETE FROM campaign_channels WHERE campaign_id = ?", (campaign_id,))
    await db.execute("DELETE FROM campaigns WHERE id = ?", (campaign_id,))


async def wipe_campaign(campaign_id: int, actor_id: int) -> None:
    """Стирает кампанию из БД — для автоочистки, когда категорию активной
    кампании удалили в Discord мимо бота. В самом Discord ничего не трогает."""
    async with transaction() as db:
        await _wipe_campaign_rows(db, campaign_id, actor_id)


async def get_active_campaigns_with_category() -> list[dict]:
    """Все активные кампании с привязанной категорией — для автоочистки,
    которая проверяет, не удалили ли категорию в Discord."""
    db = await get_connection()
    cursor = await db.execute(
        """
        SELECT id, guild_id, name, category_id, player_role_id
        FROM campaigns
        WHERE status = 'active' AND category_id IS NOT NULL
        """
    )
    return [dict(r) for r in await cursor.fetchall()]


async def set_campaign_channel_gm_only(discord_channel_id: int, gm_only: bool) -> None:
    """Обновляет флаг gm_only у канала — используется в /campaign edit → «Изменить
    доступ», когда меняют, кто может писать в уже существующем канале кампании.
    """
    async with transaction() as db:
        await db.execute(
            "UPDATE campaign_channels SET gm_only = ? WHERE discord_channel_id = ?",
            (int(gm_only), discord_channel_id),
        )


async def get_all_campaign_channels():
    """Возвращает все строки campaign_channels, независимо от статуса —
    используется периодической самоочисткой, которая сверяет их с реальным
    состоянием Discord.

    К каждой строке добавлены guild_id сервера, которому принадлежит кампания
    (самоочистке нужно знать, доступен ли этот сервер прямо сейчас), и
    campaign_name — для понятных сообщений в логах. Если сама кампания в БД
    не найдена, оба будут None.
    """
    db = await get_connection()
    cursor = await db.execute(
        """
        SELECT cc.*, c.guild_id AS guild_id, c.name AS campaign_name
        FROM campaign_channels AS cc
        LEFT JOIN campaigns AS c ON c.id = cc.campaign_id
        """
    )
    return await cursor.fetchall()


async def get_orphaned_campaign_history() -> list[dict]:
    """
    Группы записей campaign_history, чей campaign_id больше не существует
    в таблице campaigns (кампания реально удалена, например через
    /dev wipe_archive). Одна строка результата — один campaign_id.
    """
    db = await get_connection()
    cursor = await db.execute(
        """
        SELECT campaign_id, COUNT(*) AS cnt,
               MIN(created_at) AS first_at, MAX(created_at) AS last_at
        FROM campaign_history
        WHERE campaign_id NOT IN (SELECT id FROM campaigns)
        GROUP BY campaign_id
        ORDER BY last_at DESC
        """
    )
    rows = await cursor.fetchall()
    return [dict(r) for r in rows]


async def delete_orphaned_campaign_history(campaign_ids: list[int]) -> int:
    """
    Удаляет записи campaign_history для переданных campaign_id — но только
    те, что всё ещё "осиротевшие" на момент удаления (доп. проверка на
    случай, если между показом списка и подтверждением что-то изменилось).
    Возвращает количество реально удалённых строк.
    """
    if not campaign_ids:
        return 0
    async with transaction() as db:
        placeholders = ",".join("?" for _ in campaign_ids)
        cursor = await db.execute(
            f"""
            DELETE FROM campaign_history
            WHERE campaign_id IN ({placeholders})
              AND campaign_id NOT IN (SELECT id FROM campaigns)
            """,
            campaign_ids,
        )
        return cursor.rowcount
