"""Combination bets: joint probability of several legs via a Gaussian-copula Monte-Carlo simulation.

Each leg's marginal is its stored predictive distribution; dependence comes from residual correlations
estimated on out-of-sample backtest forecasts (artifact `correlations.json`). Legs in different games are
independent. Without estimated correlations the result is flagged and falls back to independence.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

import numpy as np
from scipy import stats

from app.betting.probability import GAME_MARKETS, PLAYER_MARKETS, PRED_MARKET, market_probability
from app.config import get_settings
from app.ml import dist

N_SIMS = 100_000


def correlations_path() -> Path:
    return get_settings().artifacts_dir / "correlations.json"


def load_correlations() -> dict | None:
    p = correlations_path()
    return json.loads(p.read_text()) if p.exists() else None


def _var_key(leg: dict) -> tuple:
    if leg["market"] in PLAYER_MARKETS:
        return ("player", leg["game_id"], leg["player_id"], leg["market"])
    return ("game", leg["game_id"], PRED_MARKET[leg["market"]])


def pair_corr(a: tuple, b: tuple, team_of: dict, home_of: dict, corr: dict) -> float:
    """Correlation between two latent variables (0 across different games)."""
    if a[1] != b[1]:
        return 0.0
    c = corr["pairs"]
    if a[0] == "player" and b[0] == "player":
        m = "|".join(sorted([a[3], b[3]]))
        if a[2] == b[2]:
            return c.get(f"same|{m}", 0.0)
        rel = "mate" if team_of[(a[1], a[2])] == team_of[(b[1], b[2])] else "opp"
        return c.get(f"{rel}|{m}", 0.0)
    if a[0] == "game" and b[0] == "game":
        return c.get("game|margin|total", 0.0)
    p, g = (a, b) if a[0] == "player" else (b, a)
    if g[2] == "total":
        return c.get(f"total|{p[3]}", 0.0)
    sign = 1.0 if team_of[(p[1], p[2])] == home_of[p[1]] else -1.0  # stored margin is from the home side
    return sign * c.get(f"margin|{p[3]}", 0.0)


def nearest_psd(m: np.ndarray) -> np.ndarray:
    w, v = np.linalg.eigh((m + m.T) / 2)
    m2 = (v * np.clip(w, 1e-6, None)) @ v.T
    d = np.sqrt(np.diag(m2))
    return m2 / np.outer(d, d)


def price_parlay(legs: list[dict], preds: dict, home_of: dict, *, offered_price: float | None = None,
                 n_sims: int = N_SIMS) -> dict:
    """legs: [{game_id, market, player_id, selection, line, price?}], preds as in value.latest_predictions."""
    corr = load_correlations()
    variables: list[tuple] = []
    dists: list[dict] = []
    team_of: dict = {}
    leg_info = []
    for leg in legs:
        key = (leg["game_id"], PRED_MARKET.get(leg["market"], leg["market"]), leg.get("player_id"))
        pred = preds.get(key)
        if pred is None:
            raise ValueError(f"no prediction for leg {leg}")
        vk = _var_key(leg)
        if vk not in variables:
            variables.append(vk)
            dists.append(pred["dist"])
        if vk[0] == "player":
            team_of[(leg["game_id"], leg["player_id"])] = pred["team_id"]
        pr = market_probability(pred["dist"], leg["market"], leg["selection"], leg.get("line"))
        leg_info.append({**leg, "model_prob": round(pr["p"], 5), "p_win": pr["win"], "p_push": pr["push"],
                         "enabled": pred["enabled"], "data_quality": pred["data_quality"], "var": variables.index(vk)})

    k = len(variables)
    R = np.eye(k)
    if corr is not None:
        for i in range(k):
            for j in range(i + 1, k):
                R[i, j] = R[j, i] = pair_corr(variables[i], variables[j], team_of, home_of, corr)
        R = nearest_psd(R)
    seed = int(hashlib.sha1(json.dumps([[str(x) for x in v] for v in variables] + [[l["selection"], l.get("line")] for l in legs]).encode()).hexdigest()[:8], 16)
    rng = np.random.default_rng(seed)
    z = rng.standard_normal((n_sims, k)) @ np.linalg.cholesky(R).T
    u = np.clip(stats.norm.cdf(z), 1e-9, 1 - 1e-9)
    sims = np.empty_like(z)
    for i, (vk, d) in enumerate(zip(variables, dists)):
        # ensemble marginal: mix primary and alternative model draws 50/50
        alt = d.get("alt")
        if vk[0] == "player":
            x = dist.nb_frozen(d["mu"], d["alpha"]).ppf(u[:, i])
            if alt:
                xa = dist.nb_frozen(alt["mu"], alt["alpha"]).ppf(u[:, i])
                x = np.where(np.arange(n_sims) % 2 == 0, x, xa)
        else:
            x = d["mu"] + d["sigma"] * z[:, i]
            if alt:
                x = np.where(np.arange(n_sims) % 2 == 0, x, alt["mu"] + alt["sigma"] * z[:, i])
            x = np.rint(x)
            if vk[2] == "margin":  # no ties in basketball: resample the sign for zero margins
                x = np.where(x == 0, np.where(z[:, i] >= (0 - d["mu"]) / d["sigma"], 1.0, -1.0), x)
        sims[:, i] = x

    all_win = np.ones(n_sims, dtype=bool)
    any_push = np.zeros(n_sims, dtype=bool)
    for li in leg_info:
        x = sims[:, li["var"]]
        m, sel, line = li["market"], li["selection"], li.get("line")
        if m == "moneyline":
            win, push = (x > 0) if sel == "home" else (x < 0), np.zeros(n_sims, dtype=bool)
        elif m == "spread":
            v = x + line if sel == "home" else -x + line
            win, push = v > 0, v == 0
        else:
            win, push = ((x > line) if sel == "over" else (x < line)), x == line
        li["sim_prob"] = round(float(win.mean()), 5)
        all_win &= win | push
        any_push |= push
    # A pushed leg is removed from the parlay by bookmakers; we report the clean probability that every leg wins
    # and treat "wins with at least one push" separately because its payout is lower.
    p_all = float((all_win & ~any_push).mean())
    p_with_push = float((all_win & any_push).mean())
    indep = float(np.prod([li["p_win"] for li in leg_info]))
    se = math.sqrt(max(p_all * (1 - p_all), 1e-12) / n_sims)
    prices = [l.get("price") for l in legs]
    product_price = float(np.prod(prices)) if all(p for p in prices) else None
    price = offered_price or product_price
    out = {
        "legs": [{k2: v for k2, v in li.items() if k2 not in ("var", "p_win", "p_push")} for li in leg_info],
        "joint_prob": round(p_all, 5), "joint_prob_se": round(se, 5), "prob_with_push": round(p_with_push, 5),
        "independent_prob": round(indep, 5), "correlation_factor": round(p_all / indep, 4) if indep > 0 else None,
        "fair_odds": round(1 / p_all, 3) if p_all > 0 else None, "product_price": product_price, "price": price,
        "ev": round(p_all * price - 1, 5) if price else None,
        "correlations_available": corr is not None, "same_game": len({l["game_id"] for l in legs}) < len(legs),
        "n_sims": n_sims, "seed": seed, "all_enabled": all(li["enabled"] for li in leg_info),
        "min_data_quality": min(li["data_quality"] for li in leg_info),
        "correlation_matrix": [[round(float(v), 3) for v in row] for row in R],
    }
    if corr is None:
        out["warning"] = "No estimated correlations (run a backtest). Legs are treated as independent."
    return out
