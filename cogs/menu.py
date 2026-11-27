"""
Общая основа для меню бота — сообщений с кнопками и выпадашками.

Зачем: у каждого меню есть срок жизни (timeout). Раньше по его истечении
кнопки оставались яркими, а нажатие давало «Ошибка взаимодействия» без
объяснений. Меню на основе Menu по истечении срока гасит свои кнопки
и дописывает в сообщение, что его нужно вызвать заново.

Как пользоваться:
- наследовать меню от Menu вместо discord.ui.View;
- сразу после того, как меню показано (send_message / edit_message),
  вызвать menu.remember(interaction) — иначе меню не знает, где оно
  показано, и по истечении срока просто молча перестанет работать (как раньше);
- проверку «чьё это меню» писать в check_user, а не в interaction_check.
"""

import logging
import math

import discord

logger = logging.getLogger("argus")

STALE_NOTE = "⌛ Меню устарело — вызови команду заново."


def describe_error(error: BaseException) -> str:
    """
    Текст ошибки для пользователя. Отдельно — случай, когда Discord просит
    подождать дольше, чем бот готов ждать (max_ratelimit_timeout в argus.py):
    чаще всего это лимит на переименование канала — 2 раза за 10 минут.
    """
    if isinstance(error, discord.RateLimited):
        minutes = max(1, math.ceil(error.retry_after / 60))
        return (
            "Discord временно не даёт боту это сделать: он ограничивает частоту изменений "
            "(например, название канала можно менять не больше 2 раз за 10 минут). "
            f"Попробуй примерно через {minutes} мин."
        )
    return f"Ошибка: {error}"


async def start_action(interaction: discord.Interaction, text: str) -> None:
    """
    Вызывается в самом начале действия, которое что-то меняет в Discord
    (вместо interaction.response.defer). Сразу заменяет меню текстом вроде
    «Архивирую каналы…» и убирает кнопки — чтобы действие нельзя было
    запустить второй раз, пока первое ещё идёт (а идти оно может долго:
    Discord ограничивает частоту изменений). Итог потом показывает finish_menu.

    Если меню нет (форма открыта прямо из команды) — показывает «бот думает…».
    """
    if interaction.message is not None:
        await interaction.response.edit_message(content=text, view=None)
    else:
        await interaction.response.defer(ephemeral=True, thinking=True)


async def finish_menu(interaction: discord.Interaction, text: str) -> None:
    """
    Заменяет меню (сообщение, видное только вызвавшему) итоговым текстом
    и убирает с него кнопки. Если меню нет (например, форма была открыта
    прямо из команды) — отправляет текст отдельным приватным сообщением.
    """
    try:
        await interaction.edit_original_response(content=text, view=None)
    except discord.HTTPException:
        await interaction.followup.send(text, ephemeral=True)

# Сколько последних взаимодействий помнить. Нужно несколько, а не одно:
# если последнее нажатие открыло форму (модалку), через него сообщение
# с меню не найти — тогда пробуем предыдущее.
_REMEMBER_LIMIT = 5


def component_ids(components) -> set[str]:
    """Все custom_id кнопок и выпадашек — и у меню, и у сообщения Discord
    (там они вложены в ряды, поэтому обходим рекурсивно)."""
    ids = set()
    for component in components:
        custom_id = getattr(component, "custom_id", None)
        if custom_id:
            ids.add(custom_id)
        children = getattr(component, "children", None)
        if children:
            ids |= component_ids(children)
    return ids


class Menu(discord.ui.View):
    def __init__(self, *, timeout: float | None = 180):
        super().__init__(timeout=timeout)
        self._interactions: list[discord.Interaction] = []

    def remember(self, interaction: discord.Interaction) -> None:
        """Запомнить взаимодействие, через которое можно отредактировать сообщение с меню."""
        self._interactions.append(interaction)
        del self._interactions[:-_REMEMBER_LIMIT]

    async def check_user(self, interaction: discord.Interaction) -> bool:
        """Можно ли этому пользователю нажимать кнопки меню. По умолчанию — всем."""
        return True

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not await self.check_user(interaction):
            return False
        self.remember(interaction)
        return True

    async def on_error(self, interaction: discord.Interaction, error: Exception, item) -> None:
        """Необработанная ошибка в кнопке или выпадашке: пишем в лог и коротко
        сообщаем пользователю, а не оставляем его с «Ошибкой взаимодействия»."""
        logger.error("Ошибка в меню %s: %r", type(self).__name__, error, exc_info=error)
        text = f"Что-то пошло не так. {describe_error(error)}"
        try:
            if interaction.response.is_done():
                await interaction.followup.send(text, ephemeral=True)
            else:
                await interaction.response.send_message(text, ephemeral=True)
        except discord.HTTPException:
            pass

    async def on_timeout(self) -> None:
        my_ids = component_ids(self.children)
        for interaction in reversed(self._interactions):
            try:
                message = await interaction.original_response()
            except discord.HTTPException:
                continue  # через это взаимодействие сообщение не найти — пробуем предыдущее

            if not my_ids & component_ids(message.components):
                # На сообщении уже другое меню или итог действия — не трогаем.
                return

            for item in self.children:
                item.disabled = True
            content = f"{message.content}\n\n{STALE_NOTE}" if message.content else STALE_NOTE
            try:
                await interaction.edit_original_response(content=content, view=self)
            except discord.HTTPException as e:
                logger.debug("Не удалось пометить меню устаревшим: %r", e)
            return
