# import modules.exceptions as exceptions
import logging
import datetime
from discord.ext import commands
# import discord
import configparser
logger = logging.getLogger('spiesbot.' + __name__)

config = configparser.ConfigParser()
config.read('config.ini')

try:
    discord_key = config['DEFAULT']['discord_key']
    psql_user = config['DEFAULT']['psql_user']
    psql_db = config['DEFAULT']['psql_db']
except KeyError:
    logger.error('Error finding a required setting (discord_key / psql_user / psql_db) in config.ini file')
    exit(0)

pastebin_key = config['DEFAULT'].get('pastebin_key', None)

server_ids = {'test': 478571892832206869}
owner_id = 272510639124250625  # Nelluk
bot = None
run_tasks = True  # if set as False via command line option, tasks should check this and skip

# bot invite URL https://discordapp.com/oauth2/authorize?client_id=703986191254683728&scope=bot


config = {'default':
                     {'helper_roles': ['Staff'],
                      'mod_roles': ['Mod', 'Owner'],
                      'user_roles_level_4': [],  # power user/can do some fancy matchmaking things
                      'user_roles_level_3': ['@everyone'],  # full user, host/join anything
                      'user_roles_level_2': ['@everyone'],  # normal user, can't host all match sizes
                      'user_roles_level_1': ['@everyone'],  # restricted user/newbie
                      'display_name': 'Two Spies',
                      'command_prefix': '/',
                      'bot_channels_private': [],  # channels here will pass any bot channel check, and not linked in bot messages
                      'bot_channels_strict': [],  # channels where the most limited commands work, like leaderboards
                      'bot_channels': [],  # channels were more common commands work, like matchmaking
                      'game_announce_channel': None},
            478571892832206869:  # Test server
                    {},
            656764377772457987:  # Two Spies server
                    {'bot_channels_strict': [704748719098167467]},
          }


date_cutoff = datetime.datetime.today() - datetime.timedelta(days=90)  # Players who haven't played since cutoff are not included in leaderboards


def get_setting(setting_name):
    return config['default'][setting_name]


def guild_setting(guild_id: int, setting_name: str):
    # if guild_id = None, default block will be used

    if guild_id:

        try:
            settings_obj = config[guild_id]
        except KeyError:
            logger.error(f'Unauthorized guild id {guild_id}.')
            # raise exceptions.CheckFailedError('Unauthorized: This guild is not in the config.ini file.')

        try:
            return settings_obj[setting_name]
        except KeyError:
            return config['default'][setting_name]

    else:
        return config['default'][setting_name]


def get_matching_roles(discord_member, list_of_role_names):
        # Given a Discord.Member and a ['List of', 'Role names'], return set of role names that the Member has.polytopia_id
        member_roles = [x.name for x in discord_member.roles]
        return set(member_roles).intersection(list_of_role_names)


def get_user_level(ctx, user=None):
    user = ctx.author if not user else user

    if user.id == owner_id:
        return 7
    if is_mod(ctx, user=user):
        return 6
    if is_staff(ctx, user=user):
        return 5
    if get_matching_roles(user, guild_setting(ctx.guild.id, 'user_roles_level_4')):
        return 4  # advanced matchmaking abilities (leave own match, join others to match). can use settribes in bulk
    if get_matching_roles(user, guild_setting(ctx.guild.id, 'user_roles_level_3')):
        return 3  # host/join any
    if get_matching_roles(user, guild_setting(ctx.guild.id, 'user_roles_level_2')):
        return 2  # join ranked games up to 6p, unranked up to 12p
    if get_matching_roles(user, guild_setting(ctx.guild.id, 'user_roles_level_1')):
        return 1  # join ranked games up to 3p, unranked up to 6p. no hosting
    return 0


def is_staff(ctx, user=None):
    user = ctx.author if not user else user

    if user.id == owner_id:
        return True
    helper_roles = guild_setting(ctx.guild.id, 'helper_roles')
    mod_roles = guild_setting(ctx.guild.id, 'mod_roles')

    target_match = get_matching_roles(user, helper_roles + mod_roles)
    return len(target_match) > 0


def is_mod(ctx, user=None):
    user = ctx.author if not user else user

    if ctx.author.id == owner_id:
        return True
    mod_roles = guild_setting(ctx.guild.id, 'mod_roles')

    target_match = get_matching_roles(user, mod_roles)
    return len(target_match) > 0


def is_staff_check():
    # restrict commands to is_staff with syntax like @settings.is_staff_check()

    def predicate(ctx):
        return is_staff(ctx)
    return commands.check(predicate)


def is_mod_check():
    # restrict commands to is_staff with syntax like @settings.is_mod_check()

    def predicate(ctx):
        return is_mod(ctx)
    return commands.check(predicate)


def in_bot_channel():
    async def predicate(ctx):
        if guild_setting(ctx.guild.id, 'bot_channels') is None:
            return True
        if is_mod(ctx):
            return True
        if ctx.message.channel.id in guild_setting(ctx.guild.id, 'bot_channels') + guild_setting(ctx.guild.id, 'bot_channels_private'):
            return True
        else:
            if ctx.invoked_with == 'help' and ctx.command.name != 'help':
                # Silently fail check when help cycles through every bot command for a check.
                pass
            else:
                channel_tags = [f'<#{chan_id}>' for chan_id in guild_setting(ctx.guild.id, 'bot_channels')]
                await ctx.send(f'This command can only be used in a designated ELO bot channel. Try: {" ".join(channel_tags)}')
            return False
    return commands.check(predicate)


def in_bot_channel_strict():
    async def predicate(ctx):
        if guild_setting(ctx.guild.id, 'bot_channels_strict') is None:
            if guild_setting(ctx.guild.id, 'bot_channels') is None:
                return True
            else:
                chan_list = guild_setting(ctx.guild.id, 'bot_channels')
        else:
            chan_list = guild_setting(ctx.guild.id, 'bot_channels_strict')
        if is_mod(ctx):
            return True
        if ctx.message.channel.id in chan_list + guild_setting(ctx.guild.id, 'bot_channels_private'):
            return True
        else:
            if ctx.invoked_with == 'help' and ctx.command.name != 'help':
                # Silently fail check when help cycles through every bot command for a check.
                pass
            else:
                # primary_bot_channel = chan_list[0]
                channel_tags = [f'<#{chan_id}>' for chan_id in chan_list]
                await ctx.send(f'This command can only be used in a designated bot spam channel. Try: {" ".join(channel_tags)}')
            return False
    return commands.check(predicate)
