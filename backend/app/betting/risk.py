"""Stake sizing and hard risk limits (paper betting only; there is no real-money execution path)."""
from __future__ import annotations

from dataclasses import dataclass, field

from app.betting.odds_math import kelly_fraction


@dataclass
class RiskState:
    bankroll: float
    day_start_bankroll: float
    realized_pnl_today: float = 0.0
    open_stakes_today: float = 0.0
    game_exposure: dict[str, float] = field(default_factory=dict)  # game_id -> open stake
    kill_switch: bool = False


@dataclass
class StakeDecision:
    stake: float
    kelly_full: float
    p_stake: float
    blocked: bool
    reasons: list[str]


def size_bet(*, p_model: float, p_market: float | None, price: float, game_ids: list[str], state: RiskState,
             cfg: dict, p_push: float = 0.0) -> StakeDecision:
    """Fractional Kelly on an uncertainty-shrunk probability, then caps:
    per bet, per game (all bets on that game share one budget, which also bounds correlated exposure)
    and worst-case daily loss (realised loss today + stakes still open today + this stake)."""
    reasons: list[str] = []
    if state.kill_switch:
        return StakeDecision(0.0, 0.0, p_model, True, ["kill switch active"])
    if state.bankroll <= 0:
        return StakeDecision(0.0, 0.0, p_model, True, ["bankroll exhausted"])
    # Model uncertainty: blend towards the market's fair probability before sizing.
    w = cfg["uncertainty_shrink"]
    p_stake = p_model if p_market is None else p_market + w * (p_model - p_market)
    f_full = kelly_fraction(p_stake, price, p_push)
    if f_full <= 0:
        return StakeDecision(0.0, f_full, p_stake, True, ["no edge after uncertainty shrinkage"])
    stake = cfg["kelly_fraction"] * f_full * state.bankroll

    cap_bet = cfg["max_bet_pct"] * state.bankroll
    if stake > cap_bet:
        stake, _ = cap_bet, reasons.append(f"capped at {cfg['max_bet_pct']:.1%} per bet")
    for gid in set(game_ids):
        room = cfg["max_game_pct"] * state.bankroll - state.game_exposure.get(gid, 0.0)
        if stake > room:
            stake, _ = max(0.0, room), reasons.append(f"capped by {cfg['max_game_pct']:.1%} per-game limit")
    day_budget = cfg["max_daily_loss_pct"] * state.day_start_bankroll
    used = max(0.0, -state.realized_pnl_today) + state.open_stakes_today
    if stake > day_budget - used:
        stake, _ = max(0.0, day_budget - used), reasons.append(f"capped by {cfg['max_daily_loss_pct']:.1%} daily loss limit")
    stake = min(stake, state.bankroll)
    stake = float(int(stake * 100) / 100.0)  # round down to cents
    if stake < cfg.get("min_stake", 0.0):
        return StakeDecision(0.0, f_full, p_stake, True, reasons + ["stake below minimum after limits"])
    return StakeDecision(stake, f_full, p_stake, False, reasons)


def limit_cap(*, game_ids: list[str], state: RiskState, cfg: dict) -> tuple[float, list[str]]:
    """Largest stake the hard limits allow right now, independent of Kelly (used for manually chosen stakes)."""
    if state.kill_switch:
        return 0.0, ["kill switch active"]
    caps = {f"{cfg['max_bet_pct']:.1%} per bet": cfg["max_bet_pct"] * state.bankroll,
            f"{cfg['max_daily_loss_pct']:.1%} daily loss limit": cfg["max_daily_loss_pct"] * state.day_start_bankroll
            - max(0.0, -state.realized_pnl_today) - state.open_stakes_today,
            "available bankroll": state.bankroll}
    for gid in set(game_ids):
        caps[f"{cfg['max_game_pct']:.1%} per-game limit"] = min(
            caps.get(f"{cfg['max_game_pct']:.1%} per-game limit", float("inf")),
            cfg["max_game_pct"] * state.bankroll - state.game_exposure.get(gid, 0.0))
    name = min(caps, key=caps.get)
    return max(0.0, float(int(caps[name] * 100) / 100.0)), [name]
