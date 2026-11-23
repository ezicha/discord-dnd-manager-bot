"""
Автотесты реакции на удаление события в Discord (cogs/events/events.py,
handle_scheduled_event_deleted) — должна вести себя как /event cancel:
удалить анонс и запись в БД. Сыгранные события не трогаем.
"""

import asyncio
from types import SimpleNamespace

import discord

from conftest import close_test_db, http_error, open_test_db
from cogs.events.events import handle_scheduled_event_deleted

EVENT_ID = 777
ANNOUNCE_CHANNEL_ID = 10
ANNOUNCE_MESSAGE_ID = 20


def make_guild(*, message_missing=False, channel_missing=False):
    """Поддельный сервер с каналом анонса. deleted — список удалённых сообщений."""
    deleted = []

    async def delete_message():
        deleted.append(ANNOUNCE_MESSAGE_ID)

    async def fetch_message(mid):
        if message_missing:
            raise http_error(discord.NotFound, 404)
        return SimpleNamespace(delete=delete_message)

    channel = SimpleNamespace(fetch_message=fetch_message)
    guild = SimpleNamespace(
        get_channel=lambda cid: None if channel_missing else (channel if cid == ANNOUNCE_CHANNEL_ID else None)
    )
    return guild, deleted


def make_event(guild, status=discord.EventStatus.scheduled):
    return SimpleNamespace(id=EVENT_ID, name="Сессия 1", guild=guild, status=status)


def run_scenario(event, *, with_record=True, with_announcement=True, times=1):
    """Возвращает (результаты вызовов, осталось записей в БД)."""
    async def scenario():
        conn = await open_test_db()
        try:
            if with_record:
                await conn.execute(
                    "INSERT INTO event_announcements "
                    "(scheduled_event_id, campaign_id, creator_id, announcement_channel_id, announcement_message_id) "
                    "VALUES (?, 1, 1, ?, ?)",
                    (EVENT_ID,
                     ANNOUNCE_CHANNEL_ID if with_announcement else None,
                     ANNOUNCE_MESSAGE_ID if with_announcement else None),
                )
                await conn.commit()
            results = [await handle_scheduled_event_deleted(event) for _ in range(times)]
            cur = await conn.execute("SELECT COUNT(*) FROM event_announcements")
            return results, (await cur.fetchone())[0]
        finally:
            await close_test_db()

    return asyncio.run(scenario())


def test_deleted_event_removes_announcement_and_record():
    guild, deleted = make_guild()
    results, left = run_scenario(make_event(guild))
    assert results == [True]
    assert left == 0
    assert deleted == [ANNOUNCE_MESSAGE_ID]


def test_second_call_does_nothing():
    # Так будет при /event cancel: команда уже всё убрала, потом приходит сигнал от Discord.
    guild, deleted = make_guild()
    results, left = run_scenario(make_event(guild), times=2)
    assert results == [True, False]
    assert deleted == [ANNOUNCE_MESSAGE_ID]  # анонс удалён ровно один раз


def test_completed_event_keeps_announcement_and_record():
    guild, deleted = make_guild()
    results, left = run_scenario(make_event(guild, discord.EventStatus.completed))
    assert results == [False]
    assert left == 1
    assert deleted == []


def test_event_not_from_bot_is_ignored():
    guild, deleted = make_guild()
    results, left = run_scenario(make_event(guild), with_record=False)
    assert results == [False]
    assert deleted == []


def test_announcement_already_gone_still_removes_record():
    guild, deleted = make_guild(message_missing=True)
    results, left = run_scenario(make_event(guild))
    assert results == [True]
    assert left == 0


def test_announcement_channel_gone_still_removes_record():
    guild, _ = make_guild(channel_missing=True)
    results, left = run_scenario(make_event(guild))
    assert (results, left) == ([True], 0)


def test_event_without_announcement_removes_record():
    guild, deleted = make_guild()
    results, left = run_scenario(make_event(guild), with_announcement=False)
    assert (results, left) == ([True], 0)
    assert deleted == []
