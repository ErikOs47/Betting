from datetime import date, timedelta

import numpy as np
import pytest

from app.betting import suggestions as sg
from app.db import Game, PlayerGameStat, Prediction, Suggestion, utcnow
from app.ml import predict as predict_mod
from app.ml.registry import train_and_register
from tests.synth import populate


def test_fit_k_detects_overconfidence():
    rng = np.random.default_rng(0)
    p = rng.uniform(0.57, 0.70, 20000)
    true = 1 / (1 + np.exp(-0.5 * np.log(p / (1 - p))))  # reality is half as extreme as claimed
    y = (rng.uniform(size=len(p)) < true).astype(float)
    assert sg.fit_k(p, y) == pytest.approx(0.5, abs=0.08)
    assert sg.fit_k(p, (rng.uniform(size=len(p)) < p).astype(float)) == pytest.approx(1.0, abs=0.1)
    assert sg.adjust(0.65, 0.5) < 0.65 and sg.adjust(0.65, 1.0) == pytest.approx(0.65)


def test_record_settle_and_learning(db, monkeypatch):
    populate(db, n_days=70, scheduled_days=1)
    monkeypatch.setattr(predict_mod, "today_et", lambda: date(2024, 12, 31))
    for g in db.query(Game).filter(Game.status != "final"):
        g.tip_utc = utcnow() + timedelta(hours=5)
    db.commit()
    train_and_register(db)
    predict_mod.predict_upcoming(db)
    db.query(Prediction).update({"data_quality": 0.9, "enabled": True, "quality_flags": []})
    db.flush()
    monkeypatch.setattr(sg, "MIN_P", 0.52)  # the synthetic league has few strong deviations
    r = sg.record(db)
    assert r["recorded"] > 4 and r["props"] >= 1
    first = db.query(Suggestion).count()
    assert sg.record(db)["recorded"] == r["recorded"] and db.query(Suggestion).count() == first  # replaced, not duplicated
    s = db.query(Suggestion).filter_by(kind="prop").first()
    assert s.line % 1 == 0.5 and 0.5 < s.prob <= 0.70 and s.status == "open"
    assert db.query(Suggestion).filter_by(kind="prop", player_id=s.player_id, game_id=s.game_id).count() == 1  # one per player
    # play the games: the suggested player lands on the winning side, another prop's player does not play
    g = db.get(Game, s.game_id)
    for gg in db.query(Game).filter(Game.status != "final"):
        gg.status, gg.home_pts, gg.away_pts, gg.tip_utc = "final", 110, 100, utcnow() - timedelta(hours=3)
    val = int(s.line + 3) if s.selection == "over" else max(0, int(s.line - 3))
    stat = {"pts": 0, "trb": 0, "ast": 0, "fg3": 0}
    stat[{"points": "pts", "rebounds": "trb", "assists": "ast", "threes": "fg3", "pra": "pts"}[s.market]] = val
    db.add(PlayerGameStat(game_id=g.id, player_id=s.player_id, team_id=s.team_id, opp_id="XXX", starter=True, played=True, seconds=1800, **stat))
    db.flush()
    assert sg.settle(db)["settled"] == first
    assert s.status == "won" and s.actual == val
    others = db.query(Suggestion).filter(Suggestion.kind == "prop", Suggestion.id != s.id).all()
    assert all(o.status == "void" for o in others)  # no box score row -> did not play -> void
    ml = db.query(Suggestion).filter_by(kind="moneyline").all()
    assert {m.status for m in ml} <= {"won", "lost"} and all((m.status == "won") == (m.selection == "home") for m in ml)
    st = sg.stats(db)
    assert st["props"] == {"n": 1, "hits": 1, "hit_rate": 1.0, "expected": pytest.approx(s.prob, abs=1e-4)}
    assert st["learning"][s.market]["n_live"] == 1 and st["recent"][0]["status"] == "won"
    assert sg.record(db)["recorded"] == 0  # nothing upcoming; settled suggestions stay untouched
    assert db.query(Suggestion).count() == first


def test_prior_caps_backtest_weight_and_live_results_move_k(db):
    # backtest says: claimed 60-61 %, real 55 % -> k < 1
    hist = {"10": [4000, 2200]}
    sg.store_prior(db, {"points": {"n": 4000, "hits": 2200, "expected": 0.605, "hit_rate": 0.55, "k": 0.47, "hist": hist}}, {"start": "a", "end": "b"})
    k0 = sg.learned_k(db)["points"]
    assert k0["k"] == pytest.approx(0.47, abs=0.06) and k0["n_backtest"] == 4000 and k0["n_live"] == 0
    for i in range(600):  # live suggestions hit exactly as often as claimed -> k moves towards 1
        db.add(Suggestion(kind="prop", game_id=f"g{i}", player_id="p", market="points", selection="over", line=20.5, raw_prob=0.605,
                          prob=0.6, status="won" if i % 1000 < 363 or i >= 600 else "lost"))
    db.flush()
    k1 = sg.learned_k(db)["points"]
    assert k1["n_live"] == 600 and k0["k"] < k1["k"] < 1.05
