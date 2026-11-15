"""In-process scheduler (NBI_SCHEDULER=internal): runs all refresh jobs inside the API process, so a deployment
needs only one backend container (no Redis/Celery). Run the API with a single worker when this is enabled.

On an empty database it first bootstraps the data: the last three seasons are downloaded (rate-limited to
Basketball Reference's 20 requests/minute, ~75 minutes per season), then models are trained and backtested.
"""
from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

from sqlalchemy import func, select

from app.db import Game, ModelVersion, session_scope
from app.tasks import jobs

log = logging.getLogger("scheduler")
BOOTSTRAP_SEASONS = 3
MIN_FINAL_GAMES = 500  # below this the history is too short for the models

# (job name, interval in minutes)
EVERY = [("results", 30), ("injuries", 15), ("odds", 10)]  # on start: catch up results first
state: dict = {"started": None, "bootstrap": None, "last": {}, "running": None}
_thread: threading.Thread | None = None


def _run(name: str, fn) -> None:
    state["running"] = name
    t0 = time.time()
    try:
        res = fn()
    except Exception as e:  # noqa: BLE001 - the loop must never die
        res = {"ok": False, "error": f"{type(e).__name__}: {e}"[:300]}
    state["last"][name] = {"at": datetime.now(timezone.utc).isoformat(), "seconds": round(time.time() - t0, 1), "result": res}
    state["running"] = None
    log.info("job %s done in %.0fs", name, time.time() - t0)


def needs_bootstrap() -> bool:
    with session_scope() as db:
        n = db.scalar(select(func.count()).select_from(Game).where(Game.status == "final")) or 0
        models = db.scalar(select(func.count()).select_from(ModelVersion).where(ModelVersion.active)) or 0
        seasons = db.scalar(select(func.count(func.distinct(Game.season))).where(Game.status == "final")) or 0
    return n < MIN_FINAL_GAMES or models == 0 or seasons < BOOTSTRAP_SEASONS


def bootstrap() -> dict:
    """Download missing seasons, then train, backtest and predict. Safe to re-run: stored games are skipped."""
    from app.ingest.pipeline import ingest_season
    from app.ml.predict import today_et
    from app.sources import bref
    cur = bref.season_end_year(today_et())
    out = {}
    for y in range(cur - BOOTSTRAP_SEASONS + 1, cur + 1):
        state["bootstrap"] = f"lade Saison {y - 1}-{str(y)[2:]}"
        out[f"season_{y}"] = jobs._step(f"ingest {y}", lambda db, y=y: ingest_season(db, y, live=(y == cur)))
    state["bootstrap"] = "Injury Report"
    out["injuries"] = jobs.refresh_injuries()
    state["bootstrap"] = "Training"
    out["train"] = jobs.retrain_if_needed(force=True)
    state["bootstrap"] = "Backtest"
    out["backtest"] = jobs.run_backtest_job()
    state["bootstrap"] = "Prognosen"
    out["predict"] = jobs.refresh_predictions()
    state["bootstrap"] = "fertig"
    return out


def _loop() -> None:
    time.sleep(5)
    if needs_bootstrap():
        _run("bootstrap", bootstrap)
    last_run: dict[str, float] = {}
    while True:
        now = time.time()
        for name, minutes in EVERY:
            if now - last_run.get(name, 0) >= minutes * 60:
                last_run[name] = now
                if name == "results":
                    # new box scores -> retrain if due -> fresh predictions -> settle paper bets
                    _run("results", lambda: {"results": jobs.refresh_results(), "train": jobs.retrain_if_needed(),
                                             "predict": jobs.refresh_predictions(), "settle": jobs.settle()})
                elif name == "injuries":
                    _run("injuries", lambda: {"injuries": jobs.refresh_injuries(), "predict": jobs.refresh_predictions()})
                else:
                    _run("odds", jobs.refresh_odds)
        utc = datetime.now(timezone.utc)
        if utc.weekday() == 0 and utc.hour == 11 and now - last_run.get("backtest", 0) > 20 * 3600:
            last_run["backtest"] = now
            _run("backtest", jobs.run_backtest_job)
        time.sleep(30)


def start() -> None:
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    state["started"] = datetime.now(timezone.utc).isoformat()
    _thread = threading.Thread(target=_loop, name="nbi-scheduler", daemon=True)
    _thread.start()
