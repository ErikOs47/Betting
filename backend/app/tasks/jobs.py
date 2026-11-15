"""Refresh jobs. Each step is isolated: one failing source never blocks the others, and failures are recorded
in `source_status` so the dashboard can show outages and stale data."""
from __future__ import annotations

import logging
import threading
from datetime import timedelta

from sqlalchemy import func, select

from app import settings_store
from app.db import ApiUsage, Game, ModelVersion, init_db, session_scope, utcnow
from app.sources import bref

log = logging.getLogger("jobs")
_lock = threading.Lock()
RETRAIN_AFTER_DAYS = 7


def _step(name: str, fn) -> dict:
    try:
        with session_scope() as db:
            return {"ok": True, "result": fn(db)}
    except Exception as e:  # noqa: BLE001
        log.warning("job %s failed: %s", name, e)
        return {"ok": False, "error": f"{type(e).__name__}: {e}"[:300]}


def refresh_results() -> dict:
    """Load new results of the current season and catch up on anything missed while the machine was off,
    including the end of a previous season (e.g. playoffs missed over the summer)."""
    from app.ingest.pipeline import ingest_season, stale_months
    from app.ml.predict import today_et

    def run(db):
        today = today_et()
        cur = bref.season_end_year(today)
        missed = stale_months(db, today - timedelta(days=1))
        out = {}
        for season in sorted(s for s in missed if s != cur):
            out[f"season_{season}"] = ingest_season(db, season, refresh_months=missed[season])
        out[f"season_{cur}"] = ingest_season(db, cur, live=True, refresh_months=missed.get(cur))
        return out
    return _step("results", run)


def refresh_injuries() -> dict:
    from app.ingest.pipeline import ingest_injuries
    return _step("injuries", ingest_injuries)


def refresh_odds(force: bool = False) -> dict:
    """Budget-aware: only polls when games tip off within 24h and the configured interval has elapsed."""
    from app.config import get_settings
    from app.ingest.pipeline import ingest_odds

    def run(db):
        if not get_settings().odds_api_key:
            return {"skipped": "NBI_ODDS_API_KEY not configured"}
        auto = settings_store.get(db, "automation")
        now = utcnow()
        if not force:
            soon = db.scalar(select(func.count()).select_from(Game).where(Game.status != "final", Game.tip_utc > now,
                                                                          Game.tip_utc < now + timedelta(hours=24)))
            if not soon:
                return {"skipped": "no games in the next 24h"}
            last = db.scalar(select(func.max(ApiUsage.ts)).where(ApiUsage.source == "the-odds-api", ApiUsage.endpoint == "odds"))
            if last is not None and (now - last).total_seconds() < auto["odds_interval_min"] * 60:
                return {"skipped": "interval not elapsed"}
        return ingest_odds(db, props=True, props_hours=auto["props_hours_before_tip"],
                           props_min_gap_min=0 if force else auto["odds_interval_min"])
    return _step("odds", run)


def retrain_if_needed(force: bool = False) -> dict:
    from app.ml.registry import train_and_register

    def run(db):
        last_game = db.scalar(select(func.max(Game.date)).where(Game.status == "final"))
        if last_game is None:
            return {"skipped": "no finished games"}
        mv = db.scalars(select(ModelVersion).where(ModelVersion.active).order_by(ModelVersion.train_end)).first()
        if not force and mv is not None and mv.train_end and (last_game - mv.train_end).days < RETRAIN_AFTER_DAYS:
            return {"skipped": f"models current (trained to {mv.train_end})"}
        return train_and_register(db)
    return _step("train", run)


def refresh_predictions() -> dict:
    from app.ml.predict import predict_upcoming
    return _step("predict", predict_upcoming)


def settle() -> dict:
    from app.betting.paper import settle_open_bets
    return _step("settle", settle_open_bets)


def run_backtest_job() -> dict:
    from app.backtest.walkforward import run_backtest
    return _step("backtest", lambda db: run_backtest(db)["summary"])


def refresh_all(force_odds: bool = False) -> dict:
    if not _lock.acquire(blocking=False):
        return {"skipped": "a refresh is already running"}
    try:
        init_db()
        return {"results": refresh_results(), "injuries": refresh_injuries(), "odds": refresh_odds(force_odds),
                "train": retrain_if_needed(), "predict": refresh_predictions(), "settle": settle()}
    finally:
        _lock.release()


JOBS = {"results": refresh_results, "injuries": refresh_injuries, "odds": lambda: refresh_odds(True),
        "train": lambda: retrain_if_needed(True), "predict": refresh_predictions, "settle": settle,
        "backtest": run_backtest_job, "all": lambda: refresh_all(True)}
