"""
Персистентная запись о созданном событии: кто создал + связка с сообщением-анонсом
(если оно было отправлено). Простое хранилище в JSON-файле (БД в проекте пока нет).
"""

from __future__ import annotations

import json
import os

from .event_common import logger

_STORE_PATH = os.path.join("data", "event_announcements.json")


def _load() -> dict[str, dict]:
    try:
        with open(_STORE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        logger.exception("event_announcements.json повреждён, начинаю с пустого хранилища")
        return {}


def _save(data: dict[str, dict]) -> None:
    os.makedirs(os.path.dirname(_STORE_PATH), exist_ok=True)
    with open(_STORE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def record_event(event_id: int, creator_id: int, channel_id: int | None, message_id: int | None) -> None:
    """channel_id/message_id — None, если анонс не отправлялся (нет текстового канала)."""
    data = _load()
    data[str(event_id)] = {
        "creator_id": creator_id,
        "channel_id": channel_id,
        "message_id": message_id,
    }
    _save(data)


def get_event_record(event_id: int) -> dict | None:
    return _load().get(str(event_id))


def delete_event_record(event_id: int) -> None:
    data = _load()
    if str(event_id) in data:
        del data[str(event_id)]
        _save(data)