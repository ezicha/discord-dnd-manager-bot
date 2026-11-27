"""
Автотесты дат и текстового календаря (core/dates.py).
"""

from datetime import date, timedelta

import pytest

from core.dates import (
    SERVER_TZ,
    combine_date_and_time,
    format_date_ru,
    generate_calendar_text_for_week,
    parse_time_input,
    week_dates,
    week_start_for,
)


def test_week_start_is_monday():
    # 27 ноября 2026 — пятница, понедельник той недели — 23 ноября
    assert week_start_for(date(2026, 11, 27)) == date(2026, 11, 23)
    assert week_start_for(date(2026, 11, 23)) == date(2026, 11, 23)


def test_week_dates():
    days = week_dates(date(2026, 11, 23))
    assert len(days) == 7
    assert days[0] == date(2026, 11, 23)
    assert days[-1] == date(2026, 11, 29)


def test_format_date_ru():
    assert format_date_ru(date(2026, 3, 8)) == "8 марта"


@pytest.mark.parametrize("raw, expected", [("19:00", (19, 0)), (" 7:05 ", (7, 5)), ("0:00", (0, 0))])
def test_parse_time_ok(raw, expected):
    assert parse_time_input(raw) == expected


@pytest.mark.parametrize("raw", ["19", "19-00", "24:00", "12:60", "ab:cd", ""])
def test_parse_time_bad(raw):
    with pytest.raises(ValueError):
        parse_time_input(raw)


def test_combine_date_and_time_uses_server_timezone():
    dt = combine_date_and_time("2026-12-31", 19, 30)
    assert (dt.year, dt.month, dt.day, dt.hour, dt.minute) == (2026, 12, 31, 19, 30)
    assert dt.utcoffset() == timedelta(hours=7)
    assert dt.tzinfo == SERVER_TZ


def test_calendar_marks_selected_day():
    text = generate_calendar_text_for_week(date(2026, 11, 23), "2026-11-27")
    assert "Ноябрь 2026" in text
    assert "[27]" in text


def test_calendar_week_across_two_months():
    # Неделя 30 ноября — 6 декабря 2026 задевает два месяца → две сетки
    text = generate_calendar_text_for_week(date(2026, 11, 30))
    assert "Ноябрь 2026" in text
    assert "Декабрь 2026" in text
