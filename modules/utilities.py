# import discord
from discord.ext import commands
import logging
# import settings
import modules.models as models

logger = logging.getLogger('spiesbot.' + __name__)


def connect():
    if models.db.connect(reuse_if_open=True):
        logger.debug('new db connection opened')
        return True
    else:
        logger.debug('reusing db connection')
        return False


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
