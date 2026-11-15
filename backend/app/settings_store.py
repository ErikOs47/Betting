"""Runtime-configurable settings stored in the DB (filters, limits, kill switch)."""
from __future__ import annotations

import copy
from typing import Any

from sqlalchemy.orm import Session

from app.db import Setting

DEFAULTS: dict[str, Any] = {
    "filters": {"min_edge": 0.03, "min_confidence": 70, "min_ev": 0.0, "min_data_quality": 0.6,
                "max_edge": 0.20,  # edges above this are almost always stale lines or missing information
                "min_price": 1.3, "max_price": 6.0,
                # share of the payout a bookmaker withholds on winning bets (e.g. German betting tax passed on to the
                # customer). EV, Kelly and settlement use the net price = price x (1 - payout_tax).
                "payout_tax": 0.0,
                "markets": ["points", "rebounds", "assists", "pra", "threes", "moneyline", "spread", "total"]},
    "risk": {"kelly_fraction": 0.25, "max_bet_pct": 0.01, "max_game_pct": 0.02, "max_daily_loss_pct": 0.03,
             "uncertainty_shrink": 0.5,  # weight on the model when blending model and market probability for staking
             "min_stake": 1.0},
    "bankroll": {"start": 1000.0, "currency": "EUR"},
    "kill_switch": {"active": False, "reason": "", "since": None},
    "automation": {"odds_interval_min": 60, "props_hours_before_tip": 6, "auto_paper_bet": False,
                   "manual_odds_max_age_min": 180},  # manually entered quotes count as stale after this many minutes
}

BOUNDS = {  # hard validation limits; a typo must not disable risk management
    ("risk", "kelly_fraction"): (0.0, 0.5), ("risk", "max_bet_pct"): (0.0, 0.05), ("risk", "max_game_pct"): (0.0, 0.10),
    ("risk", "max_daily_loss_pct"): (0.0, 0.20), ("risk", "uncertainty_shrink"): (0.0, 1.0),
    ("filters", "min_edge"): (0.0, 0.5), ("filters", "min_confidence"): (0, 100), ("filters", "min_data_quality"): (0.0, 1.0),
    ("filters", "max_edge"): (0.01, 1.0), ("filters", "payout_tax"): (0.0, 0.2),
    ("automation", "manual_odds_max_age_min"): (5, 1440),
}


def get(db: Session, key: str) -> Any:
    row = db.get(Setting, key)
    base = copy.deepcopy(DEFAULTS[key])
    if row is not None and isinstance(base, dict):
        base.update(row.value)
        return base
    return row.value if row is not None else base


def get_all(db: Session) -> dict[str, Any]:
    return {k: get(db, k) for k in DEFAULTS}


def put(db: Session, key: str, value: dict[str, Any]) -> Any:
    if key not in DEFAULTS:
        raise KeyError(key)
    merged = get(db, key)
    for k, v in value.items():
        if k not in DEFAULTS[key]:
            raise ValueError(f"unknown setting {key}.{k}")
        lo_hi = BOUNDS.get((key, k))
        if lo_hi is not None:
            if not isinstance(v, (int, float)) or isinstance(v, bool) or not lo_hi[0] <= v <= lo_hi[1]:
                raise ValueError(f"{key}.{k} must be between {lo_hi[0]} and {lo_hi[1]}")
        merged[k] = v
    row = db.get(Setting, key)
    if row is None:
        db.add(Setting(key=key, value=merged))
    else:
        row.value = merged
    db.flush()
    return merged
