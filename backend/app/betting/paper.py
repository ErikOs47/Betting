"""Paper betting: place, settle and evaluate simulated bets. No code path places real-money bets."""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import settings_store
from app.backtest.metrics import bet_summary
from app.betting import parlay as parlay_mod
from app.betting.odds_math import devig, expected_value, kelly_fraction
from app.betting.probability import PLAYER_MARKETS, PRED_MARKET, market_probability
from app.betting.risk import RiskState, limit_cap, size_bet
from app.betting.value import build_candidates, calibration_scores, current_quotes, latest_predictions, pair_key
from app.db import Bet, Game, LedgerEntry, OddsSnapshot, Player, PlayerGameStat, utcnow

ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")
STAT_COL = {"points": "pts", "rebounds": "trb", "assists": "ast", "threes": "fg3"}


class BetRejected(ValueError):
    pass


def balance(db: Session) -> float:
    last = db.scalars(select(LedgerEntry).order_by(LedgerEntry.id.desc()).limit(1)).first()
    if last is None:
        start = float(settings_store.get(db, "bankroll")["start"])
        db.add(LedgerEntry(delta=start, balance=start, reason="deposit"))
        db.flush()
        return start
    return float(last.balance)


def _ledger(db: Session, delta: float, reason: str, bet_id: int | None) -> None:
    db.add(LedgerEntry(delta=round(delta, 2), balance=round(balance(db) + delta, 2), reason=reason, bet_id=bet_id))
    db.flush()


def day_start_utc(now: datetime | None = None) -> datetime:
    local = (now or utcnow()).replace(tzinfo=UTC).astimezone(ET)
    return local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(UTC).replace(tzinfo=None)


def risk_state(db: Session) -> RiskState:
    bal = balance(db)
    start = day_start_utc()
    before = db.scalars(select(LedgerEntry).where(LedgerEntry.ts < start).order_by(LedgerEntry.id.desc()).limit(1)).first()
    first = db.scalars(select(LedgerEntry).order_by(LedgerEntry.id).limit(1)).first()
    day_start_bal = float(before.balance) if before else float(first.balance if first else bal)
    realized = float(db.scalar(select(func.coalesce(func.sum(Bet.pnl), 0.0)).where(Bet.settled_at >= start)) or 0.0)
    open_bets = list(db.scalars(select(Bet).where(Bet.status == "open")))
    exposure: dict[str, float] = {}
    for b in open_bets:
        for gid in b.game_ids:
            exposure[gid] = exposure.get(gid, 0.0) + b.stake
    # limits are measured against equity (cash + stakes still at risk), so placing a bet does not shrink the base
    return RiskState(bankroll=bal + sum(b.stake for b in open_bets), day_start_bankroll=day_start_bal, realized_pnl_today=realized,
                     open_stakes_today=sum(b.stake for b in open_bets if b.placed_at >= start), game_exposure=exposure,
                     kill_switch=bool(settings_store.get(db, "kill_switch")["active"]))


def describe_leg(db: Session, leg: dict) -> str:
    g = db.get(Game, leg["game_id"])
    match = f"{g.away_id}@{g.home_id}" if g else leg["game_id"]
    if leg["market"] in PLAYER_MARKETS:
        p = db.get(Player, leg["player_id"])
        return f"{p.name if p else leg['player_id']} {leg['selection']} {leg['line']} {leg['market']} ({match})"
    if leg["market"] == "moneyline":
        return f"{g.home_id if leg['selection'] == 'home' else g.away_id} moneyline ({match})"
    if leg["market"] == "spread":
        return f"{g.home_id if leg['selection'] == 'home' else g.away_id} {leg['line']:+g} ({match})"
    return f"{leg['selection']} {leg['line']} total points ({match})"


def _check_pregame(db: Session, game_ids: list[str]) -> None:
    for gid in game_ids:
        g = db.get(Game, gid)
        if g is None:
            raise BetRejected(f"unknown game {gid}")
        if g.status == "final" or (g.tip_utc is not None and g.tip_utc <= utcnow()):
            raise BetRejected(f"game {gid} has already started")


def quote_single(db: Session, leg: dict) -> dict:
    """Evaluate one leg. With a bookmaker it must match a stored current quote; otherwise `price` is a manual quote."""
    preds = latest_predictions(db, [leg["game_id"]])
    filters = settings_store.get(db, "filters")
    if leg.get("bookmaker") and leg["bookmaker"] != "manual":
        q = current_quotes(db, [leg["game_id"]])
        for c in build_candidates(q, preds, filters, calibration_scores(db)):
            same_line = leg.get("line") is None and c["line"] is None or (c["line"] is not None and leg.get("line") is not None and abs(c["line"] - leg["line"]) < 1e-9)
            if (c["market"], c["player_id"], c["selection"]) == (leg["market"], leg.get("player_id"), leg["selection"]) and same_line:
                if c["bookmaker"] != leg["bookmaker"]:
                    alt = [o for o in c["other_books"] if o["bookmaker"] == leg["bookmaker"]]
                    if not alt:
                        continue
                    c = {**c, "bookmaker": leg["bookmaker"], "price": alt[0]["price"],
                         "ev": round(expected_value(c["p_win"], alt[0]["price"], c["p_push"]), 5),
                         "kelly_full": round(kelly_fraction(c["p_win"], alt[0]["price"], c["p_push"]), 5)}
                return c
        raise BetRejected("no current bookmaker quote for this selection (odds may have moved)")
    pred = preds.get((leg["game_id"], PRED_MARKET.get(leg["market"], leg["market"]), leg.get("player_id")))
    if pred is None:
        raise BetRejected("no prediction available for this selection")
    if not leg.get("price") or leg["price"] <= 1:
        raise BetRejected("a decimal price > 1 is required")
    pr = market_probability(pred["dist"], leg["market"], leg["selection"], leg.get("line"))
    fair = None
    if leg.get("opposite_price"):
        fair = devig([leg["price"], leg["opposite_price"]])[0]
    return {**leg, "bookmaker": "manual", "model_prob": round(pr["p"], 5), "p_win": pr["win"], "p_push": pr["push"],
            "book_prob": fair, "consensus_prob": fair, "edge": None if fair is None else round(pr["p"] - fair, 5),
            "ev": round(expected_value(pr["win"], leg["price"], pr["push"]), 5), "fair_odds": round(1 / pr["p"], 3),
            "kelly_full": round(kelly_fraction(pr["win"], leg["price"], pr["push"]), 5), "confidence": None,
            "data_quality": pred["data_quality"], "quality_flags": pred["flags"], "prediction_id": pred["id"],
            "enabled": pred["enabled"], "passes": None, "fail_reasons": [] if pred["enabled"] else ["prediction disabled (data quality)"]}


def place_bet(db: Session, legs: list[dict], *, stake: float | None = None, offered_price: float | None = None) -> Bet:
    if not legs:
        raise BetRejected("no legs")
    game_ids = sorted({l["game_id"] for l in legs})
    _check_pregame(db, game_ids)
    state = risk_state(db)
    if state.kill_switch:
        raise BetRejected("kill switch active: betting is disabled")
    cfg = settings_store.get(db, "risk")
    if len(legs) == 1:
        c = quote_single(db, legs[0])
        if c.get("fail_reasons") and "prediction disabled (data quality)" in c["fail_reasons"]:
            raise BetRejected("prediction disabled because of insufficient data quality")
        kind, price, p, p_push, p_mkt = "single", c["price"], c["p_win"], c["p_push"], c.get("consensus_prob")
        ev, edge, conf, pred_ids = c["ev"], c.get("edge"), c.get("confidence"), [c["prediction_id"]]
        stored = [{k: c.get(k) for k in ("game_id", "market", "player_id", "selection", "line", "price", "bookmaker", "model_prob")}]
    else:
        preds = latest_predictions(db, game_ids)
        home_of = {g: db.get(Game, g).home_id for g in game_ids}
        r = parlay_mod.price_parlay(legs, preds, home_of, offered_price=offered_price)
        if not r["all_enabled"]:
            raise BetRejected("at least one leg is disabled because of insufficient data quality")
        if not r["price"]:
            raise BetRejected("a price is required (leg prices or an offered combined price)")
        kind, price, p, p_push, p_mkt = "parlay", r["price"], r["joint_prob"], 0.0, None
        ev, edge, conf = r["ev"], None, None
        pred_ids = [preds[(l["game_id"], PRED_MARKET.get(l["market"], l["market"]), l.get("player_id"))]["id"] for l in legs]
        stored = [{k: l.get(k) for k in ("game_id", "market", "player_id", "selection", "line", "price", "bookmaker", "model_prob")} for l in r["legs"]]
        stored[0]["custom_price"] = offered_price
    if ev <= 0:
        raise BetRejected(f"expected value is not positive ({ev:+.3f})")
    dec = size_bet(p_model=p, p_market=p_mkt, price=price, game_ids=game_ids, state=state, cfg=cfg, p_push=p_push)
    cash = balance(db)
    if stake is None:
        if dec.blocked:
            raise BetRejected("; ".join(dec.reasons))
        stake = min(dec.stake, cash)
    else:
        cap, why = limit_cap(game_ids=game_ids, state=state, cfg=cfg)
        if stake > cap + 1e-9:
            raise BetRejected(f"stake {stake:.2f} exceeds the limit of {cap:.2f} ({why[0]})")
        if stake <= 0 or stake > cash:
            raise BetRejected("stake must be positive and covered by the available balance")
    bet = Bet(kind=kind, game_ids=game_ids, legs=stored, description=" + ".join(describe_leg(db, l) for l in stored),
              stake=round(float(stake), 2), price=float(price), model_prob=float(p), fair_book_prob=p_mkt, ev=float(ev),
              edge=edge, confidence=conf, kelly_full=dec.kelly_full, prediction_ids=pred_ids)
    db.add(bet)
    db.flush()
    _ledger(db, -bet.stake, "stake", bet.id)
    return bet


# ------------------------------------------------------------------ settlement
def leg_result(db: Session, leg: dict) -> str | None:
    """won | lost | push | void, or None while the game is not final."""
    g = db.get(Game, leg["game_id"])
    if g is None:
        return "void"
    if g.status != "final":
        return None
    line, sel = leg.get("line"), leg["selection"]
    if leg["market"] in PLAYER_MARKETS:
        s = db.get(PlayerGameStat, (leg["game_id"], leg["player_id"]))
        if s is None or not s.played or s.seconds <= 0:
            return "void"  # player did not play: bookmakers void the prop
        x = s.pts + s.trb + s.ast if leg["market"] == "pra" else getattr(s, STAT_COL[leg["market"]])
    elif leg["market"] == "total":
        x = g.home_pts + g.away_pts
    else:
        margin = g.home_pts - g.away_pts
        if leg["market"] == "moneyline":
            return "won" if (margin > 0) == (sel == "home") else "lost"
        v = (margin if sel == "home" else -margin) + line
        return "won" if v > 0 else "lost" if v < 0 else "push"
    if x == line:
        return "push"
    return "won" if (x > line) == (sel == "over") else "lost"


def closing_line_value(db: Session, bet: Bet) -> tuple[float | None, float | None]:
    """(closing price, CLV). CLV = bet price x closing fair probability - 1, using the same bookmaker's last
    pre-tip quote at the same line (None when the line moved or no closing snapshot exists)."""
    if bet.kind != "single":
        return None, None
    leg = bet.legs[0]
    g = db.get(Game, leg["game_id"])
    if g is None or g.tip_utc is None or leg.get("bookmaker") in (None, "manual"):
        return None, None
    base = select(OddsSnapshot).where(OddsSnapshot.game_id == g.id, OddsSnapshot.bookmaker == leg["bookmaker"],
                                      OddsSnapshot.market == leg["market"], OddsSnapshot.fetched_at < g.tip_utc,
                                      OddsSnapshot.fetched_at > bet.placed_at)
    base = base.where(OddsSnapshot.player_id == leg["player_id"]) if leg.get("player_id") else base.where(OddsSnapshot.player_id.is_(None))
    rows = list(db.scalars(base.order_by(OddsSnapshot.fetched_at.desc()).limit(40)))
    if not rows:
        return None, None
    last = [r for r in rows if r.fetched_at == rows[0].fetched_at]
    pk = pair_key(leg["market"], leg["selection"], leg.get("line"))
    pair = [r for r in last if pair_key(r.market, r.selection, r.line) == pk]
    mine = [r for r in pair if r.selection == leg["selection"]]
    other = [r for r in pair if r.selection != leg["selection"]]
    if not mine or not other:
        return None, None
    fair = devig([mine[0].price, other[0].price])[0]
    return mine[0].price, round(bet.price * fair - 1.0, 5)


def settle_open_bets(db: Session) -> dict:
    n = 0
    for bet in db.scalars(select(Bet).where(Bet.status == "open")):
        results = [leg_result(db, l) for l in bet.legs]
        if any(r is None for r in results):
            continue
        if bet.kind == "single":
            r = results[0]
            payout = {"won": bet.stake * bet.price, "lost": 0.0, "push": bet.stake, "void": bet.stake}[r]
            bet.status = r
        else:
            if "lost" in results:
                bet.status, payout = "lost", 0.0
            else:
                live = [l for l, r in zip(bet.legs, results) if r == "won"]
                custom = bet.legs[0].get("custom_price")
                if len(live) == len(bet.legs):
                    bet.status, payout = "won", bet.stake * bet.price
                elif not live or custom or not all(l.get("price") for l in live):
                    bet.status, payout = "void", bet.stake  # cannot re-price a custom combination with removed legs
                else:
                    bet.status, payout = "won", bet.stake * float(np.prod([l["price"] for l in live]))
        bet.pnl = round(payout - bet.stake, 2)
        bet.settled_at = utcnow()
        bet.closing_price, bet.clv = closing_line_value(db, bet)
        if payout > 0:
            _ledger(db, payout, "payout" if bet.status == "won" else "refund", bet.id)
        n += 1
    db.flush()
    return {"settled": n}


def performance(db: Session) -> dict:
    start = float(settings_store.get(db, "bankroll")["start"])
    bets = list(db.scalars(select(Bet).where(Bet.status != "open").order_by(Bet.settled_at)))
    summ = bet_summary([b.stake for b in bets], [b.pnl or 0.0 for b in bets], start, clvs=[b.clv for b in bets])
    equity, cur = [], start
    for b in bets:
        cur += b.pnl or 0.0
        equity.append({"t": b.settled_at.isoformat(), "equity": round(cur, 2), "pnl": b.pnl})
    by_market: dict[str, dict] = {}
    for b in bets:
        k = b.legs[0]["market"] if b.kind == "single" else "parlay"
        d = by_market.setdefault(k, {"n": 0, "stake": 0.0, "pnl": 0.0, "won": 0})
        d["n"] += 1
        d["stake"] += b.stake
        d["pnl"] += b.pnl or 0.0
        d["won"] += b.status == "won"
    for d in by_market.values():
        d["yield"] = round(d["pnl"] / d["stake"], 4) if d["stake"] else None
        d["stake"], d["pnl"] = round(d["stake"], 2), round(d["pnl"], 2)
    open_bets = list(db.scalars(select(Bet).where(Bet.status == "open")))
    return {**summ, "start_bankroll": start, "bankroll": balance(db), "open_bets": len(open_bets),
            "open_stake": round(sum(b.stake for b in open_bets), 2), "equity": equity, "by_market": by_market,
            "won": sum(b.status == "won" for b in bets), "lost": sum(b.status == "lost" for b in bets),
            "push_void": sum(b.status in ("push", "void") for b in bets)}
