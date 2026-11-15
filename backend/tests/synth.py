"""Deterministic synthetic league used ONLY by automated tests (never by the application)."""
from __future__ import annotations

from datetime import date, datetime, timedelta

import numpy as np

from app.db import Game, GameInactive, Player, PlayerGameStat, Team, TeamGameStat

TEAMS = ["ATL", "BOS", "BRK", "CHI", "CLE", "DAL", "DEN", "DET"]


def populate(db, *, n_days: int = 60, start: date = date(2024, 10, 22), season: int = 2025, seed: int = 1,
             players_per_team: int = 9, scheduled_days: int = 0) -> list[str]:
    rng = np.random.default_rng(seed)
    for t in TEAMS:
        if db.get(Team, t) is None:
            db.add(Team(id=t, nba_abbr=t, name=t))
    skill = {t: rng.normal(0, 4) for t in TEAMS}
    roster = {}
    for t in TEAMS:
        roster[t] = []
        for i in range(players_per_team):
            pid = f"{t.lower()}p{i:02d}"
            if db.get(Player, pid) is None:
                db.add(Player(id=pid, name=f"{t} Player{i}", norm_name=f"{t.lower()}player{i}"))
            roster[t].append((pid, max(8.0, 36 - 3.2 * i), 0.3 + 0.5 * rng.random(), 0.1 + 0.2 * rng.random(), 0.05 + 0.2 * rng.random()))
    db.flush()
    ids = []
    for d in range(n_days + scheduled_days):
        day = start + timedelta(days=d)
        order = list(rng.permutation(TEAMS))
        for k in range(0, len(order), 2):
            home, away = order[k], order[k + 1]
            gid = f"{day:%Y%m%d}0{home}"
            final = d < n_days
            g = Game(id=gid, season=season, date=day, tip_utc=datetime(day.year, day.month, day.day, 23, 30), home_id=home, away_id=away,
                     status="final" if final else "scheduled")
            db.add(g)
            db.flush()  # parents first: PostgreSQL enforces foreign keys
            ids.append(gid)
            if not final:
                continue
            pts = {}
            for team, opp in ((home, away), (away, home)):
                tot = 0
                out_idx = int(rng.integers(0, players_per_team)) if rng.random() < 0.3 else -1
                for i, (pid, mins, rp, rr, ra) in enumerate(roster[team]):
                    if i == out_idx:
                        db.add(GameInactive(game_id=gid, player_id=pid, team_id=team))
                        continue
                    m = max(4.0, mins + rng.normal(0, 3))
                    p_, r_, a_ = (int(rng.poisson(max(0.1, m * x))) for x in (rp + skill[team] * 0.004, rr, ra))
                    tot += p_
                    db.add(PlayerGameStat(game_id=gid, player_id=pid, team_id=team, opp_id=opp, starter=i < 5, played=True,
                                          seconds=int(m * 60), pts=p_, trb=r_, ast=a_, fg3=int(rng.poisson(1.2)), fga=p_ // 2 + 2,
                                          fta=2, usg_pct=20.0, fg=p_ // 3, ft=1))
                pts[team] = tot + (3 if team == home else 0)
            if pts[home] == pts[away]:
                pts[home] += 1
            g.home_pts, g.away_pts = pts[home], pts[away]
            for team, opp in ((home, away), (away, home)):
                db.add(TeamGameStat(game_id=gid, team_id=team, opp_id=opp, is_home=team == home, pts=pts[team], fg=40, fga=88, fg3=12,
                                    fg3a=34, ft=18, fta=22, orb=10, drb=33, trb=43, ast=25, stl=7, blk=5, tov=13, pf=20,
                                    pace=98 + rng.normal(0, 2), off_rtg=pts[team] / 0.98, def_rtg=pts[opp] / 0.98, efg_pct=0.54,
                                    tov_pct=12.0, orb_pct=24.0, ft_rate=0.2))
    db.flush()
    return ids
