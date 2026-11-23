"""
Автотесты автоочистки каналов кампаний (cogs/cmpgn/campaigns.py).

Главное, что проверяем: запись удаляется ТОЛЬКО когда Discord прямо ответил
«канала нет» (NotFound). Во всех неясных случаях данные остаются.
"""

import asyncio
from types import SimpleNamespace

import discord

from conftest import close_test_db, http_error, open_test_db
from cogs.cmpgn.campaigns import cleanup_campaign_channels

GUILD_ID = 100
CHANNEL_ID = 555


# --- Поддельные объекты Discord ---

def make_guild(*, unavailable=False, cached_channels=()):
    return SimpleNamespace(
        unavailable=unavailable,
        get_channel=lambda cid: object() if cid in cached_channels else None,
    )


def make_bot(guild, fetch_result):
    """fetch_result — либо исключение (его «выбросит» Discord), либо объект канала."""
    calls = []

    async def fetch_channel(cid):
        calls.append(cid)
        if isinstance(fetch_result, Exception):
            raise fetch_result
        return fetch_result

    bot = SimpleNamespace(
        get_guild=lambda gid: guild if gid == GUILD_ID else None,
        fetch_channel=fetch_channel,
        user=SimpleNamespace(id=1),
    )
    return bot, calls


# --- Подготовка БД: одна кампания с одним каналом ---

async def add_campaign_with_channel(conn, guild_id=GUILD_ID):
    cur = await conn.execute(
        "INSERT INTO campaigns (guild_id, name, gm_role_id, player_role_id, created_at) "
        "VALUES (?, 'Тест', 1, 2, '2026-01-01')",
        (guild_id,),
    )
    await conn.execute(
        "INSERT INTO campaign_channels (campaign_id, discord_channel_id, channel_type) VALUES (?, ?, 'text')",
        (cur.lastrowid, CHANNEL_ID),
    )
    await conn.commit()


async def count(conn, table):
    cur = await conn.execute(f"SELECT COUNT(*) FROM {table}")
    return (await cur.fetchone())[0]


def run_scenario(guild, fetch_result, *, delete_campaign_row=False):
    """Готовит БД, запускает очистку, возвращает (удалено, каналов, кампаний, вызовы fetch)."""
    async def scenario():
        conn = await open_test_db()
        try:
            await add_campaign_with_channel(conn)
            if delete_campaign_row:
                await conn.execute("DELETE FROM campaigns")
                await conn.commit()
            bot, calls = make_bot(guild, fetch_result)
            removed = await cleanup_campaign_channels(bot)
            return removed, await count(conn, "campaign_channels"), await count(conn, "campaigns"), calls
        finally:
            await close_test_db()

    return asyncio.run(scenario())


# --- Сами тесты ---

def test_channel_in_cache_is_kept_without_asking_discord():
    removed, channels, campaigns, calls = run_scenario(
        make_guild(cached_channels={CHANNEL_ID}), http_error(discord.NotFound, 404)
    )
    assert (removed, channels, campaigns) == (0, 1, 1)
    assert calls == []  # лишних запросов к Discord нет


def test_unavailable_guild_deletes_nothing():
    # Тот самый опасный случай: сервер «временно недоступен», каналов в памяти нет.
    removed, channels, campaigns, calls = run_scenario(
        make_guild(unavailable=True), http_error(discord.NotFound, 404)
    )
    assert (removed, channels, campaigns) == (0, 1, 1)
    assert calls == []


def test_unknown_guild_deletes_nothing():
    removed, channels, campaigns, _ = run_scenario(None, http_error(discord.NotFound, 404))
    assert (removed, channels, campaigns) == (0, 1, 1)


def test_confirmed_not_found_removes_channel_and_empty_campaign():
    removed, channels, campaigns, calls = run_scenario(make_guild(), http_error(discord.NotFound, 404))
    assert (removed, channels, campaigns) == (1, 0, 0)
    assert calls == [CHANNEL_ID]


def test_forbidden_keeps_data():
    removed, channels, campaigns, _ = run_scenario(make_guild(), http_error(discord.Forbidden, 403))
    assert (removed, channels, campaigns) == (0, 1, 1)


def test_discord_outage_keeps_data():
    removed, channels, campaigns, _ = run_scenario(make_guild(), http_error(discord.DiscordServerError, 503))
    assert (removed, channels, campaigns) == (0, 1, 1)


def test_channel_found_by_direct_request_is_kept():
    removed, channels, campaigns, _ = run_scenario(make_guild(), object())
    assert (removed, channels, campaigns) == (0, 1, 1)


def test_channel_without_campaign_row_is_skipped():
    removed, channels, _, calls = run_scenario(
        make_guild(), http_error(discord.NotFound, 404), delete_campaign_row=True
    )
    assert (removed, channels) == (0, 1)
    assert calls == []
