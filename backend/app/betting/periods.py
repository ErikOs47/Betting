"""Quarter, half and team-total markets derived from the full-game model.

For each period the realised margin/total is regressed on the out-of-sample full-game prediction
(margin: through the origin; totals: with intercept). The residual standard deviation gives a normal
predictive distribution per period. Half-time/full-time uses the correlation between the first-half and
full-game residuals. All coefficients are fitted on out-of-sample backtest predictions only.
"""
from __future__ import annotations

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import Game, Setting, utcnow

KEY = "period_model"
PERIODS = ("h1", "q1", "q2", "q3", "q4")


def period_values(line: dict) -> dict | None:
    h, a = line.get("home"), line.get("away")
    if not h or not a or len(h) < 4 or len(a) < 4:
        return None
    out = {f"q{i + 1}": (h[i], a[i]) for i in range(4)}
    out["h1"] = (h[0] + h[1], a[0] + a[1])
    return out


def _ols(x: np.ndarray, y: np.ndarray, intercept: bool) -> dict:
    if intercept:
        b, a = np.polyfit(x, y, 1)
    else:
        a, b = 0.0, float((x * y).sum() / (x * x).sum())
    res = y - (a + b * x)
    return {"a": round(float(a), 4), "b": round(float(b), 5), "sd": round(float(res.std(ddof=1)), 3)}, res


def fit(db: Session, og, algos: list[str]) -> dict | None:
    """og: out-of-sample game predictions (backtest frame with <algo>_mu_margin / _mu_total and actual results)."""
    lines = {g.id: g.line_score for g in db.scalars(select(Game).where(Game.id.in_(list(og["id"])), Game.line_score.is_not(None)))}
    rows = []
    for r in og.itertuples(index=False):
        pv = period_values(lines.get(r.id) or {})
        if pv is None:
            continue
        mu_m = float(np.mean([getattr(r, f"{a}_mu_margin") for a in algos]))
        mu_t = float(np.mean([getattr(r, f"{a}_mu_total") for a in algos]))
        rows.append((mu_m, mu_t, r.margin, r.total, pv))
    if len(rows) < 300:
        return None
    mu_m, mu_t = np.array([r[0] for r in rows]), np.array([r[1] for r in rows])
    margin, total = np.array([r[2] for r in rows], float), np.array([r[3] for r in rows], float)
    model: dict = {"n": len(rows), "fitted_at": utcnow().isoformat(), "periods": {}}
    full_m, res_full = _ols(mu_m, margin, intercept=False)
    full_t, _ = _ols(mu_t, total, intercept=True)
    model["full"] = {"margin": full_m, "total": full_t}
    res_h1 = None
    for p in PERIODS:
        hp = np.array([r[4][p][0] for r in rows], float)
        ap = np.array([r[4][p][1] for r in rows], float)
        m, res = _ols(mu_m, hp - ap, intercept=False)
        t, _ = _ols(mu_t, hp + ap, intercept=True)
        tie = float(((hp - ap) == 0).mean())
        model["periods"][p] = {"margin": m, "total": t, "tie_rate": round(tie, 4)}
        if p == "h1":
            res_h1 = res
    model["htft_rho"] = round(float(np.corrcoef(res_h1, res_full)[0, 1]), 4)
    # team totals: both teams pooled; x = model-implied team points
    x = np.concatenate([(mu_t + mu_m) / 2, (mu_t - mu_m) / 2])
    y_full = np.concatenate([(total + margin) / 2, (total - margin) / 2])
    y_h1 = np.concatenate([[r[4]["h1"][0] for r in rows], [r[4]["h1"][1] for r in rows]]).astype(float)
    model["team"] = {"fg": _ols(x, y_full, True)[0], "h1": _ols(x, y_h1, True)[0]}
    # simple calibration check of the first-half winner probabilities
    from scipy import stats
    h1 = model["periods"]["h1"]["margin"]
    p_home = stats.norm.sf(0.5, h1["b"] * mu_m, h1["sd"])
    hw = np.array([r[4]["h1"][0] > r[4]["h1"][1] for r in rows], float)
    bins = np.clip((p_home * 5).astype(int), 0, 4)
    model["check_h1_home"] = [{"predicted": round(float(p_home[bins == b].mean()), 3), "observed": round(float(hw[bins == b].mean()), 3),
                               "n": int((bins == b).sum())} for b in range(5) if (bins == b).sum() >= 20]
    return model


def store(db: Session, model: dict) -> None:
    row = db.get(Setting, KEY)
    if row is None:
        db.add(Setting(key=KEY, value=model))
    else:
        row.value = model
    db.flush()


def load(db: Session) -> dict | None:
    row = db.get(Setting, KEY)
    return row.value if row is not None else None
