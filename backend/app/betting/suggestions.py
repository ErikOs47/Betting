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

from app.betting import doubles, periods
from app.betting.probability import PLAYER_MARKETS, market_probability
from app.db import Game, Player, PlayerGameStat, Prediction, Setting, Suggestion, utcnow

MIN_P, MAX_RAW_P = 0.57, 0.70
MIN_MINUTES, MIN_QUALITY = 20.0, 0.7
BLOCKING_FLAGS = {"returning_from_absence", "new_team", "questionable", "few_games"}
L10_KEY = {"points": "p_pts_l10", "rebounds": "p_trb_l10", "assists": "p_ast_l10", "threes": "p_fg3_l10", "pra": "p_pra_l10"}
STAT = {"points": "pts", "rebounds": "trb", "assists": "ast", "threes": "fg3"}
PRIOR_WEIGHT = 300.0
# what is suggested and tracked: markets the user's bookmaker actually offers for players
SUGGEST_MARKETS = ("points", "dd", "td")
MAX_PER_DAY = {"points": 12, "dd": 8, "td": 5}
DD_MIN = 0.25  # double-double "yes" is listed from this probability (bookmakers usually offer only "yes")
TD_MIN = 0.08  # triple-double "yes" is listed from this probability
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
        out[market] = _prior_entry(p, y)
    # double-double / triple-double: joint probability of points, rebounds, assists
    from app.betting import doubles
    d = op[rot]
    R = doubles.corr_matrix()
    pd_, pt_ = [], []
    for a in algos:
        mu = np.column_stack([d[f"{a}_mu_{m}"].to_numpy() for m in doubles.ORDER])
        al = np.column_stack([d[f"{a}_alpha_{m}"].to_numpy() for m in doubles.ORDER])
        x, t = doubles.double_probs(mu, al, R)
        pd_.append(x), pt_.append(t)
    p_dd, p_td = np.mean(pd_, axis=0), np.mean(pt_, axis=0)
    cnt = (d["pts"].to_numpy() >= 10).astype(int) + (d["trb"].to_numpy() >= 10) + (d["ast"].to_numpy() >= 10)
    m = p_dd >= DD_MIN
    out["dd"] = _prior_entry(p_dd[m], (cnt >= 2).astype(float)[m])
    m = p_td >= TD_MIN
    out["td"] = _prior_entry(p_td[m], (cnt == 3).astype(float)[m])
    out["dd_all"] = {"n": int(len(d)), "expected": round(float(p_dd.mean()), 4), "hit_rate": round(float((cnt >= 2).mean()), 4)}
    return out


def _prior_entry(p: np.ndarray, y: np.ndarray) -> dict:
    bins = np.clip((p * 100).astype(int), 0, 99)
    hist = {int(b): [int((bins == b).sum()), int(y[bins == b].sum())] for b in np.unique(bins)}
    return {"n": int(len(p)), "hits": int(y.sum()), "expected": round(float(p.mean()), 4) if len(p) else None,
            "hit_rate": round(float(y.mean()), 4) if len(p) else None, "k": round(fit_k(p, y), 3), "hist": hist}


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
    live: dict[str, list] = {m: [] for m in SUGGEST_MARKETS}
    for s in db.scalars(select(Suggestion).where(Suggestion.kind == "prop", Suggestion.status.in_(("won", "lost")))):
        if s.market in live:
            live[s.market].append((s.raw_prob, 1.0 if s.status == "won" else 0.0))
    out = {}
    for m in SUGGEST_MARKETS:
        ps, ys, ws = [], [], []
        pm = prior.get(m)
        if pm and pm.get("n"):
            scale = min(1.0, PRIOR_WEIGHT / pm["n"])
            for b, (n, hits) in pm["hist"].items():
                pc = (int(b) + 0.5) / 100
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
        base = {"game_id": gid, "player_id": pid, "team_id": pts.team_id, "line": None, "l10": None, "mu": None}
        avg = f.get(L10_KEY["points"])
        if avg is not None:
            line = math.floor(avg) + 0.5
            over = market_probability(pts.dist, "points", "over", line)["p"]
            sel, raw = ("over", over) if over >= 0.5 else ("under", 1 - over)
            p = adjust(raw, ks.get("points", 1.0))
            if raw <= MAX_RAW_P and p >= MIN_P:
                out.append({**base, "market": "points", "selection": sel, "line": line, "raw_prob": round(raw, 4), "prob": round(p, 4),
                            "mu": round(pts.dist["mu"], 2), "l10": round(avg, 1)})
        dbl = doubles.ensemble_from_dists({m: pr.dist for m, pr in item["preds"].items()})
        if dbl:
            p_dd, p_td = dbl
            p = adjust(p_dd, ks.get("dd", 1.0))
            if p >= DD_MIN:
                out.append({**base, "market": "dd", "selection": "yes", "raw_prob": round(p_dd, 4), "prob": round(p, 4)})
            p = adjust(p_td, ks.get("td", 1.0))
            if p >= TD_MIN:
                out.append({**base, "market": "td", "selection": "yes", "raw_prob": round(p_td, 4), "prob": round(p, 4)})
    # only the strongest per game day are shown to the user - and exactly those are tracked
    dates = {g.id: g.date for _, g in rows}
    kept, per_day = [], {}
    for c in sorted(out, key=lambda c: -c["prob"]):
        d = dates[c["game_id"]]
        key = (d, c["market"])
        if per_day.get(key, 0) < MAX_PER_DAY[c["market"]]:
            per_day[key] = per_day.get(key, 0) + 1
            kept.append(c)
    out = kept
    # team bets: the same picks the phone page lists under "Team" (tracked for information, no learning)
    P = periods.load(db)
    games = {g.id: g for _, g in rows}
    for g in games.values():
        mp = next((pr for pr, gg in rows if gg.id == g.id and pr.market == "margin" and pr.enabled), None)
        tp = next((pr for pr, gg in rows if gg.id == g.id and pr.market == "total" and pr.enabled), None)
        if mp is not None and tp is not None:
            out += team_candidates(g, mp.dist, tp.dist, P)
    return sorted(out, key=lambda c: -c["prob"])


def _jsround(x: float) -> int:
    return math.floor(x + 0.5)  # same as Math.round on the phone


def _ens(d: dict) -> float:
    return (d["mu"] + d["alt"]["mu"]) / 2 if d.get("alt") else d["mu"]


def team_candidates(g: Game, md: dict, td: dict, P: dict | None) -> list[dict]:
    """Mirror of the team section of the phone page (renderTips in phone/index.src.html)."""
    def c(market, sel, line, p, team=None):
        return {"game_id": g.id, "player_id": None, "team_id": team, "market": market, "selection": sel, "line": line,
                "raw_prob": round(p, 4), "prob": round(p, 4), "mu": None, "l10": None}
    side = lambda s: g.home_id if s == "home" else g.away_id if s == "away" else None
    out = []
    mu_m, mu_t = _ens(md), _ens(td)
    ph = market_probability(md, "moneyline", "home", None)["p"]
    fav = "home" if ph >= 0.5 else "away"
    out.append(c("moneyline", fav, None, max(ph, 1 - ph), side(fav)))
    line_fav = -max(0.5, math.floor(abs(mu_m)) - 0.5)
    out.append(c("spread", fav, line_fav, market_probability(md, "spread", fav, line_fav)["p"], side(fav)))
    lt = _jsround(mu_t) + 0.5
    po = market_probability(td, "total", "over", lt)["p"]
    out.append(c("total", "over" if po >= 0.5 else "under", lt, max(po, 1 - po)))
    if not P:
        return out
    pr = lambda key, sel, line=None: periods.derived_prob(P, key, sel, line, mu_m, mu_t)
    for s in ("away", "home"):
        key = f"team_{s}:fg"
        ln = _jsround(periods.derived_mean(P, key, mu_m, mu_t)) + 0.5
        po = pr(key, "over", ln)
        out.append(c(key, "over" if po >= 0.5 else "under", ln, max(po, 1 - po), side(s)))
    out.append(c("win:h1", fav, None, pr("win:h1", fav), side(fav)))
    hl = _jsround(periods.derived_mean(P, "total:h1", mu_m, mu_t)) + 0.5
    ho = pr("total:h1", "over", hl)
    out.append(c("total:h1", "over" if ho >= 0.5 else "under", hl, max(ho, 1 - ho)))
    qs = [pr(f"win:q{i}", fav) for i in range(1, 5)]
    best = int(np.argmax(qs))
    out.append(c(f"win:q{best + 1}", fav, None, qs[best], side(fav)))
    combos = sorted(((x, pr("htft", x)) for x in ("hh", "dh", "ah", "ha", "da", "aa")), key=lambda t: -t[1])
    out.append(c("htft", combos[0][0], None, combos[0][1]))
    return out


TEAM_CATS = {"moneyline": "ml", "spread": "spread", "total": "total", "team_home:fg": "teams", "team_away:fg": "teams",
             "win:h1": "h1", "total:h1": "h1", "htft": "htft", **{f"win:q{i}": "q" for i in range(1, 5)}}


TEAM_CAT_ORDER = ("ml", "spread", "total", "teams", "h1", "q", "htft")


def settle_team(s: Suggestion, g: Game) -> None:
    """Outcome of a team suggestion; void when the needed result is missing or the bet would be a push."""
    margin, total = g.home_pts - g.away_pts, g.home_pts + g.away_pts
    pv = periods.period_values(g.line_score or {})
    m = s.market
    if m == "moneyline":
        s.actual, won = float(margin), (margin > 0) == (s.selection == "home")
    elif m == "spread":
        own = margin if s.selection == "home" else -margin
        s.actual = float(own)
        if own + s.line == 0:
            s.status = "void"; return
        won = own + s.line > 0
    elif m == "total":
        s.actual, won = float(total), (total > s.line) == (s.selection == "over")
    elif m.startswith("team_"):
        pts = g.home_pts if m.startswith("team_home") else g.away_pts
        s.actual, won = float(pts), (pts > s.line) == (s.selection == "over")
    else:
        if pv is None:
            s.status = "void"; return
        if m.startswith("win:"):
            h, a = pv[m[4:]]
            s.actual = float(h - a)
            res = "home" if h > a else "away" if a > h else "draw"
            won = res == s.selection
        elif m == "total:h1":
            h, a = pv["h1"]
            s.actual, won = float(h + a), (h + a > s.line) == (s.selection == "over")
        elif m == "htft":
            h, a = pv["h1"]
            ht = "h" if h > a else "a" if a > h else "d"
            ft = "h" if margin > 0 else "a"
            s.actual = float(margin)
            won = ht + ft == s.selection
        else:
            s.status = "void"; return
    s.status = "won" if won else "lost"


def record(db: Session) -> dict:
    """Store the current suggestions. Open suggestions of games that have not started are replaced, so what is
    finally tracked is the last set shown before tip-off."""
    now = utcnow()
    open_games = [g for g in db.scalars(select(Game.id).where(Game.status != "final", Game.tip_utc > now))]
    if open_games:
        db.execute(delete(Suggestion).where(Suggestion.status == "open", Suggestion.game_id.in_(open_games)))
    cands = generate(db)
    for c in cands:
        db.add(Suggestion(kind="prop" if c["player_id"] else "team", **c))
    db.flush()
    return {"recorded": len(cands), "props": sum(bool(c["player_id"]) for c in cands)}


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
        if s.kind in ("team", "moneyline"):
            settle_team(s, g)
        else:
            st = db.get(PlayerGameStat, (s.game_id, s.player_id))
            if st is None or not st.played or st.seconds <= 0:
                s.status = "void"  # did not play: bookmakers void the bet
            elif s.market in ("dd", "td"):
                s.actual = float(doubles.actual_double(st.pts, st.trb, st.ast, st.stl, st.blk))
                hit = s.actual >= (2 if s.market == "dd" else 3)
                s.status = "won" if hit == (s.selection == "yes") else "lost"
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
    games = {g.id: g for g in db.scalars(select(Game).where(Game.id.in_({s.game_id for s in rows})))}
    names = {p.id: p.name for p in db.scalars(select(Player).where(Player.id.in_({s.player_id for s in rows if s.player_id})))}
    props = [s for s in rows if s.kind == "prop" and s.market in SUGGEST_MARKETS]
    team = [s for s in rows if s.kind in ("team", "moneyline") and s.market in TEAM_CATS]
    date_of = lambda s: games[s.game_id].date if s.game_id in games else None

    def item(s: Suggestion) -> dict:
        g = games.get(s.game_id)
        return {"name": names.get(s.player_id, s.player_id) if s.player_id else None, "market": s.market, "sel": s.selection,
                "line": s.line, "actual": s.actual, "status": s.status, "prob": s.prob, "date": str(g.date) if g else None,
                "home": g.home_id if g else None, "away": g.away_id if g else None,
                "score": f"{g.away_pts}:{g.home_pts}" if g and g.home_pts is not None else None}

    def block(sel: list[Suggestion]) -> dict:
        return {"props": _summary([s for s in sel if s in props_set]),
                "by_market": {m: _summary([s for s in sel if s in props_set and s.market == m]) for m in SUGGEST_MARKETS},
                "team": _summary([s for s in sel if s in team_set]),
                "by_cat": {c: _summary([s for s in sel if s in team_set and TEAM_CATS[s.market] == c]) for c in TEAM_CAT_ORDER}}

    props_set, team_set = set(props), set(team)
    decided = [s for s in props + team if s.status in ("won", "lost")]
    last_date = max((date_of(s) for s in decided if date_of(s)), default=None)
    last = [s for s in decided if date_of(s) == last_date]
    prior = _prior(db)
    return {
        **block(props + team),
        "markets": list(SUGGEST_MARKETS),
        "cats": list(TEAM_CAT_ORDER),
        "since": str(min(date_of(s) for s in decided if date_of(s))) if decided else None,
        "moneyline": _summary([s for s in team if s.market == "moneyline"]),
        "void": sum(s.status == "void" for s in rows),
        "last": {"date": str(last_date) if last_date else None, **block(last),
                 "items": [item(s) for s in sorted(last, key=lambda s: (s.player_id is None, -s.prob))]},
        "recent": [item(s) for s in decided if s in props_set][:40],
        "recent_team": [item(s) for s in decided if s in team_set][:40],
        "learning": learned_k(db),
        "backtest": {"period": prior.get("period"),
                     "markets": {m: {k: v.get(k) for k in ("n", "hits", "hit_rate", "expected", "k")} for m, v in prior.get("markets", {}).items()
                                 if m in SUGGEST_MARKETS},
                     "dd_all": prior.get("markets", {}).get("dd_all")},
    }
