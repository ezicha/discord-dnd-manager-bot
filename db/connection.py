import asyncio
from contextlib import asynccontextmanager

import aiosqlite

DB_PATH = "data/argus.db"

# Единственное соединение на всё время жизни бота.
# Не открывается заново на каждый запрос — открывается один раз при старте.
_connection: aiosqlite.Connection | None = None

# Замок для изменений базы: пока одна операция записи не закончилась,
# следующая ждёт. Иначе операции на общем соединении перемешиваются,
# и commit одной сохраняет недоделанные изменения другой.
# Хранится вместе с циклом событий, в котором создан: замок нельзя
# переносить между циклами (в автотестах у каждого сценария свой цикл).
_write_lock: asyncio.Lock | None = None
_write_lock_loop: asyncio.AbstractEventLoop | None = None


async def get_connection() -> aiosqlite.Connection:
    """Возвращает открытое соединение с БД, открывая его при первом вызове."""
    global _connection
    if _connection is None:
        _connection = await aiosqlite.connect(DB_PATH)
        # Чтобы строки результатов запроса можно было обращаться по имени
        # колонки (row["name"]), а не только по числовому индексу.
        _connection.row_factory = aiosqlite.Row
    return _connection


def _get_write_lock() -> asyncio.Lock:
    global _write_lock, _write_lock_loop
    loop = asyncio.get_running_loop()
    if _write_lock is None or _write_lock_loop is not loop:
        _write_lock = asyncio.Lock()
        _write_lock_loop = loop
    return _write_lock


@asynccontextmanager
async def transaction():
    """Обёртка для любых изменений базы:

        async with transaction() as db:
            await db.execute(...)
            await db.execute(...)

    - операции записи выполняются строго по очереди, не перемешиваясь;
    - если блок закончился без ошибок — изменения сохраняются (commit);
    - если внутри ошибка — всё, что блок успел изменить, отменяется
      (rollback), а ошибка идёт дальше, как и раньше.

    Внутри блока commit не вызывать — его делает обёртка. И не вызывать
    другие функции, которые сами открывают transaction(): замок не
    повторный, они будут ждать сами себя вечно.
    """
    async with _get_write_lock():
        db = await get_connection()
        try:
            yield db
        except BaseException:
            await db.rollback()
            raise
        await db.commit()


async def init_db() -> None:
    """Создаёт все таблицы, если их ещё нет. Вызывается один раз при старте бота."""
    async with transaction() as db:
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


async def close_db() -> None:
    """Закрывает соединение. Вызывается при остановке бота (необязательно, но аккуратно)."""
    global _connection
    if _connection is not None:
        await _connection.close()
        _connection = None
