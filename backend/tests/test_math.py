import math

import numpy as np
import pytest

from app.backtest import metrics
from app.betting import odds_math as om
from app.betting.probability import market_probability
from app.betting.risk import RiskState, limit_cap, size_bet
from app.ml import dist

CFG = {"kelly_fraction": 0.25, "max_bet_pct": 0.01, "max_game_pct": 0.02, "max_daily_loss_pct": 0.03, "uncertainty_shrink": 1.0, "min_stake": 0.0}


# ---------------------------------------------------------------- odds math
def test_ev_matches_spec_formula():
    # EV = (model probability x odds) - 1
    assert om.expected_value(0.55, 2.0) == pytest.approx(0.10)
    assert om.expected_value(0.50, 1.91) == pytest.approx(-0.045)


def test_ev_with_push_refunds_stake():
    assert om.expected_value(0.5, 2.0, p_push=0.1) == pytest.approx(0.1)  # 0.5*2 + 0.1*1 - 1


def test_devig_multiplicative_removes_margin():
    fair = om.devig([1.91, 1.91])
    assert fair == pytest.approx([0.5, 0.5])
    assert om.overround([1.91, 1.91]) == pytest.approx(2 / 1.91 - 1)
    fair = om.devig([1.5, 2.7])
    assert sum(fair) == pytest.approx(1.0) and fair[0] > fair[1]


def test_devig_power_sums_to_one_and_shades_longshot_more():
    mult, power = om.devig([1.2, 5.0]), om.devig([1.2, 5.0], "power")
    assert sum(power) == pytest.approx(1.0, abs=1e-9)
    assert power[1] < mult[1]


def test_kelly():
    assert om.kelly_fraction(0.55, 2.0) == pytest.approx(0.10)  # (bp - q)/b
    assert om.kelly_fraction(0.45, 2.0) == 0.0
    assert om.fair_odds(0.4) == pytest.approx(2.5)
    assert om.american_to_decimal(-110) == pytest.approx(1.9091, abs=1e-4)
    assert om.american_to_decimal(150) == pytest.approx(2.5)


def test_invalid_price():
    with pytest.raises(ValueError):
        om.implied(1.0)


# ---------------------------------------------------------------- distributions
def test_count_probs_sum_to_one_and_push_only_on_integer_lines():
    half = dist.count_probs(22.0, 0.08, 21.5)
    assert half["push"] == 0 and half["over"] + half["under"] == pytest.approx(1.0)
    whole = dist.count_probs(22.0, 0.08, 22)
    assert whole["push"] > 0 and sum(whole.values()) == pytest.approx(1.0)
    assert whole["over"] < half["over"]


def test_negbin_moments():
    d = dist.nb_frozen(20.0, 0.1)
    assert d.mean() == pytest.approx(20.0)
    assert d.var() == pytest.approx(20 + 0.1 * 400)


def test_dispersion_fit_recovers_overdispersion():
    rng = np.random.default_rng(0)
    mu = rng.uniform(5, 30, 20000)
    alpha = 0.08
    y = rng.negative_binomial(1 / alpha, (1 / alpha) / (1 / alpha + mu))
    a, b = dist.fit_nb_dispersion(y, mu)
    est = dist.nb_alpha(np.array([10.0, 20.0]), a, b)
    assert est == pytest.approx([alpha, alpha], rel=0.25)


def test_pit_uniform_for_calibrated_forecast():
    rng = np.random.default_rng(1)
    mu, alpha = np.full(20000, 12.0), np.full(20000, 0.1)
    y = rng.negative_binomial(10, 10 / 22.0, 20000)
    pit = dist.randomized_pit(y, mu, alpha, rng)
    cov = metrics.interval_coverage(pit)
    assert cov["0.5"] == pytest.approx(0.5, abs=0.02) and cov["0.9"] == pytest.approx(0.9, abs=0.02)


def test_home_win_prob_symmetry_and_no_tie_mass():
    assert dist.home_win_prob(0.0, 12.0) == pytest.approx(0.5)
    assert dist.home_win_prob(5.0, 12.0) + dist.home_win_prob(-5.0, 12.0) == pytest.approx(1.0)


def test_spread_and_total_probabilities_are_consistent():
    d = {"mu": 4.0, "sigma": 12.0}
    home = market_probability(d, "spread", "home", -3.5)
    away = market_probability(d, "spread", "away", 3.5)
    assert home["win"] + away["win"] == pytest.approx(1.0)
    assert home["win"] > 0.5  # model margin 4 > 3.5
    whole_h, whole_a = market_probability(d, "spread", "home", -4.0), market_probability(d, "spread", "away", 4.0)
    assert whole_h["push"] == pytest.approx(whole_a["push"]) and whole_h["push"] > 0
    assert whole_h["win"] + whole_a["win"] + whole_h["push"] == pytest.approx(1.0)
    t = market_probability({"mu": 225.0, "sigma": 18.0}, "total", "over", 225.5)
    assert t["win"] < 0.5 and t["win"] + t["lose"] == pytest.approx(1.0)


def test_ensemble_averages_both_models():
    d = {"mu": 20.0, "alpha": 0.1, "alt": {"mu": 24.0, "alpha": 0.1}}
    r = market_probability(d, "points", "over", 21.5)
    assert r["p_primary"] < r["p"] < r["p_alt"]


# ---------------------------------------------------------------- risk
def test_quarter_kelly_and_bet_cap():
    st = RiskState(bankroll=1000, day_start_bankroll=1000)
    small = size_bet(p_model=0.52, p_market=0.5, price=2.0, game_ids=["g"], state=st, cfg=CFG)
    assert small.kelly_full == pytest.approx(0.04) and small.stake == pytest.approx(10.0)  # 0.25*0.04*1000 = 10 = 1% cap
    tiny = size_bet(p_model=0.51, p_market=0.5, price=2.0, game_ids=["g"], state=st, cfg=CFG)
    assert tiny.stake == pytest.approx(5.0)  # 0.25 * 0.02 * 1000
    big = size_bet(p_model=0.70, p_market=0.5, price=2.0, game_ids=["g"], state=st, cfg=CFG)
    assert big.stake == pytest.approx(10.0) and any("per bet" in r for r in big.reasons)


def test_game_cap_and_daily_loss_cap():
    st = RiskState(bankroll=1000, day_start_bankroll=1000, game_exposure={"g": 15.0})
    d = size_bet(p_model=0.7, p_market=0.5, price=2.0, game_ids=["g"], state=st, cfg=CFG)
    assert d.stake == pytest.approx(5.0)  # 2% of 1000 = 20, 15 already used
    st = RiskState(bankroll=1000, day_start_bankroll=1000, realized_pnl_today=-12.0, open_stakes_today=14.0)
    d = size_bet(p_model=0.7, p_market=0.5, price=2.0, game_ids=["x"], state=st, cfg=CFG)
    assert d.stake == pytest.approx(4.0)  # 3% = 30, 12 lost + 14 at risk
    st.open_stakes_today = 18.0
    assert size_bet(p_model=0.7, p_market=0.5, price=2.0, game_ids=["x"], state=st, cfg={**CFG, "min_stake": 1.0}).blocked


def test_kill_switch_blocks_everything():
    st = RiskState(bankroll=1000, day_start_bankroll=1000, kill_switch=True)
    assert size_bet(p_model=0.9, p_market=0.5, price=2.0, game_ids=["g"], state=st, cfg=CFG).blocked
    assert limit_cap(game_ids=["g"], state=st, cfg=CFG)[0] == 0.0


def test_uncertainty_shrink_reduces_stake_and_no_negative_edge_bets():
    st = RiskState(bankroll=1000, day_start_bankroll=1000)
    full = size_bet(p_model=0.52, p_market=0.5, price=2.0, game_ids=["g"], state=st, cfg=CFG)
    half = size_bet(p_model=0.52, p_market=0.5, price=2.0, game_ids=["g"], state=st, cfg={**CFG, "uncertainty_shrink": 0.5})
    assert half.stake == pytest.approx(full.stake / 2) and half.p_stake == pytest.approx(0.51)
    assert size_bet(p_model=0.48, p_market=0.5, price=2.0, game_ids=["g"], state=st, cfg=CFG).blocked


def test_no_martingale_stake_does_not_grow_after_losses():
    cfg = {**CFG, "max_daily_loss_pct": 1.0}
    before = size_bet(p_model=0.55, p_market=0.5, price=2.0, game_ids=["g"], state=RiskState(1000, 1000), cfg=cfg).stake
    after = size_bet(p_model=0.55, p_market=0.5, price=2.0, game_ids=["g"], state=RiskState(900, 1000, realized_pnl_today=-100), cfg=cfg).stake
    assert after < before


# ---------------------------------------------------------------- metrics
def test_metrics():
    assert metrics.brier([1, 0, 0.5], [1, 0, 1]) == pytest.approx(0.25 / 3)
    assert metrics.log_loss([0.5, 0.5], [1, 0]) == pytest.approx(math.log(2))
    assert metrics.max_drawdown([100, 120, 90, 130, 117]) == pytest.approx(0.25)
    c = metrics.calibration(np.array([0.1] * 50 + [0.9] * 50), np.array([0] * 45 + [1] * 5 + [1] * 45 + [0] * 5))
    assert c["ece"] == pytest.approx(0.0, abs=1e-9)
    s = metrics.bet_summary([10, 10, 10], [9.1, -10, 0], 1000, clvs=[0.02, -0.01, None])
    assert s["yield"] == pytest.approx(-0.03) and s["roi"] == pytest.approx(-0.0009) and s["hit_rate"] == 0.5
    assert s["avg_clv"] == pytest.approx(0.005) and s["max_drawdown"] == pytest.approx(10 / 1009.1, abs=1e-4)
