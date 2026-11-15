"""Double-double / triple-double probabilities.

A double-double needs 10+ in at least two of points, rebounds, assists (steals/blocks doubles are too rare to model
and are only counted when settling). The three counts are dependent, so the joint probability comes from a Gaussian
copula over the three negative-binomial marginals, using the same-player correlations estimated in the backtest.
Common random numbers (fixed seed) make the result deterministic.
"""
from __future__ import annotations

import numpy as np
from scipy import stats

from app.betting.parlay import load_correlations

N_SIMS, SEED = 4000, 11
ORDER = ("points", "rebounds", "assists")


def corr_matrix(corr: dict | None = None) -> np.ndarray:
    corr = corr if corr is not None else load_correlations()
    R = np.eye(3)
    if corr:
        c = corr["pairs"]
        R[0, 1] = R[1, 0] = c.get("same|points|rebounds", 0.0)
        R[0, 2] = R[2, 0] = c.get("same|assists|points", 0.0)
        R[1, 2] = R[2, 1] = c.get("same|assists|rebounds", 0.0)
    return R


def double_probs(mu: np.ndarray, alpha: np.ndarray, R: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]:
    """mu, alpha: arrays (N, 3) in ORDER. Returns (P(double-double or better), P(triple-double))."""
    mu, alpha = np.atleast_2d(np.asarray(mu, float)), np.atleast_2d(np.asarray(alpha, float))
    R = corr_matrix() if R is None else R
    z = np.random.default_rng(SEED).standard_normal((N_SIMS, 3)) @ np.linalg.cholesky(R).T
    n = 1.0 / alpha
    below10 = stats.nbinom.cdf(9, n, n / (n + np.maximum(mu, 1e-6)))  # P(X <= 9)
    t = stats.norm.ppf(np.clip(below10, 1e-9, 1 - 1e-9))
    dd, td = np.empty(len(mu)), np.empty(len(mu))
    for i in range(0, len(mu), 2000):
        cnt = (z[None, :, :] > t[i:i + 2000, None, :]).sum(axis=2)
        dd[i:i + 2000], td[i:i + 2000] = (cnt >= 2).mean(axis=1), (cnt == 3).mean(axis=1)
    return dd, td


def ensemble_from_dists(dists: dict) -> tuple[float, float] | None:
    """dists: market -> stored prediction dist (with optional 'alt'). Average of both models' probabilities."""
    if not all(m in dists for m in ORDER):
        return None
    sets = [[(dists[m]["mu"], dists[m]["alpha"]) for m in ORDER]]
    if all(dists[m].get("alt") for m in ORDER):
        sets.append([(dists[m]["alt"]["mu"], dists[m]["alt"]["alpha"]) for m in ORDER])
    mu = np.array([[x[0] for x in s] for s in sets])
    al = np.array([[x[1] for x in s] for s in sets])
    dd, td = double_probs(mu, al)
    return float(dd.mean()), float(td.mean())


def actual_double(pts: int, trb: int, ast: int, stl: int = 0, blk: int = 0) -> int:
    """Number of categories with 10+ (2 = double-double, 3+ = triple-double)."""
    return sum(v >= 10 for v in (pts, trb, ast, stl, blk))
