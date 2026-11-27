"""
Автотесты бросков кубиков (core/dice.py).

Случайность подменяется: random.randint всегда возвращает заранее
заданные числа, поэтому результат броска известен заранее.
"""

import pytest

import core.dice as dice
from core.dice import DiceParseError, parse_and_roll


@pytest.fixture
def fixed_rolls(monkeypatch):
    """Подменяет бросок: каждый кубик выпадает числами из списка по очереди."""
    def set_rolls(*values):
        it = iter(values)
        monkeypatch.setattr(dice.random, "randint", lambda a, b: next(it))
    return set_rolls


def test_simple_roll(fixed_rolls):
    fixed_rolls(4, 5)
    total, raw, breakdown = parse_and_roll("2d6+3")
    assert total == 12
    assert breakdown == ["2d6 [4, 5] = 9"]


def test_russian_letter_k(fixed_rolls):
    fixed_rolls(7)
    total, _, _ = parse_and_roll("1к20")
    assert total == 7


def test_operator_priority(fixed_rolls):
    # Умножение раньше сложения: 2 + 3*2 = 8, а не (2+3)*2 = 10
    fixed_rolls(3)
    total, _, _ = parse_and_roll("2+1d6*2")
    assert total == 8


def test_brackets(fixed_rolls):
    fixed_rolls(3)
    total, _, _ = parse_and_roll("(2+1d6)*2")
    assert total == 10


@pytest.mark.parametrize("rounding, expected", [("down", 3), ("up", 4), ("nearest", 4)])
def test_rounding(fixed_rolls, rounding, expected):
    fixed_rolls(7)
    total, raw, _ = parse_and_roll("1d20/2", rounding)
    assert raw == 3.5
    assert total == expected


@pytest.mark.parametrize("expr", ["", "abc", "2d6+", "(1d6", "1d6/0", "101d6", "1d1", "1d1001"])
def test_bad_expressions(expr):
    with pytest.raises(DiceParseError):
        parse_and_roll(expr)
