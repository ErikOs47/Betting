"""Leakage-free feature engineering.

Every feature of a row is computed only from games played strictly BEFORE that row's game
(`shift(1)` before every rolling window, pre-game Elo, as-of joins with exact matches disallowed).
Upcoming games are appended as placeholder rows so training and live inference share one code path.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import Game, GameInactive, InjuryEntry, InjuryReport, PlayerGameStat, TeamGameStat

WINDOWS = (5, 10, 20)
PLAYER_TARGETS = {"points": "pts", "rebounds": "trb", "assists": "ast", "threes": "fg3", "pra": "pra"}
P_ROLL = ["min", "pts", "trb", "ast", "fg3", "pra", "fga", "fta", "usg_pct", "pts36", "trb36", "ast36"]
T_ROLL = ["off_rtg", "def_rtg", "net_rtg", "pace", "pts", "opp_pts", "efg_pct", "tov_pct", "orb_pct", "ft_rate",
          "opp_trb", "opp_ast", "opp_fg3", "opp_efg"]
OUT_STATUSES = ("Out", "Doubtful")
ELO_K, ELO_HOME, ELO_CARRY = 20.0, 70.0, 0.75


@dataclass
class Frames:
    players: pd.DataFrame  # one row per player-game (played) + upcoming candidates
    games: pd.DataFrame  # one row per game (home perspective)
    teams: pd.DataFrame  # one row per team-game
    data_hash: str
    injury_report_utc: pd.Timestamp | None


# ------------------------------------------------------------------ loading
def _read(db: Session, stmt) -> pd.DataFrame:
    res = db.execute(stmt)
    return pd.DataFrame(res.fetchall(), columns=list(res.keys()))


def load_raw(db: Session, upto: date | None = None) -> dict[str, pd.DataFrame]:
    g = _read(db, select(Game.__table__))
    g["date"] = pd.to_datetime(g["date"])
    if upto is not None:
        g = g[g["date"] <= pd.Timestamp(upto)]
    tg = _read(db, select(TeamGameStat.__table__))
    pg = _read(db, select(PlayerGameStat.__table__))
    ina = _read(db, select(GameInactive.__table__))
    ids = set(g["id"])
    return {"games": g, "team_stats": tg[tg["game_id"].isin(ids)], "player_stats": pg[pg["game_id"].isin(ids)],
            "inactives": ina[ina["game_id"].isin(ids)]}


def latest_injuries(db: Session) -> tuple[pd.DataFrame, pd.Timestamp | None]:
    rep = db.scalars(select(InjuryReport).order_by(InjuryReport.report_time_utc.desc()).limit(1)).first()
    if rep is None:
        return pd.DataFrame(columns=["game_date", "team_id", "player_id", "player_name", "status", "reason"]), None
    df = _read(db, select(InjuryEntry.__table__).where(InjuryEntry.report_id == rep.id))
    df["game_date"] = pd.to_datetime(df["game_date"])
    return df, pd.Timestamp(rep.report_time_utc)


# ------------------------------------------------------------------ helpers
def _roll(df: pd.DataFrame, key: str | list[str], cols: list[str], windows=WINDOWS, prefix: str = "") -> pd.DataFrame:
    """Shifted rolling means: value at row i uses rows i-n..i-1 of the same group."""
    out = {}
    grp = df.groupby(key, sort=False)
    shifted = grp[cols].shift(1)
    gkey = [df[k] for k in ([key] if isinstance(key, str) else key)]
    sg = shifted.groupby(gkey, sort=False)
    for n in windows:
        r = sg.rolling(n, min_periods=1).mean()
        r.index = r.index.droplevel(list(range(len(gkey))))
        r = r.reindex(df.index)
        for c in cols:
            out[f"{prefix}{c}_l{n}"] = r[c]
    return pd.DataFrame(out, index=df.index)


def _season_to_date(df: pd.DataFrame, keys: list[str], cols: list[str], prefix: str = "") -> pd.DataFrame:
    gkey = [df[k] for k in keys]
    shifted = df.groupby(keys, sort=False)[cols].shift(1)
    r = shifted.groupby(gkey, sort=False).expanding(min_periods=1).mean()
    r.index = r.index.droplevel(list(range(len(keys))))
    r = r.reindex(df.index)
    return r.rename(columns={c: f"{prefix}{c}_szn" for c in cols})


def compute_elo(games: pd.DataFrame) -> pd.DataFrame:
    """Pre-game Elo per game (margin-of-victory adjusted, regressed to the mean between seasons)."""
    elo: dict[str, float] = {}
    season_of: dict[str, int] = {}
    h_out, a_out = np.empty(len(games)), np.empty(len(games))
    for i, r in enumerate(games.itertuples(index=False)):
        for t in (r.home_id, r.away_id):
            if t not in elo:
                elo[t] = 1500.0
            elif season_of.get(t) != r.season:
                elo[t] = 1500.0 + ELO_CARRY * (elo[t] - 1500.0)
            season_of[t] = r.season
        eh, ea = elo[r.home_id], elo[r.away_id]
        h_out[i], a_out[i] = eh, ea
        if r.status == "final" and pd.notna(r.home_pts):
            margin = r.home_pts - r.away_pts
            exp = 1.0 / (1.0 + 10 ** (-(eh + ELO_HOME - ea) / 400.0))
            win = 1.0 if margin > 0 else 0.0
            mov = np.log(abs(margin) + 1.0) * 2.2 / ((eh + ELO_HOME - ea) * (1 if margin > 0 else -1) * 0.001 + 2.2)
            delta = ELO_K * mov * (win - exp)
            elo[r.home_id] += delta
            elo[r.away_id] -= delta
    return pd.DataFrame({"h_elo": h_out, "a_elo": a_out}, index=games.index)


# ------------------------------------------------------------------ main builder
def build_frames(db: Session, *, upto: date | None = None, upcoming_days: int = 2, today: date | None = None) -> Frames:
    raw = load_raw(db, upto)
    games = raw["games"].sort_values(["date", "id"]).reset_index(drop=True)
    today_ts = pd.Timestamp(today or date.today())
    # drop far-future schedule rows; stale "scheduled" rows in the past are unusable (missing results)
    horizon = today_ts + pd.Timedelta(days=upcoming_days)
    games = games[(games["status"] == "final") | ((games["date"] >= today_ts - pd.Timedelta(days=1)) & (games["date"] <= horizon))]
    games = games.reset_index(drop=True)
    games = pd.concat([games, compute_elo(games)], axis=1)
    inj, inj_ts = latest_injuries(db)

    # ---------------- team-game frame
    side = []
    for me, opp, home in (("home", "away", True), ("away", "home", False)):
        s = games[["id", "date", "season", "status", f"{me}_id", f"{opp}_id", f"{me[0]}_elo", f"{opp[0]}_elo"]].copy()
        s.columns = ["game_id", "date", "season", "status", "team_id", "opp_id", "elo", "opp_elo"]
        s["is_home"] = home
        side.append(s)
    teams = pd.concat(side, ignore_index=True)
    ts = raw["team_stats"].drop(columns=["opp_id", "is_home"])
    teams = teams.merge(ts, on=["game_id", "team_id"], how="left")
    opp = ts[["game_id", "team_id", "pts", "trb", "ast", "fg3", "efg_pct"]].rename(
        columns={"team_id": "opp_id", "pts": "opp_pts", "trb": "opp_trb", "ast": "opp_ast", "fg3": "opp_fg3", "efg_pct": "opp_efg"})
    teams = teams.merge(opp, on=["game_id", "opp_id"], how="left")
    teams["net_rtg"] = teams["off_rtg"] - teams["def_rtg"]
    teams = teams.sort_values(["team_id", "date", "game_id"]).reset_index(drop=True)
    teams = pd.concat([teams, _roll(teams, "team_id", T_ROLL, prefix="t_"),
                       _season_to_date(teams, ["team_id", "season"], ["off_rtg", "def_rtg", "net_rtg", "pace"], prefix="t_")], axis=1)
    prev = teams.groupby("team_id")["date"].shift(1)
    teams["rest_days"] = (teams["date"] - prev).dt.days.clip(upper=10).fillna(10)
    teams["b2b"] = (teams["rest_days"] <= 1).astype(int)
    teams["team_game_no"] = teams.groupby(["team_id", "season"]).cumcount()
    d7 = teams.set_index("date").groupby("team_id")["game_id"].rolling("7D", closed="left").count()
    teams["games_7d"] = d7.to_numpy()
    teams["games_7d"] = teams["games_7d"].fillna(0)

    # ---------------- player-game frame (played rows only)
    ps = raw["player_stats"]
    meta = games[["id", "date", "season", "home_id"]].rename(columns={"id": "game_id"})
    pl = ps[ps["played"] & (ps["seconds"] > 0)].merge(meta, on="game_id")
    pl["is_home"] = (pl["team_id"] == pl["home_id"]).astype(int)
    pl["min"] = pl["seconds"] / 60.0
    pl["pra"] = pl["pts"] + pl["trb"] + pl["ast"]
    for c in ("pts", "trb", "ast"):
        pl[f"{c}36"] = np.where(pl["min"] >= 5, pl[c] / pl["min"].clip(lower=1) * 36.0, np.nan)
    pl["starter"] = pl["starter"].astype(int)
    pl["upcoming"] = False

    # ---------------- who is out for each team-game
    out_hist = pd.concat([
        raw["inactives"][["game_id", "team_id", "player_id"]],
        ps[(~ps["played"]) & (ps["dnp_reason"].fillna("") != "Did Not Play")][["game_id", "team_id", "player_id"]],
    ], ignore_index=True).merge(meta[["game_id", "date"]], on="game_id")
    up_games = games[games["status"] != "final"]
    up_tg = teams[teams["status"] != "final"][["game_id", "team_id", "date"]]
    out_up = pd.DataFrame(columns=["game_id", "team_id", "player_id", "date"])
    if len(up_tg) and len(inj):
        o = inj[inj["status"].isin(OUT_STATUSES) & inj["player_id"].notna()]
        out_up = up_tg.merge(o[["game_date", "team_id", "player_id"]], left_on=["date", "team_id"],
                             right_on=["game_date", "team_id"]).drop(columns="game_date")
    outs = pd.concat([out_hist, out_up] if len(out_up) else [out_hist], ignore_index=True).drop_duplicates(["game_id", "player_id"])
    outs["date"] = pd.to_datetime(outs["date"])

    # ---------------- upcoming candidates: recent rotation of each team, minus players ruled out
    if len(up_tg):
        last_team = pl.sort_values("date").groupby("player_id").tail(1)[["player_id", "team_id", "date"]]
        recent = pl.merge(up_tg.rename(columns={"date": "gdate", "game_id": "up_game"}), on="team_id")
        recent = recent[(recent["date"] < recent["gdate"]) & (recent["date"] >= recent["gdate"] - pd.Timedelta(days=21))]
        cand = recent.groupby(["up_game", "team_id", "player_id", "gdate"], as_index=False).size()
        cand = cand.merge(last_team.rename(columns={"team_id": "cur_team", "date": "last_date"}), on="player_id")
        cand = cand[cand["cur_team"] == cand["team_id"]]  # traded players only count for their current team
        cand = cand.merge(outs[["game_id", "player_id"]].assign(out=1), left_on=["up_game", "player_id"],
                          right_on=["game_id", "player_id"], how="left")
        cand = cand[cand["out"].isna()]
        g2 = up_games.set_index("id")
        up_rows = pd.DataFrame({
            "game_id": cand["up_game"].to_numpy(), "player_id": cand["player_id"].to_numpy(), "team_id": cand["team_id"].to_numpy(),
            "date": cand["gdate"].to_numpy(), "upcoming": True})
        up_rows["season"] = g2.loc[up_rows["game_id"], "season"].to_numpy()
        home = g2.loc[up_rows["game_id"], "home_id"].to_numpy()
        away = g2.loc[up_rows["game_id"], "away_id"].to_numpy()
        up_rows["is_home"] = (up_rows["team_id"].to_numpy() == home).astype(int)
        up_rows["opp_id"] = np.where(up_rows["is_home"] == 1, away, home)
        pl = pd.concat([pl, up_rows], ignore_index=True)

    pl = pl.sort_values(["player_id", "date", "game_id"]).reset_index(drop=True)
    feats = [_roll(pl, "player_id", P_ROLL, prefix="p_"),
             _season_to_date(pl, ["player_id", "season"], ["min", "pts", "trb", "ast", "fg3", "pra", "usg_pct"], prefix="p_")]
    g = pl.groupby("player_id", sort=False)
    sh = g[["min", "pts", "trb", "ast", "pra", "starter"]].shift(1)
    sg = sh.groupby(pl["player_id"], sort=False)
    sd = sg[["min", "pts", "trb", "ast", "pra"]].rolling(10, min_periods=3).std()
    sd.index = sd.index.droplevel(0)
    feats.append(sd.reindex(pl.index).add_prefix("p_sd10_"))
    st = sg["starter"].rolling(5, min_periods=1).mean()
    st.index = st.index.droplevel(0)
    feats.append(st.reindex(pl.index).rename("p_starter_l5").to_frame())
    ew = sh[["min", "pts", "trb", "ast", "pra"]].groupby(pl["player_id"], sort=False).ewm(halflife=6, min_periods=1).mean()
    ew.index = ew.index.droplevel(0)
    feats.append(ew.reindex(pl.index).add_prefix("p_ewm_"))
    pl = pd.concat([pl, *feats], axis=1)
    pl["p_started_last"] = sh["starter"]
    pl["p_n_prior"] = g.cumcount()
    pl["p_n_prior_szn"] = pl.groupby(["player_id", "season"]).cumcount()
    prev_date = g["date"].shift(1)
    pl["p_gap_days"] = (pl["date"] - prev_date).dt.days.clip(upper=60)
    pl["p_b2b"] = (pl["p_gap_days"] <= 1).astype(int)
    pl["p_min_trend"] = pl["p_min_l5"] - pl["p_min_l20"]
    pl["p_new_team"] = (g["team_id"].shift(1) != pl["team_id"]).astype(int).where(prev_date.notna(), 0)

    # ---------------- absences: value of missing players measured BEFORE the game
    post = pl[~pl["upcoming"]].copy()
    pg2 = post.groupby("player_id", sort=False)
    for c in ("min", "pts", "ast"):
        r = pg2[c].rolling(10, min_periods=1).mean()
        r.index = r.index.droplevel(0)
        post[f"post_{c}"] = r
    post = post[["player_id", "date", "post_min", "post_pts", "post_ast"]].sort_values("date")
    if len(outs):
        o = outs.sort_values("date")
        o["player_id"] = o["player_id"].astype(str)
        post["last_played"] = post["date"]
        o = pd.merge_asof(o, post.assign(player_id=post["player_id"].astype(str)), on="date", by="player_id",
                          allow_exact_matches=False)
        o = o[o["post_min"].notna()]
        days = (o["date"] - o["last_played"]).dt.days
        o = o[days <= 200]  # ignore players who have not appeared this season
        fresh = (days[days <= 200] <= 21).astype(float)
        o["fresh_min"], o["fresh_pts"] = o["post_min"] * fresh, o["post_pts"] * fresh
        miss = o.groupby(["game_id", "team_id"]).agg(miss_min=("post_min", "sum"), miss_pts=("post_pts", "sum"),
                                                     miss_ast=("post_ast", "sum"), miss_min_fresh=("fresh_min", "sum"),
                                                     miss_pts_fresh=("fresh_pts", "sum"), n_out=("player_id", "count")).reset_index()
        teams = teams.merge(miss, on=["game_id", "team_id"], how="left")
    for c in ("miss_min", "miss_pts", "miss_ast", "miss_min_fresh", "miss_pts_fresh", "n_out"):
        if c not in teams:
            teams[c] = 0.0
        teams[c] = teams[c].fillna(0.0)

    # ---------------- attach team / opponent context to players
    tcols = ["game_id", "team_id", "elo", "opp_elo", "rest_days", "b2b", "games_7d", "team_game_no",
             "miss_min", "miss_pts", "miss_ast", "miss_min_fresh", "miss_pts_fresh", "n_out",
             *[f"t_{c}_l10" for c in ("off_rtg", "def_rtg", "pace", "pts")], "t_off_rtg_szn", "t_pace_szn"]
    pl = pl.merge(teams[tcols].rename(columns={"rest_days": "t_rest_days", "b2b": "t_b2b", "games_7d": "t_games_7d"}),
                  on=["game_id", "team_id"], how="left")
    ocols = {"team_id": "opp_id", "t_def_rtg_l10": "o_def_rtg_l10", "t_def_rtg_l20": "o_def_rtg_l20", "t_def_rtg_szn": "o_def_rtg_szn",
             "t_pace_l10": "o_pace_l10", "t_pace_szn": "o_pace_szn", "t_opp_pts_l10": "o_allowed_pts_l10",
             "t_opp_trb_l10": "o_allowed_trb_l10", "t_opp_ast_l10": "o_allowed_ast_l10", "t_opp_fg3_l10": "o_allowed_fg3_l10",
             "t_opp_trb_l20": "o_allowed_trb_l20", "t_opp_ast_l20": "o_allowed_ast_l20", "t_opp_fg3_l20": "o_allowed_fg3_l20",
             "t_opp_efg_l20": "o_allowed_efg_l20", "t_orb_pct_l20": "o_orb_pct_l20", "b2b": "o_b2b", "miss_min": "o_miss_min"}
    pl = pl.merge(teams[["game_id", *ocols]].rename(columns=ocols), on=["game_id", "opp_id"], how="left")
    pl["elo_diff"] = pl["elo"] - pl["opp_elo"] + np.where(pl["is_home"] == 1, ELO_HOME, -ELO_HOME)
    pl["exp_pace"] = (pl["t_pace_l10"] + pl["o_pace_l10"]) / 2
    # games the player missed since his previous appearance for the same team
    prev_no = pl.groupby(["player_id", "team_id", "season"])["team_game_no"].shift(1)
    pl["p_missed_since"] = (pl["team_game_no"] - prev_no - 1).clip(lower=0, upper=20).fillna(0)
    pl["p_share_min"] = pl["p_min_l10"] / 240.0
    pl["p_boost"] = pl["miss_min_fresh"] * pl["p_share_min"]

    # ---------------- game frame (home perspective)
    keep = ["elo", "rest_days", "b2b", "games_7d", "miss_min", "miss_pts", "miss_min_fresh", "miss_pts_fresh", "n_out",
            *[f"t_{c}_l{n}" for c in ("off_rtg", "def_rtg", "net_rtg", "pace", "efg_pct", "tov_pct", "orb_pct", "ft_rate") for n in WINDOWS],
            "t_off_rtg_szn", "t_def_rtg_szn", "t_net_rtg_szn", "t_pace_szn", "team_game_no"]
    h = teams[teams["is_home"]][["game_id", *keep]].add_prefix("h_").rename(columns={"h_game_id": "id"})
    a = teams[~teams["is_home"]][["game_id", *keep]].add_prefix("a_").rename(columns={"a_game_id": "id"})
    gf = games.drop(columns=["h_elo", "a_elo"]).merge(h, on="id").merge(a, on="id")
    gf["elo_diff"] = gf["h_elo"] - gf["a_elo"] + ELO_HOME
    for c in ("t_net_rtg_l5", "t_net_rtg_l10", "t_net_rtg_l20", "t_net_rtg_szn", "rest_days", "miss_min", "miss_pts",
              "miss_min_fresh", "miss_pts_fresh"):
        gf[f"d_{c}"] = gf[f"h_{c}"] - gf[f"a_{c}"]
    for n in WINDOWS:
        gf[f"x_pace_l{n}"] = (gf[f"h_t_pace_l{n}"] + gf[f"a_t_pace_l{n}"]) / 2
        gf[f"x_off_l{n}"] = gf[f"h_t_off_rtg_l{n}"] + gf[f"a_t_off_rtg_l{n}"]
        gf[f"x_def_l{n}"] = gf[f"h_t_def_rtg_l{n}"] + gf[f"a_t_def_rtg_l{n}"]
    gf["margin"] = gf["home_pts"] - gf["away_pts"]
    gf["total"] = gf["home_pts"] + gf["away_pts"]
    gf["home_win"] = (gf["margin"] > 0).astype(float).where(gf["margin"].notna())
    gf["playoffs"] = gf["playoffs"].astype(int)
    gf = gf.sort_values(["date", "id"]).reset_index(drop=True)

    final = games[games["status"] == "final"]
    sig = f"{len(final)}|{final['id'].max() if len(final) else ''}|{int(final['home_pts'].sum()) if len(final) else 0}|{len(pl)}"
    return Frames(players=pl, games=gf, teams=teams, data_hash=hashlib.sha1(sig.encode()).hexdigest()[:16], injury_report_utc=inj_ts)


PLAYER_FEATURES = [
    *[f"p_{c}_l{n}" for c in P_ROLL for n in WINDOWS],
    *[f"p_{c}_szn" for c in ("min", "pts", "trb", "ast", "fg3", "pra", "usg_pct")],
    *[f"p_sd10_{c}" for c in ("min", "pts", "trb", "ast", "pra")],
    *[f"p_ewm_{c}" for c in ("min", "pts", "trb", "ast", "pra")],
    "p_starter_l5", "p_started_last", "p_n_prior", "p_n_prior_szn", "p_gap_days", "p_b2b", "p_min_trend", "p_new_team",
    "p_missed_since", "p_boost", "is_home", "t_rest_days", "t_b2b", "t_games_7d", "team_game_no",
    "miss_min", "miss_pts", "miss_ast", "miss_min_fresh", "miss_pts_fresh", "n_out",
    "t_off_rtg_l10", "t_pace_l10", "t_pts_l10", "t_off_rtg_szn", "t_pace_szn",
    "o_def_rtg_l10", "o_def_rtg_l20", "o_def_rtg_szn", "o_pace_l10", "o_pace_szn", "o_allowed_pts_l10",
    "o_allowed_trb_l10", "o_allowed_ast_l10", "o_allowed_fg3_l10", "o_allowed_trb_l20", "o_allowed_ast_l20",
    "o_allowed_fg3_l20", "o_allowed_efg_l20", "o_orb_pct_l20", "o_b2b", "o_miss_min", "elo_diff", "exp_pace",
]

GAME_FEATURES = [
    "elo_diff", "h_elo", "a_elo", "playoffs",
    *[f"{s}_{c}" for s in ("h", "a") for c in (
        "rest_days", "b2b", "games_7d", "miss_min", "miss_pts", "miss_min_fresh", "miss_pts_fresh", "n_out",
        *[f"t_{k}_l{n}" for k in ("off_rtg", "def_rtg", "net_rtg", "pace", "efg_pct", "tov_pct", "orb_pct", "ft_rate") for n in WINDOWS],
        "t_off_rtg_szn", "t_def_rtg_szn", "t_net_rtg_szn", "t_pace_szn", "team_game_no")],
    *[f"d_{c}" for c in ("t_net_rtg_l5", "t_net_rtg_l10", "t_net_rtg_l20", "t_net_rtg_szn", "rest_days", "miss_min",
                         "miss_pts", "miss_min_fresh", "miss_pts_fresh")],
    *[f"x_{k}_l{n}" for k in ("pace", "off", "def") for n in WINDOWS],
]
