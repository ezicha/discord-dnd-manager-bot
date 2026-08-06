import discord
from discord.ext import commands
import logging

logger = logging.getLogger("argus")


class Housekeeping(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        # На случай, если роль появилась у бота, пока он был офлайн
        # (например, во время перезапуска или ручной правки на сервере в этот момент)
        for guild in self.bot.guilds:
            await self.cleanup_bot_roles(guild)

    @commands.Cog.listener()
    async def on_member_update(self, before: discord.Member, after: discord.Member):
        # Реагируем мгновенно, если у бота только что появилась лишняя роль,
        # без ожидания следующего перезапуска
        if after.id != self.bot.user.id:
            return
        if before.roles == after.roles:
            return
        await self.cleanup_bot_roles(after.guild)

    async def cleanup_bot_roles(self, guild: discord.Guild):
        me = guild.me

        extra_roles = [r for r in me.roles if not r.managed and r != guild.default_role]

        if not extra_roles:
            return

        try:
            await me.remove_roles(*extra_roles, reason="Автоочистка лишних ролей бота")
            logger.info(
                "Сервер «%s»: сняты лишние роли с бота: %s",
                guild.name, ", ".join(r.name for r in extra_roles)
            )
        except discord.Forbidden:
            logger.error("Сервер «%s»: не хватает прав, чтобы снять роли с бота.", guild.name)


async def setup(bot: commands.Bot):
    await bot.add_cog(Housekeeping(bot))