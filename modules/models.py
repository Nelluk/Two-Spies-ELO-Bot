import datetime
# import discord
# import re
# import psycopg2
from peewee import *
from playhouse.postgres_ext import *
# import modules.exceptions as exceptions
import settings
import logging

logger = logging.getLogger('spiesbot.' + __name__)
elo_logger = logging.getLogger('spiesbot.elo')

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

    def string_matches(player_string: str):
        # Returns QuerySet containing players in current guild matching string. Searches against discord mention ID first, then exact discord name match,
        # then falls back to substring match on name/nick

        try:
            p_id = int(player_string.strip('<>!@'))
        except ValueError:
            pass
        else:
            # lookup either on <@####> mention string or raw ID #
            query_by_id = Player.select().where(
                (Player.discord_id == p_id)
            )
            if query_by_id.count() > 0:
                return query_by_id

        if len(player_string.split('#', 1)[0]) > 2:
            discord_str = player_string.split('#', 1)[0]
            # If query is something like 'Nelluk#7034', use just the 'Nelluk' to match against discord_name.
            # This happens if user does an @Mention then removes the @ character
        else:
            discord_str = player_string

        name_exact_match = Player.select(Player).where(
            (Player.name ** discord_str)  # ** is case-insensitive
        )

        if name_exact_match.count() == 1:
            # String matches DiscordUser.name exactly
            return name_exact_match

        name_substring_match = PlayerGame.select(PlayerGame.player, fn.COUNT('*').alias('games_played')).join(Player).where(
            (Player.name.contains(player_string))
        ).group_by(PlayerGame.player).order_by(-SQL('games_played'))

        if name_substring_match.count() > 0:
            return [l.player for l in name_substring_match]

        return []


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

    def refresh(self):
        # refresh object in memory with fresh copy from database

        return type(self).get(self._pk_expr())

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

    def confirm(self, bypass_check=False):
        # Calculate elo changes for a newly-confirmed game and write new values to database

        if self.is_confirmed and not bypass_check:
            # checks to make sure we aren't confirming an already-confirmed game.
            # if bypass_check=True, confirming will be allowed to continue even if is_confirmed is set.
            # This is probably only used in recalculate_all_elo
            raise ValueError('Cannot confirm game - is_confirmed is already marked as True')

        logger.debug(f'Confirming game {self.id}')
        elo_logger.debug(f'Confirm game {self.id}')

        winner_delta = self.calc_elo_delta(for_winner=True)
        loser_delta = self.calc_elo_delta(for_winner=False)

        with db.atomic() as transaction:
            elo_logger.debug(f'Winning player {self.winning_player.name} going from {self.winning_player.elo} to {int(self.winning_player.elo + winner_delta)}')
            self.winning_player.elo = int(self.winning_player.elo + winner_delta)
            if self.winning_player.elo > self.winning_player.elo_max:
                self.winning_player.elo_max = self.winning_player.elo
            self.elo_change_winner = winner_delta

            elo_logger.debug(f'Losing player {self.losing_player.name} going from {self.losing_player.elo} to {int(self.losing_player.elo + loser_delta)}')
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

            update_count = self.save()
            if not update_count:
                # Could happen if game is deleted while Game object is still in memory and then a confirm is attempted, usually if a user deletes a game during the auto-confirm time
                transaction.rollback()
                raise Game.DoesNotExist('Game can not be found. No ELO changes saved.')

            return self.winning_player.elo, self.losing_player.elo

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

    def recalculate_all_elo():
        # Reset all ELOs to 1000,  and re-run Game.declare_winner() on all qualifying games

        logger.warn('Resetting and recalculating all ELO')
        elo_logger.info(f'recalculate_all_elo')

        with db.atomic():
            Player.update(elo=1000, elo_max=1000).execute()

            games = Game.select().where(
                (Game.is_confirmed == 1)
            ).order_by(Game.completed_ts)

            for game in games:
                game.confirm(bypass_check=True)

        elo_logger.info(f'recalculate_all_elo complete')


class PlayerGame(BaseModel):
    player = ForeignKeyField(Player, null=False, backref='playergame', on_delete='RESTRICT')
    game = ForeignKeyField(Game, null=False, backref='playergame', on_delete='CASCADE')
    elo_after_game = SmallIntegerField(default=None, null=True)  # snapshot of what elo was after game concluded


with db:
    db.create_tables([Player, Game, PlayerGame])
