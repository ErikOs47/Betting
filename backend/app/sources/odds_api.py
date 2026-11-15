"""The Odds API v4 client (https://the-odds-api.com/liveapi/guides/v4/).

Credit costs (documented by the provider):
  * /events                              free
  * /odds (featured markets)             1 credit  x markets x regions
  * /events/{id}/odds (player props)     1 credit  x markets x regions (per event)
  * /historical/... odds                 10 credits x markets x regions (paid plans only)
The API key is sent as a query parameter and never leaves the backend.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from app.config import get_settings
from app.sources.http import SourceError, fetch

BASE = "https://api.the-odds-api.com/v4"
SPORT = "basketball_nba"
FEATURED = {"h2h": "moneyline", "spreads": "spread", "totals": "total"}
PROPS = {
    "player_points": "points",
    "player_rebounds": "rebounds",
    "player_assists": "assists",
    "player_points_rebounds_assists": "pra",
    "player_threes": "threes",
}
MARKET_NAMES = {**FEATURED, **PROPS}


class MissingKey(SourceError):
    pass


def _call(path: str, params: dict) -> tuple[object, dict]:
    s = get_settings()
    if not s.odds_api_key:
        raise MissingKey("NBI_ODDS_API_KEY is not configured")
    body, headers = fetch(BASE + path, params={"apiKey": s.odds_api_key, **params}, timeout=30, retries=2)
    h = headers or {}
    usage = {"remaining": _i(h.get("x-requests-remaining")), "used": _i(h.get("x-requests-used")),
             "last": _i(h.get("x-requests-last"))}
    return json.loads(body), usage


def _i(v) -> int | None:
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def _where() -> dict:
    s = get_settings()
    return {"bookmakers": s.odds_bookmakers} if s.odds_bookmakers else {"regions": s.odds_regions}


def n_regions() -> int:
    s = get_settings()
    if s.odds_bookmakers:
        return max(1, -(-len(s.odds_bookmakers.split(",")) // 10))  # every 10 bookmakers count as one region
    return len(s.odds_regions.split(","))


def estimate_cost(n_markets: int, n_events: int = 1, historical: bool = False) -> int:
    return n_markets * n_regions() * n_events * (10 if historical else 1)


def events() -> tuple[list[dict], dict]:
    return _call(f"/sports/{SPORT}/events", {"dateFormat": "iso"})  # type: ignore[return-value]


def featured_odds() -> tuple[list[dict], dict]:
    return _call(f"/sports/{SPORT}/odds", {**_where(), "markets": ",".join(FEATURED), "oddsFormat": "decimal",
                                           "dateFormat": "iso"})  # type: ignore[return-value]


def event_props(event_id: str, markets: list[str] | None = None) -> tuple[dict, dict]:
    return _call(f"/sports/{SPORT}/events/{event_id}/odds",
                 {**_where(), "markets": ",".join(markets or PROPS), "oddsFormat": "decimal", "dateFormat": "iso"})  # type: ignore[return-value]


def historical_featured(at: datetime) -> tuple[dict, dict]:
    return _call(f"/historical/sports/{SPORT}/odds",
                 {**_where(), "markets": ",".join(FEATURED), "oddsFormat": "decimal", "dateFormat": "iso",
                  "date": at.strftime("%Y-%m-%dT%H:%M:%SZ")})  # type: ignore[return-value]


def historical_events(at: datetime) -> tuple[dict, dict]:
    return _call(f"/historical/sports/{SPORT}/events", {"dateFormat": "iso", "date": at.strftime("%Y-%m-%dT%H:%M:%SZ")})  # type: ignore[return-value]


def historical_event_props(event_id: str, at: datetime, markets: list[str] | None = None) -> tuple[dict, dict]:
    return _call(f"/historical/sports/{SPORT}/events/{event_id}/odds",
                 {**_where(), "markets": ",".join(markets or PROPS), "oddsFormat": "decimal", "dateFormat": "iso",
                  "date": at.strftime("%Y-%m-%dT%H:%M:%SZ")})  # type: ignore[return-value]


def parse_ts(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc).replace(tzinfo=None)


def flatten_event(ev: dict) -> list[dict]:
    """One row per (bookmaker, market, selection, line[, player]) from an event payload."""
    rows: list[dict] = []
    home, away = ev["home_team"], ev["away_team"]
    for bm in ev.get("bookmakers", []):
        for mk in bm.get("markets", []):
            market = MARKET_NAMES.get(mk["key"])
            if market is None:
                continue
            upd = parse_ts(mk.get("last_update") or bm.get("last_update"))
            for o in mk.get("outcomes", []):
                price = o.get("price")
                if price is None or price <= 1.0:
                    continue
                if market == "moneyline":
                    sel, line, player = ("home" if o["name"] == home else "away" if o["name"] == away else None), None, None
                elif market == "spread":
                    sel, line, player = ("home" if o["name"] == home else "away" if o["name"] == away else None), o.get("point"), None
                elif market == "total":
                    sel, line, player = o["name"].lower(), o.get("point"), None
                else:
                    sel, line, player = o["name"].lower(), o.get("point"), o.get("description")
                    if not player:
                        continue
                if sel not in ("home", "away", "over", "under") or (market != "moneyline" and line is None):
                    continue
                rows.append({"event_id": ev["id"], "bookmaker": bm["key"], "market": market, "selection": sel,
                             "line": line, "price": float(price), "player_name": player, "book_updated_at": upd})
    return rows
