"""Turn stored predictive distributions into market probabilities."""
from __future__ import annotations

from app.ml import dist

PLAYER_MARKETS = ("points", "rebounds", "assists", "threes", "pra")
GAME_MARKETS = ("moneyline", "spread", "total")
PRED_MARKET = {"moneyline": "margin", "spread": "margin", "total": "total"}  # which stored distribution prices a market


def _one(d: dict, market: str, selection: str, line: float | None) -> dict[str, float]:
    if market in PLAYER_MARKETS:
        p = dist.count_probs(d["mu"], d["alpha"], float(line))
        win, lose = (p["over"], p["under"]) if selection == "over" else (p["under"], p["over"])
        return {"win": win, "lose": lose, "push": p["push"]}
    if market == "moneyline":
        ph = dist.home_win_prob(d["mu"], d.get("sigma_ml") or d["sigma"])
        win = ph if selection == "home" else 1 - ph
        return {"win": win, "lose": 1 - win, "push": 0.0}
    if market == "spread":
        # `line` is the handicap of the selected team: home -5.5 covers when margin > 5.5
        if selection == "home":
            p = dist.normal_probs(d["mu"], d["sigma"], -float(line))
            return {"win": p["over"], "lose": p["under"], "push": p["push"]}
        p = dist.normal_probs(d["mu"], d["sigma"], float(line))
        return {"win": p["under"], "lose": p["over"], "push": p["push"]}
    if market == "total":
        p = dist.normal_probs(d["mu"], d["sigma"], float(line))
        win, lose = (p["over"], p["under"]) if selection == "over" else (p["under"], p["over"])
        return {"win": win, "lose": lose, "push": p["push"]}
    raise ValueError(f"unknown market {market}")


def market_probability(d: dict, market: str, selection: str, line: float | None) -> dict:
    """Ensemble of the primary and the alternative algorithm (when available).

    Returns win/lose/push, the conditional win probability `p` (pushes excluded, comparable with de-vigged
    bookmaker probabilities) and both single-model values for the agreement check."""
    a = _one(d, market, selection, line)
    out = dict(a)
    out["p_primary"] = a["win"] / max(1e-12, a["win"] + a["lose"])
    out["p_alt"] = None
    if d.get("alt"):
        b = _one({**d, **d["alt"]}, market, selection, line)
        out["p_alt"] = b["win"] / max(1e-12, b["win"] + b["lose"])
        for k in ("win", "lose", "push"):
            out[k] = 0.5 * (a[k] + b[k])
    out["p"] = out["win"] / max(1e-12, out["win"] + out["lose"])
    return out
