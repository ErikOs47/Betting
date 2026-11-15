"""Odds arithmetic: implied probabilities, margin removal, EV, Kelly. Pure functions."""
from __future__ import annotations

import math

from scipy import optimize


def implied(price: float) -> float:
    if price <= 1.0:
        raise ValueError("decimal odds must be > 1")
    return 1.0 / price


def american_to_decimal(a: float) -> float:
    return 1 + (a / 100.0 if a > 0 else 100.0 / abs(a))


def overround(prices: list[float]) -> float:
    """Bookmaker margin: sum of implied probabilities minus 1."""
    return sum(implied(p) for p in prices) - 1.0


def devig(prices: list[float], method: str = "multiplicative") -> list[float]:
    """Remove the bookmaker margin from a complete market (probabilities sum to 1).

    multiplicative: proportional scaling (standard default).
    power: p_i = q_i^k with k solving sum = 1; shifts more margin onto longshots (favourite-longshot bias).
    """
    q = [implied(p) for p in prices]
    s = sum(q)
    if method == "multiplicative" or abs(s - 1.0) < 1e-9:
        return [x / s for x in q]
    if method == "power":
        k = optimize.brentq(lambda k: sum(x ** k for x in q) - 1.0, 0.5, 5.0)
        return [x ** k for x in q]
    raise ValueError(f"unknown devig method {method}")


def fair_odds(p: float) -> float:
    return math.inf if p <= 0 else 1.0 / p


def expected_value(p_win: float, price: float, p_push: float = 0.0) -> float:
    """EV per unit staked. Without pushes this is the textbook  EV = p * odds - 1.
    A push refunds the stake, so its probability mass contributes a return of 1."""
    return p_win * price + p_push - 1.0


def kelly_fraction(p_win: float, price: float, p_push: float = 0.0) -> float:
    """Full-Kelly stake as a fraction of bankroll (0 when the bet has no edge)."""
    b = price - 1.0
    p_lose = 1.0 - p_win - p_push
    if b <= 0 or p_win <= 0:
        return 0.0
    return max(0.0, (p_win * b - p_lose) / b)


def parlay_price(prices: list[float]) -> float:
    out = 1.0
    for p in prices:
        out *= p
    return out
