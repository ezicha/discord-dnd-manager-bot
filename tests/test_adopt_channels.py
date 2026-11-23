"""
Автотесты «усыновления» каналов, созданных или перенесённых вручную
(cogs/cmpgn/campaigns.py: handle_channel_created, adopt_manual_channels).
"""

import asyncio
from types import SimpleNamespace

import discord

from conftest import close_test_db, open_test_db
from cogs.cmpgn.campaigns import adopt_manual_channels, handle_channel_created
from db.campaigns_db import add_campaign_channel

GUILD_ID = 100
PLAYER_ROLE_A = 11
PLAYER_ROLE_B = 21
CATEGORY_A = 900
CATEGORY_B = 901


def make_channel(channel_id, category_id, *, players_can_write=True, player_role_missing=False):
    """Поддельный текстовый канал. players_can_write=False — «только ГМ пишет».
    player_role_missing=True — роль игроков кампании удалена с сервера."""
    guild = SimpleNamespace(get_role=lambda rid: None if player_role_missing else SimpleNamespace(id=rid))
    overwrite = (
        discord.PermissionOverwrite(view_channel=True)
        if players_can_write
        else discord.PermissionOverwrite(view_channel=True, send_messages=False)
    )
    return SimpleNamespace(
        id=channel_id, category_id=category_id, guild=guild,
        overwrites_for=lambda role: overwrite,
    )


def make_bot(categories: dict, *, unavailable=False):
    """categories: {id категории: [каналы в ней]} — то, что «видит» бот на сервере."""
    def get_channel(cid):
        if cid in categories:
            return SimpleNamespace(channels=categories[cid])
        return None

    guild = SimpleNamespace(unavailable=unavailable, get_channel=get_channel)
    return SimpleNamespace(get_guild=lambda gid: guild if gid == GUILD_ID else None)


async def add_campaign(conn, category_id, player_role_id):
    cur = await conn.execute(
        "INSERT INTO campaigns (guild_id, name, gm_role_id, player_role_id, category_id, status, created_at) "
        "VALUES (?, 'Тест', 1, ?, ?, 'active', '2026-01-01')",
        (GUILD_ID, player_role_id, category_id),
    )
    await conn.commit()
    return cur.lastrowid


async def channel_rows(conn):
    cur = await conn.execute("SELECT discord_channel_id, campaign_id, gm_only FROM campaign_channels ORDER BY id")
    return [tuple(r) for r in await cur.fetchall()]


def run(scenario):
    async def wrapper():
        conn = await open_test_db()
        try:
            return await scenario(conn)
        finally:
            await close_test_db()

    return asyncio.run(wrapper())


# --- Сразу при создании канала ---

def test_new_channel_in_campaign_category_is_recorded():
    async def scenario(conn):
        camp = await add_campaign(conn, CATEGORY_A, PLAYER_ROLE_A)
        result = await handle_channel_created(make_channel(5, CATEGORY_A))
        return result, await channel_rows(conn), camp

    result, rows, camp = run(scenario)
    assert result is True
    assert rows == [(5, camp, 0)]


def test_gm_only_is_detected_from_permissions():
    async def scenario(conn):
        camp = await add_campaign(conn, CATEGORY_A, PLAYER_ROLE_A)
        await handle_channel_created(make_channel(5, CATEGORY_A, players_can_write=False))
        return await channel_rows(conn), camp

    rows, camp = run(scenario)
    assert rows == [(5, camp, 1)]


def test_channel_outside_campaigns_is_ignored():
    async def scenario(conn):
        await add_campaign(conn, CATEGORY_A, PLAYER_ROLE_A)
        r1 = await handle_channel_created(make_channel(5, None))      # без категории
        r2 = await handle_channel_created(make_channel(6, 12345))     # чужая категория
        return r1, r2, await channel_rows(conn)

    assert run(scenario) == (False, False, [])


def test_bot_command_after_listener_makes_no_duplicate_and_keeps_command_gm_only():
    # /campaign edit создаёт канал → сначала успевает обработчик, потом сама команда пишет в БД.
    async def scenario(conn):
        camp = await add_campaign(conn, CATEGORY_A, PLAYER_ROLE_A)
        await handle_channel_created(make_channel(5, CATEGORY_A))
        await add_campaign_channel(camp, 5, "text", gm_only=True)
        return await channel_rows(conn), camp

    rows, camp = run(scenario)
    assert rows == [(5, camp, 1)]


def test_listener_after_bot_command_makes_no_duplicate():
    async def scenario(conn):
        camp = await add_campaign(conn, CATEGORY_A, PLAYER_ROLE_A)
        await add_campaign_channel(camp, 5, "text", gm_only=True)
        result = await handle_channel_created(make_channel(5, CATEGORY_A))
        return result, await channel_rows(conn), camp

    result, rows, camp = run(scenario)
    assert result is False
    assert rows == [(5, camp, 1)]


# --- Еженедельная сверка ---

def test_weekly_adopts_channel_created_while_bot_was_off():
    async def scenario(conn):
        camp = await add_campaign(conn, CATEGORY_A, PLAYER_ROLE_A)
        bot = make_bot({CATEGORY_A: [make_channel(5, CATEGORY_A)]})
        counts = await adopt_manual_channels(bot)
        return counts, await channel_rows(conn), camp

    counts, rows, camp = run(scenario)
    assert counts == (1, 0)
    assert rows == [(5, camp, 0)]


def test_weekly_moves_channel_to_other_campaign():
    async def scenario(conn):
        camp_a = await add_campaign(conn, CATEGORY_A, PLAYER_ROLE_A)
        camp_b = await add_campaign(conn, CATEGORY_B, PLAYER_ROLE_B)
        await add_campaign_channel(camp_a, 5, "text", gm_only=False)
        # Канал 5 был в кампании A, а теперь лежит в категории кампании B,
        # и игрокам B в нём писать запрещено.
        bot = make_bot({CATEGORY_A: [], CATEGORY_B: [make_channel(5, CATEGORY_B, players_can_write=False)]})
        counts = await adopt_manual_channels(bot)
        return counts, await channel_rows(conn), camp_b

    counts, rows, camp_b = run(scenario)
    assert counts == (0, 1)
    assert rows == [(5, camp_b, 1)]  # режим взят из прав относительно новой кампании


def test_weekly_updates_gm_only_changed_by_hand():
    async def scenario(conn):
        camp = await add_campaign(conn, CATEGORY_A, PLAYER_ROLE_A)
        await add_campaign_channel(camp, 5, "text", gm_only=False)  # записан как «пишут все»
        await add_campaign_channel(camp, 6, "text", gm_only=True)   # записан как «только ГМ»
        bot = make_bot({CATEGORY_A: [
            make_channel(5, CATEGORY_A, players_can_write=False),  # руками запретили писать
            make_channel(6, CATEGORY_A, players_can_write=True),   # руками разрешили
        ]})
        await adopt_manual_channels(bot)
        return await channel_rows(conn), camp

    rows, camp = run(scenario)
    assert rows == [(5, camp, 1), (6, camp, 0)]


def test_weekly_keeps_gm_only_when_player_role_is_gone():
    async def scenario(conn):
        camp = await add_campaign(conn, CATEGORY_A, PLAYER_ROLE_A)
        await add_campaign_channel(camp, 5, "text", gm_only=True)
        bot = make_bot({CATEGORY_A: [make_channel(5, CATEGORY_A, player_role_missing=True)]})
        await adopt_manual_channels(bot)
        return await channel_rows(conn), camp

    rows, camp = run(scenario)
    assert rows == [(5, camp, 1)]


def test_weekly_leaves_known_channels_alone():
    async def scenario(conn):
        camp = await add_campaign(conn, CATEGORY_A, PLAYER_ROLE_A)
        await add_campaign_channel(camp, 5, "text")
        bot = make_bot({CATEGORY_A: [make_channel(5, CATEGORY_A)]})
        return await adopt_manual_channels(bot), await channel_rows(conn), camp

    counts, rows, camp = run(scenario)
    assert counts == (0, 0)
    assert rows == [(5, camp, 0)]


def test_weekly_skips_unavailable_guild():
    async def scenario(conn):
        await add_campaign(conn, CATEGORY_A, PLAYER_ROLE_A)
        bot = make_bot({CATEGORY_A: [make_channel(5, CATEGORY_A)]}, unavailable=True)
        return await adopt_manual_channels(bot), await channel_rows(conn)

    assert run(scenario) == ((0, 0), [])


def test_weekly_skips_category_missing_from_cache():
    async def scenario(conn):
        await add_campaign(conn, CATEGORY_A, PLAYER_ROLE_A)
        bot = make_bot({})
        return await adopt_manual_channels(bot), await channel_rows(conn)

    assert run(scenario) == ((0, 0), [])
