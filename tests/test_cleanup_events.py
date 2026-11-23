"""
Автотесты автоочистки записей событий (cogs/events/events.py).

Запись удаляется только если Discord подтвердил, что события нет
(NotFound) или оно уже завершено/отменено.
"""

import asyncio
from types import SimpleNamespace

import discord

from conftest import close_test_db, http_error, open_test_db
from cogs.events.events import cleanup_event_records

GUILD_ID = 100
EVENT_ID = 777


def make_guild(fetch_result, *, unavailable=False, cached_events=()):
    """fetch_result — исключение (его «выбросит» Discord) или объект события."""
    calls = []

    async def fetch_scheduled_event(eid):
        calls.append(eid)
        if isinstance(fetch_result, Exception):
            raise fetch_result
        return fetch_result

    guild = SimpleNamespace(
        unavailable=unavailable,
        get_scheduled_event=lambda eid: object() if eid in cached_events else None,
        fetch_scheduled_event=fetch_scheduled_event,
    )
    return guild, calls


def make_bot(guild):
    return SimpleNamespace(get_guild=lambda gid: guild if gid == GUILD_ID else None)


def event_with_status(status):
    return SimpleNamespace(status=status)


def run_scenario(guild, *, delete_campaign_row=False):
    """Готовит БД с одной записью события, запускает очистку,
    возвращает (удалено, осталось записей)."""
    async def scenario():
        conn = await open_test_db()
        try:
            cur = await conn.execute(
                "INSERT INTO campaigns (guild_id, name, gm_role_id, player_role_id, created_at) "
                "VALUES (?, 'Тест', 1, 2, '2026-01-01')",
                (GUILD_ID,),
            )
            await conn.execute(
                "INSERT INTO event_announcements (scheduled_event_id, campaign_id, creator_id) VALUES (?, ?, 1)",
                (EVENT_ID, cur.lastrowid),
            )
            if delete_campaign_row:
                await conn.execute("DELETE FROM campaigns")
            await conn.commit()

            removed = await cleanup_event_records(make_bot(guild))
            cur = await conn.execute("SELECT COUNT(*) FROM event_announcements")
            return removed, (await cur.fetchone())[0]
        finally:
            await close_test_db()

    return asyncio.run(scenario())


def test_event_in_cache_is_kept_without_asking_discord():
    guild, calls = make_guild(http_error(discord.NotFound, 404), cached_events={EVENT_ID})
    assert run_scenario(guild) == (0, 1)
    assert calls == []


def test_unavailable_guild_deletes_nothing():
    guild, calls = make_guild(http_error(discord.NotFound, 404), unavailable=True)
    assert run_scenario(guild) == (0, 1)
    assert calls == []


def test_unknown_guild_deletes_nothing():
    assert run_scenario(None) == (0, 1)


def test_confirmed_not_found_removes_record():
    guild, calls = make_guild(http_error(discord.NotFound, 404))
    assert run_scenario(guild) == (1, 0)
    assert calls == [EVENT_ID]


def test_completed_event_removes_record():
    guild, _ = make_guild(event_with_status(discord.EventStatus.completed))
    assert run_scenario(guild) == (1, 0)


def test_canceled_event_removes_record():
    guild, _ = make_guild(event_with_status(discord.EventStatus.canceled))
    assert run_scenario(guild) == (1, 0)


def test_scheduled_event_found_by_direct_request_is_kept():
    guild, _ = make_guild(event_with_status(discord.EventStatus.scheduled))
    assert run_scenario(guild) == (0, 1)


def test_forbidden_keeps_record():
    guild, _ = make_guild(http_error(discord.Forbidden, 403))
    assert run_scenario(guild) == (0, 1)


def test_discord_outage_keeps_record():
    guild, _ = make_guild(http_error(discord.DiscordServerError, 503))
    assert run_scenario(guild) == (0, 1)


def test_event_without_campaign_row_is_skipped():
    guild, calls = make_guild(http_error(discord.NotFound, 404))
    assert run_scenario(guild, delete_campaign_row=True) == (0, 1)
    assert calls == []
