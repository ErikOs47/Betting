import json
from datetime import datetime, timedelta

import pandas as pd
import pytest

from app import settings_store
from app.backtest import betsim
from app.betting import paper, parlay
from app.betting.value import build_candidates, confidence_score
from app.db import Game, LedgerEntry, ModelVersion, OddsSnapshot, Player, PlayerGameStat, Prediction, Team, utcnow

FILTERS = dict(settings_store.DEFAULTS["filters"])
RISK = dict(settings_store.DEFAULTS["risk"])
PT = {"family": "negbin", "mu": 24.0, "alpha": 0.08}


def quote(sel, price, line=21.5, book="b1", market="points", pid="p1", game="g1", **kw):
    return {"game_id": game, "bookmaker": book, "market": market, "player_id": pid, "player_name": "P One" if pid else None,
            "selection": sel, "line": line, "price": price, "fetched_at": datetime(2026, 1, 1, 12), **kw}


def pred(dist=PT, q=0.9, enabled=True, team="BOS"):
    return {"id": 1, "dist": dist, "data_quality": q, "flags": [], "enabled": enabled, "team_id": team}


# ---------------------------------------------------------------- value engine
def test_candidate_numbers():
    q = pd.DataFrame([quote("over", 1.95), quote("under", 1.87), quote("over", 1.90, book="b2"), quote("under", 1.90, book="b2")])
    c = {x["selection"]: x for x in build_candidates(q, {("g1", "points", "p1"): pred()}, FILTERS, {"points": 1.0})}
    o = c["over"]
    assert o["bookmaker"] == "b1" and o["price"] == 1.95 and o["n_books"] == 2  # best price across books
    fair_b1 = (1 / 1.95) / (1 / 1.95 + 1 / 1.87)
    assert o["book_prob"] == pytest.approx(fair_b1, abs=1e-4)
    assert o["consensus_prob"] == pytest.approx((fair_b1 + 0.5) / 2, abs=1e-4)
    assert o["ev"] == pytest.approx(o["p_win"] * 1.95 - 1, abs=1e-4)  # EV = p x odds - 1
    assert o["edge"] == pytest.approx(o["model_prob"] - o["consensus_prob"], abs=1e-4)
    assert o["fair_odds"] == pytest.approx(1 / o["model_prob"], abs=1e-2)
    assert o["passes"] and o["model_prob"] > 0.55
    assert not c["under"]["passes"] and "EV not positive" in c["under"]["fail_reasons"]


def test_filters_and_gating():
    q = pd.DataFrame([quote("over", 1.95), quote("under", 1.87)])
    key = ("g1", "points", "p1")
    assert not build_candidates(q, {key: pred(enabled=False)}, FILTERS, {"points": 1.0})[0]["passes"]
    low_q = build_candidates(q, {key: pred(q=0.3)}, FILTERS, {"points": 1.0})[0]
    assert "data quality below minimum" in low_q["fail_reasons"]
    wild = build_candidates(q, {key: pred(dist={**PT, "mu": 40.0})}, FILTERS, {"points": 1.0})[0]
    assert "edge implausibly large" in wild["fail_reasons"]
    strict = build_candidates(q, {key: pred()}, {**FILTERS, "min_confidence": 99}, {"points": 1.0})[0]
    assert strict["fail_reasons"] == ["confidence below minimum"]
    assert build_candidates(q.iloc[:1], {key: pred()}, FILTERS, {}) == []  # one-sided market cannot be de-vigged
    assert build_candidates(q, {}, FILTERS, {}) == []  # no prediction -> no suggestion


def test_spread_pairs_home_and_away_lines():
    q = pd.DataFrame([quote("home", 1.91, line=-5.5, market="spread", pid=None), quote("away", 1.91, line=5.5, market="spread", pid=None)])
    c = build_candidates(q, {("g1", "margin", None): pred(dist={"family": "normal", "mu": 9.0, "sigma": 12.0})}, FILTERS, {"spread": 1.0})
    assert {x["selection"] for x in c} == {"home", "away"}
    home = next(x for x in c if x["selection"] == "home")
    assert home["model_prob"] > 0.6 and home["book_prob"] == pytest.approx(0.5)


def test_confidence_components():
    conf, parts = confidence_score(data_quality=1, calibration=1, p=0.56, p_alt=0.56, p_primary=0.56, edge=0.04, n_books=4, max_edge=0.2)
    assert conf == 100.0
    conf2, parts2 = confidence_score(data_quality=1, calibration=1, p=0.56, p_alt=0.48, p_primary=0.56, edge=0.2, n_books=1, max_edge=0.2)
    assert parts2["agreement"] == 0 and parts2["market_sanity"] == 0 and conf2 == pytest.approx(100 * (0.35 + 0.20 + 0.10 * 0.25))


# ---------------------------------------------------------------- parlay / correlation
def _legs():
    return [{"game_id": "g1", "market": "points", "player_id": "p1", "selection": "over", "line": 24.5, "price": 2.0},
            {"game_id": "g1", "market": "assists", "player_id": "p1", "selection": "over", "line": 5.5, "price": 2.0}]


PREDS = {("g1", "points", "p1"): pred(), ("g1", "assists", "p1"): pred(dist={"family": "negbin", "mu": 6.0, "alpha": 0.1}),
         ("g1", "points", "p2"): pred(team="DAL"), ("g1", "margin", None): pred(dist={"family": "normal", "mu": 3.0, "sigma": 12.0}),
         ("g2", "points", "p3"): pred()}


def test_parlay_independent_without_correlations(db):
    r = parlay.price_parlay(_legs(), PREDS, {"g1": "BOS"})
    assert not r["correlations_available"] and "warning" in r
    assert r["joint_prob"] == pytest.approx(r["independent_prob"], abs=0.006)
    assert r["price"] == 4.0 and r["ev"] == pytest.approx(r["joint_prob"] * 4 - 1, abs=1e-4)
    assert r == parlay.price_parlay(_legs(), PREDS, {"g1": "BOS"})  # fixed seed -> reproducible


def test_parlay_uses_estimated_correlations(db):
    parlay.correlations_path().parent.mkdir(parents=True, exist_ok=True)
    parlay.correlations_path().write_text(json.dumps({"pairs": {"same|assists|points": 0.5, "opp|points|points": 0.2, "margin|points": 0.3}}))
    r = parlay.price_parlay(_legs(), PREDS, {"g1": "BOS"})
    assert r["correlations_available"] and r["correlation_factor"] > 1.1 and r["joint_prob"] > r["independent_prob"]
    assert r["correlation_matrix"][0][1] == pytest.approx(0.5, abs=1e-3)
    # over + under on positively correlated stats is LESS likely than independence suggests
    legs = _legs()
    legs[1]["selection"] = "under"
    assert parlay.price_parlay(legs, PREDS, {"g1": "BOS"})["correlation_factor"] < 0.95
    # player on the away team vs HOME moneyline: the sign of the margin correlation flips
    away_player = [{"game_id": "g1", "market": "points", "player_id": "p2", "selection": "over", "line": 24.5},
                   {"game_id": "g1", "market": "moneyline", "selection": "home"}]
    home_player = [{**away_player[0], "player_id": "p1"}, away_player[1]]
    assert parlay.price_parlay(away_player, PREDS, {"g1": "BOS"})["correlation_factor"] < 1 < parlay.price_parlay(home_player, PREDS, {"g1": "BOS"})["correlation_factor"]
    # different games stay independent
    cross = [_legs()[0], {"game_id": "g2", "market": "points", "player_id": "p3", "selection": "over", "line": 24.5}]
    assert parlay.price_parlay(cross, PREDS, {"g1": "BOS", "g2": "CHI"})["correlation_factor"] == pytest.approx(1.0, abs=0.03)


def test_parlay_two_lines_on_same_variable(db):
    legs = [{"game_id": "g1", "market": "points", "player_id": "p1", "selection": "over", "line": 20.5},
            {"game_id": "g1", "market": "points", "player_id": "p1", "selection": "over", "line": 26.5}]
    r = parlay.price_parlay(legs, PREDS, {"g1": "BOS"})
    assert r["joint_prob"] == pytest.approx(r["legs"][1]["sim_prob"], abs=1e-9)  # the stricter leg decides
    legs[1]["selection"] = "under"
    legs[1]["line"] = 18.5
    assert parlay.price_parlay(legs, PREDS, {"g1": "BOS"})["joint_prob"] == 0.0  # impossible combination


# ---------------------------------------------------------------- paper betting
@pytest.fixture()
def world(db):
    tip = utcnow() + timedelta(hours=5)
    for t in ("BOS", "DAL"):
        db.add(Team(id=t, nba_abbr=t, name=t))
    db.add(Player(id="p1", name="P One", norm_name="pone"))
    db.add(Game(id="g1", season=2027, date=tip.date(), tip_utc=tip, home_id="BOS", away_id="DAL"))
    mv = ModelVersion(family="player", algo="lightgbm", version="v-test", artifact_path="x", active=True)
    db.add(mv)
    db.flush()
    for market, d in (("points", PT), ("margin", {"family": "normal", "mu": 3.0, "sigma": 12.0}), ("total", {"family": "normal", "mu": 226.0, "sigma": 18.0})):
        db.add(Prediction(model_version_id=mv.id, game_id="g1", market=market, player_id="p1" if market == "points" else None,
                          team_id="BOS", mean=d["mu"], dist=d, features={}, inputs_hash="h", data_quality=0.9))
    now = utcnow()
    for sel, price in (("over", 1.95), ("under", 1.87)):
        db.add(OddsSnapshot(fetched_at=now, event_id="e", game_id="g1", bookmaker="b1", market="points", player_id="p1",
                            player_name="P One", selection=sel, line=21.5, price=price))
    db.flush()
    return db


LEG = {"game_id": "g1", "market": "points", "player_id": "p1", "selection": "over", "line": 21.5, "bookmaker": "b1"}


def _finish(db, pts, played=True, home=110, away=100):
    g = db.get(Game, "g1")
    g.status, g.home_pts, g.away_pts = "final", home, away
    db.add(PlayerGameStat(game_id="g1", player_id="p1", team_id="BOS", opp_id="DAL", starter=True, played=played,
                          seconds=1800 if played else 0, pts=pts, trb=5, ast=5))
    db.flush()


def test_place_and_settle_winning_bet(world):
    b = paper.place_bet(world, [LEG])
    assert b.mode == "paper" and b.price == 1.95 and b.stake == 10.0  # 1% of 1000 cap
    assert paper.balance(world) == 990.0
    _finish(world, pts=25)
    assert paper.settle_open_bets(world) == {"settled": 1}
    assert b.status == "won" and b.pnl == 9.5 and paper.balance(world) == 1009.5
    perf = paper.performance(world)
    assert perf["n_bets"] == 1 and perf["profit"] == 9.5 and perf["yield"] == pytest.approx(0.95) and perf["hit_rate"] == 1.0


@pytest.mark.parametrize("pts,played,status,pnl", [(18, True, "lost", -10.0), (30, False, "void", 0.0)])
def test_settle_loss_and_void(world, pts, played, status, pnl):
    b = paper.place_bet(world, [LEG])
    _finish(world, pts=pts, played=played)
    paper.settle_open_bets(world)
    assert (b.status, b.pnl) == (status, pnl) and paper.balance(world) == 1000.0 + pnl


def test_push_on_integer_line_and_game_markets(world):
    leg = {"game_id": "g1", "market": "spread", "selection": "home", "line": -10.0, "price": 4.5}
    b = paper.place_bet(world, [leg], stake=5)
    ml = paper.place_bet(world, [{"game_id": "g1", "market": "moneyline", "selection": "away", "price": 3.4}], stake=5)
    tot = paper.place_bet(world, [{"game_id": "g1", "market": "total", "selection": "under", "line": 230.5, "price": 2.2}], stake=5)
    _finish(world, pts=20)  # 110:100
    paper.settle_open_bets(world)
    assert (b.status, b.pnl) == ("push", 0.0) and (ml.status, ml.pnl) == ("lost", -5.0) and (tot.status, tot.pnl) == ("won", 6.0)


def test_limits_kill_switch_and_rejections(world):
    with pytest.raises(paper.BetRejected, match="exceeds the limit"):
        paper.place_bet(world, [LEG], stake=10.01)  # > 1% of bankroll
    paper.place_bet(world, [LEG], stake=10)
    paper.place_bet(world, [LEG], stake=10)
    with pytest.raises(paper.BetRejected, match="per-game"):
        paper.place_bet(world, [LEG], stake=1)  # 2% per game used up
    with pytest.raises(paper.BetRejected, match="not positive"):
        paper.place_bet(world, [{**LEG, "selection": "under"}], stake=1)
    settings_store.put(world, "kill_switch", {"active": True})
    with pytest.raises(paper.BetRejected, match="kill switch"):
        paper.place_bet(world, [LEG])
    settings_store.put(world, "kill_switch", {"active": False})
    world.get(Game, "g1").tip_utc = utcnow() - timedelta(minutes=1)
    with pytest.raises(paper.BetRejected, match="already started"):
        paper.place_bet(world, [LEG])


def test_disabled_prediction_blocks_bet(world):
    for p in world.query(Prediction):
        p.enabled = False
    world.flush()
    with pytest.raises(paper.BetRejected, match="data quality"):
        paper.place_bet(world, [LEG])


def test_closing_line_value(world):
    old = utcnow() - timedelta(hours=3)
    for snap in world.query(OddsSnapshot):
        snap.fetched_at = old
    world.flush()
    b = paper.place_bet(world, [LEG])
    b.placed_at = utcnow() - timedelta(hours=2)
    g = world.get(Game, "g1")
    g.tip_utc = utcnow() + timedelta(seconds=1)
    close = utcnow() - timedelta(minutes=1)
    for sel, price in (("over", 1.80), ("under", 2.02)):  # market moved towards our side
        world.add(OddsSnapshot(fetched_at=close, event_id="e", game_id="g1", bookmaker="b1", market="points", player_id="p1",
                               player_name="P One", selection=sel, line=21.5, price=price))
    world.flush()
    price, clv = paper.closing_line_value(world, b)
    fair = (1 / 1.80) / (1 / 1.80 + 1 / 2.02)
    assert price == 1.80 and clv == pytest.approx(1.95 * fair - 1, abs=1e-4) and clv > 0


def test_parlay_bet_with_void_leg_reprices(world):
    legs = [{**LEG, "price": 1.95}, {"game_id": "g1", "market": "moneyline", "selection": "home", "price": 1.9}]
    b = paper.place_bet(world, legs, stake=4)
    assert b.kind == "parlay" and b.price == pytest.approx(1.95 * 1.9)
    _finish(world, pts=0, played=False)
    paper.settle_open_bets(world)
    assert b.status == "won" and b.pnl == pytest.approx(4 * 0.9)  # prop voided, moneyline leg wins alone


# ---------------------------------------------------------------- backtest bet simulation
def test_betsim_uses_only_pre_tip_prices_and_computes_clv():
    tip = datetime(2026, 1, 1, 23)
    rows = []
    for t, over, under in ((tip - timedelta(hours=3), 1.95, 1.87), (tip - timedelta(minutes=5), 1.80, 2.02), (tip + timedelta(minutes=30), 9.0, 1.01)):
        rows += [quote("over", over) | {"fetched_at": t}, quote("under", under) | {"fetched_at": t}]
    odds = pd.DataFrame(rows)
    out = betsim.simulate(preds_by_day={tip.date(): {("g1", "points", "p1"): pred()}}, actuals={("g1", "p1"): {"pts": 25}},
                          odds=odds, tips={"g1": tip}, filters=FILTERS, risk_cfg=RISK, calib={"points": 1.0}, start_bankroll=1000)
    assert len(out["bets"]) == 1
    b = out["bets"][0]
    assert b["price"] == 1.95 and b["result"] == "won" and b["stake"] <= 10.0  # in-play 9.0 price never used
    assert b["clv"] == pytest.approx(1.95 * (1 / 1.80) / (1 / 1.80 + 1 / 2.02) - 1, abs=1e-4)
    assert out["summary"]["profit"] == pytest.approx(b["stake"] * 0.95, abs=0.01) and out["summary"]["avg_clv"] > 0
    lost = betsim.simulate(preds_by_day={tip.date(): {("g1", "points", "p1"): pred()}}, actuals={("g1", "p1"): {"pts": 10}},
                           odds=odds, tips={"g1": tip}, filters=FILTERS, risk_cfg=RISK, calib={"points": 1.0}, start_bankroll=1000)
    assert lost["summary"]["profit"] < 0 and lost["summary"]["max_drawdown"] > 0
