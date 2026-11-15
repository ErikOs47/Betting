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
