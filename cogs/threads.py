import logging

import discord
from discord import app_commands
from discord.ext import commands

logger = logging.getLogger("argus")


class ThreadNameModal(discord.ui.Modal, title="Создать приватный тред"):
    name = discord.ui.TextInput(label="Название треда (тема)", max_length=100)

    async def on_submit(self, interaction: discord.Interaction):
        view = ThreadMembersView(str(self.name), interaction.channel)
        await interaction.response.send_message(
            "Выбери участников треда:", view=view, ephemeral=True
        )


class ThreadMembersSelect(discord.ui.UserSelect):
    def __init__(self):
        super().__init__(placeholder="Выбери участников", min_values=1, max_values=25)

    async def callback(self, interaction: discord.Interaction):
        self.view.selected_members = self.values
        await interaction.response.defer()


class ThreadCreateConfirmButton(discord.ui.Button):
    def __init__(self):
        super().__init__(label="Создать тред", style=discord.ButtonStyle.primary)

    async def callback(self, interaction: discord.Interaction):
        view: ThreadMembersView = self.view

        if not view.selected_members:
            await interaction.response.send_message(
                "Сначала выбери хотя бы одного участника.", ephemeral=True
            )
            return

        channel = view.channel
        if not isinstance(channel, discord.TextChannel):
            await interaction.response.send_message(
                "Приватные треды можно создавать только в текстовых каналах.", ephemeral=True
            )
            return

        try:
            thread = await channel.create_thread(
                name=view.thread_name,
                type=discord.ChannelType.private_thread,
                invitable=True,
            )
            await thread.add_user(interaction.user)
            for member in view.selected_members:
                await thread.add_user(member)
        except discord.Forbidden:
            logger.error("Нет прав на создание приватного треда в канале %s", channel.id)
            await interaction.response.send_message(
                "Не хватает прав на создание приватных тредов в этом канале.", ephemeral=True
            )
            return
        except discord.HTTPException as e:
            logger.error("Ошибка при создании треда: %s", e)
            await interaction.response.send_message(
                "Не получилось создать тред, попробуй ещё раз.", ephemeral=True
            )
            return

        mentions = ", ".join(member.mention for member in view.selected_members)
        await interaction.response.edit_message(
            content=f"Тред {thread.mention} создан, добавлены: {mentions}.",
            view=None,
        )


class ThreadMembersView(discord.ui.View):
    def __init__(self, thread_name: str, channel: discord.abc.GuildChannel):
        super().__init__(timeout=180)
        self.thread_name = thread_name
        self.channel = channel
        self.selected_members: list[discord.Member] = []
        self.add_item(ThreadMembersSelect())
        self.add_item(ThreadCreateConfirmButton())


class Threads(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(
        name="thread_create",
        description="Создать приватный тред в этом канале с выбранными участниками",
    )
    async def thread_create(self, interaction: discord.Interaction):
        await interaction.response.send_modal(ThreadNameModal())


async def setup(bot: commands.Bot):
    await bot.add_cog(Threads(bot))