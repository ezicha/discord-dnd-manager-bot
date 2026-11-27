"""
Автотесты обёртки transaction() (db/connection.py): изменения базы либо
сохраняются целиком, либо отменяются целиком, и одновременные операции
записи не перемешиваются.
"""

import asyncio

import pytest

from conftest import close_test_db, open_test_db
from db.campaigns_db import create_campaign
from db.connection import transaction
from db.events_db import delete_event_record


def run(scenario):
    async def wrapper():
        conn = await open_test_db()
        try:
            return await scenario(conn)
        finally:
            await close_test_db()
    return asyncio.run(wrapper())


async def count(conn, table: str) -> int:
    cur = await conn.execute(f"SELECT COUNT(*) FROM {table}")
    return (await cur.fetchone())[0]


async def insert_campaign(db) -> None:
    await db.execute(
        "INSERT INTO campaigns (guild_id, name, gm_role_id, player_role_id, category_id, created_at) "
        "VALUES (1, 'Тест', 2, 3, 4, '2026-01-01')"
    )


def test_success_is_saved():
    async def scenario(conn):
        campaign_id = await create_campaign(1, "Тест", 2, 3, 4, actor_id=5)
        return campaign_id, await count(conn, "campaigns"), await count(conn, "campaign_history"), conn.in_transaction

    campaign_id, campaigns, history, open_tx = run(scenario)
    assert campaign_id == 1
    assert (campaigns, history) == (1, 1)
    assert open_tx is False  # всё сохранено, незавершённых изменений нет


def test_error_midway_rolls_back_everything():
    async def scenario(conn):
        with pytest.raises(RuntimeError):
            async with transaction() as db:
                await insert_campaign(db)
                raise RuntimeError("сбой посередине")
        # Любая следующая запись (тут — удаление события) раньше сохраняла
        # бы и недоделанную кампанию. Теперь её уже нет.
        await delete_event_record(123)
        return await count(conn, "campaigns")

    assert run(scenario) == 0


def test_concurrent_write_waits_and_does_not_save_foreign_half():
    """Пока операция А не закончилась, операция Б ждёт; А падает —
    её изменения не попадают в базу через commit операции Б."""
    async def scenario(conn):
        order = []
        a_started = asyncio.Event()

        async def op_a():
            try:
                async with transaction() as db:
                    await insert_campaign(db)
                    a_started.set()
                    await asyncio.sleep(0.05)  # Б пытается вклиниться здесь
                    order.append("A")
                    raise RuntimeError("сбой в А")
            except RuntimeError:
                pass

        async def op_b():
            await a_started.wait()
            await delete_event_record(123)
            order.append("B")

        await asyncio.gather(op_a(), op_b())
        return order, await count(conn, "campaigns")

    order, campaigns = run(scenario)
    assert order == ["A", "B"]
    assert campaigns == 0
