import logging
import os

import discord
from discord import app_commands
from discord.ext import commands

from cogs.cmpgn.campaign_common import ARCHIVE_CATEGORY_NAME, GM_ROLE_PREFIX, channel_select_option

logger = logging.getLogger("argus")

# Команда только для этапа разработки: полностью очищает архив (кроме
# выбранных каналов) вместе с ролями кампаний, которые из-за этого нигде
# больше не используются. Регистрируется только если в .env стоит
# DEV_MODE=1 — см. setup() в самом низу файла. НЕ включать на боевом сервере.


def _channel_roles(channel: discord.abc.GuildChannel) -> set[discord.Role]:
    """Роли (кроме @everyone), у которых есть свои overwrites на канале."""
    return {
        target for target in channel.overwrites
        if isinstance(target, discord.Role) and not target.is_default()
    }

def is_campaign_role(role: discord.Role, guild: discord.Guild) -> bool:
    """Роль кампании — либо 'ГМ: <название>', либо участник (есть парная ГМ-роль)."""
    if role.is_default() or role.managed:
        return False
    if role.name.startswith(GM_ROLE_PREFIX):
        return True
    return discord.utils.get(guild.roles, name=f"{GM_ROLE_PREFIX}{role.name}") is not None

class ConfirmWipeView(discord.ui.View):
    """Финальное подтверждение перед необратимым удалением."""

    def __init__(self, owner_id: int):
        super().__init__(timeout=60)
        self.owner_id = owner_id
        self.confirmed: bool | None = None

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("Эта кнопка не для тебя.", ephemeral=True)
            return False
        return True

    @discord.ui.button(label="Удалить", style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.confirmed = True
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content="Удаляю…", view=self)
        self.stop()

    @discord.ui.button(label="Отмена", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.confirmed = False
        for child in self.children:
            child.disabled = True
        await interaction.response.edit_message(content="Отменено.", view=self)
        self.stop()


class KeepChannelsSelect(discord.ui.Select):
    """
    Выбор каналов, которые НЕ нужно удалять. Показывает максимум 25 каналов —
    больше select-меню Discord не поддерживает (та же известная шероховатость,
    что и в остальных select-меню бота). Если в архиве больше 25 каналов,
    остальные в этом запуске команды не тронутся вообще.

    Сам по себе выбор здесь ничего не запускает — только запоминает
    состояние. Переход дальше — по кнопке "Готово" в KeepChannelsView,
    иначе сценарий "оставить пусто = удалить всё" не работал бы: Discord
    не шлёт интеракцию, если выбор в select-меню не изменился относительно
    начального состояния (а начальное состояние — пустой выбор).
    """

    def __init__(self, archive_channels: list[discord.abc.GuildChannel]):
        # Срез применяем один раз и храним именно его — иначе то, что не
        # попало в опции select-меню, ошибочно попадёт в список на удаление.
        self.archive_channels = archive_channels[:25]
        options = [channel_select_option(ch) for ch in self.archive_channels]
        super().__init__(
            placeholder="Каналы, которые НЕ нужно удалять",
            min_values=0,
            max_values=len(options),
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        chosen_channels = [ch for ch in self.archive_channels if str(ch.id) in self.values]
        if chosen_channels:
            chosen = ", ".join(
                f"«{ch.name}» ({'войс' if isinstance(ch, discord.VoiceChannel) else 'текст'})"
                for ch in chosen_channels
            )
        else:
            chosen = "ничего — значит, удалятся все показанные каналы"
        await interaction.response.edit_message(
            content=f"Оставить: {chosen}.\nКогда закончишь выбор — нажми «Готово».",
            view=self.view,
        )

class KeepRolesSelect(discord.ui.Select):
    """Выбор ролей, которые НЕ нужно снимать. Логика та же, что у KeepChannelsSelect."""

    def __init__(self, campaign_roles: list[discord.Role]):
        self.campaign_roles = campaign_roles[:25]
        options = [
            discord.SelectOption(label=r.name, value=str(r.id))
            for r in self.campaign_roles
        ]
        super().__init__(
            placeholder="Роли, которые НЕ нужно снимать",
            min_values=0,
            max_values=len(options),
            options=options,
        )

    async def callback(self, interaction: discord.Interaction):
        chosen_roles = [r for r in self.campaign_roles if str(r.id) in self.values]
        chosen = ", ".join(f"«{r.name}»" for r in chosen_roles) if chosen_roles else "ничего — значит, снимутся все показанные роли"
        await interaction.response.edit_message(
            content=f"Оставить: {chosen}.\nКогда закончишь выбор — нажми «Готово».",
            view=self.view,
        )


class ProceedRolesButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Готово", style=discord.ButtonStyle.primary, row=1)

    async def callback(self, interaction: discord.Interaction):
        view: "KeepRolesView" = self.view
        keep_ids = {int(v) for v in view.select.values}
        to_remove = [r for r in view.select.campaign_roles if r.id not in keep_ids]

        if not to_remove:
            await interaction.response.edit_message(
                content="Нечего снимать — все показанные роли выбраны для сохранения.",
                view=None,
            )
            return

        names = "\n".join(f"- {r.name}" for r in to_remove)
        confirm_view = ConfirmWipeView(interaction.user.id)
        await interaction.response.edit_message(
            content=f"С тебя будут сняты **{len(to_remove)}** ролей:\n{names}\n\nПодтвердить?",
            view=confirm_view,
        )
        await confirm_view.wait()

        if not confirm_view.confirmed:
            return

        member = interaction.user
        try:
            await member.remove_roles(*to_remove, reason="dev_strip_my_roles: очистка ролей тестировщика")
        except discord.HTTPException as e:
            logger.warning("dev_strip_my_roles: не удалось снять роли: %s", e)
            await interaction.followup.send(f"Не всё получилось снять: {e}", ephemeral=True)
            return

        await interaction.followup.send(f"Готово. Снято ролей: {len(to_remove)}.", ephemeral=True)


class KeepRolesView(discord.ui.View):
    def __init__(self, campaign_roles: list[discord.Role]):
        super().__init__(timeout=120)
        self.select = KeepRolesSelect(campaign_roles)
        self.add_item(self.select)
        self.add_item(ProceedRolesButton())

class ProceedButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Готово", style=discord.ButtonStyle.primary, row=1)

    async def callback(self, interaction: discord.Interaction):
        view: "KeepChannelsView" = self.view
        keep_ids = {int(v) for v in view.select.values}
        to_delete = [ch for ch in view.select.archive_channels if ch.id not in keep_ids]

        if not to_delete:
            await interaction.response.edit_message(
                content="Нечего удалять — все показанные каналы архива выбраны для сохранения.",
                view=None,
            )
            return

        names = "\n".join(
            f"- {ch.name} ({'войс' if isinstance(ch, discord.VoiceChannel) else 'текст'})"
            for ch in to_delete
        )
        confirm_view = ConfirmWipeView(interaction.user.id)
        await interaction.response.edit_message(
            content=(
                f"Будут удалены **{len(to_delete)}** каналов архива "
                f"(и роли кампаний, которые из-за этого больше нигде не используются):\n{names}\n\n"
                "Это необратимо. Подтвердить?"
            ),
            view=confirm_view,
        )
        await confirm_view.wait()

        if not confirm_view.confirmed:
            return

        guild = interaction.guild
        touched_roles: set[discord.Role] = set()
        deleted_names = []

        for ch in to_delete:
            touched_roles |= _channel_roles(ch)
            deleted_names.append(ch.name)
            try:
                await ch.delete(reason="dev_wipe_archive: очистка архива на этапе разработки")
            except discord.HTTPException as e:
                logger.warning("dev_wipe_archive: не удалось удалить канал %s: %s", ch.name, e)

        # Роль удаляем, только если она больше нигде не используется на
        # сервере — ни в одном оставшемся канале.
        roles_in_use: set[discord.Role] = set()
        for ch in guild.channels:
            roles_in_use |= _channel_roles(ch)

        deleted_role_names = []
        for role in touched_roles:
            if role.managed or role in roles_in_use:
                continue
            try:
                await role.delete(reason="dev_wipe_archive: роль больше не используется")
                deleted_role_names.append(role.name)
            except discord.HTTPException as e:
                logger.warning("dev_wipe_archive: не удалось удалить роль %s: %s", role.name, e)

        summary = f"Готово. Удалено каналов: {len(deleted_names)}."
        if deleted_role_names:
            summary += f"\nУдалено ролей: {len(deleted_role_names)} ({', '.join(deleted_role_names)})."
        else:
            summary += "\nРолей на удаление не нашлось."

        await interaction.followup.send(summary, ephemeral=True)


class KeepChannelsView(discord.ui.View):
    def __init__(self, archive_channels: list[discord.abc.GuildChannel]):
        super().__init__(timeout=120)
        self.select = KeepChannelsSelect(archive_channels)
        self.add_item(self.select)
        self.add_item(ProceedButton())


class CampaignDevTools(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="dev_wipe_archive",
        description="[DEV] Удалить каналы архива, кроме выбранных, вместе с их ролями",
    )
    async def dev_wipe_archive(self, interaction: discord.Interaction):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("Только на сервере.", ephemeral=True)
            return

        # Доп. проверка на всякий случай — даже если DEV_MODE случайно
        # включат не там, кроме владельца сервера никто не сможет вызвать.
        if interaction.user.id != guild.owner_id:
            await interaction.response.send_message(
                "Эта команда доступна только владельцу сервера.", ephemeral=True
            )
            return

        archive_category = discord.utils.get(guild.categories, name=ARCHIVE_CATEGORY_NAME)
        if archive_category is None or not archive_category.channels:
            await interaction.response.send_message("Архив пуст.", ephemeral=True)
            return

        archive_channels = list(archive_category.channels)
        note = ""
        if len(archive_channels) > 25:
            note = (
                f"\n(в архиве {len(archive_channels)} каналов, покажу первые 25 — "
                "остальные в этот раз не тронутся)"
            )

        await interaction.response.send_message(
            f"Выбери каналы, которые НЕ нужно удалять:{note}",
            view=KeepChannelsView(archive_channels),
            ephemeral=True,
        )

    @app_commands.command(
        name="dev_strip_my_roles",
        description="[DEV] Снять с себя роли кампаний (ГМ/участник), кроме выбранных",
    )
    async def dev_strip_my_roles(self, interaction: discord.Interaction):
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message("Только на сервере.", ephemeral=True)
            return

        if interaction.user.id != guild.owner_id:
            await interaction.response.send_message(
                "Эта команда доступна только владельцу сервера.", ephemeral=True
            )
            return

        member = interaction.user
        campaign_roles = [r for r in member.roles if is_campaign_role(r, guild)]
        if not campaign_roles:
            await interaction.response.send_message("У тебя сейчас нет ролей кампаний.", ephemeral=True)
            return

        note = ""
        if len(campaign_roles) > 25:
            note = (
                f"\n(у тебя {len(campaign_roles)} ролей кампаний, покажу первые 25 — "
                "остальные в этот раз не тронутся)"
            )

        await interaction.response.send_message(
            f"Выбери роли, которые НЕ нужно снимать:{note}",
            view=KeepRolesView(campaign_roles),
            ephemeral=True,
        )


async def setup(bot: commands.Bot):
    if os.getenv("DEV_MODE", "").lower() not in ("1", "true", "yes"):
        logger.info("DEV_MODE выключен — cogs.campaign_devtools не загружается")
        return
    await bot.add_cog(CampaignDevTools(bot))
    logger.info("cogs.campaign_devtools загружен (DEV_MODE включён)")