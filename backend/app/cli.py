"""Command line entry point:  python -m app.cli <command>"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime

import typer

from app.db import init_db, session_scope

app = typer.Typer(no_args_is_help=True, add_completion=False)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(message)s")


def _p(x) -> None:
    typer.echo(json.dumps(x, indent=2, default=str))


@app.command("init-db")
def init_db_cmd() -> None:
    """Create all tables."""
    init_db()
    typer.echo("ok")


@app.command()
def ingest(seasons: list[int] = typer.Argument(..., help="season end years, e.g. 2025 2026 2027"),
           cached_only: bool = False, live: bool = False) -> None:
    """Load schedule + box scores for the given seasons."""
    from app.ingest.pipeline import ingest_season
    init_db()
    for y in seasons:
        with session_scope() as db:
            _p(ingest_season(db, y, live=live, cached_only=cached_only))


@app.command()
def injuries() -> None:
    """Fetch the newest official NBA injury report."""
    from app.ingest.pipeline import ingest_injuries
    init_db()
    with session_scope() as db:
        _p(ingest_injuries(db))


@app.command()
def odds(props: bool = True) -> None:
    """Snapshot current odds from The Odds API (needs NBI_ODDS_API_KEY)."""
    from app.ingest.pipeline import ingest_odds
    init_db()
    with session_scope() as db:
        _p(ingest_odds(db, props=props))


@app.command("historical-odds")
def historical_odds(start: str, end: str, props: bool = False) -> None:
    """Backfill historical odds snapshots day by day (paid Odds API plan; 10 credits per market and region)."""
    from datetime import timedelta
    from app.ingest.pipeline import ingest_historical_odds
    d, e = date.fromisoformat(start), date.fromisoformat(end)
    while d <= e:
        with session_scope() as db:
            _p(ingest_historical_odds(db, d, props=props))
        d += timedelta(days=1)


@app.command()
def train(algo: str = "all", family: str = "all") -> None:
    """Train and register production models on all finished games."""
    from app.ml.registry import train_and_register
    init_db()
    with session_scope() as db:
        _p(train_and_register(db, algo=algo, family=family))


@app.command()
def backtest(name: str = "walkforward", algos: str = "lightgbm,xgboost", step_days: int = 30,
             min_train_days: int = 300) -> None:
    """Walk-forward backtest; stores out-of-sample predictions and metrics."""
    from app.backtest.walkforward import run_backtest
    init_db()
    with session_scope() as db:
        _p(run_backtest(db, name=name, algos=algos.split(","), step_days=step_days, min_train_days=min_train_days)["summary"])


@app.command()
def predict() -> None:
    """Generate and store predictions for upcoming games."""
    from app.ml.predict import predict_upcoming
    with session_scope() as db:
        _p(predict_upcoming(db))


@app.command()
def refresh() -> None:
    """One full refresh cycle: results, injuries, odds, predictions, settlement."""
    from app.tasks.jobs import refresh_all
    _p(refresh_all())


@app.command("phone-export")
def phone_export(out: str = "../phone/data.json") -> None:
    """Write the data file for the phone page."""
    from pathlib import Path
    from app.export.phone import write
    with session_scope() as db:
        _p(write(db, Path(out).resolve()))


@app.command("suggestion-prior")
def suggestion_prior(parquet: str, algos: str = "lightgbm,xgboost") -> None:
    """Replay the suggestion rule on stored out-of-sample backtest predictions and save it as learning prior."""
    import pandas as pd
    from app.betting import suggestions
    init_db()
    op = pd.read_parquet(parquet)
    with session_scope() as db:
        prior = suggestions.backtest_prior(op, algos.split(","))
        suggestions.store_prior(db, prior, {"start": str(op["date"].min().date()), "end": str(op["date"].max().date()), "n_player_rows": int(len(op))})
        _p({m: {k: v[k] for k in ("n", "hits", "hit_rate", "expected", "k")} for m, v in prior.items()})


@app.command("scheduled-update")
def scheduled_update(out: str = "../phone/data.json") -> None:
    """One unattended update run: results, injuries, odds, retrain if due, predictions, settlement, phone export."""
    from pathlib import Path
    from app.export.phone import write
    from app.tasks.jobs import refresh_all
    from app.betting import suggestions
    r = refresh_all()
    with session_scope() as db:
        r["suggestions"] = {"ok": True, **suggestions.settle(db), **suggestions.record(db)}
        r["export"] = write(db, Path(out).resolve())
    summary = {k: (v if k in ("export", "suggestions") else {"ok": v.get("ok"), "error": v.get("error")}) for k, v in r.items()}
    typer.echo(json.dumps(summary, default=str))  # one line, parsed by scripts/scheduled_update.sh


if __name__ == "__main__":
    app()
