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


def test_odds_payload_to_value_bets_and_paper_bet(trained):
    """The Odds API payload format -> stored snapshots -> value candidates -> paper bet at the stored price."""
    from datetime import timedelta
    from app.betting import paper
    from app.betting.value import find_value_bets
    from app.db import Game, OddsSnapshot, Prediction, utcnow
    from app.ingest.pipeline import store_event_rows
    from app.teams import NAME_OF
    db = trained
    tip = (utcnow() + timedelta(hours=5)).replace(microsecond=0)
    for g in db.query(Game).filter(Game.status != "final"):
        g.tip_utc, g.date = tip, tip.replace(tzinfo=None).date()
    predict_mod.predict_upcoming(db)
    db.query(Prediction).update({"data_quality": 0.9, "enabled": True})  # synthetic league has no injury report
    g = db.query(Game).filter(Game.status != "final").first()
    g.date = (tip - timedelta(hours=5)).date()  # US-Eastern game date of a tip-off 5 hours earlier than UTC
    p = db.query(Prediction).filter_by(game_id=g.id, market="points", is_latest=True).order_by(Prediction.mean.desc()).first()
    from app.db import Player
    name = db.get(Player, p.player_id).name
    line = round(p.mean) - 2.5
    ev = {"id": "evt1", "commence_time": tip.isoformat() + "Z", "home_team": NAME_OF[g.home_id], "away_team": NAME_OF[g.away_id],
          "bookmakers": [{"key": bk, "last_update": utcnow().isoformat() + "Z", "markets": [
              {"key": "h2h", "outcomes": [{"name": NAME_OF[g.home_id], "price": 1.9 + d}, {"name": NAME_OF[g.away_id], "price": 1.9 - d}]},
              {"key": "player_points", "outcomes": [{"name": "Over", "description": name, "price": 1.9 + d, "point": line},
                                                    {"name": "Under", "description": name, "price": 1.9 - d, "point": line}]}]}
              for bk, d in (("book_a", 0.0), ("book_b", 0.04))]}
    from zoneinfo import ZoneInfo
    g.date = tip.replace(tzinfo=ZoneInfo("UTC")).astimezone(ZoneInfo("America/New_York")).date()
    db.flush()
    assert store_event_rows(db, ev, utcnow()) == 8
    assert store_event_rows(db, ev, tip + timedelta(minutes=1)) == 0  # in-play prices are never stored as pre-game odds
    snap = db.query(OddsSnapshot).filter_by(market="points").first()
    assert snap.game_id == g.id and snap.player_id == p.player_id  # event and player were matched to our ids
    cands = find_value_bets(db)
    over = next(c for c in cands if c["market"] == "points" and c["selection"] == "over")
    assert over["bookmaker"] == "book_b" and over["price"] == pytest.approx(1.94) and over["n_books"] == 2
    assert over["ev"] == pytest.approx(over["p_win"] * 1.94 + over["p_push"] - 1, abs=1e-4)
    assert {c["market"] for c in cands} == {"points", "moneyline"}
    best = next((c for c in cands if c["ev"] > 0 and not any("disabled" in r for r in c["fail_reasons"])), None)
    assert best is not None
    bet = paper.place_bet(db, [{k: best[k] for k in ("game_id", "market", "player_id", "selection", "line", "bookmaker")}], stake=2)
    assert bet.price == best["price"] and bet.legs[0]["bookmaker"] == best["bookmaker"]


def test_manual_tipico_quotes_drive_value_finder_and_tax(trained):
    """Without any odds feed: hand-entered quotes are stored with a timestamp and priced like API odds."""
    from datetime import timedelta
    from app.db import Game, OddsSnapshot, Prediction, utcnow
    db = trained
    for g in db.query(Game).filter(Game.status != "final"):
        g.tip_utc = utcnow() + timedelta(hours=5)
    predict_mod.predict_upcoming(db)
    db.query(Prediction).update({"data_quality": 0.9, "enabled": True})
    db.commit()
    from app.api.main import app
    c = TestClient(app)
    g = db.query(Game).filter(Game.status != "final").first()
    p = db.query(Prediction).filter_by(game_id=g.id, market="points", is_latest=True).order_by(Prediction.mean.desc()).first()
    line = round(p.mean) - 2.5
    body = {"game_id": g.id, "quotes": [{"market": "points", "player_id": p.player_id, "line": line, "price_a": 1.9, "price_b": 1.9},
                                        {"market": "moneyline", "price_a": 1.9, "price_b": 1.9},
                                        {"market": "spread", "line": -3.5, "price_a": 1.9, "price_b": 1.9}]}
    r = c.post("/api/odds/manual", json=body).json()
    assert r == {"stored": 6, "fetched_at": r["fetched_at"], "bookmaker": "tipico"}
    snaps = db.query(OddsSnapshot).filter_by(source="manual", market="spread").all()
    assert sorted((s.selection, s.line) for s in snaps) == [("away", 3.5), ("home", -3.5)]  # away side gets the mirrored handicap
    assert len(c.get(f"/api/odds/{g.id}").json()["quotes"]) == 6
    cands = c.get("/api/value-bets", params={"all": True}).json()["candidates"]
    assert {x["market"] for x in cands} == {"points", "moneyline", "spread"} and all(x["bookmaker"] == "tipico" for x in cands)
    over = next(x for x in cands if x["market"] == "points" and x["selection"] == "over")
    assert over["price_net"] == 1.9 and over["ev"] == pytest.approx(over["p_win"] * 1.9 + over["p_push"] - 1, abs=1e-4)
    # a 5.3 % payout tax lowers the net price and therefore EV and stake
    assert c.put("/api/settings/filters", json={"payout_tax": 0.053}).status_code == 200
    taxed = next(x for x in c.get("/api/value-bets", params={"all": True}).json()["candidates"] if x["market"] == "points" and x["selection"] == "over")
    assert taxed["price_net"] == pytest.approx(1.9 * 0.947) and taxed["ev"] == pytest.approx(over["ev"] - over["p_win"] * 1.9 * 0.053, abs=1e-4)
    # model-only fair lines: the minimum price guarantees the configured edge after tax
    fl = c.get("/api/fair-lines", params={"game_id": g.id}).json()
    row = next(x for x in fl["lines"] if x["market"] == "points" and x["player_id"] == p.player_id)
    assert row["fair_a"] == pytest.approx(1 / row["p_a"], abs=0.01)
    assert row["min_a"] == pytest.approx(1 / (row["p_a"] - fl["min_edge"]) / 0.947, abs=0.01)
    assert {"moneyline", "spread", "total"} <= {x["market"] for x in fl["lines"]}
    # quotes for a started game are refused; stale manual quotes fail the filter
    assert c.post("/api/odds/manual", json={**body, "game_id": "nope"}).status_code == 404
    db.query(OddsSnapshot).update({"fetched_at": utcnow() - timedelta(hours=4)})
    db.commit()
    assert all("odds stale" in x["fail_reasons"] for x in c.get("/api/value-bets", params={"all": True}).json()["candidates"])
