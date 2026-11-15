"""Ingestion: schedule + box scores, injury reports and odds snapshots. Every job records source health."""
from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import (ApiUsage, Game, GameInactive, InjuryEntry, InjuryReport, OddsEvent, OddsSnapshot, Player,
                    PlayerGameStat, SourceStatus, Team, TeamGameStat, utcnow)
from app.sources import bref, injuries, odds_api
from app.sources.http import SourceError
from app.teams import BY_NAME, TEAMS, norm_last_first, norm_name

log = logging.getLogger("ingest")
ET = ZoneInfo("America/New_York")
UTC = ZoneInfo("UTC")

P_COLS = ["pts", "trb", "ast", "fg", "fga", "fg3", "fg3a", "ft", "fta", "orb", "drb", "stl", "blk", "tov", "pf"]
T_COLS = ["pts", "fg", "fga", "fg3", "fg3a", "ft", "fta", "orb", "drb", "trb", "ast", "stl", "blk", "tov", "pf"]


@contextmanager
def track(db: Session, source: str):
    """Record attempt/success/error for a source so stale or failing feeds are visible and can gate predictions."""
    st = db.get(SourceStatus, source) or SourceStatus(source=source, detail={})
    db.add(st)
    st.last_attempt = utcnow()
    detail: dict = {}
    try:
        yield detail
        st.last_success = utcnow()
        st.last_error = None
        st.detail = {**(st.detail or {}), **detail}
    except Exception as e:  # noqa: BLE001 - status must capture every failure
        db.rollback()
        st = db.get(SourceStatus, source) or SourceStatus(source=source, detail={})
        db.add(st)
        st.last_attempt = st.last_error_at = utcnow()
        st.last_error = f"{type(e).__name__}: {e}"[:500]
        db.commit()
        raise
    db.commit()


def sync_teams(db: Session) -> None:
    for b, n, name in TEAMS:
        if db.get(Team, b) is None:
            db.add(Team(id=b, nba_abbr=n, name=name))
    db.flush()


def _tip_utc(d: date, start_et: str | None) -> datetime | None:
    if not start_et:
        return None
    h, m = map(int, start_et.split(":"))
    return datetime(d.year, d.month, d.day, h, m, tzinfo=ET).astimezone(UTC).replace(tzinfo=None)


def _upsert_players(db: Session, people: dict[str, str]) -> None:
    if not people:
        return
    have = set(db.scalars(select(Player.id).where(Player.id.in_(people))))
    for pid, name in people.items():
        if pid not in have:
            db.add(Player(id=pid, name=name, norm_name=norm_name(name)))
    db.flush()


def store_boxscore(db: Session, game: Game, box: dict) -> None:
    db.execute(delete(PlayerGameStat).where(PlayerGameStat.game_id == game.id))
    db.execute(delete(TeamGameStat).where(TeamGameStat.game_id == game.id))
    db.execute(delete(GameInactive).where(GameInactive.game_id == game.id))
    people = {p["player_id"]: p["name"] for p in box["players"]}
    for team, lst in box["inactive"].items():
        people.update({p["player_id"]: p["name"] for p in lst})
    _upsert_players(db, people)
    for team, t in box["teams"].items():
        opp = box["home"] if team == box["away"] else box["away"]
        db.add(TeamGameStat(game_id=game.id, team_id=team, opp_id=opp, is_home=team == box["home"],
                            **{k: int(t[k] or 0) for k in T_COLS},
                            **{k: t.get(k) for k in ["pace", "off_rtg", "def_rtg", "efg_pct", "tov_pct", "orb_pct", "ft_rate"]}))
    for p in box["players"]:
        db.add(PlayerGameStat(game_id=game.id, player_id=p["player_id"], team_id=p["team"], opp_id=p["opp"],
                              starter=p["starter"] and p["played"], played=p["played"], dnp_reason=p["dnp_reason"],
                              seconds=p["seconds"], **{k: p.get(k, 0) for k in P_COLS},
                              **{k: p.get(k) for k in ["plus_minus", "usg_pct", "ts_pct", "ast_pct", "trb_pct", "off_rtg", "def_rtg"]}))
    seen = set(people) - {p["player_id"] for p in box["players"]}
    for team, lst in box["inactive"].items():
        for p in lst:
            if p["player_id"] in seen:
                seen.discard(p["player_id"])
                db.add(GameInactive(game_id=game.id, player_id=p["player_id"], team_id=team))
    game.home_pts = int(box["teams"][box["home"]]["pts"])
    game.away_pts = int(box["teams"][box["away"]]["pts"])
    game.periods = box["periods"]
    game.status = "final"


def ingest_season(db: Session, end_year: int, *, live: bool = False, cached_only: bool = False, max_new: int | None = None) -> dict:
    """Upsert the schedule and load all box scores that are not stored yet.

    live=True re-downloads the schedule pages around today (results and tip times change).
    cached_only=True never hits the network for box scores (used while a bulk download is running).
    """
    with track(db, "basketball-reference") as detail:
        sync_teams(db)
        refresh = None
        if live:
            today = datetime.now(ET).date()
            refresh = {(today - timedelta(days=1)).strftime("%B").lower(), today.strftime("%B").lower(),
                       (today + timedelta(days=7)).strftime("%B").lower()}
        sched = bref.fetch_schedule(end_year, refresh_months=refresh)
        if not sched:
            raise SourceError(f"empty schedule for season {end_year}")
        existing = {g.id: g for g in db.scalars(select(Game).where(Game.season == end_year))}
        loaded = failed = 0
        for g in sched:
            if g["away"] is None:
                continue
            row = existing.get(g["game_id"])
            if row is None:
                row = Game(id=g["game_id"], season=end_year, date=g["date"], home_id=g["home"], away_id=g["away"],
                           playoffs=g["playoffs"], status="scheduled")
                db.add(row)
                existing[row.id] = row
            row.tip_utc = _tip_utc(g["date"], g["start_et"]) or row.tip_utc
            row.date = g["date"]
            if row.status == "final" or not g["has_box"]:
                continue
            if cached_only and not bref._cache("box", row.id).exists():
                continue
            if max_new is not None and loaded >= max_new:
                continue
            try:
                store_boxscore(db, row, bref.fetch_boxscore(row.id))
                loaded += 1
                if loaded % 200 == 0:
                    db.commit()
                    log.info("season %s: %s box scores stored", end_year, loaded)
            except SourceError as e:
                failed += 1
                log.warning("box score %s failed: %s", row.id, e)
        # games that vanished from the schedule (postponed/rescheduled) and were never played
        ids = {g["game_id"] for g in sched}
        for gid, row in existing.items():
            if gid not in ids and row.status != "final":
                db.delete(row)
        detail.update({f"season_{end_year}": {"games": len(sched), "new_boxscores": loaded, "failed": failed}})
        return detail[f"season_{end_year}"]


# ------------------------------------------------------------------ injuries
def match_player(db: Session, name_norm: str, team_id: str | None, cache: dict | None = None) -> str | None:
    """Resolve a normalised name to a player id, preferring players who recently appeared for the team."""
    cands = list(db.scalars(select(Player.id).where(Player.norm_name == name_norm)))
    if not cands:
        return None
    if len(cands) == 1:
        return cands[0]
    best = db.execute(
        select(PlayerGameStat.player_id, func.max(PlayerGameStat.game_id)).where(PlayerGameStat.player_id.in_(cands))
        .where(PlayerGameStat.team_id == team_id if team_id else True).group_by(PlayerGameStat.player_id)
        .order_by(func.max(PlayerGameStat.game_id).desc())).first()
    return best[0] if best else None


def ingest_injuries(db: Session, *, max_reports: int = 1) -> dict:
    """Store the newest official injury report(s). Older reports stay in the DB, giving a timestamped history."""
    with track(db, "nba-injury-report") as detail:
        today = datetime.now(ET).date()
        reports = injuries.list_reports(bref.season_end_year(today))
        known = set(db.scalars(select(InjuryReport.url)))
        new = [(u, t) for u, t in reports if u not in known][-max_reports:]
        n_rows = unmatched = 0
        for url, ts in new:
            try:
                rows = injuries.fetch_report(url)
            except SourceError as e:
                if "403" in str(e) or "404" in str(e):  # link listed before the PDF is published
                    continue
                raise
            rep = InjuryReport(url=url, report_time_utc=ts)
            db.add(rep)
            db.flush()
            for r in rows:
                pid = match_player(db, norm_last_first(r["player_name"]), r["team_id"])
                unmatched += pid is None
                last, _, first = r["player_name"].partition(",")
                db.add(InjuryEntry(report_id=rep.id, game_date=r["game_date"], team_id=r["team_id"], player_id=pid,
                                   player_name=f"{first} {last}", status=r["status"], reason=(r["reason"] or "")[:255]))
            n_rows += len(rows)
        latest = db.scalar(select(func.max(InjuryReport.report_time_utc)))
        detail.update({"new_reports": len(new), "rows": n_rows, "unmatched": unmatched,
                       "latest_report_utc": latest.isoformat() if latest else None})
        return dict(detail)


# ------------------------------------------------------------------ odds
def credits_used_this_month(db: Session) -> int:
    start = utcnow().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return int(db.scalar(select(func.coalesce(func.sum(ApiUsage.credits), 0)).where(ApiUsage.source == "the-odds-api", ApiUsage.ts >= start)) or 0)


def _spend(db: Session, endpoint: str, usage: dict, estimate: int) -> None:
    db.add(ApiUsage(source="the-odds-api", endpoint=endpoint, credits=usage.get("last") if usage.get("last") is not None else estimate,
                    remaining=usage.get("remaining")))
    db.flush()


def _match_game(db: Session, home_name: str, away_name: str, commence: datetime) -> str | None:
    home, away = BY_NAME.get(home_name), BY_NAME.get(away_name)
    if not home or not away:
        return None
    d = commence.replace(tzinfo=UTC).astimezone(ET).date()
    return db.scalar(select(Game.id).where(Game.home_id == home, Game.away_id == away, Game.date == d))


def store_event_rows(db: Session, ev: dict, fetched_at: datetime, *, historical: bool = False) -> int:
    commence = odds_api.parse_ts(ev["commence_time"])
    assert commence is not None
    oe = db.get(OddsEvent, ev["id"])
    if oe is None:
        oe = OddsEvent(id=ev["id"], commence_utc=commence, home_name=ev["home_team"], away_name=ev["away_team"])
        db.add(oe)
    oe.commence_utc = commence
    oe.game_id = oe.game_id or _match_game(db, ev["home_team"], ev["away_team"], commence)
    rows = odds_api.flatten_event(ev)
    if fetched_at >= commence:
        return 0  # never store in-play prices as pre-game odds
    pid_cache: dict[str, str | None] = {}
    for r in rows:
        pid = None
        if r["player_name"]:
            key = norm_name(r["player_name"])
            if key not in pid_cache:
                pid_cache[key] = match_player(db, key, None)
            pid = pid_cache[key]
        db.add(OddsSnapshot(fetched_at=fetched_at, historical=historical, event_id=ev["id"], game_id=oe.game_id,
                            bookmaker=r["bookmaker"], market=r["market"], player_id=pid, player_name=r["player_name"],
                            selection=r["selection"], line=r["line"], price=r["price"], book_updated_at=r["book_updated_at"]))
    return len(rows)


def ingest_odds(db: Session, *, props: bool = True, props_hours: float = 6.0, props_min_gap_min: float = 0.0) -> dict:
    """Snapshot current odds. Refuses to exceed the configured monthly credit budget."""
    with track(db, "the-odds-api") as detail:
        budget = get_settings().odds_monthly_credit_budget
        used = credits_used_this_month(db)
        cost = odds_api.estimate_cost(len(odds_api.FEATURED))
        if used + cost > budget:
            raise SourceError(f"monthly odds credit budget exhausted ({used}/{budget})")
        now = utcnow()
        data, usage = odds_api.featured_odds()
        _spend(db, "odds", usage, cost)
        n = sum(store_event_rows(db, ev, now) for ev in data)
        n_props = 0
        if props:
            soon = [ev for ev in data if now < odds_api.parse_ts(ev["commence_time"]) <= now + timedelta(hours=props_hours)]
            for ev in soon:
                if props_min_gap_min > 0:
                    last = db.scalar(select(func.max(OddsSnapshot.fetched_at)).where(
                        OddsSnapshot.event_id == ev["id"], OddsSnapshot.player_name.is_not(None)))
                    if last is not None and (now - last).total_seconds() < props_min_gap_min * 60:
                        continue
                cost = odds_api.estimate_cost(len(odds_api.PROPS))
                if credits_used_this_month(db) + cost > budget:
                    detail["props_skipped"] = "budget"
                    break
                pdata, usage = odds_api.event_props(ev["id"])
                _spend(db, "event_odds", usage, cost)
                n_props += store_event_rows(db, pdata, utcnow())
        detail.update({"events": len(data), "rows": n, "prop_rows": n_props, "credits_used_month": credits_used_this_month(db),
                       "credits_remaining": usage.get("remaining")})
        return dict(detail)


def ingest_historical_odds(db: Session, day: date, *, props: bool = False, minutes_before_tip: int = 60) -> dict:
    """Load one historical snapshot per game day (paid plans). Stored with the snapshot's own timestamp, so
    backtests can only ever see prices that existed before tip-off."""
    with track(db, "the-odds-api") as detail:
        games = list(db.scalars(select(Game).where(Game.date == day, Game.tip_utc.is_not(None))))
        if not games:
            return {"day": str(day), "rows": 0}
        first_tip = min(g.tip_utc for g in games)
        at = first_tip - timedelta(minutes=minutes_before_tip)
        payload, usage = odds_api.historical_featured(at)
        _spend(db, "historical_odds", usage, odds_api.estimate_cost(len(odds_api.FEATURED), historical=True))
        ts = odds_api.parse_ts(payload.get("timestamp")) or at
        n = sum(store_event_rows(db, ev, ts, historical=True) for ev in payload.get("data", []))
        if props:
            for ev in payload.get("data", []):
                c = odds_api.parse_ts(ev["commence_time"])
                if c is None or c.replace(tzinfo=UTC).astimezone(ET).date() != day:
                    continue
                p, usage = odds_api.historical_event_props(ev["id"], c - timedelta(minutes=minutes_before_tip))
                _spend(db, "historical_event_odds", usage, odds_api.estimate_cost(len(odds_api.PROPS), historical=True))
                n += store_event_rows(db, p.get("data", {}), odds_api.parse_ts(p.get("timestamp")) or at, historical=True)
        detail.update({"historical_day": str(day), "historical_rows": n})
        return {"day": str(day), "rows": n}
