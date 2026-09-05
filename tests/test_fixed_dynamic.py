from CTFd.models import Solves
from CTFd.plugins.fixed_dynamic_scoring import AwardedValue, get_user_standings


def test_snapshot_is_not_repriced(app, db, user, challenge):
    challenge.function = "fixed_dynamic"
    challenge.initial = 100
    challenge.minimum = 50
    challenge.decay = 10
    challenge.value = 100
    db.session.commit()

    first = Solves(user_id=user.id, challenge_id=challenge.id)
    db.session.add(first)
    db.session.commit()
    snapshot = AwardedValue.query.filter_by(solve_id=first.id).one()

    challenge.value = 90
    db.session.commit()
    assert snapshot.awarded_value == 100


def test_fixed_dynamic_score_uses_snapshot(app, db, user, challenge):
    challenge.function = "fixed_dynamic"
    challenge.value = 100
    db.session.commit()
    solve = Solves(user_id=user.id, challenge_id=challenge.id)
    db.session.add(solve)
    db.session.commit()
    challenge.value = 50
    db.session.commit()

    standings = get_user_standings(admin=True)
    row = next(row for row in standings if row.user_id == user.id)
    assert row.score == 100
