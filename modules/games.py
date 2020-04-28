import discord
from discord.ext import commands
import modules.utilities as utilities
import settings
# import modules.exceptions as exceptions
import peewee
from modules.models import Game, Player
import logging
# import datetime

logger = logging.getLogger('spiesbot.' + __name__)
elo_logger = logging.getLogger('spiesbot.elo')


class SpiesGame(commands.Converter):
    async def convert(self, ctx, game_id):
        # allows a SpiesGame to be used as a parameter for a discord command, and get converted into a database object on the fly

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
        # Listen for changes to member roles or display names and update database if any relevant changes detected
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

    @commands.command(usage='@Opponent "Optional Game Name"', aliases=['loseto'])
    async def defeat(self, ctx, *input_args):
        game_name = None
        args = list(input_args)
        if not args:
            return await ctx.send(f'**Usage:** `{ctx.prefix}{ctx.invoked_with} @Opponent [Losing Score] "Optional Game Name"')

        losing_score = None
        for arg in args:
            try:
                if int(arg) in [0, 1, 2]:
                    losing_score = int(arg)
                    args.remove(arg)
                    break
            except ValueError:
                pass  # arg is non-numeric

        guild_matches = await utilities.get_guild_member(ctx, args[0])
        if len(guild_matches) == 0:
            return await ctx.send(f'Could not find any server member matching *{args[0]}*. Try specifying with an @Mention')
        elif len(guild_matches) > 1:
            return await ctx.send(f'Found {len(guild_matches)} server members matching *{args[0]}*. Try specifying with an @Mention')
        target_discord_member = guild_matches[0]

        if target_discord_member.id == ctx.author.id:
            return await ctx.send('Stop beating yourself up.')

        if len(args) > 1:
            # Combine all args after the first one into a game name
            game_name = ' '.join(args[1:])

        if ctx.invoked_with == 'defeat':
            winning_player, _ = Player.get_or_create(discord_id=ctx.author.id, defaults={'name': ctx.author.display_name})
            losing_player, _ = Player.get_or_create(discord_id=target_discord_member.id, defaults={'name': target_discord_member.display_name})
            confirm_win = False
        else:
            # invoked with 'loseto', so swap player targets and confirm the game in one step
            losing_player, _ = Player.get_or_create(discord_id=ctx.author.id, defaults={'name': ctx.author.display_name})
            winning_player, _ = Player.get_or_create(discord_id=target_discord_member.id, defaults={'name': target_discord_member.display_name})
            confirm_win = True

        game, created = Game.get_or_create_pending_game(winning_player=winning_player, losing_player=losing_player, name=game_name, losing_score=losing_score)
        if not game:
            return await ctx.send(f'The loser player\'s score is required to calculate margin of victory. **Example:**: `{ctx.prefix}{ctx.invoked_with} @Nelluk 0` for a 3-0 game. Value must be 0, 1, or 2. '
                'The score can be omitted if you are confirming a pending loss.')

        if not confirm_win:
            if not created:
                return await ctx.send(f'There is already an unconfirmed game with these two opponents. Game {game.id} must be confirmed or deleted before another game is entered.')

            return await ctx.send(f'Game {game.id} created and waiting for defeated player <@{losing_player.discord_id}> to confirm loss. '
                f'Use `{ctx.prefix}loseto` <@{winning_player.discord_id}> to confirm loss.')
        else:
            game.confirm()
            return await ctx.send(f'Game {game.id} has been confirmed with <@{winning_player.discord_id}> ({winning_player.elo} +{game.elo_change_winner}) '
                f'defeating <@{losing_player.discord_id}> ({losing_player.elo} {game.elo_change_loser}). Good game! ')

    @commands.command()
    async def lb(self, ctx):
        leaderboard = []
        date_cutoff = settings.date_cutoff

        def process_leaderboard():
            utilities.connect()
            leaderboard_query = Player.leaderboard(date_cutoff=date_cutoff)

            for counter, player in enumerate(leaderboard_query[:2000]):
                wins, losses = player.get_record()
                leaderboard.append(
                    (f'{(counter + 1):>3}. {player.name}', f'`ELO {player.elo}\u00A0\u00A0\u00A0\u00A0W {wins} / L {losses}`')
                )
            return leaderboard, leaderboard_query.count()

        leaderboard, leaderboard_size = await self.bot.loop.run_in_executor(None, process_leaderboard)

        print(leaderboard)


def setup(bot):
    bot.add_cog(elo_games(bot))
