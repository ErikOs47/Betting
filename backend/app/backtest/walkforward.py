"""Walk-forward backtest (expanding window, strictly chronological).

For every block of `step_days` the models are trained only on games before the block and evaluated on the block.
All reported numbers are out-of-sample. Results feed (a) model comparison, (b) per-market calibration scores used
by the confidence score and (c) the residual correlations used for combination bets.
"""
from __future__ import annotations

import json
import logging
from datetime import timedelta

import numpy as np
import pandas as pd
from scipy import stats
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import settings_store
from app.backtest import betsim
from app.backtest.metrics import brier, calibration, interval_coverage, log_loss
from app.betting.parlay import correlations_path
from app.config import get_settings
from app.db import BacktestRun, OddsSnapshot
from app.features.build import ELO_HOME, PLAYER_TARGETS, Frames, build_frames
from app.ml import dist
from app.ml import models as M
from app.ml.predict import MIN_ENABLED_QUALITY, MIN_MINUTES, player_quality
from app.ml.registry import code_sha

log = logging.getLogger("backtest")
MIN_GAME_TRAIN = 400


def _over_prob(mu, alpha, line):
    return dist.nb_frozen(mu, alpha).sf(np.floor(line))


def run_walkforward(frames: Frames, algos: list[str], step_days: int, min_train_days: int) -> tuple[pd.DataFrame, pd.DataFrame, list[dict]]:
    P, G = M.player_training_rows(frames.players), M.game_training_rows(frames.games)
    lo = P["date"].min() + pd.Timedelta(days=min_train_days)
    end = P["date"].max()
    p_parts, g_parts, folds = [], [], []
    while lo <= end:
        hi = lo + pd.Timedelta(days=step_days)
        te_p, te_g = P[(P["date"] >= lo) & (P["date"] < hi)], G[(G["date"] >= lo) & (G["date"] < hi)]
        if len(te_p):
            tr_p, tr_g = P[P["date"] < lo], G[G["date"] < lo]
            assert tr_p["date"].max() < te_p["date"].min(), "training data must precede test data"
            op = te_p[["game_id", "player_id", "team_id", "opp_id", "date", "is_home", "p_min_l10", "p_n_prior", "p_gap_days",
                       "p_sd10_min", "p_new_team", "p_min_trend", "p_missed_since", *PLAYER_TARGETS.values(),
                       *[f"p_{c}_l10" for c in PLAYER_TARGETS.values()], *[f"p_{c}_szn" for c in PLAYER_TARGETS.values()]]].copy()
            og = te_g[["id", "date", "home_id", "away_id", "margin", "total", "home_win", "elo_diff", "x_off_l10", "x_pace_l10"]].copy()
            fold = {"start": str(lo.date()), "end": str((hi - pd.Timedelta(days=1)).date()), "n_player_rows": len(te_p),
                    "n_games": len(te_g), "n_train_rows": len(tr_p)}
            for algo in algos:
                pm = M.PlayerModel(algo).fit(tr_p)
                pred = pm.predict(te_p)
                for c in pred:
                    op[f"{algo}_{c}"] = pred[c].to_numpy()
                fold[f"{algo}_points_mae"] = round(float(np.abs(op["pts"] - op[f"{algo}_mu_points"]).mean()), 4)
                if len(tr_g) >= MIN_GAME_TRAIN and len(te_g):
                    gm = M.GameModel(algo).fit(tr_g)
                    gp = gm.predict(te_g)
                    for c in gp:
                        og[f"{algo}_{c}"] = gp[c].to_numpy()
                    ph = [dist.home_win_prob(m, gm.sigma["margin"]) for m in gp["mu_margin"]]
                    fold[f"{algo}_home_brier"] = round(brier(ph, og["home_win"]), 5)
            p_parts.append(op)
            if any(c.endswith("mu_margin") for c in og.columns):
                g_parts.append(og)
            folds.append(fold)
            log.info("fold %s", fold)
        lo = hi
    return (pd.concat(p_parts, ignore_index=True) if p_parts else pd.DataFrame(),
            pd.concat(g_parts, ignore_index=True) if g_parts else pd.DataFrame(), folds)


# ------------------------------------------------------------------ evaluation
def eval_players(op: pd.DataFrame, algos: list[str]) -> tuple[dict, dict]:
    rng = np.random.default_rng(7)
    out: dict = {a: {} for a in algos}
    out["ensemble"] = {}
    rot = (op["p_min_l10"] >= 20) & (op["p_n_prior"] >= 10)
    ece: dict = {}
    for market, col in PLAYER_TARGETS.items():
        y = op[col].to_numpy(float)
        line = np.floor(op[f"p_{col}_l10"].to_numpy(float)) + 0.5  # reference threshold: the player's own L10 mean
        ev_y = (y > line).astype(float)[rot]
        probs = []
        for a in algos:
            mu, al = op[f"{a}_mu_{market}"].to_numpy(), op[f"{a}_alpha_{market}"].to_numpy()
            pit = dist.randomized_pit(y, mu, al, rng)
            op[f"{a}_pit_{market}"] = pit
            p = _over_prob(mu, al, line)[rot]
            probs.append(p)
            out[a][market] = {
                "n": int(len(y)), "mae": round(float(np.abs(y - mu).mean()), 4),
                "mae_baseline_l10": round(float(np.abs(y - op[f"p_{col}_l10"]).mean()), 4),
                "mae_baseline_season": round(float(np.abs(y - op[f"p_{col}_szn"].fillna(op[f"p_{col}_l10"])).mean()), 4),
                "nll": round(float(-dist.nb_frozen(mu, al).logpmf(y).mean()), 4),
                "coverage": interval_coverage(pit),
                "event": {"n": int(rot.sum()), "brier": round(brier(p, ev_y), 5), "log_loss": round(log_loss(p, ev_y), 5),
                          "base_rate": round(float(ev_y.mean()), 4), "brier_base_rate": round(brier(np.full_like(p, ev_y.mean()), ev_y), 5),
                          **calibration(p, ev_y)},
            }
        pe = np.mean(probs, axis=0)
        out["ensemble"][market] = {"event": {"n": int(rot.sum()), "brier": round(brier(pe, ev_y), 5), "log_loss": round(log_loss(pe, ev_y), 5),
                                             "base_rate": round(float(ev_y.mean()), 4), **calibration(pe, ev_y)}}
        ece[market] = out["ensemble"][market]["event"]["ece"]
    return out, ece


def eval_games(og: pd.DataFrame, algos: list[str]) -> tuple[dict, dict]:
    if og.empty:
        return {}, {}
    y = og["home_win"].to_numpy(float)
    out: dict = {}
    p_elo = 1 / (1 + 10 ** (-og["elo_diff"].to_numpy() / 400.0))
    base = {"brier": round(brier(p_elo, y), 5), "log_loss": round(log_loss(p_elo, y), 5), "accuracy": round(float(((p_elo > 0.5) == (y == 1)).mean()), 4),
            **calibration(p_elo, y)}
    out["elo_baseline"] = {"n": int(len(y)), "moneyline": base}
    out["home_rate_baseline"] = {"moneyline": {"brier": round(brier(np.full(len(y), y.mean()), y), 5), "log_loss": round(log_loss(np.full(len(y), y.mean()), y), 5)}}
    sp_line = np.round(og["elo_diff"].to_numpy() / 28.0) + 0.5  # reference threshold: Elo-implied spread
    to_line = np.round(og["x_off_l10"].to_numpy() * og["x_pace_l10"].to_numpy() / 100.0) + 0.5  # rolling pace x efficiency
    ys, yt = (og["margin"].to_numpy() > sp_line).astype(float), (og["total"].to_numpy() > to_line).astype(float)
    pm, ps, pt = [], [], []
    for a in algos:
        mu, sg = og[f"{a}_mu_margin"].to_numpy(), og[f"{a}_sigma_margin"].to_numpy()
        mt, st = og[f"{a}_mu_total"].to_numpy(), og[f"{a}_sigma_total"].to_numpy()
        ph = np.array([dist.home_win_prob(m, s) for m, s in zip(mu, sg)])
        p_s, p_t = stats.norm.sf(sp_line, mu, sg), stats.norm.sf(to_line, mt, st)
        pm.append(ph), ps.append(p_s), pt.append(p_t)
        og[f"{a}_z_margin"], og[f"{a}_z_total"] = (og["margin"] - mu) / sg, (og["total"] - mt) / st
        out[a] = {"n": int(len(y)),
                  "moneyline": {"brier": round(brier(ph, y), 5), "log_loss": round(log_loss(ph, y), 5),
                                "accuracy": round(float(((ph > 0.5) == (y == 1)).mean()), 4), **calibration(ph, y)},
                  "spread": {"mae": round(float(np.abs(og["margin"] - mu).mean()), 3), "rmse": round(float(np.sqrt(((og["margin"] - mu) ** 2).mean())), 3),
                             "mae_elo": round(float(np.abs(og["margin"] - og["elo_diff"] / 28.0).mean()), 3),
                             "brier": round(brier(p_s, ys), 5), **calibration(p_s, ys)},
                  "total": {"mae": round(float(np.abs(og["total"] - mt).mean()), 3), "rmse": round(float(np.sqrt(((og["total"] - mt) ** 2).mean())), 3),
                            "mae_baseline": round(float(np.abs(og["total"] - (to_line - 0.5)).mean()), 3),
                            "brier": round(brier(p_t, yt), 5), **calibration(p_t, yt)}}
    e = {"moneyline": (np.mean(pm, axis=0), y), "spread": (np.mean(ps, axis=0), ys), "total": (np.mean(pt, axis=0), yt)}
    out["ensemble"] = {k: {"brier": round(brier(p, yy), 5), "log_loss": round(log_loss(p, yy), 5), **calibration(p, yy)} for k, (p, yy) in e.items()}
    return out, {k: out["ensemble"][k]["ece"] for k in e}


def estimate_correlations(op: pd.DataFrame, og: pd.DataFrame, algo: str) -> dict:
    """Pairwise correlations of normal scores of out-of-sample PITs (Gaussian-copula parameters)."""
    markets = list(PLAYER_TARGETS)
    w = op[op["p_min_l10"] >= 18][["game_id", "player_id", "team_id", "is_home"]].copy()
    for m in markets:
        w[m] = stats.norm.ppf(op.loc[w.index, f"{algo}_pit_{m}"])
    pairs: dict[str, float] = {}
    counts: dict[str, int] = {}
    c = w[markets].corr()
    for i, a in enumerate(markets):
        for b in markets[i + 1:]:
            k = "same|" + "|".join(sorted([a, b]))
            pairs[k], counts[k] = round(float(c.loc[a, b]), 4), int(len(w))
    j = w.merge(w, on="game_id", suffixes=("_a", "_b"))
    j = j[j["player_id_a"] < j["player_id_b"]]
    for rel, sub in (("mate", j[j["team_id_a"] == j["team_id_b"]]), ("opp", j[j["team_id_a"] != j["team_id_b"]])):
        for i, a in enumerate(markets):
            for b in markets[i:]:
                x = np.concatenate([sub[f"{a}_a"], sub[f"{b}_a"]]) if a != b else sub[f"{a}_a"].to_numpy()
                y = np.concatenate([sub[f"{b}_b"], sub[f"{a}_b"]]) if a != b else sub[f"{a}_b"].to_numpy()
                k = f"{rel}|" + "|".join(sorted([a, b]))
                pairs[k], counts[k] = round(float(np.corrcoef(x, y)[0, 1]), 4), int(len(x))
    if not og.empty and f"{algo}_z_margin" in og:
        g = w.merge(og[["id", f"{algo}_z_margin", f"{algo}_z_total"]], left_on="game_id", right_on="id")
        own = np.where(g["is_home"] == 1, 1.0, -1.0) * g[f"{algo}_z_margin"]
        for m in markets:
            pairs[f"margin|{m}"], counts[f"margin|{m}"] = round(float(np.corrcoef(g[m], own)[0, 1]), 4), int(len(g))
            pairs[f"total|{m}"], counts[f"total|{m}"] = round(float(np.corrcoef(g[m], g[f"{algo}_z_total"])[0, 1]), 4), int(len(g))
        pairs["game|margin|total"] = round(float(np.corrcoef(og[f"{algo}_z_margin"], og[f"{algo}_z_total"])[0, 1]), 4)
        counts["game|margin|total"] = int(len(og))
    return {"algo": algo, "pairs": pairs, "n": counts}


# ------------------------------------------------------------------ odds-based simulation
def odds_simulation(db: Session, op: pd.DataFrame, og: pd.DataFrame, frames: Frames, algos: list[str], calib: dict) -> dict:
    gids = set(op["game_id"]) | (set(og["id"]) if not og.empty else set())
    res = db.execute(select(OddsSnapshot.__table__).where(OddsSnapshot.game_id.in_(gids)))
    odds = pd.DataFrame(res.fetchall(), columns=list(res.keys()))
    if odds.empty:
        return {"available": False, "reason": "No historical odds snapshots stored for the out-of-sample games. ROI, yield, CLV and "
                                              "drawdown need real historical prices (The Odds API paid plan: `historical-odds` command)."}
    a0, a1 = algos[0], (algos[1] if len(algos) > 1 else None)
    preds_by_day: dict = {}
    actuals: dict = {}
    for r in op.itertuples(index=False):
        q, flags = player_quality(r, 1.0, None)
        day = preds_by_day.setdefault(r.date.date(), {})
        actuals[(r.game_id, r.player_id)] = {c: getattr(r, c) for c in PLAYER_TARGETS.values()}
        for m in PLAYER_TARGETS:
            d = {"family": "negbin", "mu": getattr(r, f"{a0}_mu_{m}"), "alpha": getattr(r, f"{a0}_alpha_{m}")}
            if a1:
                d["alt"] = {"mu": getattr(r, f"{a1}_mu_{m}"), "alpha": getattr(r, f"{a1}_alpha_{m}")}
            day[(r.game_id, m, r.player_id)] = {"id": None, "dist": d, "data_quality": q, "flags": flags, "team_id": r.team_id,
                                                "enabled": q >= MIN_ENABLED_QUALITY and r.p_min_l10 >= MIN_MINUTES}
    for r in og.itertuples(index=False):
        day = preds_by_day.setdefault(r.date.date(), {})
        actuals[(r.id, None)] = {"margin": r.margin, "total": r.total}
        for t in ("margin", "total"):
            d = {"family": "normal", "mu": getattr(r, f"{a0}_mu_{t}"), "sigma": getattr(r, f"{a0}_sigma_{t}")}
            if a1:
                d["alt"] = {"mu": getattr(r, f"{a1}_mu_{t}"), "sigma": getattr(r, f"{a1}_sigma_{t}")}
            day[(r.id, t, None)] = {"id": None, "dist": d, "data_quality": 0.9, "flags": [], "enabled": True, "team_id": r.home_id}
    tips = frames.games.set_index("id")["tip_utc"].to_dict()
    sim = betsim.simulate(preds_by_day=preds_by_day, actuals=actuals, odds=odds, tips=tips,
                          filters=settings_store.get(db, "filters"), risk_cfg=settings_store.get(db, "risk"), calib=calib,
                          start_bankroll=float(settings_store.get(db, "bankroll")["start"]))
    step = max(1, len(sim["equity"]) // 300)
    return {"available": True, "n_snapshots": int(len(odds)), "games_with_odds": int(odds["game_id"].nunique()),
            "summary": sim["summary"], "by_market": sim["by_market"], "equity": sim["equity"][::step], "bets_sample": sim["bets"][-50:]}


def run_backtest(db: Session, *, name: str = "walkforward", algos: list[str] | None = None, step_days: int = 30,
                 min_train_days: int = 300, frames: Frames | None = None) -> dict:
    algos = algos or ["lightgbm", "xgboost"]
    frames = frames or build_frames(db)
    op, og, folds = run_walkforward(frames, algos, step_days, min_train_days)
    if op.empty:
        raise RuntimeError("not enough history for a walk-forward backtest")
    player, ece_p = eval_players(op, algos)
    game, ece_g = eval_games(og, algos)
    ece = {**ece_p, **ece_g}
    calib = {m: float(np.clip(1 - e / 0.05, 0, 1)) for m, e in ece.items()}
    corr = estimate_correlations(op, og, algos[0])
    correlations_path().parent.mkdir(parents=True, exist_ok=True)
    correlations_path().write_text(json.dumps(corr, indent=1))
    metrics = {
        "period": {"start": str(op["date"].min().date()), "end": str(op["date"].max().date()), "n_player_rows": int(len(op)),
                   "n_games": int(len(og)), "n_folds": len(folds), "train_from": str(frames.games["date"].min().date())},
        "player": player, "game": game, "folds": folds, "calibration_ece": ece, "correlations": corr["pairs"],
        "odds": odds_simulation(db, op, og, frames, algos, calib), "data_hash": frames.data_hash,
    }
    run = BacktestRun(name=name, config={"algos": algos, "step_days": step_days, "min_train_days": min_train_days,
                                         "features_player": len(M.PLAYER_FEATURES), "features_game": len(M.GAME_FEATURES)},
                      metrics=metrics, code_sha=code_sha())
    db.add(run)
    db.flush()
    out_dir = get_settings().artifacts_dir / "backtests"
    out_dir.mkdir(parents=True, exist_ok=True)
    op.to_parquet(out_dir / f"{run.id}_players.parquet")
    if not og.empty:
        og.to_parquet(out_dir / f"{run.id}_games.parquet")
    summary = {"run_id": run.id, "period": metrics["period"], "calibration_ece": ece,
               "points": {a: {k: player[a]["points"][k] for k in ("mae", "mae_baseline_l10", "nll", "coverage")} for a in algos},
               "moneyline": {a: {k: v for k, v in game[a]["moneyline"].items() if k != "bins"} for a in game if "moneyline" in game[a]},
               "odds": {k: v for k, v in metrics["odds"].items() if k in ("available", "reason", "summary")}}
    return {"run": run, "summary": summary}
