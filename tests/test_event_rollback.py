"""
Автотесты отката неудавшегося /event create
(cogs/events/event_common.py: rollback_created_event).
"""

import asyncio
from types import SimpleNamespace

import discord

from conftest import http_error
from cogs.events.event_common import rollback_created_event


def make_deletable(item_id, error=None):
    """Поддельный объект Discord (событие или сообщение) с методом delete()."""
    calls = []

    async def delete():
        calls.append(item_id)
        if error is not None:
            raise error

    return SimpleNamespace(id=item_id, delete=delete), calls


def test_rollback_deletes_announcement_and_event():
    event, event_calls = make_deletable(1)
    message, message_calls = make_deletable(2)
    asyncio.run(rollback_created_event(event, message))
    assert event_calls == [1]
    assert message_calls == [2]


def test_rollback_without_announcement_deletes_event():
    event, event_calls = make_deletable(1)
    asyncio.run(rollback_created_event(event, None))
    assert event_calls == [1]


def test_rollback_still_deletes_event_if_announcement_fails():
    event, event_calls = make_deletable(1)
    message, _ = make_deletable(2, error=http_error(discord.Forbidden, 403))
    asyncio.run(rollback_created_event(event, message))  # не должно упасть
    assert event_calls == [1]


def test_rollback_does_not_raise_if_event_delete_fails():
    event, _ = make_deletable(1, error=http_error(discord.NotFound, 404))
    asyncio.run(rollback_created_event(event, None))  # не должно упасть
