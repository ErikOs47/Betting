"""End-to-end on the synthetic test league: train -> predict -> walk-forward backtest -> API."""
from datetime import date

import pytest
from fastapi.testclient import TestClient

from app import db as dbm
from app.backtest.walkforward import run_backtest
from app.betting.parlay import load_correlations
from app.db import BacktestRun, ModelVersion, Prediction
from app.ml import predict as predict_mod
from app.ml.registry import train_and_register
from tests.synth import populate


@pytest.fixture()
def trained(db, monkeypatch):
    populate(db, n_days=70, scheduled_days=1)
    monkeypatch.setattr(predict_mod, "today_et", lambda: date(2024, 12, 31))
    db.commit()
    train_and_register(db)
    db.commit()
    return db


def test_train_predict_is_reproducible(trained):
    db = trained
    assert db.query(ModelVersion).filter_by(active=True).count() == 4  # player+game x lightgbm+xgboost
    r1 = predict_mod.predict_upcoming(db)
    assert r1["games"] == 4 and r1["player_predictions"] > 100 and r1["game_predictions"] == 8
    first = {(p.game_id, p.market, p.player_id): (p.mean, p.inputs_hash) for p in db.query(Prediction).filter_by(is_latest=True)}
    predict_mod.predict_upcoming(db)
    second = {(p.game_id, p.market, p.player_id): (p.mean, p.inputs_hash) for p in db.query(Prediction).filter_by(is_latest=True)}
    assert first == second  # same data + same model version -> identical stored predictions
    assert db.query(Prediction).filter_by(is_latest=False).count() == len(first)  # history is kept, not overwritten
    p = db.query(Prediction).filter_by(is_latest=True, market="points").first()
    assert p.dist["family"] == "negbin" and p.dist["alt"]["algo"] == "xgboost" and "p_pts_l10" in p.features
    assert "injury_report_stale" in p.quality_flags  # no injury report loaded -> flagged and quality reduced


def test_stale_results_disable_predictions(trained, monkeypatch):
    monkeypatch.setattr(predict_mod, "today_et", lambda: date(2025, 1, 5))  # scheduled games are now days in the past
    r = predict_mod.predict_upcoming(trained, days=0)
    assert r["results_stale_games"] == 4


def test_walkforward_backtest(trained):
    out = run_backtest(trained, step_days=15, min_train_days=35, algos=["lightgbm", "xgboost"])
    m = out["run"].metrics
    assert m["period"]["n_folds"] >= 2 and m["period"]["start"] >= "2024-11-26"
    pts = m["player"]["lightgbm"]["points"]
    assert pts["mae"] < pts["mae_baseline_l10"] * 1.1 and 0.6 < pts["coverage"]["0.8"] < 0.95
    assert 0 <= m["calibration_ece"]["points"] < 0.2
    assert m["odds"]["available"] is False  # no odds stored -> no ROI numbers are fabricated
    corr = load_correlations()
    assert "same|assists|points" in corr["pairs"] and all(-1 <= v <= 1 for v in corr["pairs"].values())
    assert trained.query(BacktestRun).count() == 1


def test_api_endpoints(trained):
    from datetime import timedelta
    from app.db import Game, utcnow
    for g in trained.query(Game).filter(Game.status != "final"):
        g.tip_utc = utcnow() + timedelta(hours=5)
    predict_mod.predict_upcoming(trained)
    trained.commit()
    from app.api.main import app
    c = TestClient(app)
    assert c.get("/api/health").json()["ok"]
    st = c.get("/api/status").json()
    assert any("odds API key" in w for w in st["warnings"]) and len(st["models"]) == 4
    games = c.get("/api/games", params={"day": "2024-12-31"}).json()["games"]
    assert len(games) == 4 and 0 < games[0]["model"]["home_win_prob"] < 1
    det = c.get(f"/api/games/{games[0]['id']}").json()
    assert len(det["players"]) >= 10 and det["form"]["home"]["n"] == 10
    pl = det["players"][0]
    pd_ = c.get(f"/api/players/{pl['player_id']}", params={"game_id": games[0]["id"]}).json()
    assert abs(sum(pd_["distributions"]["points"]["pmf"]) - 1) < 0.01 and len(pd_["log"]) == 20
    # manual quote evaluation: EV = p * odds - 1
    leg = {"game_id": games[0]["id"], "market": "points", "player_id": pl["player_id"], "selection": "over",
           "line": pd_["distributions"]["points"]["q"]["50"] - 0.5, "price": 2.4, "opposite_price": 1.6}
    ev = c.post("/api/evaluate", json=leg).json()
    assert ev["ev"] == pytest.approx(ev["p_win"] * 2.4 - 1, abs=1e-4) and ev["edge"] is not None
    par = c.post("/api/parlay/price", json={"legs": [leg, {"game_id": games[0]["id"], "market": "moneyline", "selection": "home", "price": 1.8}]}).json()
    assert 0 < par["joint_prob"] < 1 and par["same_game"]
    assert c.post("/api/evaluate", json={**leg, "market": "bogus"}).status_code == 422
    # settings validation and kill switch
    assert c.put("/api/settings/risk", json={"max_bet_pct": 0.5}).status_code == 422
    assert c.put("/api/settings/filters", json={"min_edge": 0.05}).json()["min_edge"] == 0.05
    assert c.post("/api/kill-switch", json={"active": True, "reason": "test"}).json()["active"]
    assert c.post("/api/bets", json={"legs": [leg]}).status_code == 422
    assert c.post("/api/kill-switch", json={"active": False}).json()["active"] is False
    bet = c.post("/api/bets", json={"legs": [leg], "stake": 5})
    assert bet.status_code == 200, bet.text
    assert c.get("/api/bets").json()["bankroll"] == 995.0
    assert c.get("/api/overview").json()["bankroll"]["open_bets"] == 1
    assert c.get("/api/value-bets").json()["candidates"] == []
