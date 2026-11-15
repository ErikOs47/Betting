"""Celery worker + beat schedule (Redis broker). Start:  celery -A app.tasks.celery_app worker -B"""
from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from app.config import get_settings
from app.tasks import jobs

celery = Celery("nbi", broker=get_settings().redis_url, backend=get_settings().redis_url)
celery.conf.update(timezone="UTC", task_acks_late=True, worker_prefetch_multiplier=1, worker_concurrency=1,
                   broker_connection_retry_on_startup=True)


@celery.task(name="nbi.results")
def results():
    out = jobs.refresh_results()
    return {"results": out, "train": jobs.retrain_if_needed(), "predict": jobs.refresh_predictions(), "settle": jobs.settle()}


@celery.task(name="nbi.injuries")
def injuries():
    return {"injuries": jobs.refresh_injuries(), "predict": jobs.refresh_predictions()}


@celery.task(name="nbi.odds")
def odds():
    return jobs.refresh_odds()


@celery.task(name="nbi.backtest")
def backtest():
    return jobs.run_backtest_job()


@celery.task(name="nbi.job")
def run_job(name: str):
    return jobs.JOBS[name]()


celery.conf.beat_schedule = {
    "results": {"task": "nbi.results", "schedule": crontab(minute="5,35")},  # box scores, retrain if due, settle
    "injuries": {"task": "nbi.injuries", "schedule": crontab(minute="*/15")},  # official report updates every 15 min
    "odds": {"task": "nbi.odds", "schedule": crontab(minute="*/10")},  # job itself enforces interval and budget
    "backtest": {"task": "nbi.backtest", "schedule": crontab(hour=11, minute=0, day_of_week="mon")},
}
