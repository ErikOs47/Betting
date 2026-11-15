"""Generate and store reproducible predictions for upcoming games, with data-quality gating."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import Game, InjuryEntry, InjuryReport, Prediction, utcnow
from app.features.build import PLAYER_TARGETS, build_frames
from app.ml import models as M
from app.ml.registry import PRIMARY, active_models

ET = ZoneInfo("America/New_York")
MIN_ENABLED_QUALITY = 0.4
MIN_MINUTES = 12.0


def today_et():
    return datetime.now(ET).date()


def results_stale(db: Session) -> int:
    """Number of past games that still have no result: models would train/predict on incomplete history."""
    return int(db.scalar(select(func.count()).select_from(Game).where(Game.status != "final", Game.date < today_et() - timedelta(days=1))) or 0)


def injury_age_hours(report_ts) -> float | None:
    if report_ts is None or pd.isna(report_ts):
        return None
    return (utcnow() - pd.Timestamp(report_ts).to_pydatetime()).total_seconds() / 3600.0


def _freshness(age_h: float | None) -> float:
    limit = get_settings().stale_injuries_s / 3600.0
    if age_h is None:
        return 0.0
    return float(np.clip(1.0 - (age_h - limit) / (4 * limit), 0.0, 1.0))


def player_quality(r, inj_fresh: float, status: str | None) -> tuple[float, list[str]]:
    flags: list[str] = []
    n = float(np.clip(r.p_n_prior / 20.0, 0, 1))
    gap = r.p_gap_days if pd.notna(r.p_gap_days) else 60
    rec = float(np.clip(1 - (gap - 7) / 23.0, 0, 1))
    cv = (r.p_sd10_min / max(r.p_min_l10, 1.0)) if pd.notna(r.p_sd10_min) and pd.notna(r.p_min_l10) else 0.5
    stab = float(np.clip(1 - cv / 0.5, 0, 1))
    role = 0.0 if r.p_new_team == 1 else float(np.clip(1 - abs(r.p_min_trend if pd.notna(r.p_min_trend) else 0) / 10.0, 0, 1))
    q = 0.30 * n + 0.20 * rec + 0.20 * stab + 0.10 * role + 0.20 * inj_fresh
    if r.p_n_prior < 10:
        flags.append("few_games")
    if gap > 14 or r.p_missed_since >= 3:
        flags.append("returning_from_absence")
    if r.p_new_team == 1:
        flags.append("new_team")
    if inj_fresh < 1.0:
        flags.append("injury_report_stale")
    if status in ("Questionable", "Probable"):
        flags.append(status.lower())
        q *= 0.7 if status == "Questionable" else 0.9
    if pd.isna(r.p_min_l10) or r.p_min_l10 < MIN_MINUTES:
        flags.append("low_minutes")
    return round(float(q), 4), flags


def _feat_dict(row: pd.Series, features: list[str]) -> dict:
    return {f: (None if pd.isna(row[f]) else round(float(row[f]), 4)) for f in features}


def _hash(version: str, feats: dict) -> str:
    return hashlib.sha1((version + json.dumps(feats, sort_keys=True)).encode()).hexdigest()


def predict_upcoming(db: Session, days: int = 2) -> dict:
    frames = build_frames(db, upcoming_days=days, today=today_et())
    pm, gm = active_models(db, "player"), active_models(db, "game")
    if PRIMARY not in pm or PRIMARY not in gm:
        return {"error": "no active models; run `train` first"}
    stale = results_stale(db)
    age = injury_age_hours(frames.injury_report_utc)
    inj_fresh = _freshness(age)
    global_flags = (["results_stale"] if stale else [])

    rep = db.scalars(select(InjuryReport).order_by(InjuryReport.report_time_utc.desc()).limit(1)).first()
    status = {}
    if rep is not None:
        for e in db.scalars(select(InjuryEntry).where(InjuryEntry.report_id == rep.id, InjuryEntry.player_id.is_not(None))):
            status[(e.player_id, pd.Timestamp(e.game_date))] = e.status

    up_games = frames.games[frames.games["status"] != "final"]
    game_ids = list(up_games["id"])
    if not game_ids:
        return {"games": 0, "player_predictions": 0, "game_predictions": 0, "results_stale_games": stale}
    db.execute(update(Prediction).where(Prediction.game_id.in_(game_ids), Prediction.is_latest).values(is_latest=False))
    n_p = n_g = n_dis = 0

    # ---------------- player props
    pl = frames.players[frames.players["upcoming"] & (frames.players["p_n_prior"] >= M.MIN_PRIOR_GAMES)]
    if len(pl):
        mv, model = pm[PRIMARY]
        pred = model.predict(pl)
        alt = pm["xgboost"][1].predict(pl) if "xgboost" in pm else None
        for i, (idx, r) in enumerate(pl.iterrows()):
            feats = _feat_dict(r, model.features)
            q, flags = player_quality(r, inj_fresh, status.get((r.player_id, r.date)))
            flags = flags + global_flags
            enabled = q >= MIN_ENABLED_QUALITY and "low_minutes" not in flags and not stale
            n_dis += not enabled
            h = _hash(mv.version, feats)
            for market in PLAYER_TARGETS:
                d = {"family": "negbin", "mu": round(float(pred[f"mu_{market}"].iat[i]), 4), "alpha": round(float(pred[f"alpha_{market}"].iat[i]), 5)}
                if alt is not None:
                    d["alt"] = {"algo": "xgboost", "version": pm["xgboost"][0].version, "mu": round(float(alt[f"mu_{market}"].iat[i]), 4),
                                "alpha": round(float(alt[f"alpha_{market}"].iat[i]), 5)}
                db.add(Prediction(model_version_id=mv.id, game_id=r.game_id, market=market, player_id=r.player_id, team_id=r.team_id,
                                  mean=d["mu"], dist=d, features=feats if market == "points" else {"same_as": "points"},
                                  inputs_hash=h, data_quality=q, quality_flags=flags, enabled=bool(enabled)))
                n_p += 1

    # ---------------- games
    mv, model = gm[PRIMARY]
    pred = model.predict(up_games)
    alt = gm["xgboost"][1].predict(up_games) if "xgboost" in gm else None
    for i, (idx, r) in enumerate(up_games.iterrows()):
        feats = _feat_dict(r, model.features)
        n_games = min(r.h_team_game_no, r.a_team_game_no)
        q = round(float(0.6 * np.clip(n_games / 20.0, 0, 1) + 0.4 * inj_fresh), 4)
        flags = list(global_flags) + (["few_games"] if n_games < 10 else []) + (["injury_report_stale"] if inj_fresh < 1 else [])
        enabled = q >= MIN_ENABLED_QUALITY and n_games >= 5 and not stale
        h = _hash(mv.version, feats)
        for target in ("margin", "total"):
            d = {"family": "normal", "mu": round(float(pred[f"mu_{target}"].iat[i]), 3), "sigma": round(float(model.sigma[target]), 3),
                 "rho": round(float(model.rho), 4)}
            if alt is not None:
                d["alt"] = {"algo": "xgboost", "version": gm["xgboost"][0].version, "mu": round(float(alt[f"mu_{target}"].iat[i]), 3),
                            "sigma": round(float(gm["xgboost"][1].sigma[target]), 3)}
            db.add(Prediction(model_version_id=mv.id, game_id=r.id, market=target, player_id=None, team_id=r.home_id, mean=d["mu"],
                              dist=d, features=feats if target == "margin" else {"same_as": "margin"}, inputs_hash=h,
                              data_quality=q, quality_flags=flags, enabled=bool(enabled)))
            n_g += 1
    db.flush()
    return {"games": len(game_ids), "player_predictions": n_p, "game_predictions": n_g, "disabled_players": n_dis,
            "results_stale_games": stale, "injury_report_age_h": None if age is None else round(age, 2), "data_hash": frames.data_hash}
