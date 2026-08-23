"""
Персистентная связка Discord Scheduled Event -> сообщение-анонс в текстовом канале.
Простое хранилище в JSON-файле (БД в проекте пока нет).
"""

from __future__ import annotations

import json
import os

from .event_common import logger

_STORE_PATH = os.path.join("data", "event_announcements.json")


def _load() -> dict[str, dict[str, int]]:
    try:
        with open(_STORE_PATH, encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError:
        logger.exception("event_announcements.json повреждён, начинаю с пустого хранилища")
        return {}


def _save(data: dict[str, dict[str, int]]) -> None:
    os.makedirs(os.path.dirname(_STORE_PATH), exist_ok=True)
    with open(_STORE_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def set_announcement(event_id: int, channel_id: int, message_id: int) -> None:
    data = _load()
    data[str(event_id)] = {"channel_id": channel_id, "message_id": message_id}
    _save(data)


def get_announcement(event_id: int) -> tuple[int, int] | None:
    data = _load()
    entry = data.get(str(event_id))
    if entry is None:
        return None
    return entry["channel_id"], entry["message_id"]


def delete_announcement(event_id: int) -> None:
    data = _load()
    if str(event_id) in data:
        del data[str(event_id)]
        _save(data)