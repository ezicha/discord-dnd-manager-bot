"""
Автотесты автоочистки каналов кампаний (cogs/cmpgn/campaigns.py).

Главное, что проверяем: запись удаляется ТОЛЬКО когда Discord прямо ответил
«канала нет» (NotFound). Во всех неясных случаях данные остаются.
"""

import asyncio
from types import SimpleNamespace

import discord

from conftest import close_test_db, http_error, open_test_db
from cogs.cmpgn.campaigns import cleanup_campaign_channels, cleanup_deleted_categories

GUILD_ID = 100
CHANNEL_ID = 555
CATEGORY_ID = 900


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

async def add_campaign_with_channel(conn, *, status="active"):
    """Активная кампания — с категорией; заархивированная — без (как в боте)."""
    cur = await conn.execute(
        "INSERT INTO campaigns (guild_id, name, gm_role_id, player_role_id, category_id, status, created_at) "
        "VALUES (?, 'Тест', 1, 2, ?, ?, '2026-01-01')",
        (GUILD_ID, CATEGORY_ID if status == "active" else None, status),
    )
    campaign_id = cur.lastrowid
    await conn.execute(
        "INSERT INTO campaign_channels (campaign_id, discord_channel_id, channel_type, status) "
        "VALUES (?, ?, 'text', ?)",
        (campaign_id, CHANNEL_ID, status),
    )
    await conn.execute(
        "INSERT INTO event_announcements (scheduled_event_id, campaign_id, creator_id) VALUES (1, ?, 1)",
        (campaign_id,),
    )
    await conn.commit()


async def count(conn, table):
    cur = await conn.execute(f"SELECT COUNT(*) FROM {table}")
    return (await cur.fetchone())[0]


def run_scenario(guild, fetch_result, *, delete_campaign_row=False, status="active", cleanup=cleanup_campaign_channels):
    """Готовит БД, запускает очистку, возвращает (удалено, каналов, кампаний, вызовы fetch)."""
    async def scenario():
        conn = await open_test_db()
        try:
            await add_campaign_with_channel(conn, status=status)
            if delete_campaign_row:
                await conn.execute("DELETE FROM campaigns")
                await conn.commit()
            bot, calls = make_bot(guild, fetch_result)
            removed = await cleanup(bot)
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


def test_confirmed_not_found_removes_channel_but_keeps_active_campaign():
    # У активной кампании могут быть каналы, созданные вручную, — её не стираем.
    removed, channels, campaigns, calls = run_scenario(make_guild(), http_error(discord.NotFound, 404))
    assert (removed, channels, campaigns) == (1, 0, 1)
    assert calls == [CHANNEL_ID]


def test_last_channel_of_archived_campaign_wipes_campaign():
    removed, channels, campaigns, _ = run_scenario(
        make_guild(), http_error(discord.NotFound, 404), status="archived"
    )
    assert (removed, channels, campaigns) == (1, 0, 0)


# --- Удалённая категория активной кампании ---

def run_category_scenario(guild, fetch_result, **kwargs):
    return run_scenario(guild, fetch_result, cleanup=cleanup_deleted_categories, **kwargs)


def test_deleted_category_wipes_campaign_with_channels_and_events():
    async def scenario():
        conn = await open_test_db()
        try:
            await add_campaign_with_channel(conn)
            bot, calls = make_bot(make_guild(), http_error(discord.NotFound, 404))
            wiped = await cleanup_deleted_categories(bot)
            cur = await conn.execute("SELECT action FROM campaign_history")
            history = [r[0] for r in await cur.fetchall()]
            return (wiped, await count(conn, "campaigns"), await count(conn, "campaign_channels"),
                    await count(conn, "event_announcements"), history, calls)
        finally:
            await close_test_db()

    wiped, campaigns, channels, events, history, calls = asyncio.run(scenario())
    assert (wiped, campaigns, channels, events) == (1, 0, 0, 0)
    assert history == ["wiped"]
    assert calls == [CATEGORY_ID]


def test_category_in_cache_keeps_campaign():
    wiped, channels, campaigns, calls = run_category_scenario(
        make_guild(cached_channels={CATEGORY_ID}), http_error(discord.NotFound, 404)
    )
    assert (wiped, channels, campaigns) == (0, 1, 1)
    assert calls == []


def test_category_check_with_unavailable_guild_keeps_campaign():
    wiped, channels, campaigns, calls = run_category_scenario(
        make_guild(unavailable=True), http_error(discord.NotFound, 404)
    )
    assert (wiped, channels, campaigns) == (0, 1, 1)
    assert calls == []


def test_category_check_with_discord_outage_keeps_campaign():
    wiped, channels, campaigns, _ = run_category_scenario(
        make_guild(), http_error(discord.DiscordServerError, 503)
    )
    assert (wiped, channels, campaigns) == (0, 1, 1)


def test_archived_campaign_is_not_checked_by_category():
    wiped, channels, campaigns, calls = run_category_scenario(
        make_guild(), http_error(discord.NotFound, 404), status="archived"
    )
    assert (wiped, channels, campaigns) == (0, 1, 1)
    assert calls == []


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
