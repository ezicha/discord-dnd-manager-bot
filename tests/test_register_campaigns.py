"""
Автотесты записи в БД кампаний, о которых БД не знает
(cogs/cmpgn/campaigns.py: match_campaign_category, register_unknown_campaigns).

Кампания узнаётся по правам категории — так, как их выставляет /campaign create.
"""

import asyncio
from types import SimpleNamespace

import discord

from conftest import close_test_db, open_test_db
from cogs.cmpgn.campaigns import match_campaign_category, register_unknown_campaigns

GUILD_ID = 100
BOT_ID = 1


class FakeRole:
    """Роль: только id и название. Обычный класс, а не SimpleNamespace —
    роли служат ключами словаря прав, им нужно быть «хешируемыми»."""
    def __init__(self, role_id, name):
        self.id = role_id
        self.name = name


EVERYONE = FakeRole(GUILD_ID, "@everyone")
HIDDEN = discord.PermissionOverwrite(view_channel=False)
PLAYER = discord.PermissionOverwrite(view_channel=True)
GM = discord.PermissionOverwrite(view_channel=True, manage_channels=True)
READ_ONLY = discord.PermissionOverwrite(view_channel=True, send_messages=False)


def make_guild(roles=(), *, unavailable=False):
    by_id = {r.id: r for r in roles}
    return SimpleNamespace(
        id=GUILD_ID, unavailable=unavailable, default_role=EVERYONE,
        get_role=by_id.get, categories=[],
    )


def make_channel(guild, channel_id, player_role, *, gm_only=False):
    def overwrites_for(role):
        if role is player_role and gm_only:
            return READ_ONLY
        return PLAYER
    return SimpleNamespace(id=channel_id, name=f"канал-{channel_id}", guild=guild, overwrites_for=overwrites_for)


def make_campaign_category(guild, category_id, name, player_role, gm_role, channels=()):
    """Категория с правами, как у созданной ботом кампании."""
    category = SimpleNamespace(
        id=category_id, name=name, guild=guild, channels=list(channels),
        overwrites={EVERYONE: HIDDEN, player_role: PLAYER, gm_role: GM},
    )
    guild.categories.append(category)
    return category


def make_bot(*guilds):
    return SimpleNamespace(guilds=list(guilds), user=SimpleNamespace(id=BOT_ID))


def run(scenario):
    async def wrapper():
        conn = await open_test_db()
        try:
            return await scenario(conn)
        finally:
            await close_test_db()

    return asyncio.run(wrapper())


async def campaign_rows(conn):
    cur = await conn.execute(
        "SELECT name, gm_role_id, player_role_id, category_id, status FROM campaigns ORDER BY id"
    )
    return [tuple(r) for r in await cur.fetchall()]


async def channel_rows(conn):
    cur = await conn.execute("SELECT discord_channel_id, gm_only FROM campaign_channels ORDER BY id")
    return [tuple(r) for r in await cur.fetchall()]


async def history_actions(conn):
    cur = await conn.execute("SELECT action, actor_id FROM campaign_history ORDER BY id")
    return [tuple(r) for r in await cur.fetchall()]


# --- Распознавание категории ---

def test_bot_style_category_is_recognized():
    guild = make_guild()
    player, gm = FakeRole(11, "Лекторий"), FakeRole(12, "ГМ: Лекторий")
    category = make_campaign_category(guild, 900, "Лекторий", player, gm)
    assert match_campaign_category(category) == (player, gm)


def test_category_visible_to_everyone_is_not_a_campaign():
    guild = make_guild()
    player, gm = FakeRole(11, "Лекторий"), FakeRole(12, "ГМ: Лекторий")
    category = make_campaign_category(guild, 900, "Лекторий", player, gm)
    del category.overwrites[EVERYONE]
    assert match_campaign_category(category) is None


def test_gm_role_without_manage_channels_is_not_a_campaign():
    guild = make_guild()
    player, gm = FakeRole(11, "Лекторий"), FakeRole(12, "ГМ: Лекторий")
    category = make_campaign_category(guild, 900, "Лекторий", player, gm)
    category.overwrites[gm] = PLAYER
    assert match_campaign_category(category) is None


def test_role_names_must_match_category_name():
    guild = make_guild()
    player, gm = FakeRole(11, "Другая"), FakeRole(12, "ГМ: Другая")
    category = make_campaign_category(guild, 900, "Лекторий", player, gm)
    assert match_campaign_category(category) is None


def test_archive_category_is_ignored():
    guild = make_guild()
    player, gm = FakeRole(11, "Архив"), FakeRole(12, "ГМ: Архив")
    category = make_campaign_category(guild, 900, "Архив", player, gm)
    assert match_campaign_category(category) is None


# --- Запись в БД ---

def test_unknown_campaign_is_registered_with_channels():
    async def scenario(conn):
        player, gm = FakeRole(11, "Лекторий"), FakeRole(12, "ГМ: Лекторий")
        guild = make_guild([player, gm])
        make_campaign_category(guild, 900, "Лекторий", player, gm, [
            make_channel(guild, 5, player),
            make_channel(guild, 6, player, gm_only=True),
        ])
        count = await register_unknown_campaigns(make_bot(guild))
        return count, await campaign_rows(conn), await channel_rows(conn), await history_actions(conn)

    count, campaigns, channels, history = run(scenario)
    assert count == 1
    assert campaigns == [("Лекторий", 12, 11, 900, "active")]
    assert channels == [(5, 0), (6, 1)]
    assert history == [("registered", BOT_ID)]


def test_second_pass_registers_nothing_new():
    async def scenario(conn):
        player, gm = FakeRole(11, "Лекторий"), FakeRole(12, "ГМ: Лекторий")
        guild = make_guild([player, gm])
        make_campaign_category(guild, 900, "Лекторий", player, gm, [make_channel(guild, 5, player)])
        bot = make_bot(guild)
        first = await register_unknown_campaigns(bot)
        second = await register_unknown_campaigns(bot)
        return first, second, len(await campaign_rows(conn)), len(await channel_rows(conn))

    assert run(scenario) == (1, 0, 1, 1)


def test_known_campaign_is_not_duplicated():
    async def scenario(conn):
        await conn.execute(
            "INSERT INTO campaigns (guild_id, name, gm_role_id, player_role_id, category_id, status, created_at) "
            "VALUES (?, 'Лекторий', 12, 11, 900, 'active', '2026-01-01')",
            (GUILD_ID,),
        )
        await conn.commit()
        player, gm = FakeRole(11, "Лекторий"), FakeRole(12, "ГМ: Лекторий")
        guild = make_guild([player, gm])
        make_campaign_category(guild, 900, "Лекторий", player, gm)
        count = await register_unknown_campaigns(make_bot(guild))
        return count, len(await campaign_rows(conn))

    assert run(scenario) == (0, 1)


def test_category_with_gm_role_of_other_campaign_is_skipped():
    # Например, кампанию воскрешают из архива: категория уже создана, а БД
    # ещё не успела записать её за старой (заархивированной) кампанией.
    async def scenario(conn):
        await conn.execute(
            "INSERT INTO campaigns (guild_id, name, gm_role_id, player_role_id, category_id, status, created_at) "
            "VALUES (?, 'Лекторий', 12, 11, NULL, 'archived', '2026-01-01')",
            (GUILD_ID,),
        )
        await conn.commit()
        player, gm = FakeRole(11, "Лекторий"), FakeRole(12, "ГМ: Лекторий")
        guild = make_guild([player, gm])
        make_campaign_category(guild, 901, "Лекторий", player, gm, [make_channel(guild, 5, player)])
        count = await register_unknown_campaigns(make_bot(guild))
        return count, await campaign_rows(conn), await channel_rows(conn)

    count, campaigns, channels = run(scenario)
    assert count == 0
    assert campaigns == [("Лекторий", 12, 11, None, "archived")]
    assert channels == []


def test_unavailable_guild_is_skipped():
    async def scenario(conn):
        player, gm = FakeRole(11, "Лекторий"), FakeRole(12, "ГМ: Лекторий")
        guild = make_guild([player, gm], unavailable=True)
        make_campaign_category(guild, 900, "Лекторий", player, gm)
        return await register_unknown_campaigns(make_bot(guild)), await campaign_rows(conn)

    assert run(scenario) == (0, [])
