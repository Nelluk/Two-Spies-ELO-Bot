import discord
from discord.ext import commands
import logging
import asyncio
import re
import modules.models as models

logger = logging.getLogger('spiesbot.' + __name__)


def connect():
    if models.db.connect(reuse_if_open=True):
        logger.debug('new db connection opened')
        return True
    else:
        logger.debug('reusing db connection')
        return False


def escape_role_mentions(input: str):
    # like escape_mentions but allow user mentions. disallows everyone/here/role

    return re.sub(r'@(everyone|here|&[0-9]{17,21})', '@\u200b\\1', str(input))


def escape_everyone_here_roles(input: str):
    # escapes @everyone and @here

    return re.sub(r'@(everyone|here)', '@\u200b\\1', str(input))


async def paginate(bot, ctx, title, message_list, page_start=0, page_end=10, page_size=10):
    # Allows user to page through a long list of messages with reactions
    # message_list should be a [(List of, two-item tuples)]. Each tuple will be split into an embed field name/value

    page_end = page_end if len(message_list) > page_end else len(message_list)

    first_loop = True
    while True:
        embed = discord.Embed(title=title)
        for entry in range(page_start, page_end):
            embed.add_field(name=message_list[entry][0], value=message_list[entry][1], inline=False)
        if page_size < len(message_list):
            embed.set_footer(text=f'{page_start + 1} - {page_end} of {len(message_list)}')

        if first_loop is True:
            sent_message = await ctx.send(embed=embed)
        else:
            try:
                await sent_message.clear_reactions()
            except (discord.ext.commands.errors.CommandInvokeError, discord.errors.Forbidden):
                logger.warn('Unable to clear message reaction due to insufficient permissions. Giving bot \'Manage Messages\' permission will improve usability.')
            await sent_message.edit(embed=embed)

        if page_start > 0:
            await sent_message.add_reaction('⏪')
            await sent_message.add_reaction('⬅')
        if page_end < len(message_list):
            await sent_message.add_reaction('➡')
            await sent_message.add_reaction('⏩')

        def check(reaction, user):
            e = str(reaction.emoji)

            if page_size < len(message_list):
                compare = e.startswith(('⏪', '⏩', '➡', '⬅'))
            else:
                compare = False
            return ((user == ctx.message.author) and (reaction.message.id == sent_message.id) and compare)

        try:
            reaction, user = await bot.wait_for('reaction_add', timeout=45.0, check=check)
        except asyncio.TimeoutError:
            try:
                await sent_message.clear_reactions()
            except (discord.ext.commands.errors.CommandInvokeError, discord.errors.Forbidden):
                logger.warn('Unable to clear message reaction due to insufficient permissions. Giving bot \'Manage Messages\' permission will improve usability.')
            finally:
                break
        else:

            if '⏪' in str(reaction.emoji):
                # all the way to beginning
                page_start = 0
                page_end = page_start + page_size

            if '⏩' in str(reaction.emoji):
                # last page
                page_end = len(message_list)
                page_start = page_end - page_size

            if '➡' in str(reaction.emoji):
                # next page
                page_start = page_start + page_size
                page_end = page_start + page_size

            if '⬅' in str(reaction.emoji):
                # previous page
                page_start = page_start - page_size
                page_end = page_start + page_size

            if page_start < 0:
                page_start = 0
                page_end = page_start + page_size

            if page_end > len(message_list):
                page_end = len(message_list)
                page_start = page_end - page_size if (page_end - page_size) >= 0 else 0

            first_loop = False


async def get_guild_member(ctx, input):

        # Find matching Guild member by @Mention or Name. Fall back to case-insensitive search
        # TODO: support Username#Discriminator (ie an @mention without the @)
        # TODO: use exceptions.NoSingleMatch etc like Player.get_or_except()

        guild_matches, substring_matches = [], []
        try:
            guild_matches.append(await commands.MemberConverter().convert(ctx, input))
        except commands.errors.BadArgument:
            pass
            # No matches in standard MemberConverter. Move on to a case-insensitive search.
            input = input.strip('@')  # Attempt to handle fake @Mentions that sometimes slip through
            for p in ctx.guild.members:
                name_str = p.nick.upper() + p.name.upper() if p.nick else p.name.upper()
                if p.name.upper() == input.upper():
                    guild_matches.append(p)
                elif input.upper() in name_str:
                    substring_matches.append(p)

            return guild_matches + substring_matches
            # if len(guild_matches) > 0:
            #     return guild_matches
            # if len(input) > 2:
            #     return substring_matches

        return guild_matches
