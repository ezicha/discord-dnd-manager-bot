import random
import re
import discord
from discord.ext import commands

# Максимальные разумные ограничения, чтобы никто не мог попросить бота
# бросить, например, миллион кубиков и не подвесить его на этом
MAX_DICE_COUNT = 100
MAX_DICE_SIDES = 1000


class DiceParseError(Exception):
    pass


def parse_and_roll(expression: str):
    """
    Разбирает строку вида "2d6+3-1d4" на отдельные слагаемые,
    бросает каждый кубик и возвращает (итоговая сумма, список строк с расшифровкой).
    """
    expression = expression.replace(" ", "").lower()

    # Разбиваем строку на "куски" вида "+2d6", "-1d4", "+3" — каждый со своим знаком
    tokens = re.findall(r'[+-]?[^+-]+', expression)
    if not tokens:
        raise DiceParseError("Пустое выражение.")

    total = 0
    breakdown = []

    for token in tokens:
        sign = 1
        if token.startswith('-'):
            sign = -1
            token = token[1:]
        elif token.startswith('+'):
            token = token[1:]

        if 'd' in token:
            # Формат NdM, например "2d6" или просто "d20" (без числа кубиков — значит один)
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
            subtotal = sum(rolls) * sign
            total += subtotal

            sign_text = "+" if sign > 0 else "-"
            breakdown.append(f"{sign_text}{count}d{sides} [{', '.join(map(str, rolls))}] = {sign * sum(rolls)}")
        else:
            # Просто число-модификатор, например "+3"
            if not token.isdigit():
                raise DiceParseError(f"Не понял часть выражения: «{token}».")
            value = int(token) * sign
            total += value
            sign_text = "+" if sign > 0 else "-"
            breakdown.append(f"{sign_text}{token}")

    return total, breakdown


class Dice(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @discord.app_commands.command(name="roll", description="Бросить кубики (например: 1d20, 2d6+3, 4d6-1)")
    @discord.app_commands.describe(dice="Выражение вида NdM, можно с модификаторами: 2d6+3")
    async def roll(self, interaction: discord.Interaction, dice: str = "1d20"):
        try:
            total, breakdown = parse_and_roll(dice)
        except DiceParseError as e:
            await interaction.response.send_message(
                f"Не смог разобрать выражение: {e}\nПример правильного формата: `2d6+3`",
                ephemeral=True
            )
            return

        breakdown_text = "\n".join(breakdown)
        await interaction.response.send_message(
            f"🎲 **{interaction.user.display_name}** бросает `{dice}`:\n"
            f"{breakdown_text}\n"
            f"**Итого: {total}**"
        )


async def setup(bot: commands.Bot):
    await bot.add_cog(Dice(bot))