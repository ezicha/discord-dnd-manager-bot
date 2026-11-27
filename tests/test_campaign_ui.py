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
