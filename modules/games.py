import discord
from discord.ext import commands
import modules.utilities as utilities
import settings
# import modules.exceptions as exceptions
import peewee
from modules.models import Game, Player, db
import logging
import datetime

logger = logging.getLogger('spiesbot.' + __name__)
elo_logger = logging.getLogger('spiesbot.elo')

yesterday = (datetime.datetime.now() + datetime.timedelta(hours=-24))


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


class games(commands.Cog):

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
            utilities.connect()
            try:
                player = player_query.get()
            except peewee.DoesNotExist:
                return
            player.is_banned = True
            player.save()
            logger.info(f'ELO Ban added for player {player.id} {player.name}')

        if banned_role in before.roles and banned_role not in after.roles:
            utilities.connect()
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
        utilities.connect()
        try:
            player = player_query.get()
        except peewee.DoesNotExist:
            return
        player.display_name = after.display_name
        player.save()

    @commands.command(usage='@Opponent [Losing Score] "Optional Game Name"', aliases=['loseto'])
    async def defeat(self, ctx, *input_args):
        """Enter a game result

        Use /loseto to enter a game where you are the loser, or /defeat to enter the game where you are the winner.

        If a winner is claiming a game, the loser must use /loseto to confirm the result and finalize the ELO changes. Pending games
        will auto-confirm after a period of time.

        Ranked games are assumed to be first-to-3, with possible scores of 3-0, 3-1, or 3-2. Enter the losing player's score as the second argument.
        This can be omitted if you are confirming a loss.

        You can enter a game name, such as the invite code word pair, at the end of the command.

        **Example:**
        `[p]defeat @Nelluk 2` - Claim a 3-2 win against Nelluk
        `[p]loseto @DuffyDood 1` - Acknowledge a 3-1 loss against DuffyDood.
        `[p]loseto @DuffyDood` - Confirm an already-claimed loss
        `[p]defeat @Nelluk 0 Loud Wire` - Enter a 3-0 claim and include a game name
        """
        game_name = None
        args = list(input_args)
        if not args:
            return await ctx.send(f'**Usage:** `{ctx.prefix}{ctx.invoked_with} @Opponent [Losing Score] "Optional Game Name"`')

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

        if target_discord_member.bot:
            return await ctx.send('Nice try, bot-bully.')

        if len(args) > 1:
            # Combine all args after the first one into a game name
            game_name = ' '.join(args[1:])

        if ctx.invoked_with == 'defeat':
            winning_player, _ = Player.get_or_create(discord_id=ctx.author.id, defaults={'name': ctx.author.display_name})
            losing_player, _ = Player.get_or_create(discord_id=target_discord_member.id, defaults={'name': target_discord_member.display_name})
            winning_member = ctx.author
            confirm_win = False
        else:
            # invoked with 'loseto', so swap player targets and confirm the game in one step
            losing_player, _ = Player.get_or_create(discord_id=ctx.author.id, defaults={'name': ctx.author.display_name})
            winning_player, _ = Player.get_or_create(discord_id=target_discord_member.id, defaults={'name': target_discord_member.display_name})
            winning_member = ctx.guild.get_member(target_discord_member.id)
            confirm_win = True

        if losing_player.is_banned or winning_player.is_banned:
            return await ctx.send(f'Your opponent has the **ELO Banned** role and can not participate in ELO Games.')

        game, created = Game.get_or_create_pending_game(winning_player=winning_player, losing_player=losing_player, name=game_name, losing_score=losing_score)
        if not game:
            return await ctx.send(f'The loser player\'s score is required to calculate margin of victory. **Example:**: `{ctx.prefix}{ctx.invoked_with} @Nelluk 0` for a 3-0 game. Value must be 0, 1, or 2. '
                'The score can be omitted if you are confirming a pending loss.')

        if not confirm_win:
            if not created:
                return await ctx.send(f'There is already an unconfirmed game with these two opponents. Game {game.id} must be confirmed or deleted before another game is entered.')

            confirm_msg = await ctx.send(f'Game {game.id} created and waiting for defeated player <@{losing_player.discord_id}> to confirm loss. React below.')
            confirm_status = await utilities.wait_for_confirmation(self.bot, ctx, game=game, losing_member=guild_matches[0], message=confirm_msg)

            try:
                game = game.refresh()  # Update game from database in case is_confirmed flag changed during reaction wait time
            except peewee.DoesNotExist:
                return await ctx.send(f'Game {game.id} cannot be found. Most likely it was deleted by a user while waiting for confirmation. No ELO has changed.')

            if confirm_status:
                confirm_win = True
            else:
                if game.is_confirmed:
                    return await ctx.send(f'Game {game.id} is already marked as confirmed.')
                return await ctx.send(f'Confirmation has been *rejected*. Game {game.id} is still pending. Contact your opponent <@{winning_player.discord_id}> or server staff '
                    f'to resolve the dispute. To manually confirm the game please use the command `{ctx.prefix}loseto @{winning_player.name}`')
        if confirm_win:
            # not using an else since confirm_win value can change after it is checked as False
            try:
                winning_player_new_elo, losing_player_new_elo = game.confirm()
            except ValueError:
                return await ctx.send(f'Game {game.id} is already marked as confirmed.')

            rank_winner, _ = winning_player.leaderboard_rank(date_cutoff=settings.date_cutoff)
            rank_loser, _ = losing_player.leaderboard_rank(date_cutoff=settings.date_cutoff)

            champion_role = discord.utils.get(ctx.guild.roles, name=settings.guild_setting(ctx.guild.id, 'champion_role_name'))
            hero_role = discord.utils.get(ctx.guild.roles, name=settings.guild_setting(ctx.guild.id, 'hero_role_name'))

            if rank_winner == 1 and champion_role and winning_member and champion_role not in winning_member.roles:
                for member in hero_role.members:
                    try:
                        await member.remove_roles(champion_role, reason='Dethroned champion')
                    except discord.DiscordException as e:
                        logger.warn(f'Could not remove champion role: {e}')
                        await ctx.send(f'**Warning** Tried to remove champion role from {member.display_name} but got a discord error: {e}')
                try:
                    await winning_member.add_roles(champion_role, reason='New champion')
                except discord.DiscordException as e:
                    logger.warn(f'Could not apply champion role: {e}')
                    await ctx.send(f'**Warning** Tried to apply champion role to {winning_member.display_name} but got a discord error: {e}')

            if hero_role and winning_player_new_elo > 1200 and hero_role not in winning_member.roles:
                try:
                    await winning_member.add_roles(hero_role, reason='New Hero')
                except discord.DiscordException as e:
                    logger.warn(f'Could not apply Hero role: {e}')
                    await ctx.send(f'**Warning** Tried to apply Hero role to {winning_member.display_name} but got a discord error: {e}')

            return await ctx.send(f'Game {game.id} has been confirmed with <@{winning_player.discord_id}> `({winning_player_new_elo} +{game.elo_change_winner} 📈{rank_winner})` '
                f'defeating <@{losing_player.discord_id}> `({losing_player_new_elo} {game.elo_change_loser} 📉{rank_loser})`. Good game! ')

    @settings.in_bot_channel_strict()
    @commands.command(aliases=['lbmax'])
    async def lb(self, ctx):
        """Display leaderboard - use lbmax to sort by maximum ELO achieved"""

        leaderboard = []
        lb_title = 'Two Spies Leaderboard'
        date_cutoff = settings.date_cutoff
        if ctx.invoked_with == 'lbmax':
            max_flag = True
            max_str = ' (by Maximum ELO Achieved)'
        else:
            max_flag = False
            max_str = ''

        def process_leaderboard():
            utilities.connect()
            leaderboard_query = Player.leaderboard(date_cutoff=date_cutoff, max_flag=max_flag)

            for counter, player in enumerate(leaderboard_query[:2000]):
                wins, losses = player.get_record()
                elo_field = player.elo_max if max_flag else player.elo
                leaderboard.append(
                    (f'{(counter + 1):>3}. {player.name}', f'`ELO {elo_field}\u00A0\u00A0\u00A0\u00A0W {wins} / L {losses}`')
                )
            return leaderboard, leaderboard_query.count()

        leaderboard, leaderboard_size = await self.bot.loop.run_in_executor(None, process_leaderboard)

        await utilities.paginate(self.bot, ctx, title=f'**{lb_title}**\n{leaderboard_size} ranked players{max_str}', message_list=leaderboard, page_start=0, page_end=12, page_size=12)

    @commands.command(usage='game_id')
    async def delete(self, ctx, game: SpiesGame = None):
        """Deletes a game

        You can delete a game if you are the winner and the win was claimed in the last 24 hours.
        Staff can delete any completed games.

        ELO changes will be reversed and the ELO changes for any games that had been claimed subsequent to the deleted game will be recalculated.

        **Example:**
        `[p]delete 25`
        """
        if not game:
            return await ctx.send(f'Game ID not provided. Usage: __`{ctx.prefix}delete GAME_ID`__')

        if settings.is_staff(ctx) or (game.winning_player.discord_id == ctx.author.id and game.win_claimed_ts > yesterday):
            pass
        else:
            return await ctx.send(f'To delete a game you must be server staff, or be the winner of a game claimed in the last 24 hours.')

        gid = game.id
        async with ctx.typing():
            await self.bot.loop.run_in_executor(None, game.delete_game)
            # Allows bot to remain responsive while this large operation is running.
            await ctx.send(f'Game with ID {gid} has been deleted and team/player ELO changes have been reverted, if applicable.')

    @commands.command(brief='See details on a player', usage='player_name', aliases=['elo', 'rank'])
    async def player(self, ctx, *args):
        """See your own player card or the card of another player

        **Examples**
        `[p]player` - See your own player card
        `[p]player Nelluk` - See Nelluk's card
        """

        args_list = list(args)
        if len(args_list) == 0:
            # Player looking for info on themselves
            args_list.append(f'<@{ctx.author.id}>')

        # Otherwise look for a player matching whatever they entered
        player_mention = ' '.join(args_list)
        player_mention_safe = utilities.escape_role_mentions(player_mention)

        player_results = Player.string_matches(player_string=player_mention)
        if len(player_results) > 1:
            p_names = [p.name for p in player_results]
            p_names_str = '**, **'.join(p_names[:10])
            return await ctx.send(f'Found {len(player_results)} players matching *{player_mention_safe}*. Be more specific or use an @Mention.\nFound: **{p_names_str}**')
        elif len(player_results) == 0:
            # No Player matches - check for guild membership
            guild_matches = await utilities.get_guild_member(ctx, player_mention)
            if len(guild_matches) > 1:
                p_names = [p.display_name for p in guild_matches]
                p_names_str = '**, **'.join(p_names[:10])
                return await ctx.send(f'There is more than one member found with name *{player_mention_safe}*. Be more specific or use an @Mention.\nFound: **{p_names_str}**')
            if len(guild_matches) == 0:
                return await ctx.send(f'Could not find *{player_mention_safe}* by Discord name or ID.')

            return await ctx.send(f'*{guild_matches[0].display_name}* was found but has no game history.')
        else:
            player = player_results[0]

        def async_create_player_embed():
            utilities.connect()
            wins, losses = player.get_record()
            rank, lb_length = player.leaderboard_rank(settings.date_cutoff)

            if rank is None:
                rank_str = 'Unranked'
            else:
                rank_str = f'{rank} of {lb_length}'

            max_str = f'(Max: {player.elo_max})\n' if player.elo_max > player.elo else ''
            results_str = f'ELO: {player.elo}\n{max_str}W\u00A0{wins}\u00A0/\u00A0L\u00A0{losses}'

            embed = discord.Embed(description=f'__Player card for <@{player.discord_id}>__')
            embed.add_field(name='**Results**', value=results_str)
            embed.add_field(name='**Ranking**', value=rank_str)

            guild_member = ctx.guild.get_member(player.discord_id)
            if guild_member:
                embed.set_thumbnail(url=guild_member.avatar_url_as(size=512))

            return embed

        embed = await self.bot.loop.run_in_executor(None, async_create_player_embed)
        await ctx.send(embed=embed)

    @commands.command(aliases=['dbb'])
    @commands.is_owner()
    async def backup_db(self, ctx):
        """*Owner*: Backup PSQL database to a file

        Intended to be used when a change to the ELO math is made to apply to all games retroactively
        """
        import subprocess
        from subprocess import PIPE

        async with ctx.typing():
            await ctx.send('Executing backup script')
            process = subprocess.run(['/home/nelluk/backup_spies_db.sh'], stdout=PIPE, stderr=PIPE)
            if process.returncode == 0:
                logger.info('Backup script executed')
                return await ctx.send(f'Execution successful: {str(process.stdout)}')
            else:
                logger.error('Error during execution')
                return await ctx.send(f'Error during execution: {str(process.stderr)}')

    @commands.is_owner()
    @commands.command()
    async def quit(self, ctx):
        """ *Owner*: Close database connection and quit bot gracefully """

        message = ''
        try:
            if db.close():
                message = 'db connecton closing normally'
            else:
                message = 'db connection was already closed'

        except peewee.PeeweeException as e:
            message = f'Error during post_invoke_cleanup db.close(): {e}'
        finally:
            logger.info(message)

        await ctx.send('Shutting down')
        await self.bot.close()


def setup(bot):
    bot.add_cog(games(bot))
