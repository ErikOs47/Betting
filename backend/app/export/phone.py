"""Export everything the phone page needs into one JSON file (data.json).

The page computes all market probabilities itself from these distributions (see phone/nbmath.js), so the
JSON stays small: per player and market only mean and dispersion of the two models.
"""
from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import settings_store
from app.betting import paper
from app.betting.value import calibration_scores
from app.db import BacktestRun, Game, InjuryEntry, InjuryReport, ModelVersion, Player, Prediction, utcnow
from app.ml.predict import results_stale, today_et
from app.teams import NAME_OF

ET = ZoneInfo("America/New_York")
PLAYER_MARKETS = ("points", "rebounds", "assists", "threes", "pra")
MIN_PLAYER_MINUTES = 10.0


def _iso(dt) -> str | None:
    return dt.isoformat() + "Z" if dt else None


def _dist(d: dict) -> dict:
    keep = {k: round(float(v), 4) for k, v in d.items() if k in ("mu", "alpha", "sigma", "sigma_ml")}
    if d.get("alt"):
        keep["alt"] = {k: round(float(v), 4) for k, v in d["alt"].items() if k in ("mu", "alpha", "sigma", "sigma_ml")}
    return keep


def build(db: Session, days: int = 2) -> dict:
    today = today_et()
    now = utcnow()
    games = list(db.scalars(select(Game).where(Game.date >= today, Game.date <= today + timedelta(days=days - 1), Game.status != "final")
                            .order_by(Game.tip_utc, Game.id)))
    gids = [g.id for g in games]
    preds = list(db.scalars(select(Prediction).where(Prediction.is_latest, Prediction.game_id.in_(gids)))) if gids else []
    names = {p.id: p.name for p in db.scalars(select(Player).where(Player.id.in_({p.player_id for p in preds if p.player_id})))}
    rep = db.scalars(select(InjuryReport).order_by(InjuryReport.report_time_utc.desc()).limit(1)).first()
    inj: dict[tuple, list] = {}
    if rep is not None:
        for e in db.scalars(select(InjuryEntry).where(InjuryEntry.report_id == rep.id, InjuryEntry.status != "Available")):
            inj.setdefault((str(e.game_date), e.team_id), []).append({"name": e.player_name.strip(), "status": e.status, "reason": e.reason or ""})

    out_games = []
    for g in games:
        gp = [p for p in preds if p.game_id == g.id]
        margin = next((p for p in gp if p.market == "margin"), None)
        total = next((p for p in gp if p.market == "total"), None)
        players: dict[str, dict] = {}
        for p in gp:
            if not p.player_id:
                continue
            row = players.setdefault(p.player_id, {"id": p.player_id, "name": names.get(p.player_id, p.player_id), "team": p.team_id,
                                                   "q": round(p.data_quality, 2), "on": p.enabled, "flags": p.quality_flags, "d": {}})
            row["d"][p.market] = _dist(p.dist)
            if p.market == "points":
                row["min"] = round((p.features or {}).get("p_min_l10") or 0, 1)
        plist = sorted((r for r in players.values() if r.get("min", 0) >= MIN_PLAYER_MINUTES), key=lambda r: -r["min"])
        out_games.append({
            "id": g.id, "tip": _iso(g.tip_utc), "home": g.home_id, "away": g.away_id,
            "homeName": NAME_OF.get(g.home_id, g.home_id), "awayName": NAME_OF.get(g.away_id, g.away_id),
            "model": None if not (margin and total) else {
                "margin": _dist(margin.dist), "total": _dist(total.dist), "q": round(margin.data_quality, 2), "on": margin.enabled,
                "flags": margin.quality_flags},
            "players": plist,
            "injuries": {"home": inj.get((str(g.date), g.home_id), []), "away": inj.get((str(g.date), g.away_id), [])},
        })

    yesterday = today - timedelta(days=1)
    results = [{"home": g.home_id, "away": g.away_id, "hp": g.home_pts, "ap": g.away_pts}
               for g in db.scalars(select(Game).where(Game.date == yesterday, Game.status == "final").order_by(Game.id))]
    run = db.scalars(select(BacktestRun).order_by(BacktestRun.created_at.desc()).limit(1)).first()
    models = list(db.scalars(select(ModelVersion).where(ModelVersion.active)))
    filters, risk = settings_store.get(db, "filters"), settings_store.get(db, "risk")
    return {
        "generatedAt": _iso(now),
        "today": str(today),
        "data": {
            "lastResult": str(db.scalar(select(func.max(Game.date)).where(Game.status == "final"))),
            "staleResults": results_stale(db),
            "injuryReport": _iso(rep.report_time_utc) if rep else None,
            "finalGames": db.scalar(select(func.count()).select_from(Game).where(Game.status == "final")),
            "models": sorted({m.version for m in models}),
            "trainEnd": str(max((m.train_end for m in models), default="")) or None,
        },
        "settings": {"minEdge": filters["min_edge"], "maxEdge": filters["max_edge"], "payoutTax": filters.get("payout_tax", 0.0),
                     "kelly": risk["kelly_fraction"], "maxBet": risk["max_bet_pct"], "shrink": risk["uncertainty_shrink"],
                     "minPrice": filters["min_price"], "maxPrice": filters["max_price"], "bankroll": paper.balance(db)},
        "calibration": {k: round(v, 3) for k, v in calibration_scores(db).items()},
        "backtest": None if run is None else {"period": run.metrics.get("period"), "ece": run.metrics.get("calibration_ece")},
        "yesterday": {"date": str(yesterday), "games": results},
        "games": out_games,
    }


def write(db: Session, path: Path) -> dict:
    payload = build(db)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return {"path": str(path), "bytes": path.stat().st_size, "games": len(payload["games"]),
            "players": sum(len(g["players"]) for g in payload["games"])}
