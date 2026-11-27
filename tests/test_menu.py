"""
Автотесты общей основы меню (cogs/menu.py): что происходит,
когда у меню истёк срок жизни.
"""

import asyncio
from types import SimpleNamespace

import discord

from conftest import http_error
from cogs.menu import STALE_NOTE, Menu


class SampleMenu(Menu):
    @discord.ui.button(label="Кнопка")
    async def press(self, interaction, button):
        pass


def fake_message(content, custom_ids):
    """Сообщение Discord: кнопки лежат внутри рядов (как в настоящем API)."""
    row = SimpleNamespace(children=[SimpleNamespace(custom_id=cid) for cid in custom_ids])
    return SimpleNamespace(content=content, components=[row])


def fake_interaction(message=None, error=None):
    """Взаимодействие, через которое можно получить и отредактировать сообщение."""
    edits = []

    async def original_response():
        if error is not None:
            raise error
        return message

    async def edit_original_response(**kwargs):
        edits.append(kwargs)

    return SimpleNamespace(original_response=original_response,
                           edit_original_response=edit_original_response), edits


def run_timeout(setup):
    """Создаёт меню внутри цикла asyncio, настраивает и вызывает on_timeout."""
    async def scenario():
        menu = SampleMenu(timeout=None)
        setup(menu)
        await menu.on_timeout()
        return menu
    return asyncio.run(scenario())


def my_ids(menu):
    return [item.custom_id for item in menu.children]


def test_stale_menu_is_disabled_and_marked():
    state = {}

    def setup(menu):
        interaction, edits = fake_interaction(fake_message("Выбери канал:", my_ids(menu)))
        state["edits"] = edits
        menu.remember(interaction)

    menu = run_timeout(setup)
    assert len(state["edits"]) == 1
    assert state["edits"][0]["content"] == f"Выбери канал:\n\n{STALE_NOTE}"
    assert all(item.disabled for item in menu.children)


def test_message_with_another_menu_is_left_alone():
    # Пользователь уже перешёл к следующему меню на том же сообщении —
    # старое меню не должно его затирать.
    state = {}

    def setup(menu):
        interaction, edits = fake_interaction(fake_message("Другое меню", ["чужая-кнопка"]))
        state["edits"] = edits
        menu.remember(interaction)

    run_timeout(setup)
    assert state["edits"] == []


def test_falls_back_to_earlier_interaction():
    # Последнее нажатие открыло форму — через него сообщение не найти,
    # но через предыдущее можно.
    state = {}

    def setup(menu):
        good, good_edits = fake_interaction(fake_message("Меню", my_ids(menu)))
        bad, bad_edits = fake_interaction(error=http_error(discord.NotFound, 404))
        state["good"], state["bad"] = good_edits, bad_edits
        menu.remember(good)
        menu.remember(bad)

    run_timeout(setup)
    assert len(state["good"]) == 1
    assert state["bad"] == []


def test_menu_without_remembered_interaction_does_nothing():
    menu = run_timeout(lambda menu: None)
    assert not any(item.disabled for item in menu.children)


# --- Понятный текст ошибок и начало действия ---

from cogs.menu import describe_error, start_action  # noqa: E402


def test_rate_limit_text_says_when_to_retry():
    text = describe_error(discord.RateLimited(retry_after=400))  # 6,7 минуты
    assert "2 раз за 10 минут" in text
    assert "через 7 мин" in text


def test_short_rate_limit_rounds_up_to_one_minute():
    assert "через 1 мин" in describe_error(discord.RateLimited(retry_after=5))


def test_other_errors_are_shown_as_is():
    assert describe_error(ValueError("что-то сломалось")) == "Ошибка: что-то сломалось"


def fake_response_interaction(message):
    calls = []

    async def edit_message(**kwargs):
        calls.append(("edit_message", kwargs))

    async def defer(**kwargs):
        calls.append(("defer", kwargs))

    interaction = SimpleNamespace(message=message,
                                  response=SimpleNamespace(edit_message=edit_message, defer=defer))
    return interaction, calls


def test_start_action_removes_menu_buttons_at_once():
    interaction, calls = fake_response_interaction(message=object())
    asyncio.run(start_action(interaction, "Архивирую каналы…"))
    assert calls == [("edit_message", {"content": "Архивирую каналы…", "view": None})]


def test_start_action_without_menu_shows_thinking():
    # Форма открыта прямо из команды — меню, которое можно заменить, нет.
    interaction, calls = fake_response_interaction(message=None)
    asyncio.run(start_action(interaction, "Возвращаю…"))
    assert calls == [("defer", {"ephemeral": True, "thinking": True})]
