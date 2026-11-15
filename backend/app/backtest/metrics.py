"""Evaluation metrics (pure functions)."""
from __future__ import annotations

import numpy as np


def brier(p, y) -> float:
    p, y = np.asarray(p, float), np.asarray(y, float)
    return float(np.mean((p - y) ** 2))


def log_loss(p, y, eps: float = 1e-6) -> float:
    p, y = np.clip(np.asarray(p, float), eps, 1 - eps), np.asarray(y, float)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def calibration(p, y, bins: int = 10) -> dict:
    """Reliability table and expected calibration error (ECE)."""
    p, y = np.asarray(p, float), np.asarray(y, float)
    edges = np.linspace(0, 1, bins + 1)
    idx = np.clip(np.digitize(p, edges[1:-1]), 0, bins - 1)
    rows, ece = [], 0.0
    for b in range(bins):
        m = idx == b
        if m.sum() == 0:
            continue
        pm, ym = float(p[m].mean()), float(y[m].mean())
        ece += m.sum() / len(p) * abs(pm - ym)
        rows.append({"bin": f"{edges[b]:.1f}-{edges[b + 1]:.1f}", "n": int(m.sum()), "predicted": round(pm, 4), "observed": round(ym, 4)})
    return {"ece": round(float(ece), 5), "bins": rows}


def max_drawdown(equity) -> float:
    """Largest peak-to-trough decline as a fraction of the peak."""
    e = np.asarray(equity, float)
    if len(e) == 0:
        return 0.0
    peak = np.maximum.accumulate(e)
    return float(np.max((peak - e) / np.where(peak > 0, peak, 1.0)))


def bet_summary(stakes, pnls, start_bankroll: float, clvs=None, won=None) -> dict:
    stakes, pnls = np.asarray(stakes, float), np.asarray(pnls, float)
    n = len(stakes)
    if n == 0:
        return {"n_bets": 0, "turnover": 0.0, "profit": 0.0, "yield": None, "roi": None, "hit_rate": None,
                "max_drawdown": 0.0, "avg_clv": None, "clv_positive_share": None}
    equity = start_bankroll + np.cumsum(pnls)
    decided = pnls != 0
    out = {"n_bets": int(n), "turnover": round(float(stakes.sum()), 2), "profit": round(float(pnls.sum()), 2),
           "yield": round(float(pnls.sum() / stakes.sum()), 5) if stakes.sum() > 0 else None,  # profit / turnover
           "roi": round(float(pnls.sum() / start_bankroll), 5),  # profit / starting bankroll
           "hit_rate": round(float((pnls[decided] > 0).mean()), 4) if decided.any() else None,
           "max_drawdown": round(max_drawdown(np.concatenate([[start_bankroll], equity])), 5),
           "avg_clv": None, "clv_positive_share": None}
    if clvs is not None:
        c = np.asarray([x for x in clvs if x is not None and not np.isnan(x)], float)
        if len(c):
            out["avg_clv"], out["clv_positive_share"] = round(float(c.mean()), 5), round(float((c > 0).mean()), 4)
    return out


def interval_coverage(pit, levels=(0.5, 0.8, 0.9)) -> dict:
    """Share of outcomes inside the central prediction interval; calibrated forecasts match the nominal level."""
    pit = np.asarray(pit, float)
    return {str(l): round(float(((pit > (1 - l) / 2) & (pit < 1 - (1 - l) / 2)).mean()), 4) for l in levels}
