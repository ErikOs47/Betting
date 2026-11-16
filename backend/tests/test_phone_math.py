"""The phone page recomputes probabilities in JavaScript; they must equal the backend's Python results."""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from app.betting.odds_math import devig, expected_value, kelly_fraction
from app.betting.probability import market_probability
from app.betting.risk import RiskState, size_bet

JS = Path(__file__).resolve().parents[2] / "phone" / "nbmath.js"
CASES = [
    ({"mu": 24.5, "alpha": 0.084, "alt": {"mu": 25.1, "alpha": 0.08}}, "points", "over", 23.5),
    ({"mu": 24.5, "alpha": 0.084}, "points", "under", 24.0),
    ({"mu": 6.2, "alpha": 0.12, "alt": {"mu": 5.8, "alpha": 0.15}}, "assists", "over", 5.5),
    ({"mu": 1.7, "alpha": 0.3}, "threes", "under", 1.5),
    ({"mu": 38.0, "alpha": 0.05}, "pra", "over", 40.0),
    ({"mu": 12.5, "sigma": 14.6, "sigma_ml": 10.95, "alt": {"mu": 13.0, "sigma": 14.6, "sigma_ml": 11.35}}, "moneyline", "home", None),
    ({"mu": -4.0, "sigma": 14.6, "sigma_ml": 10.95}, "moneyline", "away", None),
    ({"mu": 6.0, "sigma": 14.6}, "spread", "home", -5.5),
    ({"mu": 6.0, "sigma": 14.6, "alt": {"mu": 7.0, "sigma": 14.0}}, "spread", "away", 6.0),
    ({"mu": 226.0, "sigma": 17.9}, "total", "over", 226.0),
    ({"mu": 226.0, "sigma": 17.9, "alt": {"mu": 224.4, "sigma": 18.2}}, "total", "under", 229.5),
]


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_js_matches_python():
    script = f"""
const M = require({json.dumps(str(JS))});
const cases = {json.dumps(CASES)};
const cfg = {{kelly: 0.25, maxBet: 0.01, shrink: 0.5}};
console.log(JSON.stringify({{
  probs: cases.map(([d, m, s, l]) => M.marketProbability(d, m, s, l)),
  devig: M.devig([1.95, 1.87]), ev: M.ev(0.55, 1.9, 0.02), kelly: M.kelly(0.55, 2.0, 0),
  stake: M.stake({{pWin: 0.56, pPush: 0, pMarket: 0.5, price: 2.0, bankroll: 1000, cfg}}),
  stakeBig: M.stake({{pWin: 0.75, pPush: 0, pMarket: null, price: 2.0, bankroll: 1000, cfg}}),
}}));
"""
    out = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)
    for (d, m, s, l), js in zip(CASES, out["probs"]):
        py = market_probability(d, m, s, l)
        for k in ("win", "lose", "push", "p"):
            assert js[k] == pytest.approx(py[k], abs=2e-6), (m, s, l, k)
    assert out["devig"] == pytest.approx(devig([1.95, 1.87]))
    assert out["ev"] == pytest.approx(expected_value(0.55, 1.9, 0.02))
    assert out["kelly"] == pytest.approx(kelly_fraction(0.55, 2.0))
    cfg = {"kelly_fraction": 0.25, "max_bet_pct": 0.01, "max_game_pct": 1.0, "max_daily_loss_pct": 1.0, "uncertainty_shrink": 0.5, "min_stake": 0}
    for key, p, mkt in (("stake", 0.56, 0.5), ("stakeBig", 0.75, None)):
        py = size_bet(p_model=p, p_market=mkt, price=2.0, game_ids=["g"], state=RiskState(1000, 1000), cfg=cfg)
        assert out[key]["amount"] == pytest.approx(py.stake)


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_js_derived_period_markets():
    """Quarter/half/team/half-time-full-time probabilities: internal consistency of the JavaScript implementation."""
    from scipy import stats
    P = {"periods": {p: {"margin": {"a": 0, "b": b, "sd": sd}, "total": {"a": -4.4, "b": tb, "sd": tsd}}
                     for p, b, sd, tb, tsd in (("h1", 0.58, 11.2, 0.52, 12.3), ("q1", 0.30, 8.3, 0.29, 8.3), ("q2", 0.28, 8.4, 0.23, 8.4),
                                               ("q3", 0.25, 8.3, 0.28, 8.6), ("q4", 0.14, 8.1, 0.31, 8.9))},
         "full": {"margin": {"a": 0, "b": 0.98, "sd": 14.5}}, "htft_rho": 0.67,
         "team": {"fg": {"a": 2.0, "b": 0.98, "sd": 11.5}, "h1": {"a": 1.0, "b": 0.5, "sd": 8.0}}}
    script = f"""
const M = require({json.dumps(str(JS))}); const P = {json.dumps(P)}; const muM = 6, muT = 226;
const w = (k, s, l) => M.derived(P, k, s, l, muM, muT).win;
console.log(JSON.stringify({{
  q1: ["home", "draw", "away"].map((s) => w("win:q1", s, null)),
  htft: ["hh", "dh", "ah", "ha", "da", "aa"].map((s) => w("htft", s, null)),
  h1Total: [w("total:h1", "over", 113.5), w("total:h1", "under", 113.5), M.derivedMean(P, "total:h1", muM, muT)],
  team: [M.derivedMean(P, "team_home:fg", muM, muT), M.derivedMean(P, "team_away:fg", muM, muT), w("team_home:fg", "over", 115.5), w("team_away:fg", "over", 115.5)],
  whole: M.derived(P, "team_home:h1", "over", 57, muM, muT),
}}));
"""
    o = json.loads(subprocess.run(["node", "-e", script], capture_output=True, text=True, check=True).stdout)
    assert sum(o["q1"]) == pytest.approx(1.0) and o["q1"][0] > o["q1"][2] > o["q1"][1] > 0.03  # favourite > underdog > tie
    assert o["q1"][0] == pytest.approx(stats.norm.sf(0.5, 0.30 * 6, 8.3), abs=1e-6)
    assert sum(o["htft"]) == pytest.approx(1.0, abs=1e-6)
    hh, dh, ah, ha, da, aa = o["htft"]
    assert hh > aa > ah > ha  # leading at half-time and winning is most likely; comebacks are rarer
    assert hh + ha == pytest.approx(stats.norm.sf(0.5, 0.58 * 6, 11.2), abs=2e-3)  # marginal half-time home lead
    assert hh + dh + ah == pytest.approx(stats.norm.sf(0, 0.98 * 6, 14.5), abs=5e-3)  # marginal full-time home win
    assert o["h1Total"][0] + o["h1Total"][1] == pytest.approx(1.0) and o["h1Total"][2] == pytest.approx(-4.4 + 0.52 * 226)
    assert o["team"][0] - o["team"][1] == pytest.approx(0.98 * 6) and o["team"][2] > o["team"][3]
    assert o["whole"]["push"] > 0 and o["whole"]["win"] + o["whole"]["lose"] + o["whole"]["push"] == pytest.approx(1.0)
    # the backend's twin (used to record and settle team suggestions) gives the same numbers
    from app.betting import periods
    py = lambda k, s, l=None: periods.derived_prob(P, k, s, l, 6, 226)
    assert [py("win:q1", s) for s in ("home", "draw", "away")] == pytest.approx(o["q1"], abs=1e-6)
    assert [py("htft", s) for s in ("hh", "dh", "ah", "ha", "da", "aa")] == pytest.approx(o["htft"], abs=1e-6)
    assert py("total:h1", "over", 113.5) == pytest.approx(o["h1Total"][0], abs=1e-6)
    assert py("team_home:fg", "over", 115.5) == pytest.approx(o["team"][2], abs=1e-6)
    assert periods.derived_mean(P, "team_away:fg", 6, 226) == pytest.approx(o["team"][1])


def test_period_model_fit_and_line_score(db):
    import numpy as np
    import pandas as pd
    from app.betting import periods
    from app.db import Game, Team
    from datetime import date
    rng = np.random.default_rng(3)
    for t in ("AAA", "BBB"):
        db.add(Team(id=t, nba_abbr=t, name=t))
    db.flush()
    rows = []
    for i in range(500):
        mu_m, mu_t = rng.normal(0, 6), rng.normal(225, 8)
        q = [(rng.normal((mu_t + mu_m) / 8, 4), rng.normal((mu_t - mu_m) / 8, 4)) for _ in range(4)]
        home, away = [int(round(x[0])) for x in q], [int(round(x[1])) for x in q]
        db.add(Game(id=f"g{i}", season=2026, date=date(2026, 1, 1), home_id="AAA", away_id="BBB", status="final",
                    home_pts=sum(home), away_pts=sum(away), line_score={"home": home, "away": away}))
        rows.append({"id": f"g{i}", "lightgbm_mu_margin": mu_m, "lightgbm_mu_total": mu_t, "margin": sum(home) - sum(away), "total": sum(home) + sum(away)})
    db.flush()
    m = periods.fit(db, pd.DataFrame(rows), ["lightgbm"])
    assert m["n"] == 500 and m["periods"]["q1"]["margin"]["b"] == pytest.approx(0.25, abs=0.11)
    assert m["periods"]["h1"]["margin"]["b"] == pytest.approx(0.5, abs=0.15) and m["periods"]["h1"]["total"]["b"] == pytest.approx(0.5, abs=0.1)
    assert m["team"]["fg"]["b"] == pytest.approx(1.0, abs=0.12) and 0.5 < m["htft_rho"] < 0.9
    assert periods.period_values({"home": [30, 20, 25, 25, 12], "away": [28, 22, 25, 25, 9]})["h1"] == (50, 50)
    assert periods.period_values({"home": [30, 20], "away": [1, 2]}) is None
