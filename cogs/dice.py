import random
import re
import math
import discord
from discord.ext import commands

MAX_DICE_COUNT = 100
MAX_DICE_SIDES = 1000

# Порядок важен: сначала пробуем распознать "кубик" (NdM), потом просто число, потом знак операции
TOKEN_RE = re.compile(r'\d*d\d+|\d+(?:\.\d+)?|[+\-*/()]')


class DiceParseError(Exception):
    pass


class Tokenizer:
    """Простой курсор по списку токенов — помогает писать разбор выражения по шагам."""
    def __init__(self, tokens):
        self.tokens = tokens
        self.pos = 0

    def peek(self):
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def next(self):
        token = self.peek()
        self.pos += 1
        return token


def roll_dice_term(token: str, breakdown: list) -> float:
    count_str, sides_str = token.split('d', 1)
    count = int(count_str) if count_str else 1
    if not sides_str.isdigit():
        raise DiceParseError(f"Не понял часть выражения: «{token}».")
    sides = int(sides_str)

    if count < 1 or count > MAX_DICE_COUNT:
        raise DiceParseError(f"Количество кубиков должно быть от 1 до {MAX_DICE_COUNT}.")
    if sides < 2 or sides > MAX_DICE_SIDES:
        raise DiceParseError(f"Количество граней должно быть от 2 до {MAX_DICE_SIDES}.")

    rolls = [random.randint(1, sides) for _ in range(count)]
    subtotal = sum(rolls)
    breakdown.append(f"{count}d{sides} [{', '.join(map(str, rolls))}] = {subtotal}")
    return float(subtotal)


# --- Разбор выражения с правильным приоритетом операций (умножение/деление раньше сложения/вычитания) ---

def parse_primary(tk: Tokenizer, breakdown: list) -> float:
    token = tk.next()
    if token is None:
        raise DiceParseError("Выражение обрывается раньше времени.")

    if token == '(':
        value = parse_expr(tk, breakdown)
        closing = tk.next()
        if closing != ')':
            raise DiceParseError("Не хватает закрывающей скобки «)».")
        return value

    if 'd' in token:
        return roll_dice_term(token, breakdown)

    try:
        return float(token)
    except ValueError:
        raise DiceParseError(f"Не понял часть выражения: «{token}».")


def parse_factor(tk: Tokenizer, breakdown: list) -> float:
    sign = 1
    while tk.peek() in ('+', '-'):
        if tk.next() == '-':
            sign *= -1

    return sign * parse_primary(tk, breakdown)


def parse_term(tk: Tokenizer, breakdown: list) -> float:
    value = parse_factor(tk, breakdown)
    while tk.peek() in ('*', '/'):
        op = tk.next()
        rhs = parse_factor(tk, breakdown)
        if op == '*':
            value *= rhs
        else:
            if rhs == 0:
                raise DiceParseError("Деление на ноль.")
            value /= rhs
    return value


def parse_expr(tk: Tokenizer, breakdown: list) -> float:
    value = parse_term(tk, breakdown)
    while tk.peek() in ('+', '-'):
        op = tk.next()
        rhs = parse_term(tk, breakdown)
        value = value + rhs if op == '+' else value - rhs
    return value


def apply_rounding(value: float, mode: str) -> int:
    if mode == "up":
        return math.ceil(value)
    elif mode == "nearest":
        return round(value)
    else:  # "down" — по умолчанию, как в большинстве настольных систем
        return math.floor(value)


def parse_and_roll(expression: str, rounding: str = "down"):
    expression = expression.replace(" ", "").lower()
    expression = expression.replace("к", "d")

    tokens = TOKEN_RE.findall(expression)
    if not tokens or "".join(tokens) != expression:
        raise DiceParseError("Выражение содержит недопустимые символы или пустое.")

    breakdown = []
    tk = Tokenizer(tokens)
    raw_total = parse_expr(tk, breakdown)

    if tk.peek() is not None:
        raise DiceParseError(f"Не понял часть выражения рядом с «{tk.peek()}».")

    total = apply_rounding(raw_total, rounding)
    return total, raw_total, breakdown


class Dice(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @discord.app_commands.command(name="roll", description="Бросить кубики: 1d20, 2d6*2, 2к6/2 и т.п.")
    @discord.app_commands.describe(
        dice="Выражение: 1d20, 2к6+3, 2d6*2, 10d6/2",
        rounding="Как округлять, если получилась дробь (по умолчанию — вниз)"
    )
    @discord.app_commands.choices(rounding=[
        discord.app_commands.Choice(name="Вниз", value="down"),
        discord.app_commands.Choice(name="Вверх", value="up"),
        discord.app_commands.Choice(name="Обычное (математическое)", value="nearest"),
    ])
    async def roll(self, interaction: discord.Interaction, dice: str = "1d20", rounding: str = "down"):
        try:
            total, raw_total, breakdown = parse_and_roll(dice, rounding)
        except DiceParseError as e:
            await interaction.response.send_message(
                f"Не смог разобрать выражение: {e}\nПример: `2d6+3`, `2d6*2`, `10d6/2`",
                ephemeral=True
            )
            return

        breakdown_text = "\n".join(breakdown) if breakdown else "(кубиков в выражении не было)"

        rounding_note = ""
        if abs(raw_total - total) > 1e-9:
            rounding_note = f" (точный результат: {raw_total:.2f}, округлено)"

        await interaction.response.send_message(
            f"🎲 **{interaction.user.display_name}** бросает `{dice}`:\n"
            f"{breakdown_text}\n"
            f"**Итого: {total}**{rounding_note}"
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Dice(bot))