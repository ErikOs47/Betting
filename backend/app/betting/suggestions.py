"""Suggested bets ("candidates"): generation, tracking of outcomes and learning from them.

Rule: a player prop is suggested when the model's probability at the reference line (floor of the player's
last-10 average + 0.5, where bookmaker lines usually sit) is clearly away from 50 %.

Learning: picking the more likely side of many props overstates the probability (selection effect). The correction
p_adj = sigmoid(k * logit(p)) is fitted per market by maximum likelihood on (a) the same rule replayed on
out-of-sample backtest predictions (capped at PRIOR_WEIGHT effective observations) and (b) every settled live
suggestion (weight 1 each), so live results gradually take over.
"""
from __future__ import annotations

import math
from datetime import timedelta

import numpy as np
from scipy import optimize
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.betting.probability import PLAYER_MARKETS, market_probability
from app.db import Game, Player, PlayerGameStat, Prediction, Setting, Suggestion, utcnow

MIN_P, MAX_RAW_P = 0.57, 0.70
MIN_MINUTES, MIN_QUALITY = 20.0, 0.7
BLOCKING_FLAGS = {"returning_from_absence", "new_team", "questionable", "few_games"}
L10_KEY = {"points": "p_pts_l10", "rebounds": "p_trb_l10", "assists": "p_ast_l10", "threes": "p_fg3_l10", "pra": "p_pra_l10"}
STAT = {"points": "pts", "rebounds": "trb", "assists": "ast", "threes": "fg3"}
PRIOR_WEIGHT = 300.0
MAX_PER_DAY = 20
PRIOR_KEY = "suggestion_prior"


def adjust(p: float, k: float) -> float:
    p = min(max(p, 1e-6), 1 - 1e-6)
    return 1.0 / (1.0 + math.exp(-k * math.log(p / (1 - p))))


def fit_k(p: np.ndarray, y: np.ndarray, w: np.ndarray | None = None) -> float:
    """Slope k of logit(p_adj) = k * logit(p); k < 1 means the raw probabilities were overconfident."""
    if len(p) == 0:
        return 1.0
    w = np.ones(len(p)) if w is None else w
    z = np.log(np.clip(p, 1e-6, 1 - 1e-6) / (1 - np.clip(p, 1e-6, 1 - 1e-6)))

    def nll(k):
        q = np.clip(1 / (1 + np.exp(-k * z)), 1e-6, 1 - 1e-6)
        return -(w * (y * np.log(q) + (1 - y) * np.log(1 - q))).sum()

    return float(optimize.minimize_scalar(nll, bounds=(0.0, 1.5), method="bounded").x)


# ------------------------------------------------------------------ prior from the walk-forward backtest
def backtest_prior(op, algos: list[str]) -> dict:
    """Replay the suggestion rule on out-of-sample backtest predictions. Returns per market the sufficient data
    to refit later: histogram of raw probabilities (1-point bins) with hit counts."""
    from app.features.build import PLAYER_TARGETS
    from app.ml import dist
    out = {}
    rot = (op["p_min_l10"] >= MIN_MINUTES) & (op["p_n_prior"] >= 10)
    for market, col in PLAYER_TARGETS.items():
        d = op[rot & op[f"p_{col}_l10"].notna()]
        line = np.floor(d[f"p_{col}_l10"].to_numpy(float)) + 0.5
        keep = np.ones(len(d), bool) if market != "threes" else line >= 1.5
        over = np.mean([dist.nb_frozen(d[f"{a}_mu_{market}"].to_numpy(), d[f"{a}_alpha_{market}"].to_numpy()).sf(np.floor(line)) for a in algos], axis=0)
        y_over = d[col].to_numpy(float) > line
        p = np.maximum(over, 1 - over)
        y = np.where(over >= 0.5, y_over, ~y_over).astype(float)
        m = keep & (p >= MIN_P) & (p <= MAX_RAW_P)
        p, y = p[m], y[m]
        bins = np.clip(((p - 0.5) * 100).astype(int), 0, 49)
        hist = {int(b): [int((bins == b).sum()), int(y[bins == b].sum())] for b in np.unique(bins)}
        out[market] = {"n": int(len(p)), "hits": int(y.sum()), "expected": round(float(p.mean()), 4) if len(p) else None,
                       "hit_rate": round(float(y.mean()), 4) if len(p) else None, "k": round(fit_k(p, y), 3), "hist": hist}
    return out


def store_prior(db: Session, prior: dict, period: dict | None = None) -> None:
    row = db.get(Setting, PRIOR_KEY)
    value = {"markets": prior, "period": period, "computed_at": utcnow().isoformat()}
    if row is None:
        db.add(Setting(key=PRIOR_KEY, value=value))
    else:
        row.value = value
    db.flush()


def _prior(db: Session) -> dict:
    row = db.get(Setting, PRIOR_KEY)
    return row.value if row is not None else {"markets": {}, "period": None}


def learned_k(db: Session) -> dict[str, dict]:
    """Per market: k fitted on the capped backtest prior plus all settled live suggestions."""
    prior = _prior(db)["markets"]
    live: dict[str, list] = {m: [] for m in PLAYER_MARKETS}
    for s in db.scalars(select(Suggestion).where(Suggestion.kind == "prop", Suggestion.status.in_(("won", "lost")))):
        live[s.market].append((s.raw_prob, 1.0 if s.status == "won" else 0.0))
    out = {}
    for m in PLAYER_MARKETS:
        ps, ys, ws = [], [], []
        pm = prior.get(m)
        if pm and pm["n"]:
            scale = min(1.0, PRIOR_WEIGHT / pm["n"])
            for b, (n, hits) in pm["hist"].items():
                pc = 0.5 + (int(b) + 0.5) / 100
                ps += [pc, pc]; ys += [1.0, 0.0]; ws += [hits * scale, (n - hits) * scale]
        for p, y in live[m]:
            ps.append(p); ys.append(y); ws.append(1.0)
        k = fit_k(np.array(ps), np.array(ys), np.array(ws)) if ps else 1.0
        out[m] = {"k": round(k, 3), "n_live": len(live[m]), "n_backtest": pm["n"] if pm else 0,
                  "k_backtest": pm["k"] if pm else None}
    return out


# ------------------------------------------------------------------ generation
def generate(db: Session) -> list[dict]:
    now = utcnow()
    ks = {m: v["k"] for m, v in learned_k(db).items()}
    rows = db.execute(select(Prediction, Game).join(Game, Game.id == Prediction.game_id).where(
        Prediction.is_latest, Game.status != "final", Game.tip_utc > now)).all()
    by_player: dict[tuple, dict] = {}
    for pr, g in rows:
        if pr.player_id:
            by_player.setdefault((g.id, pr.player_id), {"game": g, "preds": {}})["preds"][pr.market] = pr
    out = []
    for (gid, pid), item in by_player.items():
        pts = item["preds"].get("points")
        if pts is None or not pts.enabled or pts.data_quality < MIN_QUALITY:
            continue
        f = pts.features or {}
        if (f.get("p_min_l10") or 0) < MIN_MINUTES or BLOCKING_FLAGS & set(pts.quality_flags or []):
            continue
        best = None
        for m in PLAYER_MARKETS:
            pr, avg = item["preds"].get(m), f.get(L10_KEY[m])
            if pr is None or avg is None:
                continue
            line = math.floor(avg) + 0.5
            if m == "threes" and line < 1.5:
                continue
            over = market_probability(pr.dist, m, "over", line)["p"]
            sel, raw = ("over", over) if over >= 0.5 else ("under", 1 - over)
            p = adjust(raw, ks.get(m, 1.0))
            if raw > MAX_RAW_P or p < MIN_P:
                continue
            c = {"game_id": gid, "player_id": pid, "team_id": pts.team_id, "market": m, "selection": sel, "line": line,
                 "raw_prob": round(raw, 4), "prob": round(p, 4), "mu": round(pr.dist["mu"], 2), "l10": round(avg, 1)}
            if best is None or c["prob"] > best["prob"]:
                best = c
        if best:
            out.append(best)
    # only the strongest per game day are shown to the user - and exactly those are tracked
    dates = {g.id: g.date for _, g in rows}
    kept, per_day = [], {}
    for c in sorted(out, key=lambda c: -c["prob"]):
        d = dates[c["game_id"]]
        if per_day.get(d, 0) < MAX_PER_DAY:
            per_day[d] = per_day.get(d, 0) + 1
            kept.append(c)
    out = kept
    # game winners: the model's favourite of every open game (tracked for information, no learning)
    for g in {g.id: g for _, g in rows}.values():
        m = next((pr for pr, gg in rows if gg.id == g.id and pr.market == "margin" and pr.enabled), None)
        if m is None:
            continue
        ph = market_probability(m.dist, "moneyline", "home", None)["p"]
        sel, p = ("home", ph) if ph >= 0.5 else ("away", 1 - ph)
        out.append({"game_id": g.id, "player_id": None, "team_id": g.home_id if sel == "home" else g.away_id, "market": "moneyline",
                    "selection": sel, "line": None, "raw_prob": round(p, 4), "prob": round(p, 4), "mu": round(m.dist["mu"], 2), "l10": None})
    return sorted(out, key=lambda c: -c["prob"])


def record(db: Session) -> dict:
    """Store the current suggestions. Open suggestions of games that have not started are replaced, so what is
    finally tracked is the last set shown before tip-off."""
    now = utcnow()
    open_games = [g for g in db.scalars(select(Game.id).where(Game.status != "final", Game.tip_utc > now))]
    if open_games:
        db.execute(delete(Suggestion).where(Suggestion.status == "open", Suggestion.game_id.in_(open_games)))
    cands = generate(db)
    for c in cands:
        db.add(Suggestion(kind="moneyline" if c["market"] == "moneyline" else "prop", **c))
    db.flush()
    return {"recorded": len(cands), "props": sum(c["market"] != "moneyline" for c in cands)}


def settle(db: Session) -> dict:
    n = 0
    for s in db.scalars(select(Suggestion).where(Suggestion.status == "open")):
        g = db.get(Game, s.game_id)
        if g is None:
            s.status = "void"
            continue
        if g.status != "final":
            if g.tip_utc and g.tip_utc < utcnow() - timedelta(days=3):
                s.status = "void"  # postponed or never played
            continue
        if s.kind == "moneyline":
            s.actual = float(g.home_pts - g.away_pts)
            s.status = "won" if (s.actual > 0) == (s.selection == "home") else "lost"
        else:
            st = db.get(PlayerGameStat, (s.game_id, s.player_id))
            if st is None or not st.played or st.seconds <= 0:
                s.status = "void"  # did not play: bookmakers void the bet
            else:
                s.actual = float(st.pts + st.trb + st.ast if s.market == "pra" else getattr(st, STAT[s.market]))
                s.status = "won" if (s.actual > s.line) == (s.selection == "over") else "lost"
        s.settled_at = utcnow()
        n += 1
    db.flush()
    return {"settled": n}


def _summary(rows: list[Suggestion]) -> dict:
    done = [s for s in rows if s.status in ("won", "lost")]
    n, hits = len(done), sum(s.status == "won" for s in done)
    return {"n": n, "hits": hits, "hit_rate": round(hits / n, 4) if n else None,
            "expected": round(sum(s.prob for s in done) / n, 4) if n else None}


def stats(db: Session) -> dict:
    rows = list(db.scalars(select(Suggestion).where(Suggestion.status != "open").order_by(Suggestion.settled_at.desc())))
    props = [s for s in rows if s.kind == "prop"]
    decided = [s for s in props if s.status in ("won", "lost")][:40]
    names = {p.id: p.name for p in db.scalars(select(Player).where(Player.id.in_({s.player_id for s in decided if s.player_id})))}
    games = {g.id: g for g in db.scalars(select(Game).where(Game.id.in_({s.game_id for s in decided})))}
    prior = _prior(db)
    return {
        "props": _summary(props),
        "by_market": {m: _summary([s for s in props if s.market == m]) for m in PLAYER_MARKETS},
        "moneyline": _summary([s for s in rows if s.kind == "moneyline"]),
        "void": sum(s.status == "void" for s in rows),
        "recent": [{"name": names.get(s.player_id, s.player_id), "market": s.market, "sel": s.selection, "line": s.line, "actual": s.actual,
                    "status": s.status, "prob": s.prob, "date": str(games[s.game_id].date) if s.game_id in games else None}
                   for s in decided],
        "learning": learned_k(db),
        "backtest": {"period": prior.get("period"),
                     "markets": {m: {k: v[k] for k in ("n", "hits", "hit_rate", "expected", "k")} for m, v in prior.get("markets", {}).items()}},
    }
