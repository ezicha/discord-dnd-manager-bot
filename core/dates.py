"""
Даты и текстовый календарь для событий кампаний: часовой пояс сервера,
недели, русские названия месяцев, разбор времени «ЧЧ:ММ».

Модуль ядра — не зависит от Discord (и от любой другой платформы).
"""

import calendar as calendar_module
from datetime import date, datetime, timedelta, timezone


# Фиксированный часовой пояс сервера для ввода времени событий: NSK, UTC+7, без перехода на летнее.
SERVER_TZ = timezone(timedelta(hours=7), name="NSK")

# Насколько далеко вперёд разрешена навигация по неделям в календаре.
MAX_DAYS_AHEAD = 60

_WEEKDAY_NAMES_RU = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
_MONTH_NAMES_RU = [
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
]
_MONTH_NAMES_RU_NOM = [
    "Январь", "Февраль", "Март", "Апрель", "Май", "Июнь",
    "Июль", "Август", "Сентябрь", "Октябрь", "Ноябрь", "Декабрь",
]


def today_server() -> date:
    """Сегодняшняя дата в часовом поясе сервера (SERVER_TZ)."""
    return datetime.now(SERVER_TZ).date()


def week_start_for(d: date) -> date:
    """Понедельник недели, в которую входит дата d."""
    return d - timedelta(days=d.weekday())


def week_dates(week_start: date) -> list[date]:
    """7 дат недели, начиная с понедельника week_start."""
    return [week_start + timedelta(days=i) for i in range(7)]


def format_date_ru(d: date) -> str:
    return f"{d.day} {_MONTH_NAMES_RU[d.month - 1]}"


def generate_month_calendar_text(month_first_day: date, selected_date_iso: str | None = None) -> str:
    """
    Текстовая сетка-календарь месяца, в котором лежит month_first_day.
    Каждая ячейка ровно 4 символа шириной — и обычная (" 25 "), и выбранная
    ("[25]") — поэтому колонки не съезжают при подсветке.
    """
    weeks = calendar_module.monthcalendar(month_first_day.year, month_first_day.month)
    title = f"{_MONTH_NAMES_RU_NOM[month_first_day.month - 1]} {month_first_day.year}"

    selected_day = None
    if selected_date_iso:
        sd = datetime.strptime(selected_date_iso, "%Y-%m-%d").date()
        if sd.year == month_first_day.year and sd.month == month_first_day.month:
            selected_day = sd.day

    header = "".join(f" {name} " for name in _WEEKDAY_NAMES_RU)
    lines = [title.center(len(header)), header]
    for week in weeks:
        cells = []
        for day in week:
            if day == 0:
                cells.append("    ")
            elif day == selected_day:
                cells.append(f"[{day:2d}]")
            else:
                cells.append(f" {day:2d} ")
        lines.append("".join(cells))
    return "```\n" + "\n".join(lines) + "\n```"

def generate_calendar_text_for_week(week_start: date, selected_date_iso: str | None = None) -> str:
    """
    Сетка(-и) месяца/месяцев, которые пересекает отображаемая неделя.
    Если неделя целиком в одном месяце — одна сетка, как раньше. Если неделя
    переходит границу месяца (например Пн 31 августа — Вс 6 сентября) — две
    отдельные сетки друг под другом, каждая со своим заголовком месяца, чтобы
    даты соседних месяцев визуально не путались.
    """
    seen: list[tuple[int, int]] = []
    for d in week_dates(week_start):
        key = (d.year, d.month)
        if key not in seen:
            seen.append(key)
    blocks = [
        generate_month_calendar_text(date(year, month, 1), selected_date_iso)
        for year, month in seen
    ]
    return "\n".join(blocks)


def parse_time_input(raw: str) -> tuple[int, int]:
    """Строго парсит 'ЧЧ:ММ'. Бросает ValueError с понятным текстом при неверном формате."""
    raw = raw.strip()
    parts = raw.split(":")
    if len(parts) != 2 or not (parts[0].isdigit() and parts[1].isdigit()):
        raise ValueError("Введите время в формате ЧЧ:ММ, например 19:00")
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError("Час должен быть 0–23, минуты 0–59")
    return hour, minute


def combine_date_and_time(date_iso: str, hour: int, minute: int) -> datetime:
    """Дата (ISO YYYY-MM-DD) + час/минута -> aware datetime в SERVER_TZ (discord.py сам сконвертирует в UTC)."""
    d = datetime.strptime(date_iso, "%Y-%m-%d").date()
    return datetime(d.year, d.month, d.day, hour, minute, tzinfo=SERVER_TZ)
