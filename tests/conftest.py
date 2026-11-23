"""
Общие заготовки для автотестов.

Тесты не подключаются к настоящему Discord и не трогают настоящую БД
(data/argus.db): вместо них — «поддельный» бот и временная БД в памяти.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

import aiosqlite
import discord

# Чтобы тесты видели пакеты проекта (db, cogs) при запуске из любой папки.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import db.connection as connection  # noqa: E402


async def open_test_db() -> aiosqlite.Connection:
    """Открывает пустую БД в памяти и подменяет ею соединение бота."""
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    connection._connection = conn
    await connection.init_db()
    return conn


async def close_test_db() -> None:
    if connection._connection is not None:
        await connection._connection.close()
        connection._connection = None


def http_error(cls: type[discord.HTTPException], status: int) -> discord.HTTPException:
    """Создаёт ошибку Discord (NotFound, Forbidden и т.п.) без реального запроса."""
    return cls(SimpleNamespace(status=status, reason="test"), "test")
