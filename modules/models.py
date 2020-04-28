import datetime
# import discord
# import re
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

    def leaderboard_rank(self, date_cutoff):
        # Returns player's position in the leaderboard, and total size of leaderboard

        # TODO: This could be replaced with Postgresql Window functions to have the DB calculate the rank.
        # Advantages: Probably moderately more efficient, and will resolve ties in a sensible way
        # But no idea how to write the query :/
        # http://docs.peewee-orm.com/en/latest/peewee/query_examples.html#find-the-top-three-revenue-generating-facilities

        query = Player.leaderboard(date_cutoff=date_cutoff)

        player_found = False
        for counter, p in enumerate(query.tuples()):
            if p[0] == self.id:
                player_found = True
                break

        rank = counter + 1 if player_found else None
        return (rank, query.count())

    def wins(self):

        q = Game.select().where(
            (Game.is_confirmed == 1) & (Game.winning_player == self)
        )

        return q

    def losses(self):
        q = Game.select().where(
            (Game.is_confirmed == 1) & (Game.losing_player == self)
        )

        return q

    def get_record(self):

        return (self.wins().count(), self.losses().count())

    def leaderboard(date_cutoff, max_flag: bool = False):

        if max_flag:
            elo_field = Player.elo_max
        else:
            elo_field = Player.elo

        query = Player.select().join(PlayerGame).join(Game).where(
            (Game.is_confirmed == 1) & (Game.completed_ts > date_cutoff) & (Player.is_banned == 0)
        ).distinct().order_by(-elo_field)

        if query.count() < 10:
            # Include all registered players on leaderboard if not many games played
            query = Player.select().order_by(-elo_field)

        return query


class Game(BaseModel):
    name = TextField(null=True)
    is_confirmed = BooleanField(default=False)
    win_claimed_ts = DateTimeField(default=datetime.datetime.now)  # set when game is claimed/entered
    completed_ts = DateTimeField(null=True, default=None)  # set when game is confirmed and ELO is calculated
    name = TextField(null=True)
    losing_score = SmallIntegerField(default=1, null=True)  # Score of losing player, so assumed to be 0, 1, or 2. Assumed that basically all ranked games are first to 3 (3-0, 3-1, 3-2)
    losing_player = ForeignKeyField(Player, null=False, backref='losing_player', on_delete='RESTRICT')
    winning_player = ForeignKeyField(Player, null=False, backref='winning_player', on_delete='RESTRICT')
    elo_change_winner = SmallIntegerField(default=0)
    elo_change_loser = SmallIntegerField(default=0)

    def __setattr__(self, name, value):
        if name == 'name':
            value = value.strip('\"').strip('\'').strip('”').strip('“').title()[:35].strip() if value else value
        return super().__setattr__(name, value)

    def get_or_create_pending_game(winning_player, losing_player, name=None, losing_score=None):
        game, created = Game.get_or_create(winning_player=winning_player, losing_player=losing_player, is_confirmed=False, defaults={'name': name, 'losing_score': losing_score})
        if created and losing_score is None:
            # Attempted to input a game with no losing score - not allowed
            game.delete_instance()
            return None, False
        if created:
            PlayerGame.create(player=winning_player, game=game)
            PlayerGame.create(player=losing_player, game=game)
        return game, created

    def confirm(self):
        # Calculate elo changes for a newly-confirmed game and write new values to database
        winner_delta = self.calc_elo_delta(for_winner=True)
        loser_delta = self.calc_elo_delta(for_winner=False)

        with db.atomic():
            self.winning_player.elo = int(self.winning_player.elo + winner_delta)
            if self.winning_player.elo > self.winning_player.elo_max:
                self.winning_player.elo_max = self.winning_player.elo
            self.elo_change_winner = winner_delta

            self.losing_player.elo = int(self.losing_player.elo + loser_delta)
            self.elo_change_loser = loser_delta

            self.winning_player.save()
            self.losing_player.save()
            self.is_confirmed = True
            self.completed_ts = datetime.datetime.now()

            for playergame in self.playergame:
                if playergame.player == self.winning_player:
                    playergame.elo_after_game = self.winning_player.elo
                else:
                    playergame.elo_after_game = self.losing_player.elo
                playergame.save()

            self.save()

    def calc_elo_delta(self, for_winner=True):
        max_elo_delta = 32  # elo 'k' value

        def chance_of_winning(target_elo, opponent_elo):
            # Calculate the expected chance of winning based on one player's elo compared to their opponent's elo.
            return round(1 / (1 + (10 ** ((opponent_elo - target_elo) / 400.0))), 3)

        # Calculate a base change of elo based on your chance of winning and whether or not you won
        if for_winner is True:
            elo = self.winning_player.elo
            elo_delta = int(round((max_elo_delta * (1 - chance_of_winning(target_elo=elo, opponent_elo=self.losing_player.elo))), 0))
        else:
            elo = self.losing_player.elo
            elo_delta = int(round((max_elo_delta * (0 - chance_of_winning(target_elo=elo, opponent_elo=self.winning_player.elo))), 0))

        elo_boost = .60 * ((1200 - max(min(elo, 1200), 900)) / 300)  # 60% boost to delta at elo 900, gradually shifts to 0% boost at 1200 ELO

        elo_bonus = int(abs(elo_delta) * elo_boost)
        elo_delta += elo_bonus

        if self.losing_score == 0:
            elo_delta = int(round(elo_delta * 1.15))  # larger delta for a 3-0 blowout
        elif self.losing_score == 2:
            elo_delta = int(round(elo_delta * 0.85))  # smaller delta for a 3-2 close game

        return elo_delta

    def reverse_confirmation(self):
        # revert elo changes and return game to unconfirmed state
        self.winning_player.elo += self.elo_change_winner * -1
        self.winning_player.save()
        self.elo_change_winner = 0

        self.losing_player.elo += self.elo_change_loser * -1
        self.losing_player.save()
        self.elo_change_loser = 0

        for playergame in self.playergame:
            playergame.elo_after_game = None
            playergame.save()

        self.is_confirmed = False
        self.completed_ts = None

        self.save()

    def delete_game(self):
        # resets any relevant ELO changes to players and teams, deletes related lineup records, and deletes the game entry itself

        logger.info(f'Deleting game {self.id}')
        recalculate = False
        with db.atomic():
            if self.is_confirmed:
                self.is_confirmed = False
                recalculate = True
                since = self.completed_ts

                self.reverse_confirmation()

            for playergame in self.playergame:
                playergame.delete_instance()

            self.delete_instance()

            if recalculate:
                Game.recalculate_elo_since(timestamp=since)

    def recalculate_elo_since(timestamp):
        games = Game.select().where(
            (Game.is_confirmed == 1) & (Game.completed_ts >= timestamp)
        ).order_by(Game.completed_ts)

        elo_logger.debug(f'recalculate_elo_since {timestamp}')
        for g in games:
            g.reverse_confirmation()

        for g in games:
            g.confirm()
        elo_logger.debug(f'recalculate_elo_since complete')


class PlayerGame(BaseModel):
    player = ForeignKeyField(Player, null=False, backref='playergame', on_delete='RESTRICT')
    game = ForeignKeyField(Game, null=False, backref='playergame', on_delete='CASCADE')
    elo_after_game = SmallIntegerField(default=None, null=True)  # snapshot of what elo was after game concluded


with db:
    db.create_tables([Player, Game, PlayerGame])
