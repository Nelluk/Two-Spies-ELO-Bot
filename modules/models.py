import datetime
# import discord
import re
# import psycopg2
from peewee import *
from playhouse.postgres_ext import *
# import modules.exceptions as exceptions
import settings
import logging

logger = logging.getLogger('spybot.' + __name__)
elo_logger = logging.getLogger('spybot.elo')

db = PostgresqlDatabase(settings.psql_db, autorollback=True, user=settings.psql_user, autoconnect=False)


def tomorrow():
    return (datetime.datetime.now() + datetime.timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")


class BaseModel(Model):
    class Meta:
        database = db


class Player(BaseModel):
    discord_id = BitField(unique=True, null=False)
    name = TextField(unique=False)
    elo = SmallIntegerField(default=1000)
    elo_max = SmallIntegerField(default=1000)
    is_banned = BooleanField(default=False)


class Game(BaseModel):
    name = TextField(null=True)
    is_confirmed = BooleanField(default=False)
    win_claimed_ts = DateTimeField(default=datetime.datetime.now)  # set when game is claimed/entered
    completed_ts = DateTimeField(null=True, default=None)  # set when game is confirmed and ELO is calculated
    name = TextField(null=True)
    losing_player = ForeignKeyField(Player, null=False, backref='losing_player', on_delete='RESTRICT')
    winning_player = ForeignKeyField(Player, null=False, backref='winning_player', on_delete='RESTRICT')
    elo_change_winner = SmallIntegerField(default=0)
    elo_change_loser = SmallIntegerField(default=0)

    def __setattr__(self, name, value):
        if name == 'name':
            value = value.strip('\"').strip('\'').strip('”').strip('“').title()[:35].strip() if value else value
        return super().__setattr__(name, value)


with db:
    db.create_tables([Player, Game])
