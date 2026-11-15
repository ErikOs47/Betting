"""FastAPI application. All data-provider keys stay server-side; the frontend only talks to this API."""
from __future__ import annotations

import threading
from datetime import date, datetime, timedelta
from typing import Any

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import Session

from app import settings_store
from app.betting import paper
from app.betting import parlay as parlay_mod
from app.betting.probability import GAME_MARKETS, PLAYER_MARKETS, PRED_MARKET, market_probability
from app.betting.risk import size_bet
from app.betting.value import current_quotes, fair_lines, find_value_bets, latest_predictions
from app.config import get_settings
from app.db import (ApiUsage, BacktestRun, Bet, Game, InjuryEntry, InjuryReport, ModelVersion, OddsSnapshot, Player,
                    PlayerGameStat, Prediction, SourceStatus, TeamGameStat, get_session, init_db, utcnow)
from app.ingest.pipeline import credits_used_this_month
from app.ml import dist
from app.ml.predict import injury_age_hours, results_stale, today_et
from app.tasks import jobs
from app.teams import NAME_OF

app = FastAPI(title="NBA Betting Intelligence", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=get_settings().cors_origins.split(","), allow_methods=["*"], allow_headers=["*"])


@app.on_event("startup")
def _startup() -> None:
    init_db()
    if get_settings().scheduler == "internal":
        from app.tasks import scheduler
        scheduler.start()


def require_token(authorization: str | None = Header(default=None)) -> None:
    """Mutating endpoints require the bearer token when NBI_API_TOKEN is set."""
    token = get_settings().api_token
    if token and authorization != f"Bearer {token}":
        raise HTTPException(401, "invalid or missing API token")


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat() + "Z" if dt else None


# ------------------------------------------------------------------ status
STALE = {"basketball-reference": "stale_games_s", "nba-injury-report": "stale_injuries_s", "the-odds-api": "stale_odds_s"}


def source_health(db: Session) -> list[dict]:
    s = get_settings()
    rows = {r.source: r for r in db.scalars(select(SourceStatus))}
    out = []
    for name, attr in STALE.items():
        r = rows.get(name)
        age = (utcnow() - r.last_success).total_seconds() if r and r.last_success else None
        configured = True if name != "the-odds-api" else bool(s.odds_api_key)
        state = ("not_configured" if not configured else "never_run" if r is None or (r.last_success is None and r.last_error is None)
                 else "error" if r.last_error else "stale" if age is None or age > getattr(s, attr) else "ok")
        out.append({"source": name, "state": state, "configured": configured, "last_success": iso(r.last_success) if r else None,
                    "age_s": None if age is None else int(age), "stale_after_s": getattr(s, attr),
                    "last_error": r.last_error if r else None, "last_error_at": iso(r.last_error_at) if r else None,
                    "detail": (r.detail if r else {}) or {}})
    out.append({"source": "sportsdataio", "state": "ok" if s.sportsdataio_key else "not_configured", "configured": bool(s.sportsdataio_key),
                "last_success": None, "age_s": None, "stale_after_s": None, "last_error": None, "last_error_at": None, "detail": {}})
    return out


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "time": iso(utcnow())}


@app.get("/api/status")
def status(db: Session = Depends(get_session)) -> dict:
    rep = db.scalars(select(InjuryReport).order_by(InjuryReport.report_time_utc.desc()).limit(1)).first()
    last_final = db.scalar(select(func.max(Game.date)).where(Game.status == "final"))
    last_odds = db.scalar(select(func.max(OddsSnapshot.fetched_at)))
    s = get_settings()
    stale_games = results_stale(db)
    age = injury_age_hours(rep.report_time_utc) if rep else None
    warnings = []
    if stale_games:
        warnings.append(f"{stale_games} past games have no result yet: predictions are disabled until results are loaded.")
    if age is None or age * 3600 > s.stale_injuries_s:
        warnings.append("Injury report is missing or stale: data quality and confidence are reduced.")
    if not s.odds_api_key:
        warnings.append("No odds API key configured: enter bookmaker quotes manually in the game analysis; the value finder uses them.")
    return {
        "sources": source_health(db),
        "kill_switch": settings_store.get(db, "kill_switch"),
        "warnings": warnings,
        "data": {"final_games": db.scalar(select(func.count()).select_from(Game).where(Game.status == "final")),
                 "seasons": [r for r in db.scalars(select(Game.season).distinct().order_by(Game.season))],
                 "last_final_game_date": str(last_final) if last_final else None, "stale_result_games": stale_games,
                 "injury_report_utc": iso(rep.report_time_utc) if rep else None, "injury_report_age_h": None if age is None else round(age, 2),
                 "odds_snapshots": db.scalar(select(func.count()).select_from(OddsSnapshot)), "last_odds_snapshot": iso(last_odds),
                 "predictions_latest": db.scalar(select(func.count()).select_from(Prediction).where(Prediction.is_latest))},
        "odds_credits": {"used_this_month": credits_used_this_month(db), "budget": s.odds_monthly_credit_budget,
                         "remaining_reported": db.scalar(select(ApiUsage.remaining).where(ApiUsage.remaining.is_not(None)).order_by(ApiUsage.id.desc()).limit(1))},
        "models": [{"version": m.version, "family": m.family, "algo": m.algo, "train_end": str(m.train_end), "n_train": m.n_train}
                   for m in db.scalars(select(ModelVersion).where(ModelVersion.active))],
    }


# ------------------------------------------------------------------ games
def _game_row(g: Game, preds: dict) -> dict:
    m, t = preds.get((g.id, "margin", None)), preds.get((g.id, "total", None))
    out = {"id": g.id, "date": str(g.date), "tip_utc": iso(g.tip_utc), "status": g.status, "home": g.home_id, "away": g.away_id,
           "home_name": NAME_OF.get(g.home_id, g.home_id), "away_name": NAME_OF.get(g.away_id, g.away_id),
           "home_pts": g.home_pts, "away_pts": g.away_pts, "model": None}
    if m and t:
        pr = market_probability(m["dist"], "moneyline", "home", None)
        out["model"] = {"home_win_prob": round(pr["p"], 4), "margin": round(m["dist"]["mu"], 1), "total": round(t["dist"]["mu"], 1),
                        "sigma_margin": m["dist"]["sigma"], "sigma_total": t["dist"]["sigma"], "fair_home": round(1 / pr["p"], 2),
                        "fair_away": round(1 / (1 - pr["p"]), 2), "data_quality": m["data_quality"], "enabled": m["enabled"],
                        "flags": m["flags"]}
    return out


@app.get("/api/games")
def games(day: date | None = None, db: Session = Depends(get_session)) -> dict:
    day = day or today_et()
    rows = list(db.scalars(select(Game).where(Game.date == day).order_by(Game.tip_utc, Game.id)))
    preds = latest_predictions(db, [g.id for g in rows]) if rows else {}
    nxt = db.scalar(select(func.min(Game.date)).where(Game.date > day))
    prv = db.scalar(select(func.max(Game.date)).where(Game.date < day))
    return {"date": str(day), "prev_date": str(prv) if prv else None, "next_date": str(nxt) if nxt else None,
            "games": [_game_row(g, preds) for g in rows]}


def _team_form(db: Session, team: str, before: date, n: int = 10) -> dict:
    rows = db.execute(select(TeamGameStat, Game).join(Game, Game.id == TeamGameStat.game_id)
                      .where(TeamGameStat.team_id == team, Game.date < before).order_by(Game.date.desc()).limit(n)).all()
    if not rows:
        return {"team": team, "name": NAME_OF.get(team, team), "n": 0}
    avg = lambda k: round(sum(getattr(t, k) or 0 for t, _ in rows) / len(rows), 1)  # noqa: E731
    wins = sum((g.home_pts > g.away_pts) == t.is_home for t, g in rows)
    last = rows[0][1].date
    return {"team": team, "name": NAME_OF.get(team, team), "n": len(rows), "record": f"{wins}-{len(rows) - wins}",
            "off_rtg": avg("off_rtg"), "def_rtg": avg("def_rtg"), "net_rtg": round(avg("off_rtg") - avg("def_rtg"), 1),
            "pace": avg("pace"), "pts": avg("pts"), "rest_days": (before - last).days, "back_to_back": (before - last).days <= 1,
            "last_games": [{"date": str(g.date), "opp": t.opp_id, "home": t.is_home, "pts": t.pts,
                            "opp_pts": g.away_pts if t.is_home else g.home_pts} for t, g in rows[:5]]}


@app.get("/api/games/{game_id}")
def game_detail(game_id: str, db: Session = Depends(get_session)) -> dict:
    g = db.get(Game, game_id)
    if g is None:
        raise HTTPException(404, "game not found")
    preds = latest_predictions(db, [g.id])
    names = {p.id: p.name for p in db.scalars(select(Player).where(Player.id.in_([k[2] for k in preds if k[2]])))}
    players: dict[str, dict] = {}
    for (gid, market, pid), p in preds.items():
        if pid is None:
            continue
        row = players.setdefault(pid, {"player_id": pid, "name": names.get(pid, pid), "team": p["team_id"], "enabled": p["enabled"],
                                       "data_quality": p["data_quality"], "flags": p["flags"], "proj": {}})
        d = p["dist"]
        q = dist.nb_frozen(d["mu"], d["alpha"])
        row["proj"][market] = {"mean": round(d["mu"], 1), "p10": int(q.ppf(0.1)), "p50": int(q.ppf(0.5)), "p90": int(q.ppf(0.9)),
                               "alt_mean": round(d["alt"]["mu"], 1) if d.get("alt") else None}
    # expected minutes (L10) for ordering
    feats = {p.player_id: p.features for p in db.scalars(select(Prediction).where(Prediction.game_id == g.id, Prediction.is_latest,
                                                                                  Prediction.market == "points"))}
    for pid, row in players.items():
        f = feats.get(pid) or {}
        row["min_l10"], row["usg_l10"], row["pts_l5"], row["pts_l20"] = f.get("p_min_l10"), f.get("p_usg_pct_l10"), f.get("p_pts_l5"), f.get("p_pts_l20")
        row["starter_rate"] = f.get("p_starter_l5")
    rep = db.scalars(select(InjuryReport).order_by(InjuryReport.report_time_utc.desc()).limit(1)).first()
    inj = []
    if rep is not None:
        inj = [{"team": e.team_id, "player": e.player_name, "player_id": e.player_id, "status": e.status, "reason": e.reason}
               for e in db.scalars(select(InjuryEntry).where(InjuryEntry.report_id == rep.id, InjuryEntry.game_date == g.date,
                                                             InjuryEntry.team_id.in_([g.home_id, g.away_id])))]
    h2h = [{"id": x.id, "date": str(x.date), "home": x.home_id, "away": x.away_id, "home_pts": x.home_pts, "away_pts": x.away_pts}
           for x in db.scalars(select(Game).where(Game.status == "final", Game.date < g.date,
                                                  or_((Game.home_id == g.home_id) & (Game.away_id == g.away_id),
                                                      (Game.home_id == g.away_id) & (Game.away_id == g.home_id)))
                               .order_by(Game.date.desc()).limit(6))]
    box = []
    if g.status == "final":
        box = [{"player_id": s.player_id, "name": n, "team": s.team_id, "min": round(s.seconds / 60, 1), "pts": s.pts, "trb": s.trb,
                "ast": s.ast, "fg3": s.fg3, "starter": s.starter}
               for s, n in db.execute(select(PlayerGameStat, Player.name).join(Player, Player.id == PlayerGameStat.player_id)
                                      .where(PlayerGameStat.game_id == g.id, PlayerGameStat.played).order_by(PlayerGameStat.seconds.desc()))]
    mfeat = db.scalars(select(Prediction).where(Prediction.game_id == g.id, Prediction.is_latest, Prediction.market == "margin")).first()
    return {"game": _game_row(g, preds), "form": {"home": _team_form(db, g.home_id, g.date), "away": _team_form(db, g.away_id, g.date)},
            "players": sorted(players.values(), key=lambda r: -(r["min_l10"] or 0)), "injuries": inj,
            "injury_report_utc": iso(rep.report_time_utc) if rep else None, "head_to_head": h2h, "box": box,
            "model_inputs": {k: (mfeat.features or {}).get(k) for k in ("elo_diff", "h_miss_min", "a_miss_min", "h_rest_days", "a_rest_days",
                                                                         "h_t_net_rtg_l10", "a_t_net_rtg_l10", "x_pace_l10")} if mfeat else None}


@app.get("/api/players/{player_id}")
def player_detail(player_id: str, game_id: str | None = None, db: Session = Depends(get_session)) -> dict:
    p = db.get(Player, player_id)
    if p is None:
        raise HTTPException(404, "player not found")
    log = [{"date": str(g.date), "opp": s.opp_id, "min": round(s.seconds / 60, 1), "pts": s.pts, "trb": s.trb, "ast": s.ast,
            "fg3": s.fg3, "pra": s.pts + s.trb + s.ast, "starter": s.starter}
           for s, g in db.execute(select(PlayerGameStat, Game).join(Game, Game.id == PlayerGameStat.game_id)
                                  .where(PlayerGameStat.player_id == player_id, PlayerGameStat.played).order_by(Game.date.desc()).limit(20))]
    dists = {}
    if game_id:
        for pr in db.scalars(select(Prediction).where(Prediction.game_id == game_id, Prediction.player_id == player_id, Prediction.is_latest)):
            dists[pr.market] = {**dist.count_summary(pr.dist["mu"], pr.dist["alpha"]), "alt_mean": (pr.dist.get("alt") or {}).get("mu"),
                                "data_quality": pr.data_quality, "flags": pr.quality_flags, "enabled": pr.enabled,
                                "prediction_id": pr.id, "inputs_hash": pr.inputs_hash, "created_at": iso(pr.created_at)}
    return {"player_id": p.id, "name": p.name, "log": log, "distributions": dists}


# ------------------------------------------------------------------ value bets / evaluation
def _decorate(db: Session, cands: list[dict]) -> list[dict]:
    if not cands:
        return cands
    state, cfg = paper.risk_state(db), settings_store.get(db, "risk")
    gmap = {g.id: g for g in db.scalars(select(Game).where(Game.id.in_({c["game_id"] for c in cands})))}
    for c in cands:
        g = gmap[c["game_id"]]
        c["matchup"], c["tip_utc"] = f"{g.away_id} @ {g.home_id}", iso(g.tip_utc)
        c["fetched_at"] = iso(c["fetched_at"]) if isinstance(c.get("fetched_at"), datetime) else (c["fetched_at"].isoformat() + "Z" if c.get("fetched_at") is not None else None)
        d = size_bet(p_model=c["p_win"], p_market=c["consensus_prob"], price=c["price_net"], game_ids=[c["game_id"]], state=state,
                     cfg=cfg, p_push=c["p_push"])
        c["stake"] = {"amount": d.stake, "blocked": d.blocked, "reasons": d.reasons, "kelly_full": round(d.kelly_full, 5),
                      "pct_bankroll": round(d.stake / state.bankroll, 5) if state.bankroll else 0}
    return cands


@app.get("/api/value-bets")
def value_bets(all: bool = False, db: Session = Depends(get_session)) -> dict:  # noqa: A002
    cands = _decorate(db, find_value_bets(db, only_passing=not all))
    return {"filters": settings_store.get(db, "filters"), "kill_switch": settings_store.get(db, "kill_switch")["active"],
            "count_passing": sum(c["passes"] for c in cands), "candidates": cands[:500]}


class Leg(BaseModel):
    game_id: str
    market: str
    selection: str
    player_id: str | None = None
    line: float | None = None
    price: float | None = Field(default=None, gt=1)
    opposite_price: float | None = Field(default=None, gt=1)
    bookmaker: str | None = None

    def clean(self) -> dict:
        if self.market not in PLAYER_MARKETS + GAME_MARKETS:
            raise HTTPException(422, f"unknown market {self.market}")
        ok = ("over", "under") if self.market in PLAYER_MARKETS + ("total",) else ("home", "away")
        if self.selection not in ok:
            raise HTTPException(422, f"selection must be one of {ok}")
        if self.market in PLAYER_MARKETS and not self.player_id:
            raise HTTPException(422, "player_id required")
        if self.market != "moneyline" and self.line is None:
            raise HTTPException(422, "line required")
        return self.model_dump()


@app.post("/api/evaluate")
def evaluate(leg: Leg, db: Session = Depends(get_session)) -> dict:
    """Price one selection against a manually entered quote (works without any odds feed)."""
    try:
        c = paper.quote_single(db, {**leg.clean(), "bookmaker": leg.bookmaker or "manual"})
    except paper.BetRejected as e:
        raise HTTPException(422, str(e)) from e
    state, cfg = paper.risk_state(db), settings_store.get(db, "risk")
    d = size_bet(p_model=c["p_win"], p_market=c.get("consensus_prob"), price=c["price_net"], game_ids=[leg.game_id], state=state, cfg=cfg,
                 p_push=c["p_push"])
    c["stake"] = {"amount": d.stake, "blocked": d.blocked, "reasons": d.reasons, "kelly_full": round(d.kelly_full, 5)}
    return c


class ParlayReq(BaseModel):
    legs: list[Leg] = Field(min_length=1, max_length=12)
    offered_price: float | None = Field(default=None, gt=1)
    stake: float | None = Field(default=None, gt=0)


@app.post("/api/parlay/price")
def parlay_price(req: ParlayReq, db: Session = Depends(get_session)) -> dict:
    legs = [l.clean() for l in req.legs]
    gids = sorted({l["game_id"] for l in legs})
    home_of = {}
    for gid in gids:
        g = db.get(Game, gid)
        if g is None:
            raise HTTPException(404, f"game {gid} not found")
        home_of[gid] = g.home_id
    try:
        r = parlay_mod.price_parlay(legs, latest_predictions(db, gids), home_of, offered_price=req.offered_price,
                                    payout_tax=settings_store.get(db, "filters").get("payout_tax", 0.0))
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    for l in r["legs"]:
        l["description"] = paper.describe_leg(db, l)
    if r["price"] and r["ev"] is not None:
        d = size_bet(p_model=r["joint_prob"], p_market=None, price=r["price_net"], game_ids=gids, state=paper.risk_state(db),
                     cfg=settings_store.get(db, "risk"))
        r["stake"] = {"amount": d.stake, "blocked": d.blocked, "reasons": d.reasons, "kelly_full": round(d.kelly_full, 5)}
    return r


# ------------------------------------------------------------------ manual odds (works without any odds feed)
class ManualQuote(BaseModel):
    market: str
    player_id: str | None = None
    line: float | None = None  # spread: handicap of the HOME team (e.g. -5.5)
    price_a: float = Field(gt=1)  # over / home
    price_b: float = Field(gt=1)  # under / away


class ManualOdds(BaseModel):
    game_id: str
    bookmaker: str = Field(default="tipico", min_length=1, max_length=32, pattern=r"^[a-z0-9_\-]+$")
    quotes: list[ManualQuote] = Field(min_length=1, max_length=200)


@app.post("/api/odds/manual", dependencies=[Depends(require_token)])
def manual_odds(req: ManualOdds, db: Session = Depends(get_session)) -> dict:
    """Store hand-entered bookmaker quotes as timestamped snapshots (both sides, so the margin can be removed)."""
    g = db.get(Game, req.game_id)
    if g is None:
        raise HTTPException(404, "game not found")
    if g.status == "final" or (g.tip_utc is not None and g.tip_utc <= utcnow()):
        raise HTTPException(422, "game has already started; only pre-game quotes are stored")
    now, n = utcnow(), 0
    for q in req.quotes:
        if q.market not in PLAYER_MARKETS + GAME_MARKETS:
            raise HTTPException(422, f"unknown market {q.market}")
        player = None
        if q.market in PLAYER_MARKETS:
            player = db.get(Player, q.player_id or "")
            if player is None:
                raise HTTPException(422, "player_id required for player markets")
        if q.market != "moneyline" and q.line is None:
            raise HTTPException(422, f"line required for {q.market}")
        sides = ("over", "under") if q.market in PLAYER_MARKETS + ("total",) else ("home", "away")
        for sel, price in zip(sides, (q.price_a, q.price_b)):
            line = None if q.market == "moneyline" else (-q.line if (q.market == "spread" and sel == "away") else q.line)
            db.add(OddsSnapshot(fetched_at=now, source="manual", event_id=f"manual-{g.id}", game_id=g.id, bookmaker=req.bookmaker,
                                market=q.market, player_id=player.id if player else None, player_name=player.name if player else None,
                                selection=sel, line=line, price=price, book_updated_at=now))
            n += 1
    db.flush()
    return {"stored": n, "fetched_at": iso(now), "bookmaker": req.bookmaker}


@app.delete("/api/odds/manual", dependencies=[Depends(require_token)])
def delete_manual_odds(game_id: str, bookmaker: str, db: Session = Depends(get_session)) -> dict:
    """Remove hand-entered quotes of one bookmaker for a game (typo correction). API snapshots are never deleted."""
    res = db.execute(delete(OddsSnapshot).where(OddsSnapshot.game_id == game_id, OddsSnapshot.bookmaker == bookmaker,
                                                OddsSnapshot.source == "manual"))
    return {"deleted": res.rowcount}


@app.get("/api/odds/{game_id}")
def game_odds(game_id: str, db: Session = Depends(get_session)) -> dict:
    q = current_quotes(db, [game_id])
    rows = [] if q.empty else [
        {"bookmaker": r.bookmaker, "source": r.source, "market": r.market, "player_id": r.player_id if isinstance(r.player_id, str) else None,
         "player_name": r.player_name if isinstance(r.player_name, str) else None, "selection": r.selection,
         "line": None if r.line != r.line else r.line, "price": r.price, "fetched_at": iso(r.fetched_at.to_pydatetime()), "stale": bool(r.stale)}
        for r in q.sort_values(["market", "player_name", "bookmaker", "selection"]).itertuples()]
    return {"quotes": rows}


@app.get("/api/fair-lines")
def fair_lines_api(game_id: str | None = None, db: Session = Depends(get_session)) -> dict:
    f = settings_store.get(db, "filters")
    return {"min_edge": f["min_edge"], "payout_tax": f.get("payout_tax", 0.0), "lines": fair_lines(db, game_id)}


# ------------------------------------------------------------------ paper bets
def _bet(b: Bet) -> dict:
    return {"id": b.id, "placed_at": iso(b.placed_at), "kind": b.kind, "description": b.description, "legs": b.legs, "stake": b.stake,
            "price": b.price, "model_prob": b.model_prob, "ev": b.ev, "edge": b.edge, "confidence": b.confidence, "status": b.status,
            "pnl": b.pnl, "settled_at": iso(b.settled_at), "closing_price": b.closing_price, "clv": b.clv, "mode": b.mode}


@app.get("/api/bets")
def list_bets(status: str | None = None, limit: int = 200, db: Session = Depends(get_session)) -> dict:
    stmt = select(Bet).order_by(Bet.placed_at.desc()).limit(min(limit, 1000))
    if status:
        stmt = stmt.where(Bet.status == status)
    return {"bets": [_bet(b) for b in db.scalars(stmt)], "bankroll": paper.balance(db)}


@app.post("/api/bets", dependencies=[Depends(require_token)])
def create_bet(req: ParlayReq, db: Session = Depends(get_session)) -> dict:
    try:
        b = paper.place_bet(db, [l.clean() for l in req.legs], stake=req.stake, offered_price=req.offered_price)
    except (paper.BetRejected, ValueError) as e:
        raise HTTPException(422, str(e)) from e
    return _bet(b)


@app.post("/api/bets/settle", dependencies=[Depends(require_token)])
def settle_bets(db: Session = Depends(get_session)) -> dict:
    return paper.settle_open_bets(db)


@app.get("/api/performance")
def performance(db: Session = Depends(get_session)) -> dict:
    return paper.performance(db)


# ------------------------------------------------------------------ backtests & models
@app.get("/api/backtests")
def backtests(db: Session = Depends(get_session)) -> dict:
    runs = db.scalars(select(BacktestRun).order_by(BacktestRun.created_at.desc()).limit(30))
    return {"runs": [{"id": r.id, "name": r.name, "created_at": iso(r.created_at), "config": r.config, "period": r.metrics.get("period"),
                      "calibration_ece": r.metrics.get("calibration_ece"), "odds_available": r.metrics.get("odds", {}).get("available")}
                     for r in runs]}


@app.get("/api/backtests/{run_id}")
def backtest_detail(run_id: int, db: Session = Depends(get_session)) -> dict:
    r = db.get(BacktestRun, run_id)
    if r is None:
        raise HTTPException(404, "run not found")
    return {"id": r.id, "name": r.name, "created_at": iso(r.created_at), "config": r.config, "code_sha": r.code_sha, "metrics": r.metrics}


@app.get("/api/models")
def models(db: Session = Depends(get_session)) -> dict:
    return {"models": [{"id": m.id, "version": m.version, "family": m.family, "algo": m.algo, "active": m.active, "created_at": iso(m.created_at),
                        "train_start": str(m.train_start), "train_end": str(m.train_end), "n_train": m.n_train, "n_features": len(m.features),
                        "metrics": m.metrics, "params": m.params, "data_hash": m.data_hash, "code_sha": m.code_sha}
                       for m in db.scalars(select(ModelVersion).order_by(ModelVersion.created_at.desc()).limit(40))]}


# ------------------------------------------------------------------ settings & operations
@app.get("/api/settings")
def get_settings_api(db: Session = Depends(get_session)) -> dict:
    s = get_settings()
    return {**settings_store.get_all(db), "defaults": settings_store.DEFAULTS,
            "providers": {"odds_api_key_set": bool(s.odds_api_key), "sportsdataio_key_set": bool(s.sportsdataio_key),
                          "odds_regions": s.odds_regions, "odds_bookmakers": s.odds_bookmakers,
                          "odds_monthly_credit_budget": s.odds_monthly_credit_budget, "api_token_required": bool(s.api_token)}}


@app.put("/api/settings/{key}", dependencies=[Depends(require_token)])
def put_settings(key: str, value: dict[str, Any], db: Session = Depends(get_session)) -> dict:
    if key == "kill_switch":
        raise HTTPException(422, "use /api/kill-switch")
    try:
        return settings_store.put(db, key, value)
    except (KeyError, ValueError) as e:
        raise HTTPException(422, str(e)) from e


class KillReq(BaseModel):
    active: bool
    reason: str = ""


@app.post("/api/kill-switch", dependencies=[Depends(require_token)])
def kill_switch(req: KillReq, db: Session = Depends(get_session)) -> dict:
    """Immediately blocks all new bets and stake suggestions."""
    return settings_store.put(db, "kill_switch", {"active": req.active, "reason": req.reason[:200], "since": iso(utcnow()) if req.active else None})


_job_state: dict[str, dict] = {}


def _run_job(name: str) -> None:
    _job_state[name] = {"running": True, "started": iso(utcnow())}
    try:
        res = jobs.JOBS[name]()
        _job_state[name] = {"running": False, "finished": iso(utcnow()), "result": res}
    except Exception as e:  # noqa: BLE001
        _job_state[name] = {"running": False, "finished": iso(utcnow()), "result": {"ok": False, "error": str(e)[:300]}}


@app.post("/api/jobs/{name}", dependencies=[Depends(require_token)])
def start_job(name: str, bg: BackgroundTasks) -> dict:
    if name not in jobs.JOBS:
        raise HTTPException(404, f"unknown job; available: {sorted(jobs.JOBS)}")
    if _job_state.get(name, {}).get("running"):
        return {"started": False, "reason": "already running"}
    _job_state[name] = {"running": True, "started": iso(utcnow())}
    threading.Thread(target=_run_job, args=(name,), daemon=True).start()
    return {"started": True}


@app.get("/api/jobs")
def job_state() -> dict:
    from app.tasks import scheduler
    return {"jobs": _job_state, "available": sorted(jobs.JOBS),
            "scheduler": {"mode": get_settings().scheduler, **scheduler.state} if get_settings().scheduler == "internal" else {"mode": get_settings().scheduler}}


@app.get("/api/overview")
def overview(db: Session = Depends(get_session)) -> dict:
    day = today_et()
    todays = games(day, db)
    if not todays["games"] and todays["next_date"]:
        todays = games(date.fromisoformat(todays["next_date"]), db)
    perf = paper.performance(db)
    vb = find_value_bets(db, only_passing=True)
    return {"date": str(day), "games_date": todays["date"], "games": todays["games"],
            "bankroll": {k: perf[k] for k in ("bankroll", "start_bankroll", "profit", "roi", "yield", "hit_rate", "n_bets", "open_bets",
                                              "open_stake", "max_drawdown", "avg_clv")},
            "equity": perf["equity"][-200:], "value_bets": {"count": len(vb), "top": _decorate(db, vb[:5])},
            "status": status(db)}
