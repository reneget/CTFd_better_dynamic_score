"""Dynamic scoring which never reprices an existing solve.

This plugin is intentionally implemented without changes to CTFd core. It
stores the awarded value in its own table and replaces CTFd's score queries at
plugin load time.
"""

import datetime

from flask import Blueprint
from sqlalchemy import event, select, union_all

from CTFd.cache import clear_challenges, clear_standings
from CTFd.exceptions.challenges import ChallengeCreateException
from CTFd.models import Awards, Brackets, Challenges, Solves, Teams, Users, db
from CTFd.plugins import register_plugin_assets_directory
from CTFd.plugins.challenges import CHALLENGE_CLASSES, BaseChallenge
from CTFd.plugins.challenges.decay import DECAY_FUNCTIONS
from CTFd.plugins.migrations import upgrade
from CTFd.utils import get_config
from CTFd.utils.dates import isoformat, unix_time_to_utc
from CTFd.utils.modes import get_model


class AwardedValue(db.Model):
    __tablename__ = "fixed_dynamic_awarded_values"
    solve_id = db.Column(
        db.Integer,
        db.ForeignKey("solves.id", ondelete="CASCADE"),
        primary_key=True,
    )
    awarded_value = db.Column(db.Integer, nullable=False)
    created = db.Column(db.DateTime, default=datetime.datetime.utcnow)


class FixedDynamicChallenge(Challenges):
    __mapper_args__ = {"polymorphic_identity": "fixed_dynamic"}
    id = db.Column(
        db.Integer, db.ForeignKey("challenges.id", ondelete="CASCADE"), primary_key=True
    )
    fixed_initial = db.Column(db.Integer, default=0)
    fixed_minimum = db.Column(db.Integer, default=0)
    fixed_decay = db.Column(db.Integer, default=0)

    @property
    def initial(self):
        return self.fixed_initial

    @initial.setter
    def initial(self, value):
        self.fixed_initial = value

    @property
    def minimum(self):
        return self.fixed_minimum

    @minimum.setter
    def minimum(self, value):
        self.fixed_minimum = value

    @property
    def decay(self):
        return self.fixed_decay

    @decay.setter
    def decay(self, value):
        self.fixed_decay = value

    def __init__(self, *args, **kwargs):
        super().__init__(**kwargs)
        if "initial" not in kwargs:
            raise ChallengeCreateException("Missing initial value for challenge")
        self.function = "fixed_dynamic"
        self.value = kwargs["initial"]


class FixedDynamicChallengeType(BaseChallenge):
    id = "fixed_dynamic"
    name = "Fixed Dynamic"
    templates = {
        "create": "/plugins/fixed_dynamic_scoring/assets/create.html",
        "update": "/plugins/fixed_dynamic_scoring/assets/update.html",
        "view": "/plugins/challenges/assets/view.html",
    }
    scripts = {
        "create": "/plugins/challenges/assets/create.js",
        "update": "/plugins/challenges/assets/update.js",
        "view": "/plugins/challenges/assets/view.js",
    }
    route = "/plugins/fixed_dynamic_scoring/assets/"
    blueprint = Blueprint(
        "fixed_dynamic", __name__, template_folder="templates", static_folder="assets"
    )
    challenge_model = FixedDynamicChallenge

    @classmethod
    def read(cls, challenge):
        data = super().read(challenge)
        data.update(
            initial=challenge.initial,
            minimum=challenge.minimum,
            decay=challenge.decay,
            function="fixed_dynamic",
        )
        return data

    @classmethod
    def solve(cls, user, team, challenge, request):
        return super().solve(user, team, challenge, request)


def fixed_dynamic(challenge):
    """Return the value for the next solve using CTFd's linear formula."""
    # CTFd recalculates after inserting the solve; exclude that solve so the
    # first solver keeps the initial value and the next solver sees it.
    solve_count = max(_visible_solve_count(challenge) - 1, 0)
    value = challenge.initial - challenge.decay * solve_count
    return max(int(value), int(challenge.minimum))


def _visible_solve_count(challenge):
    Model = get_model()
    return (
        Solves.query.join(Model, Solves.account_id == Model.id)
        .filter(Solves.challenge_id == challenge.id)
        .filter(Model.hidden == False, Model.banned == False)
        .count()
    )


def _snapshot_after_insert(mapper, connection, solve):
    challenge_type = connection.execute(
        select(Challenges.type).where(Challenges.id == solve.challenge_id)
    ).scalar_one()
    if challenge_type != "fixed_dynamic":
        return
    value = connection.execute(
        select(Challenges.value).where(Challenges.id == solve.challenge_id)
    ).scalar_one()
    connection.execute(
        AwardedValue.__table__.insert().values(
            solve_id=solve.id,
            awarded_value=int(value),
        )
    )


def _remove_snapshot(mapper, connection, solve):
    connection.execute(
        AwardedValue.__table__.delete().where(AwardedValue.solve_id == solve.id)
    )


def _solve_value():
    return db.func.coalesce(AwardedValue.awarded_value, Challenges.value)


def _freeze_filter(query, column, admin):
    freeze = get_config("freeze")
    if not admin and freeze:
        query = query.filter(column < unix_time_to_utc(freeze))
    return query


def _standings(
    account_column,
    account_model,
    account_label,
    count=None,
    bracket_id=None,
    admin=False,
    fields=None,
):
    fields = fields or []
    value = _solve_value()
    scores = (
        db.session.query(
            account_column.label(account_label),
            db.func.sum(value).label("score"),
            db.func.max(Solves.id).label("id"),
            db.func.max(Solves.date).label("date"),
        )
        .join(Challenges)
        .outerjoin(AwardedValue, AwardedValue.solve_id == Solves.id)
        .filter(value != 0)
        .group_by(account_column)
    )
    awards = db.session.query(
        getattr(Awards, account_label).label(account_label),
        db.func.sum(Awards.value).label("score"),
        db.func.max(Awards.id).label("id"),
        db.func.max(Awards.date).label("date"),
    ).filter(Awards.value != 0).group_by(getattr(Awards, account_label))
    scores = _freeze_filter(scores, Solves.date, admin)
    awards = _freeze_filter(awards, Awards.date, admin)
    results = union_all(scores, awards).alias("results")
    sumscores = (
        db.session.query(
            getattr(results.c, account_label),
            db.func.sum(results.c.score).label("score"),
            db.func.max(results.c.id).label("id"),
            db.func.max(results.c.date).label("date"),
        )
        .group_by(getattr(results.c, account_label))
        .subquery()
    )
    query = (
        db.session.query(
            account_model.id.label(account_label),
            account_model.oauth_id.label("oauth_id"),
            account_model.name.label("name"),
            account_model.bracket_id.label("bracket_id"),
            Brackets.name.label("bracket_name"),
            sumscores.c.score,
            *fields,
        )
        .join(sumscores, account_model.id == getattr(sumscores.c, account_label))
        .join(Brackets, isouter=True)
        .order_by(sumscores.c.score.desc(), sumscores.c.date.asc(), sumscores.c.id.asc())
    )
    if not admin:
        query = query.filter(account_model.banned == False, account_model.hidden == False)
    if bracket_id is not None:
        query = query.filter(account_model.bracket_id == bracket_id)
    return query.limit(count).all() if count is not None else query.all()


def get_standings(count=None, bracket_id=None, admin=False, fields=None):
    return _standings(Solves.account_id, get_model(), "account_id", count, bracket_id, admin, fields)


def get_user_standings(count=None, bracket_id=None, admin=False, fields=None):
    return _standings(Solves.user_id, Users, "user_id", count, bracket_id, admin, fields)


def get_team_standings(count=None, bracket_id=None, admin=False, fields=None):
    return _standings(Solves.team_id, Teams, "team_id", count, bracket_id, admin, fields)


def get_scoreboard_detail(count, bracket_id=None):
    from collections import defaultdict
    from CTFd.utils.modes import generate_account_url

    standings = get_standings(count=count, bracket_id=bracket_id)
    account_ids = [row.account_id for row in standings]
    solves = Solves.query.filter(Solves.account_id.in_(account_ids))
    awards = Awards.query.filter(Awards.account_id.in_(account_ids))
    freeze = get_config("freeze")
    if freeze:
        solves = solves.filter(Solves.date < unix_time_to_utc(freeze))
        awards = awards.filter(Awards.date < unix_time_to_utc(freeze))
    solves = solves.all()
    awards = awards.all()
    mapper = defaultdict(list)
    for solve in solves:
        awarded = AwardedValue.query.filter_by(solve_id=solve.id).first()
        mapper[solve.account_id].append({
            "challenge_id": solve.challenge_id,
            "account_id": solve.account_id,
            "team_id": solve.team_id,
            "user_id": solve.user_id,
            "value": awarded.awarded_value if awarded else solve.challenge.value,
            "date": isoformat(solve.date),
        })
    for award in awards:
        mapper[award.account_id].append({
            "challenge_id": None, "account_id": award.account_id,
            "team_id": award.team_id, "user_id": award.user_id,
            "value": award.value, "date": isoformat(award.date),
        })
    response = {}
    for index, row in enumerate(standings):
        response[index + 1] = {
            "id": row.account_id,
            "account_url": generate_account_url(account_id=row.account_id),
            "name": row.name,
            "score": int(row.score),
            "bracket_id": row.bracket_id,
            "bracket_name": row.bracket_name,
            "solves": sorted(mapper.get(row.account_id, []), key=lambda item: item["date"]),
        }
    return response


def _user_score(self, admin=False):
    value = _solve_value()
    query = db.session.query(db.func.sum(value)).join(Challenges).outerjoin(
        AwardedValue, AwardedValue.solve_id == Solves.id
    ).filter(Solves.user_id == self.id)
    query = _freeze_filter(query, Solves.date, admin)
    solve_score = query.scalar() or 0
    award = db.session.query(db.func.sum(Awards.value)).filter_by(user_id=self.id)
    award = _freeze_filter(award, Awards.date, admin).scalar() or 0
    return int(solve_score) + int(award)


def load(app):
    # Import the model before SQLite's create_all shortcut in plugin migrations.
    upgrade(plugin_name="fixed_dynamic_scoring")
    DECAY_FUNCTIONS["fixed_dynamic"] = fixed_dynamic
    CHALLENGE_CLASSES["fixed_dynamic"] = FixedDynamicChallengeType
    if not getattr(Solves, "_fixed_dynamic_listeners_registered", False):
        event.listen(Solves, "after_insert", _snapshot_after_insert)
        event.listen(Solves, "after_delete", _remove_snapshot)
        Solves._fixed_dynamic_listeners_registered = True

    Users.get_score = _user_score
    Teams.get_score = lambda self, admin=False: sum(
        member.get_score(admin=admin) for member in self.members
    )

    import CTFd.utils.scores as scores
    import CTFd.utils.scoreboard as scoreboard
    scores.get_standings = get_standings
    scores.get_user_standings = get_user_standings
    scores.get_team_standings = get_team_standings
    scoreboard.get_standings = get_standings
    scoreboard.get_scoreboard_detail = get_scoreboard_detail

    # These modules imported the functions before plugins are initialized.
    import CTFd.api.v1.scoreboard as api_scoreboard
    import CTFd.admin.scoreboard as admin_scoreboard
    import CTFd.scoreboard as public_scoreboard
    import CTFd.utils.csv as csv_utils
    import CTFd.api.v1.statistics.scores as statistics_scores
    import CTFd.api.v1.statistics.progression as statistics_progression
    api_scoreboard.get_standings = get_standings
    api_scoreboard.get_user_standings = get_user_standings
    api_scoreboard.get_scoreboard_detail = get_scoreboard_detail
    admin_scoreboard.get_standings = get_standings
    admin_scoreboard.get_user_standings = get_user_standings
    public_scoreboard.get_standings = get_standings
    csv_utils.get_standings = get_standings
    statistics_scores.get_standings = get_standings
    statistics_progression.get_standings = get_standings
    clear_standings()
    clear_challenges()
    register_plugin_assets_directory(app, base_path="/plugins/fixed_dynamic_scoring/assets/")
