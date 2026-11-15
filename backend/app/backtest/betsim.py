"""Bet simulation on out-of-sample predictions and timestamped historical odds.

Only prices observed BEFORE tip-off are used: the first stored snapshot of a game is the decision price,
the last pre-tip snapshot is the closing price (for CLV). Stakes follow the same risk engine as live paper betting.
"""
from __future__ import annotations

import pandas as pd

from app.backtest.metrics import bet_summary
from app.betting.odds_math import devig
from app.betting.risk import RiskState, size_bet
from app.betting.value import build_candidates, pair_key

STAT = {"points": "pts", "rebounds": "trb", "assists": "ast", "threes": "fg3", "pra": "pra"}


def outcome(c: dict, actual: dict) -> str:
    """won | lost | push given realised values {margin,total} or {pts,trb,ast,fg3,pra}."""
    m, sel, line = c["market"], c["selection"], c["line"]
    if m == "moneyline":
        return "won" if (actual["margin"] > 0) == (sel == "home") else "lost"
    if m == "spread":
        v = (actual["margin"] if sel == "home" else -actual["margin"]) + line
        return "won" if v > 0 else "lost" if v < 0 else "push"
    x = actual["total"] if m == "total" else actual[STAT[m]]
    if x == line:
        return "push"
    return "won" if (x > line) == (sel == "over") else "lost"


def split_snapshots(odds: pd.DataFrame, tips: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(decision quotes, closing quotes): first and last pre-tip fetch per game/bookmaker/market/player."""
    o = odds.copy()
    o["tip"] = o["game_id"].map(tips)
    o = o[o["tip"].notna() & (o["fetched_at"] < o["tip"])]
    if o.empty:
        return o, o
    k = pd.Series(["|".join(map(str, t)) for t in zip(o["game_id"], o["bookmaker"], o["market"], o["player_name"])], index=o.index)
    first = o[o["fetched_at"] == o.groupby(k)["fetched_at"].transform("min")]
    last = o[o["fetched_at"] == o.groupby(k)["fetched_at"].transform("max")]
    return first, last


def closing_clv(c: dict, closing: pd.DataFrame) -> float | None:
    if closing.empty:
        return None
    m = closing[(closing["game_id"] == c["game_id"]) & (closing["bookmaker"] == c["bookmaker"]) & (closing["market"] == c["market"])
                & (closing["fetched_at"] > c["fetched_at"])]
    m = m[m["player_id"] == c["player_id"]] if c["player_id"] else m[m["player_id"].isna()]
    if m.empty:
        return None
    pk = pair_key(c["market"], c["selection"], c["line"])
    m = m[[pair_key(a, b, l) == pk for a, b, l in zip(m["market"], m["selection"], m["line"])]]
    mine, other = m[m["selection"] == c["selection"]], m[m["selection"] != c["selection"]]
    if mine.empty or other.empty:
        return None  # line moved: no like-for-like closing price
    return float(c["price"] * devig([float(mine["price"].iloc[0]), float(other["price"].iloc[0])])[0] - 1.0)


def simulate(*, preds_by_day: dict, actuals: dict, odds: pd.DataFrame, tips: dict, filters: dict, risk_cfg: dict,
             calib: dict, start_bankroll: float = 1000.0) -> dict:
    """preds_by_day: date -> {(game_id, pred market, player_id): pred dict}; actuals: same keys minus market -> values."""
    decision, closing = split_snapshots(odds, tips)
    bankroll = start_bankroll
    bets: list[dict] = []
    for day in sorted(preds_by_day):
        preds = preds_by_day[day]
        gids = {k[0] for k in preds}
        q = decision[decision["game_id"].isin(gids)]
        if q.empty:
            continue
        state = RiskState(bankroll=bankroll, day_start_bankroll=bankroll)
        day_bets = []
        for c in build_candidates(q, preds, filters, calib):
            if not c["passes"]:
                continue
            dec = size_bet(p_model=c["p_win"], p_market=c["consensus_prob"], price=c["price_net"], game_ids=[c["game_id"]],
                           state=state, cfg=risk_cfg, p_push=c["p_push"])
            if dec.blocked:
                continue
            state.open_stakes_today += dec.stake
            state.game_exposure[c["game_id"]] = state.game_exposure.get(c["game_id"], 0.0) + dec.stake
            day_bets.append((c, dec.stake))
        for c, stake in day_bets:
            res = outcome(c, actuals[(c["game_id"], c["player_id"])])
            pnl = stake * (c["price_net"] - 1) if res == "won" else -stake if res == "lost" else 0.0
            bankroll += pnl
            bets.append({"date": str(day), "game_id": c["game_id"], "market": c["market"], "player_id": c["player_id"],
                         "selection": c["selection"], "line": c["line"], "bookmaker": c["bookmaker"], "price": c["price"],
                         "model_prob": c["model_prob"], "edge": c["edge"], "ev": c["ev"], "confidence": c["confidence"],
                         "stake": stake, "result": res, "pnl": round(pnl, 2), "clv": closing_clv(c, closing),
                         "bankroll": round(bankroll, 2)})
    summ = bet_summary([b["stake"] for b in bets], [b["pnl"] for b in bets], start_bankroll, clvs=[b["clv"] for b in bets])
    by_market = {}
    for m in sorted({b["market"] for b in bets}):
        mb = [b for b in bets if b["market"] == m]
        by_market[m] = bet_summary([b["stake"] for b in mb], [b["pnl"] for b in mb], start_bankroll, clvs=[b["clv"] for b in mb])
    return {"summary": summ, "by_market": by_market, "bets": bets,
            "equity": [{"t": b["date"], "equity": b["bankroll"]} for b in bets]}
