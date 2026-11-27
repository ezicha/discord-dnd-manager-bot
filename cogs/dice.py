"""
Discord-обёртка для бросков кубиков: команда /roll.
Сама логика разбора и броска — в core/dice.py (не зависит от Discord).
"""

import discord
from discord.ext import commands

from core.dice import DiceParseError, parse_and_roll


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