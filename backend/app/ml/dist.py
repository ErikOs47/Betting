"""Probability distributions used by all markets (pure functions, no I/O)."""
from __future__ import annotations

import math

import numpy as np
from scipy import optimize, stats


# ---------------------------------------------------------------- negative binomial (player counting stats)
def nb_alpha(mu, a: float, b: float):
    """Dispersion as a function of the mean: Var = mu + alpha * mu^2, alpha = exp(a + b*log(mu))."""
    return np.clip(np.exp(a + b * np.log(np.maximum(mu, 1e-3))), 1e-5, 5.0)


def nb_frozen(mu, alpha):
    mu = np.maximum(np.asarray(mu, dtype=float), 1e-6)
    n = 1.0 / np.asarray(alpha, dtype=float)
    return stats.nbinom(n, n / (n + mu))


def fit_nb_dispersion(y: np.ndarray, mu: np.ndarray) -> tuple[float, float]:
    """Maximum-likelihood (a, b) of the dispersion curve given out-of-sample means."""
    y = np.asarray(y, dtype=float)
    mu = np.maximum(np.asarray(mu, dtype=float), 1e-3)

    def nll(theta):
        return -nb_frozen(mu, nb_alpha(mu, theta[0], theta[1])).logpmf(y).mean()

    res = optimize.minimize(nll, x0=np.array([-2.0, -0.5]), method="Nelder-Mead", options={"xatol": 1e-3, "fatol": 1e-6, "maxiter": 400})
    return float(res.x[0]), float(res.x[1])


def count_probs(mu: float, alpha: float, line: float) -> dict[str, float]:
    """P(over), P(under), P(push) of a counting stat against a line (integer lines can push)."""
    d = nb_frozen(mu, alpha)
    k = math.floor(line)
    is_int = float(line) == k
    over = float(d.sf(k))  # P(X > k)
    push = float(d.pmf(k)) if is_int else 0.0
    under = max(0.0, 1.0 - over - push)
    return {"over": over, "under": under, "push": push}


def count_summary(mu: float, alpha: float) -> dict:
    d = nb_frozen(mu, alpha)
    hi = int(d.ppf(0.999)) + 1
    ks = np.arange(0, hi + 1)
    return {"mean": float(mu), "sd": float(d.std()), "q": {str(q): int(d.ppf(q / 100)) for q in (5, 25, 50, 75, 95)},
            "pmf": [round(float(v), 5) for v in d.pmf(ks)]}


def randomized_pit(y: np.ndarray, mu: np.ndarray, alpha: np.ndarray, rng: np.random.Generator) -> np.ndarray:
    """Randomised probability integral transform for discrete data: uniform iff the forecast is calibrated."""
    d = nb_frozen(mu, alpha)
    lo, hi = d.cdf(y - 1), d.cdf(y)
    return np.clip(lo + rng.uniform(size=len(y)) * (hi - lo), 1e-6, 1 - 1e-6)


# ---------------------------------------------------------------- normal (game margin / total)
def normal_probs(mu: float, sigma: float, threshold: float) -> dict[str, float]:
    """P(X > threshold) etc. for an integer-valued outcome approximated by a normal (continuity corrected)."""
    is_int = float(threshold) == math.floor(threshold)
    if is_int:
        over = float(stats.norm.sf(threshold + 0.5, mu, sigma))
        under = float(stats.norm.cdf(threshold - 0.5, mu, sigma))
        return {"over": over, "under": under, "push": max(0.0, 1 - over - under)}
    over = float(stats.norm.sf(threshold, mu, sigma))
    return {"over": over, "under": 1 - over, "push": 0.0}


def home_win_prob(mu_margin: float, sigma: float) -> float:
    """NBA games cannot end tied: renormalise the continuity-corrected mass around zero."""
    win = stats.norm.sf(0.5, mu_margin, sigma)
    lose = stats.norm.cdf(-0.5, mu_margin, sigma)
    return float(win / (win + lose))


def fit_win_scale(mu_margin: np.ndarray, home_win: np.ndarray, start: float) -> float:
    """Scale s of P(home win) = Phi(mu / s), fitted by Bernoulli maximum likelihood (bounded, shrunk to `start`).

    The shrinkage (weight of 50 pseudo-games on the robust margin scale) keeps small calibration blocks stable."""
    mu, y = np.asarray(mu_margin, float), np.asarray(home_win, float)

    def nll(s):
        p = np.clip(stats.norm.cdf(mu / s), 1e-6, 1 - 1e-6)
        return -(y * np.log(p) + (1 - y) * np.log(1 - p)).sum()

    s_hat = optimize.minimize_scalar(nll, bounds=(7.0, 22.0), method="bounded").x
    w = len(y) / (len(y) + 50.0)
    return float(w * s_hat + (1 - w) * start)
