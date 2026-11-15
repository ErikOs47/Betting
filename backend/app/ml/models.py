"""Gradient-boosted mean models (LightGBM / XGBoost) with fitted predictive distributions.

Player markets:  count ~ NegBin(mean = GBM(x), dispersion = f(mean)), one model per market.
Game markets:    margin, total ~ Normal(mean = GBM(x), sigma estimated out-of-sample).
Training is strictly chronological: the last `calib_frac` of the training window is held out for early stopping
and for fitting the dispersion, then the mean model is refit on the full window with the chosen tree count.
"""
from __future__ import annotations

import pickle
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from app.features.build import GAME_FEATURES, PLAYER_FEATURES, PLAYER_TARGETS
from app.ml import dist

SEED = 20240101
LGB_PLAYER = dict(objective="poisson", learning_rate=0.04, num_leaves=31, min_data_in_leaf=120, feature_fraction=0.7,
                  bagging_fraction=0.8, bagging_freq=1, lambda_l2=5.0, max_bin=127, verbose=-1, seed=SEED,
                  deterministic=True, force_row_wise=True, num_threads=4)
XGB_PLAYER = dict(objective="count:poisson", eta=0.04, max_depth=5, min_child_weight=40, subsample=0.8,
                  colsample_bytree=0.7, reg_lambda=5.0, tree_method="hist", max_bin=128, seed=SEED, nthread=4, verbosity=0)
LGB_GAME = dict(objective="regression", learning_rate=0.02, num_leaves=6, min_data_in_leaf=60, feature_fraction=0.5,
                bagging_fraction=0.8, bagging_freq=1, lambda_l2=20.0, max_bin=63, verbose=-1, seed=SEED,
                deterministic=True, force_row_wise=True, num_threads=4)
XGB_GAME = dict(objective="reg:squarederror", eta=0.02, max_depth=3, min_child_weight=30, subsample=0.8,
                colsample_bytree=0.5, reg_lambda=20.0, tree_method="hist", max_bin=64, seed=SEED, nthread=4, verbosity=0)
MAX_ROUNDS, EARLY = 1500, 60


def _fit_gbm(algo: str, params: dict, X: pd.DataFrame, y: np.ndarray, Xv: pd.DataFrame | None, yv: np.ndarray | None,
             rounds: int | None = None):
    """Returns (booster, best_iteration)."""
    if algo == "lightgbm":
        import lightgbm as lgb
        dtr = lgb.Dataset(X, y, free_raw_data=False)
        if rounds is not None:
            return lgb.train(params, dtr, num_boost_round=rounds), rounds
        b = lgb.train(params, dtr, num_boost_round=MAX_ROUNDS, valid_sets=[lgb.Dataset(Xv, yv, reference=dtr)],
                      callbacks=[lgb.early_stopping(EARLY, verbose=False)])
        return b, max(20, b.best_iteration)
    if algo == "xgboost":
        import xgboost as xgb
        dtr = xgb.DMatrix(X, y)
        if rounds is not None:
            return xgb.train(params, dtr, num_boost_round=rounds), rounds
        b = xgb.train(params, dtr, num_boost_round=MAX_ROUNDS, evals=[(xgb.DMatrix(Xv, yv), "v")],
                      early_stopping_rounds=EARLY, verbose_eval=False)
        return b, max(20, b.best_iteration + 1)
    raise ValueError(f"unknown algo {algo}")


def _predict(algo: str, booster, X: pd.DataFrame, rounds: int | None = None) -> np.ndarray:
    if algo == "lightgbm":
        return booster.predict(X, num_iteration=rounds)
    import xgboost as xgb
    return booster.predict(xgb.DMatrix(X), iteration_range=(0, rounds) if rounds else (0, 0))


def _split(df: pd.DataFrame, calib_frac: float) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Chronological split on dates (all rows of one day stay together)."""
    days = np.sort(df["date"].unique())
    cut = days[int(len(days) * (1 - calib_frac))]
    return df[df["date"] < cut], df[df["date"] >= cut]


def _importance(algo: str, booster, features: list[str], top: int = 15) -> dict[str, float]:
    if algo == "lightgbm":
        imp = dict(zip(features, booster.feature_importance("gain")))
    else:
        raw = booster.get_score(importance_type="total_gain")
        imp = {f: raw.get(f, 0.0) for f in features}
    tot = sum(imp.values()) or 1.0
    return {k: round(float(v / tot), 4) for k, v in sorted(imp.items(), key=lambda kv: -kv[1])[:top]}


# ------------------------------------------------------------------ player props
MIN_PRIOR_GAMES = 3


def player_training_rows(players: pd.DataFrame) -> pd.DataFrame:
    df = players[(~players["upcoming"]) & (players["p_n_prior"] >= MIN_PRIOR_GAMES)]
    return df.sort_values(["date", "game_id", "player_id"])


@dataclass
class PlayerModel:
    algo: str = "lightgbm"
    features: list[str] = field(default_factory=lambda: list(PLAYER_FEATURES))
    boosters: dict = field(default_factory=dict)
    rounds: dict = field(default_factory=dict)
    disp: dict = field(default_factory=dict)  # market -> (a, b)
    info: dict = field(default_factory=dict)

    def fit(self, train: pd.DataFrame, calib_frac: float = 0.15) -> "PlayerModel":
        params = LGB_PLAYER if self.algo == "lightgbm" else XGB_PLAYER
        tr, ca = _split(train, calib_frac)
        Xtr, Xca, Xall = tr[self.features], ca[self.features], train[self.features]
        for market, col in PLAYER_TARGETS.items():
            b, n = _fit_gbm(self.algo, params, Xtr, tr[col].to_numpy(float), Xca, ca[col].to_numpy(float))
            mu_ca = np.maximum(_predict(self.algo, b, Xca, n), 1e-3)
            self.disp[market] = dist.fit_nb_dispersion(ca[col].to_numpy(float), mu_ca)
            n_full = int(n * len(train) / max(1, len(tr)))  # more data -> proportionally more trees
            self.boosters[market], self.rounds[market] = _fit_gbm(self.algo, params, Xall, train[col].to_numpy(float), None, None, rounds=n_full)
            self.info[market] = {"rounds": n_full, "calib_mae": round(float(np.abs(ca[col] - mu_ca).mean()), 4),
                                 "calib_n": int(len(ca)), "importance": _importance(self.algo, self.boosters[market], self.features)}
        return self

    def predict(self, df: pd.DataFrame) -> pd.DataFrame:
        """Columns: mu_<market>, alpha_<market>."""
        out = {}
        X = df[self.features]
        for market in PLAYER_TARGETS:
            mu = np.maximum(_predict(self.algo, self.boosters[market], X), 1e-3)
            a, b = self.disp[market]
            out[f"mu_{market}"] = mu
            out[f"alpha_{market}"] = dist.nb_alpha(mu, a, b)
        return pd.DataFrame(out, index=df.index)


# ------------------------------------------------------------------ games
def game_training_rows(games: pd.DataFrame) -> pd.DataFrame:
    df = games[(games["status"] == "final") & (games["h_team_game_no"] >= 5) & (games["a_team_game_no"] >= 5)]
    return df.sort_values(["date", "id"])


@dataclass
class GameModel:
    algo: str = "lightgbm"
    features: list[str] = field(default_factory=lambda: list(GAME_FEATURES))
    boosters: dict = field(default_factory=dict)
    rounds: dict = field(default_factory=dict)
    sigma: dict = field(default_factory=dict)
    rho: float = 0.0  # residual correlation margin/total
    info: dict = field(default_factory=dict)

    def fit(self, train: pd.DataFrame, calib_frac: float = 0.2) -> "GameModel":
        params = LGB_GAME if self.algo == "lightgbm" else XGB_GAME
        tr, ca = _split(train, calib_frac)
        res = {}
        for target in ("margin", "total"):
            b, n = _fit_gbm(self.algo, params, tr[self.features], tr[target].to_numpy(float), ca[self.features], ca[target].to_numpy(float))
            res[target] = ca[target].to_numpy(float) - _predict(self.algo, b, ca[self.features], n)
            self.sigma[target] = float(np.sqrt(np.mean(res[target] ** 2)))
            n_full = int(n * len(train) / max(1, len(tr)))
            self.boosters[target], self.rounds[target] = _fit_gbm(self.algo, params, train[self.features], train[target].to_numpy(float), None, None, rounds=n_full)
            self.info[target] = {"rounds": n_full, "calib_rmse": round(self.sigma[target], 3), "calib_n": int(len(ca)),
                                 "importance": _importance(self.algo, self.boosters[target], self.features)}
        self.rho = float(np.corrcoef(res["margin"], res["total"])[0, 1]) if len(ca) > 10 else 0.0
        return self

    def predict(self, df: pd.DataFrame) -> pd.DataFrame:
        X = df[self.features]
        out = pd.DataFrame({"mu_margin": _predict(self.algo, self.boosters["margin"], X),
                            "mu_total": _predict(self.algo, self.boosters["total"], X)}, index=df.index)
        out["sigma_margin"], out["sigma_total"] = self.sigma["margin"], self.sigma["total"]
        return out


def save(model, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as f:
        pickle.dump(model, f)


def load(path: Path):
    with open(path, "rb") as f:
        return pickle.load(f)  # noqa: S301 - artifacts are produced locally by this application
