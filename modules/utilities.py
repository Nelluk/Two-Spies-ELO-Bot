# import discord
# from discord.ext import commands
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
