from db.connection import get_connection


async def record_event(
    scheduled_event_id: int,
    campaign_id: int,
    creator_id: int,
    announcement_channel_id: int | None = None,
    announcement_message_id: int | None = None,
) -> None:
    """Записывает новое событие — вызывается сразу после создания
    Discord Scheduled Event через /event create.
    """
    db = await get_connection()
    await db.execute(
        """
        INSERT INTO event_announcements
            (scheduled_event_id, campaign_id, creator_id, announcement_channel_id, announcement_message_id)
        VALUES (?, ?, ?, ?, ?)
        """,
        (scheduled_event_id, campaign_id, creator_id, announcement_channel_id, announcement_message_id),
    )
    await db.commit()


async def get_event_record(scheduled_event_id: int):
    """Возвращает запись события по его Discord Scheduled Event ID, или None,
    если события с таким ID в БД нет. Нужна для /event cancel и /event edit —
    чтобы получить creator_id (для проверки прав) и текущий анонс.
    """
    db = await get_connection()
    cursor = await db.execute(
        "SELECT * FROM event_announcements WHERE scheduled_event_id = ?",
        (scheduled_event_id,),
    )
    return await cursor.fetchone()


async def update_event_announcement(
    scheduled_event_id: int,
    announcement_channel_id: int | None,
    announcement_message_id: int | None,
) -> None:
    """Обновляет привязку к сообщению-анонсу — вызывается из /event edit,
    когда канал анонса поменялся (или анонс появился/пропал). Если канал не
    менялся, вызывающий код просто не трогает эту функцию — редактируется то
    же самое сообщение, в БД менять нечего.
    """
    db = await get_connection()
    await db.execute(
        """
        UPDATE event_announcements
        SET announcement_channel_id = ?, announcement_message_id = ?
        WHERE scheduled_event_id = ?
        """,
        (announcement_channel_id, announcement_message_id, scheduled_event_id),
    )
    await db.commit()


async def delete_event_record(scheduled_event_id: int) -> None:
    """Удаляет запись события — вызывается из /event cancel и из
    периодической автоочистки мёртвых записей.
    """
    db = await get_connection()
    await db.execute(
        "DELETE FROM event_announcements WHERE scheduled_event_id = ?",
        (scheduled_event_id,),
    )
    await db.commit()


async def get_all_scheduled_event_ids() -> list[int]:
    """Все scheduled_event_id, которые сейчас есть в БД — нужно периодической
    задаче автоочистки, чтобы сверить со списком реальных guild.scheduled_events
    и найти "мёртвые" записи (события, отменённые не через /event cancel).
    """
    db = await get_connection()
    cursor = await db.execute("SELECT scheduled_event_id FROM event_announcements")
    rows = await cursor.fetchall()
    return [row["scheduled_event_id"] for row in rows]