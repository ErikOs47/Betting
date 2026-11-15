"""Value-bet engine: compare model probabilities with de-vigged bookmaker prices.

The core (`build_candidates`) is a pure function on DataFrames/dicts so the live system and the backtest
share exactly the same logic.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import settings_store
from app.betting.odds_math import devig, expected_value, fair_odds, kelly_fraction, overround
from app.betting.probability import PRED_MARKET, market_probability
from app.config import get_settings
from app.db import BacktestRun, Game, OddsSnapshot, Prediction, utcnow

CONF_WEIGHTS = {"data_quality": 0.35, "calibration": 0.20, "agreement": 0.20, "market_sanity": 0.15, "depth": 0.10}


def confidence_score(*, data_quality: float, calibration: float, p: float, p_alt: float | None, p_primary: float,
                     edge: float, n_books: int, max_edge: float) -> tuple[float, dict]:
    """0-100. Transparent weighted sum of five components (each 0-1):
      data_quality   sample size, recency, role stability, injury-report freshness of the prediction inputs
      calibration    how well this market was calibrated in the latest walk-forward backtest (1 - ECE/5%)
      agreement      LightGBM vs XGBoost probability gap (0 at >= 8 points apart; 0.5 if only one model exists)
      market_sanity  1 up to a 6-point edge, falling to 0 at `max_edge`: huge edges usually mean missing information
      depth          number of bookmakers quoting this exact line (4+ = 1)
    """
    agree = 0.5 if p_alt is None else float(np.clip(1 - abs(p_primary - p_alt) / 0.08, 0, 1))
    sanity = float(np.clip(1 - (edge - 0.06) / max(1e-9, max_edge - 0.06), 0, 1))
    parts = {"data_quality": float(data_quality), "calibration": float(calibration), "agreement": agree,
             "market_sanity": sanity, "depth": min(n_books, 4) / 4.0}
    return round(100 * sum(CONF_WEIGHTS[k] * v for k, v in parts.items()), 1), {k: round(v, 3) for k, v in parts.items()}


def pair_key(market: str, selection: str, line) -> float | None:
    """Both sides of one two-way market share a key (spread: the home handicap)."""
    if market == "moneyline" or line is None or pd.isna(line):
        return None
    return -float(line) if (market == "spread" and selection == "away") else float(line)


def build_candidates(quotes: pd.DataFrame, preds: dict, filters: dict, calib: dict[str, float], *,
                     devig_method: str = "multiplicative") -> list[dict]:
    """quotes: one row per current (game, bookmaker, market, player, selection, line) price.
    preds: (game_id, prediction market, player_id|None) -> {id, dist, data_quality, flags, enabled}."""
    if quotes.empty:
        return []
    q = quotes.copy()
    q["pkey"] = [pair_key(m, s, l) for m, s, l in zip(q["market"], q["selection"], q["line"])]
    q["player_id"] = q["player_id"].where(q["player_id"].notna(), None)
    gcols = ["game_id", "market", "player_id", "pkey", "bookmaker"]
    q["_g"] = ["|".join(map(str, t)) for t in zip(*(q[c] for c in gcols))]
    fair_rows = []
    for _, grp in q.groupby("_g", sort=False):
        if len(grp) != 2 or set(grp["selection"]) not in ({"over", "under"}, {"home", "away"}):
            continue  # one-sided or malformed market: the margin cannot be removed
        fp = devig(list(grp["price"]), devig_method)
        ov = overround(list(grp["price"]))
        for (idx, r), f in zip(grp.iterrows(), fp):
            fair_rows.append({**r.to_dict(), "book_fair": f, "overround": ov})
    if not fair_rows:
        return []
    f = pd.DataFrame(fair_rows)
    f["_m"] = ["|".join(map(str, t)) for t in zip(*(f[c] for c in ("game_id", "market", "player_id", "pkey", "selection")))]
    out: list[dict] = []
    for _, grp in f.groupby("_m", sort=False):
        best = grp.loc[grp["price"].idxmax()]
        pid = best["player_id"] if isinstance(best["player_id"], str) else None  # pandas turns missing ids into NaN
        pred = preds.get((best["game_id"], PRED_MARKET.get(best["market"], best["market"]), pid))
        if pred is None:
            continue
        line = None if best["market"] == "moneyline" else float(best["line"])
        pr = market_probability(pred["dist"], best["market"], best["selection"], line)
        consensus = float(grp["book_fair"].mean())
        edge = pr["p"] - consensus
        net = float(best["price"]) * (1.0 - filters.get("payout_tax", 0.0))  # what a winning unit stake really pays
        ev = expected_value(pr["win"], net, pr["push"])
        conf, parts = confidence_score(data_quality=pred["data_quality"], calibration=calib.get(best["market"], 0.5), p=pr["p"],
                                       p_alt=pr["p_alt"], p_primary=pr["p_primary"], edge=edge, n_books=len(grp),
                                       max_edge=filters["max_edge"])
        fails = []
        if not pred["enabled"]:
            fails.append("prediction disabled (data quality)")
        if best["market"] not in filters["markets"]:
            fails.append("market disabled")
        if edge < filters["min_edge"]:
            fails.append("edge below minimum")
        if edge > filters["max_edge"]:
            fails.append("edge implausibly large")
        if ev <= filters["min_ev"]:
            fails.append("EV not positive")
        if conf < filters["min_confidence"]:
            fails.append("confidence below minimum")
        if pred["data_quality"] < filters["min_data_quality"]:
            fails.append("data quality below minimum")
        if not filters["min_price"] <= net <= filters["max_price"]:
            fails.append("price outside allowed range")
        if best.get("stale", False):
            fails.append("odds stale")
        out.append({
            "game_id": best["game_id"], "market": best["market"], "player_id": pid,
            "player_name": best.get("player_name") if pid else None, "selection": best["selection"], "line": line,
            "bookmaker": best["bookmaker"], "price": float(best["price"]), "price_net": round(net, 4),
            "fetched_at": best.get("fetched_at"),
            "model_prob": round(pr["p"], 5), "p_win": round(pr["win"], 5), "p_push": round(pr["push"], 5),
            "p_primary": round(pr["p_primary"], 5), "p_alt": None if pr["p_alt"] is None else round(pr["p_alt"], 5),
            "book_prob": round(float(best["book_fair"]), 5), "consensus_prob": round(consensus, 5),
            "implied_prob": round(1 / float(best["price"]), 5), "overround": round(float(best["overround"]), 5),
            "n_books": int(len(grp)), "fair_odds": round(fair_odds(pr["p"]), 3), "ev": round(ev, 5), "edge": round(edge, 5),
            "kelly_full": round(kelly_fraction(pr["win"], net, pr["push"]), 5),
            "confidence": conf, "confidence_parts": parts, "data_quality": pred["data_quality"],
            "quality_flags": pred["flags"], "prediction_id": pred["id"], "passes": not fails, "fail_reasons": fails,
            "other_books": [{"bookmaker": r["bookmaker"], "price": float(r["price"])} for _, r in grp.sort_values("price", ascending=False).iloc[1:6].iterrows()],
        })
    out.sort(key=lambda c: (-int(c["passes"]), -c["ev"]))
    return out


# ------------------------------------------------------------------ live wiring
def calibration_scores(db: Session) -> dict[str, float]:
    """Per-market calibration quality from the latest backtest: 1 - ECE/0.05 (0.5 if no backtest exists)."""
    run = db.scalars(select(BacktestRun).order_by(BacktestRun.created_at.desc()).limit(1)).first()
    if run is None:
        return {}
    return {m: float(np.clip(1 - e / 0.05, 0, 1)) for m, e in (run.metrics.get("calibration_ece") or {}).items()}


def current_quotes(db: Session, game_ids: list[str] | None = None, now: datetime | None = None) -> pd.DataFrame:
    """Latest pre-game price per bookmaker market. Lines a book no longer quotes are dropped."""
    now = now or utcnow()
    stmt = (select(OddsSnapshot.__table__, Game.tip_utc).join(Game, Game.id == OddsSnapshot.game_id)
            .where(Game.status != "final", Game.tip_utc > now, OddsSnapshot.fetched_at >= now - timedelta(hours=36)))
    if game_ids:
        stmt = stmt.where(OddsSnapshot.game_id.in_(game_ids))
    res = db.execute(stmt)
    q = pd.DataFrame(res.fetchall(), columns=list(res.keys()))
    if q.empty:
        return q
    q["player_id"] = q["player_id"].where(q["player_id"].notna(), None)
    k = pd.Series(["|".join(map(str, t)) for t in zip(q["game_id"], q["bookmaker"], q["market"], q["player_name"])], index=q.index)
    q = q[q["fetched_at"] == q.groupby(k)["fetched_at"].transform("max")].copy()
    age = (now - q["fetched_at"]).dt.total_seconds()
    manual_limit = settings_store.get(db, "automation")["manual_odds_max_age_min"] * 60
    q["stale"] = age > np.where(q["source"] == "manual", manual_limit, get_settings().stale_odds_s)
    return q


def latest_predictions(db: Session, game_ids: list[str] | None = None) -> dict:
    stmt = select(Prediction).where(Prediction.is_latest)
    if game_ids:
        stmt = stmt.where(Prediction.game_id.in_(game_ids))
    return {(p.game_id, p.market, p.player_id): {"id": p.id, "dist": p.dist, "data_quality": p.data_quality,
                                                 "flags": p.quality_flags, "enabled": p.enabled, "team_id": p.team_id}
            for p in db.scalars(stmt)}


def find_value_bets(db: Session, *, only_passing: bool = False) -> list[dict]:
    filters = settings_store.get(db, "filters")
    cands = build_candidates(current_quotes(db), latest_predictions(db), filters, calibration_scores(db))
    return [c for c in cands if c["passes"]] if only_passing else cands


def fair_lines(db: Session, game_id: str | None = None, min_minutes: float = 18.0) -> list[dict]:
    """Model-only view for use without any odds feed: for every enabled prediction the fair line, fair odds and the
    minimum bookmaker price that still satisfies the edge filter.

    A quoted price contains the bookmaker margin, so the market's fair probability is at most 1/price. A price of at
    least 1 / (p - min_edge) therefore guarantees edge >= min_edge without knowing the opposite quote; the payout tax
    is added on top."""
    from app.db import Player
    from app.ml import dist
    filters = settings_store.get(db, "filters")
    tax, min_edge = filters.get("payout_tax", 0.0), filters["min_edge"]
    stmt = select(Prediction, Game).join(Game, Game.id == Prediction.game_id).where(
        Prediction.is_latest, Prediction.enabled, Game.status != "final", Game.tip_utc > utcnow())
    if game_id:
        stmt = stmt.where(Prediction.game_id == game_id)
    rows = db.execute(stmt).all()
    names = {p.id: p.name for p in db.scalars(select(Player).where(Player.id.in_({pr.player_id for pr, _ in rows if pr.player_id})))}
    minutes = {(pr.game_id, pr.player_id): (pr.features or {}).get("p_min_l10") for pr, _ in rows if pr.market == "points"}

    def need(p: float) -> float | None:
        return round(1.0 / (p - min_edge) / (1.0 - tax), 2) if p - min_edge > 0.02 else None

    out = []
    for pr, g in rows:
        base = {"game_id": g.id, "matchup": f"{g.away_id} @ {g.home_id}", "tip_utc": g.tip_utc.isoformat() + "Z",
                "data_quality": pr.data_quality, "flags": pr.quality_flags, "prediction_id": pr.id}
        if pr.player_id:
            if (minutes.get((pr.game_id, pr.player_id)) or 0) < min_minutes:
                continue
            line = float(dist.nb_frozen(pr.dist["mu"], pr.dist["alpha"]).ppf(0.5)) - 0.5
            if line < 0:
                continue
            m = market_probability(pr.dist, pr.market, "over", line)
            out.append({**base, "market": pr.market, "player_id": pr.player_id, "player_name": names.get(pr.player_id), "team": pr.team_id,
                        "mean": round(pr.dist["mu"], 1), "line": line, "a": "over", "b": "under", "p_a": round(m["p"], 4),
                        "fair_a": round(1 / m["p"], 2), "fair_b": round(1 / (1 - m["p"]), 2), "min_a": need(m["p"]), "min_b": need(1 - m["p"]),
                        "min_minutes": minutes.get((pr.game_id, pr.player_id))})
        elif pr.market == "margin":
            m = market_probability(pr.dist, "moneyline", "home", None)
            out.append({**base, "market": "moneyline", "player_id": None, "player_name": None, "team": None, "mean": round(pr.dist["mu"], 1),
                        "line": None, "a": "home", "b": "away", "p_a": round(m["p"], 4), "fair_a": round(1 / m["p"], 2),
                        "fair_b": round(1 / (1 - m["p"]), 2), "min_a": need(m["p"]), "min_b": need(1 - m["p"])})
            line = -(round(pr.dist["mu"]) + 0.5) if pr.dist["mu"] >= 0 else -(round(pr.dist["mu"]) - 0.5)
            m = market_probability(pr.dist, "spread", "home", line)
            out.append({**base, "market": "spread", "player_id": None, "player_name": None, "team": None, "mean": round(pr.dist["mu"], 1),
                        "line": line, "a": "home", "b": "away", "p_a": round(m["p"], 4), "fair_a": round(1 / m["p"], 2),
                        "fair_b": round(1 / (1 - m["p"]), 2), "min_a": need(m["p"]), "min_b": need(1 - m["p"])})
        elif pr.market == "total":
            line = round(pr.dist["mu"]) + 0.5
            m = market_probability(pr.dist, "total", "over", line)
            out.append({**base, "market": "total", "player_id": None, "player_name": None, "team": None, "mean": round(pr.dist["mu"], 1),
                        "line": line, "a": "over", "b": "under", "p_a": round(m["p"], 4), "fair_a": round(1 / m["p"], 2),
                        "fair_b": round(1 / (1 - m["p"]), 2), "min_a": need(m["p"]), "min_b": need(1 - m["p"])})
    out.sort(key=lambda r: (r["tip_utc"], r["game_id"], r["player_id"] is not None, -(r.get("min_minutes") or 0), r["market"]))
    return out
