import aiosqlite

DB_PATH = "data/argus.db"

# Единственное соединение на всё время жизни бота.
# Не открывается заново на каждый запрос — открывается один раз при старте.
_connection: aiosqlite.Connection | None = None


async def get_connection() -> aiosqlite.Connection:
    """Возвращает открытое соединение с БД, открывая его при первом вызове."""
    global _connection
    if _connection is None:
        _connection = await aiosqlite.connect(DB_PATH)
        # Чтобы строки результатов запроса можно было обращаться по имени
        # колонки (row["name"]), а не только по числовому индексу.
        _connection.row_factory = aiosqlite.Row
    return _connection


async def init_db() -> None:
    """Создаёт все таблицы, если их ещё нет. Вызывается один раз при старте бота."""
    db = await get_connection()

    await db.execute("""
        CREATE TABLE IF NOT EXISTS campaigns (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            guild_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            gm_role_id INTEGER NOT NULL,
            player_role_id INTEGER NOT NULL,
            category_id INTEGER,
            status TEXT NOT NULL DEFAULT 'active',
            created_at TEXT NOT NULL
        )
    """)

    await db.execute("""
        CREATE TABLE IF NOT EXISTS campaign_channels (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
            discord_channel_id INTEGER NOT NULL,
            channel_type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            gm_only INTEGER NOT NULL DEFAULT 0
        )
    """)

    await db.execute("""
        CREATE TABLE IF NOT EXISTS event_announcements (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            scheduled_event_id INTEGER NOT NULL UNIQUE,
            campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
            creator_id INTEGER NOT NULL,
            announcement_channel_id INTEGER,
            announcement_message_id INTEGER
        )
    """)

    await db.execute("""
        CREATE TABLE IF NOT EXISTS campaign_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            campaign_id INTEGER NOT NULL REFERENCES campaigns(id),
            action TEXT NOT NULL,
            actor_id INTEGER NOT NULL,
            created_at TEXT NOT NULL
        )
    """)

    await db.commit()


async def close_db() -> None:
    """Закрывает соединение. Вызывается при остановке бота (необязательно, но аккуратно)."""
    global _connection
    if _connection is not None:
        await _connection.close()
        _connection = None