"""SportsDataIO NBA client (optional, paid). Only used when NBI_SPORTSDATAIO_KEY is set.

Note: the free trial returns *scrambled* statistics, so it must never feed the models.
We only consume projected starting lineups / injury flags, which is what the paid feeds add over the free sources.
"""
from __future__ import annotations

import json
from datetime import date

from app.config import get_settings
from app.sources.http import SourceError, fetch

BASE = "https://api.sportsdata.io/v3/nba"


def enabled() -> bool:
    return bool(get_settings().sportsdataio_key)


def _call(path: str):
    key = get_settings().sportsdataio_key
    if not key:
        raise SourceError("NBI_SPORTSDATAIO_KEY is not configured")
    body, _ = fetch(BASE + path, headers={"Ocp-Apim-Subscription-Key": key}, timeout=30, retries=2)
    return json.loads(body)


def injuries() -> list[dict]:
    return [{"player_name": f"{p.get('FirstName', '')} {p.get('LastName', '')}".strip(), "team": p.get("Team"),
             "status": p.get("InjuryStatus"), "body_part": p.get("InjuryBodyPart")}
            for p in _call("/projections/json/InjuredPlayers")]


def starting_lineups(d: date) -> list[dict]:
    out = []
    for g in _call(f"/projections/json/StartingLineupsByDate/{d:%Y-%m-%d}"):
        for side in ("HomeLineup", "AwayLineup"):
            for p in g.get(side) or []:
                out.append({"game_key": g.get("GameID"), "team": g.get("HomeTeam") if side == "HomeLineup" else g.get("AwayTeam"),
                            "player_name": f"{p.get('FirstName', '')} {p.get('LastName', '')}".strip(),
                            "starting": bool(p.get("Starting")), "confirmed": bool(p.get("Confirmed"))})
    return out
