"""
Автотесты формы «Название и время» в /event create и /event edit
(cogs/events/event_create.py: TitleTimeModal).
"""

import asyncio
from types import SimpleNamespace

import conftest  # noqa: F401 — добавляет корень проекта в sys.path
from cogs.events.event_create import TitleTimeModal


def make_parent(**values):
    """Поддельное меню события: только то, что читает и пишет форма."""
    state = dict(title=None, time_str=None, description=None, time_draft=None)
    state.update(values)
    return SimpleNamespace(**state, build_embed=lambda: "карточка")


def submit(parent, title, time, description=""):
    """Открывает форму, «вводит» значения и отправляет её. Возвращает, что бот показал."""
    shown = {}

    async def edit_message(**kwargs):
        shown.update(kwargs)

    interaction = SimpleNamespace(response=SimpleNamespace(edit_message=edit_message))

    async def scenario():
        modal = TitleTimeModal(parent)
        modal.title_input._value = title
        modal.time_input._value = time
        modal.description_input._value = description
        await modal.on_submit(interaction)

    asyncio.run(scenario())
    return shown


def test_valid_time_is_saved():
    parent = make_parent()
    shown = submit(parent, "Сессия 5", "19:00", "Идём в подземелье")
    assert (parent.title, parent.time_str, parent.description) == ("Сессия 5", "19:00", "Идём в подземелье")
    assert parent.time_draft is None
    assert shown["content"] is None  # предупреждение, если было, убирается


def test_wrong_time_keeps_title_and_description():
    parent = make_parent()
    shown = submit(parent, "Сессия 5", "19.00", "Идём в подземелье")
    assert parent.title == "Сессия 5"
    assert parent.description == "Идём в подземелье"
    assert parent.time_str is None
    assert parent.time_draft == "19.00"
    assert "19.00" in shown["content"]


def test_wrong_time_is_offered_for_correction():
    parent = make_parent(time_draft="19.00")

    async def build():
        return TitleTimeModal(parent)

    modal = asyncio.run(build())
    assert modal.time_input.default == "19.00"


def test_wrong_time_does_not_erase_previous_valid_time():
    parent = make_parent(time_str="18:00")
    submit(parent, "Сессия 5", "25:00")
    assert parent.time_str == "18:00"
