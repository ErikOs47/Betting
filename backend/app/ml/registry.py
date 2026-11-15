"""Model registry: train, version, persist and load models. Every version records its data and code fingerprint."""
from __future__ import annotations

import subprocess
from functools import lru_cache

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.config import REPO_ROOT, get_settings
from app.db import ModelVersion, utcnow
from app.features.build import Frames, build_frames
from app.ml import models as M

ALGOS = ("lightgbm", "xgboost")
PRIMARY = "lightgbm"


def code_sha() -> str:
    try:
        return subprocess.run(["git", "rev-parse", "HEAD"], cwd=REPO_ROOT, capture_output=True, text=True, timeout=5).stdout.strip()[:40]
    except Exception:  # noqa: BLE001
        return ""


def _register(db: Session, family: str, algo: str, model, train, frames: Frames, params: dict) -> ModelVersion:
    stamp = utcnow().strftime("%Y%m%d%H%M%S")
    version = f"{family}-{algo}-{stamp}-{frames.data_hash[:8]}"
    path = get_settings().artifacts_dir / f"{version}.pkl"
    M.save(model, path)
    db.execute(update(ModelVersion).where(ModelVersion.family == family, ModelVersion.algo == algo).values(active=False))
    mv = ModelVersion(family=family, algo=algo, version=version, train_start=train["date"].min().date(),
                      train_end=train["date"].max().date(), n_train=len(train), params=params, features=model.features,
                      metrics=model.info | ({"dispersion": model.disp} if family == "player" else {"sigma": model.sigma, "rho": model.rho}),
                      data_hash=frames.data_hash, code_sha=code_sha(), artifact_path=str(path), active=True)
    db.add(mv)
    db.flush()
    load_model.cache_clear()
    return mv


def train_and_register(db: Session, algo: str = "all", family: str = "all", frames: Frames | None = None) -> list[dict]:
    frames = frames or build_frames(db)
    out = []
    for a in (ALGOS if algo == "all" else (algo,)):
        if family in ("all", "player"):
            tr = M.player_training_rows(frames.players)
            mv = _register(db, "player", a, M.PlayerModel(a).fit(tr), tr, frames, M.LGB_PLAYER if a == "lightgbm" else M.XGB_PLAYER)
            out.append({"version": mv.version, "n_train": mv.n_train, "train_end": str(mv.train_end)})
        if family in ("all", "game"):
            tr = M.game_training_rows(frames.games)
            mv = _register(db, "game", a, M.GameModel(a).fit(tr), tr, frames, M.LGB_GAME if a == "lightgbm" else M.XGB_GAME)
            out.append({"version": mv.version, "n_train": mv.n_train, "train_end": str(mv.train_end)})
    return out


@lru_cache(maxsize=8)
def load_model(path: str):
    """Artifacts are stored with absolute paths; fall back to the configured artifacts folder when the data
    directory was moved or restored on another machine."""
    from pathlib import Path
    p = Path(path)
    if not p.exists():
        p = get_settings().artifacts_dir / p.name
    return M.load(str(p))


def active_models(db: Session, family: str) -> dict[str, tuple[ModelVersion, object]]:
    rows = db.scalars(select(ModelVersion).where(ModelVersion.family == family, ModelVersion.active))
    return {mv.algo: (mv, load_model(mv.artifact_path)) for mv in rows}
