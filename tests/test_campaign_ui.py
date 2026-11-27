"""
Автотесты проверок в меню кампаний (cogs/cmpgn/campaign_common.py).
"""

from types import SimpleNamespace

import conftest  # noqa: F401 — добавляет корень проекта в sys.path
from cogs.cmpgn.campaign_common import campaign_name_conflict


def make_guild(categories=(), roles=()):
    """Поддельный сервер: только названия категорий и ролей."""
    return SimpleNamespace(
        categories=[SimpleNamespace(name=n) for n in categories],
        roles=[SimpleNamespace(name=n) for n in roles],
    )


def test_free_name_is_allowed():
    guild = make_guild(categories=["Общее"], roles=["Модератор"])
    assert campaign_name_conflict(guild, "Лекторий") is None


def test_empty_name_is_rejected():
    assert campaign_name_conflict(make_guild(), "") is not None


def test_existing_category_is_rejected():
    guild = make_guild(categories=["Лекторий"])
    assert "категория" in campaign_name_conflict(guild, "Лекторий")


def test_existing_role_is_rejected():
    # Совпадение с обычной серверной ролью тоже опасно: бот ищет игроков по имени роли.
    guild = make_guild(roles=["Модератор"])
    assert "роль" in campaign_name_conflict(guild, "Модератор")


def test_existing_gm_role_is_rejected():
    guild = make_guild(roles=["ГМ: Лекторий"])
    assert "ГМ: Лекторий" in campaign_name_conflict(guild, "Лекторий")


# --- «Изменить доступ»: меню показывает текущий режим канала ---

import asyncio  # noqa: E402

import discord  # noqa: E402

from cogs.cmpgn.campaign_edit import ChangeAccessApplyView, access_mode_text  # noqa: E402


def make_channel(player_role, overwrite: discord.PermissionOverwrite):
    """Поддельный канал: у роли игроков — заданные права."""
    guild = SimpleNamespace(get_role=lambda role_id: player_role if role_id == player_role.id else None)
    return SimpleNamespace(guild=guild, overwrites_for=lambda role: overwrite)


def selected_values(view: ChangeAccessApplyView) -> list[str]:
    return [opt.value for opt in view.select_access.options if opt.default]


def build_view(channel, player_role):
    async def build():
        return ChangeAccessApplyView(channel, player_role, gm_role=None)
    return asyncio.run(build())


def test_access_menu_marks_gm_only_channel():
    role = SimpleNamespace(id=1)
    channel = make_channel(role, discord.PermissionOverwrite(view_channel=True, send_messages=False))
    view = build_view(channel, role)
    assert view.current_gm_only is True
    assert view.gm_only is True  # «Применить» без выбора не откроет канал игрокам
    assert selected_values(view) == ["gm_only"]


def test_access_menu_marks_open_channel():
    role = SimpleNamespace(id=1)
    channel = make_channel(role, discord.PermissionOverwrite(view_channel=True))
    view = build_view(channel, role)
    assert view.current_gm_only is False
    assert selected_values(view) == ["all"]


def test_access_menu_without_player_role():
    channel = make_channel(SimpleNamespace(id=1), discord.PermissionOverwrite())
    view = build_view(channel, None)
    assert view.current_gm_only is None
    assert selected_values(view) == []
    assert access_mode_text(None) == "не удалось определить"
