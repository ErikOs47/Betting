"""Leakage tests: features of a game must not change when that game's (or any later game's) outcome changes."""
from datetime import date

import numpy as np
import pandas as pd
from sqlalchemy import select, update

from app.db import Game, PlayerGameStat, TeamGameStat
from app.features.build import GAME_FEATURES, PLAYER_FEATURES, build_frames
from tests.synth import populate


def test_features_do_not_see_current_or_future_outcomes(db):
    populate(db, n_days=40)
    db.commit()
    f1 = build_frames(db, today=date(2025, 6, 1))
    cut = pd.Timestamp("2024-11-20")
    late = [g for g in db.scalars(select(Game.id).where(Game.date >= cut.date()))]
    db.execute(update(PlayerGameStat).where(PlayerGameStat.game_id.in_(late)).values(pts=PlayerGameStat.pts + 17, trb=99, ast=0, seconds=600))
    db.execute(update(TeamGameStat).where(TeamGameStat.game_id.in_(late)).values(pts=TeamGameStat.pts + 30, off_rtg=150.0, def_rtg=80.0, pace=120.0))
    db.execute(update(Game).where(Game.id.in_(late)).values(home_pts=Game.home_pts + 40))
    db.commit()
    f2 = build_frames(db, today=date(2025, 6, 1))

    key = ["game_id", "player_id"]
    a = f1.players[f1.players["date"] <= cut].sort_values(key).reset_index(drop=True)
    b = f2.players[f2.players["date"] <= cut].sort_values(key).reset_index(drop=True)
    assert len(a) == len(b) > 500
    pd.testing.assert_frame_equal(a[key + PLAYER_FEATURES], b[key + PLAYER_FEATURES])  # includes the first changed day itself
    ga = f1.games[f1.games["date"] <= cut].sort_values("id").reset_index(drop=True)
    gb = f2.games[f2.games["date"] <= cut].sort_values("id").reset_index(drop=True)
    pd.testing.assert_frame_equal(ga[["id"] + GAME_FEATURES], gb[["id"] + GAME_FEATURES])
    # sanity: the mutation is visible in later rows, so the test would catch leakage
    la, lb = f1.players[f1.players["date"] > cut + pd.Timedelta(days=3)], f2.players[f2.players["date"] > cut + pd.Timedelta(days=3)]
    assert not np.allclose(la["p_pts_l5"].to_numpy(), lb["p_pts_l5"].to_numpy(), equal_nan=True)


def test_rolling_window_values(db):
    populate(db, n_days=30)
    db.commit()
    f = build_frames(db, today=date(2025, 6, 1))
    p = f.players[f.players["player_id"] == "bosp00"].sort_values("date").reset_index(drop=True)
    assert pd.isna(p.loc[0, "p_pts_l5"]) and p.loc[0, "p_n_prior"] == 0
    assert p.loc[6, "p_pts_l5"] == p.loc[1:5, "pts"].mean()
    assert p.loc[6, "p_pts_szn"] == p.loc[0:5, "pts"].mean()
    assert p.loc[6, "p_gap_days"] == (p.loc[6, "date"] - p.loc[5, "date"]).days


def test_upcoming_rows_use_all_history_and_exclude_out_players(db):
    from app.db import InjuryEntry, InjuryReport
    populate(db, n_days=30, scheduled_days=1)
    day = date(2024, 11, 21)
    rep = InjuryReport(url="u", report_time_utc=pd.Timestamp("2024-11-21 15:00").to_pydatetime())
    db.add(rep)
    db.flush()
    db.add(InjuryEntry(report_id=rep.id, game_date=day, team_id="BOS", player_id="bosp00", player_name="x", status="Out", reason=""))
    db.add(InjuryEntry(report_id=rep.id, game_date=day, team_id="BOS", player_id="bosp01", player_name="y", status="Questionable", reason=""))
    db.commit()
    f = build_frames(db, today=day)
    up = f.players[f.players["upcoming"]]
    assert up["game_id"].nunique() == 4 and "bosp00" not in set(up["player_id"]) and "bosp01" in set(up["player_id"])
    row = up[up["player_id"] == "bosp01"].iloc[0]
    hist = f.players[(f.players["player_id"] == "bosp01") & ~f.players["upcoming"]].sort_values("date")
    assert row["p_pts_l5"] == hist["pts"].tail(5).mean()
    assert row["miss_min"] > 0  # the ruled-out starter's minutes count as missing for his team
    g = f.games[f.games["status"] != "final"]
    assert len(g) == 4 and g["margin"].isna().all() and g[GAME_FEATURES].notna().mean().mean() > 0.95
