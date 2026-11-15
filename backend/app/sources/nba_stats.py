"""stats.nba.com client (optional). The endpoint blocks most datacenter IPs, so it is a secondary source:
if it is unreachable the pipeline keeps running on Basketball Reference. Used for cross-checking box scores."""
from __future__ import annotations

import json

from app.sources.http import SourceError, fetch

HEADERS = {
    "Referer": "https://www.nba.com/", "Origin": "https://www.nba.com", "Accept": "application/json, text/plain, */*",
    "x-nba-stats-origin": "stats", "x-nba-stats-token": "true",
}


def player_game_logs(season: str, season_type: str = "Regular Season") -> list[dict]:
    """season like '2025-26'. Returns one dict per player-game."""
    body, _ = fetch("https://stats.nba.com/stats/leaguegamelog", headers=HEADERS, timeout=20, retries=1,
                    params={"Counter": 0, "Direction": "ASC", "LeagueID": "00", "PlayerOrTeam": "P", "Season": season,
                            "SeasonType": season_type, "Sorter": "DATE"})
    rs = json.loads(body)["resultSets"][0]
    if not rs["rowSet"]:
        raise SourceError(f"stats.nba.com returned no rows for {season}")
    return [dict(zip(rs["headers"], row)) for row in rs["rowSet"]]


def reachable() -> bool:
    try:
        fetch("https://stats.nba.com/stats/commonallplayers", headers=HEADERS, timeout=8, retries=1,
              params={"IsOnlyCurrentSeason": 1, "LeagueID": "00", "Season": "2025-26"})
        return True
    except Exception:
        return False
