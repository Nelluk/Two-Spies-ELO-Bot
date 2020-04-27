import discord
from discord.ext import commands
import modules.utilities as utilities
import settings
# import modules.exceptions as exceptions
import peewee
from modules.models import Game, db, Player
import logging
# import datetime

logger = logging.getLogger('spiesbot.' + __name__)
elo_logger = logging.getLogger('spiesbot.elo')


class SpiesGame(commands.Converter):
    async def convert(self, ctx, game_id):

        utilities.connect()
        try:
            game = Game.get(id=int(game_id))
        except (ValueError, peewee.DataError):
            await ctx.send(f'Invalid game ID "{game_id}".')
            raise commands.UserInputError()
        except peewee.DoesNotExist:
            await ctx.send(f'Game with ID {game_id} cannot be found.')
            raise commands.UserInputError()
        else:
            logger.debug(f'Game with ID {game_id} found.')
            return game


class elo_games(commands.Cog):

    def __init__(self, bot):
        self.bot = bot
        if settings.run_tasks:
            pass
            # self.bg_task = bot.loop.create_task(self.task_purge_game_channels())

    @commands.Cog.listener()
    async def on_member_update(self, before, after):
        player_query = Player.select().where(
            (Player.discord_id == after.id)
        )

        banned_role = discord.utils.get(before.guild.roles, name='ELO Banned')
        if banned_role not in before.roles and banned_role in after.roles:
            try:
                player = player_query.get()
            except peewee.DoesNotExist:
                return
            player.is_banned = True
            player.save()
            logger.info(f'ELO Ban added for player {player.id} {player.name}')

        if banned_role in before.roles and banned_role not in after.roles:
            try:
                player = player_query.get()
            except peewee.DoesNotExist:
                return
            player.is_banned = False
            player.save()
            logger.info(f'ELO Ban removed for player {player.id} {player.name}')

        # Updates display name in DB if user changes their display name
        if before.display_name == after.display_name:
            return

        logger.debug(f'Attempting to change displayname for {before.display_name} to {after.display_name}')
        # update name in guild's Player record
        try:
            player = player_query.get()
        except peewee.DoesNotExist:
            return
        player.display_name = after.display_name
        player.save()


def setup(bot):
    bot.add_cog(elo_games(bot))
