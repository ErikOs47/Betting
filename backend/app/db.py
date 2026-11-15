"""Database schema (SQLAlchemy 2.0). PostgreSQL in production, SQLite for local dev/tests."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timezone
from typing import Any, Iterator

from sqlalchemy import (JSON, Boolean, Date, DateTime, Float, ForeignKey, Index, Integer, String, Text,
                        UniqueConstraint, create_engine)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

from app.config import get_settings


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)  # naive UTC everywhere in the DB


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


# ------------------------------------------------------------------ reference
class Team(Base):
    __tablename__ = "teams"
    id: Mapped[str] = mapped_column(String(3), primary_key=True)  # Basketball Reference abbreviation
    nba_abbr: Mapped[str] = mapped_column(String(3), index=True)
    name: Mapped[str] = mapped_column(String(64))


class Player(Base):
    __tablename__ = "players"
    id: Mapped[str] = mapped_column(String(16), primary_key=True)  # Basketball Reference id
    name: Mapped[str] = mapped_column(String(96))
    norm_name: Mapped[str] = mapped_column(String(96), index=True)


# ------------------------------------------------------------------ games & stats
class Game(Base):
    __tablename__ = "games"
    id: Mapped[str] = mapped_column(String(16), primary_key=True)
    season: Mapped[int] = mapped_column(Integer, index=True)  # season end year
    date: Mapped[date] = mapped_column(Date, index=True)  # local (US Eastern) game date
    tip_utc: Mapped[datetime | None] = mapped_column(DateTime)
    home_id: Mapped[str] = mapped_column(ForeignKey("teams.id"))
    away_id: Mapped[str] = mapped_column(ForeignKey("teams.id"))
    home_pts: Mapped[int | None]
    away_pts: Mapped[int | None]
    status: Mapped[str] = mapped_column(String(12), default="scheduled", index=True)  # scheduled | final
    playoffs: Mapped[bool] = mapped_column(Boolean, default=False)
    periods: Mapped[int] = mapped_column(Integer, default=4)
    line_score: Mapped[dict[str, Any] | None] = mapped_column(JSON)  # {"home": [q1..q4, ot..], "away": [...]}
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class TeamGameStat(Base):
    __tablename__ = "team_game_stats"
    game_id: Mapped[str] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), primary_key=True)
    team_id: Mapped[str] = mapped_column(ForeignKey("teams.id"), primary_key=True)
    opp_id: Mapped[str] = mapped_column(String(3))
    is_home: Mapped[bool]
    pts: Mapped[int]
    fg: Mapped[int]
    fga: Mapped[int]
    fg3: Mapped[int]
    fg3a: Mapped[int]
    ft: Mapped[int]
    fta: Mapped[int]
    orb: Mapped[int]
    drb: Mapped[int]
    trb: Mapped[int]
    ast: Mapped[int]
    stl: Mapped[int]
    blk: Mapped[int]
    tov: Mapped[int]
    pf: Mapped[int]
    pace: Mapped[float | None]
    off_rtg: Mapped[float | None]
    def_rtg: Mapped[float | None]
    efg_pct: Mapped[float | None]
    tov_pct: Mapped[float | None]
    orb_pct: Mapped[float | None]
    ft_rate: Mapped[float | None]


class PlayerGameStat(Base):
    __tablename__ = "player_game_stats"
    game_id: Mapped[str] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), primary_key=True)
    player_id: Mapped[str] = mapped_column(ForeignKey("players.id"), primary_key=True)
    team_id: Mapped[str] = mapped_column(String(3), index=True)
    opp_id: Mapped[str] = mapped_column(String(3))
    starter: Mapped[bool]
    played: Mapped[bool]
    dnp_reason: Mapped[str | None] = mapped_column(String(64))
    seconds: Mapped[int] = mapped_column(Integer, default=0)
    pts: Mapped[int] = mapped_column(Integer, default=0)
    trb: Mapped[int] = mapped_column(Integer, default=0)
    ast: Mapped[int] = mapped_column(Integer, default=0)
    fg: Mapped[int] = mapped_column(Integer, default=0)
    fga: Mapped[int] = mapped_column(Integer, default=0)
    fg3: Mapped[int] = mapped_column(Integer, default=0)
    fg3a: Mapped[int] = mapped_column(Integer, default=0)
    ft: Mapped[int] = mapped_column(Integer, default=0)
    fta: Mapped[int] = mapped_column(Integer, default=0)
    orb: Mapped[int] = mapped_column(Integer, default=0)
    drb: Mapped[int] = mapped_column(Integer, default=0)
    stl: Mapped[int] = mapped_column(Integer, default=0)
    blk: Mapped[int] = mapped_column(Integer, default=0)
    tov: Mapped[int] = mapped_column(Integer, default=0)
    pf: Mapped[int] = mapped_column(Integer, default=0)
    plus_minus: Mapped[float | None]
    usg_pct: Mapped[float | None]
    ts_pct: Mapped[float | None]
    ast_pct: Mapped[float | None]
    trb_pct: Mapped[float | None]
    off_rtg: Mapped[float | None]
    def_rtg: Mapped[float | None]


class GameInactive(Base):
    """Players officially inactive for a played game (from the box score)."""
    __tablename__ = "game_inactives"
    game_id: Mapped[str] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), primary_key=True)
    player_id: Mapped[str] = mapped_column(ForeignKey("players.id"), primary_key=True)
    team_id: Mapped[str] = mapped_column(String(3))


# ------------------------------------------------------------------ injuries
class InjuryReport(Base):
    __tablename__ = "injury_reports"
    id: Mapped[int] = mapped_column(primary_key=True)
    url: Mapped[str] = mapped_column(String(255), unique=True)
    report_time_utc: Mapped[datetime] = mapped_column(DateTime, index=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)


class InjuryEntry(Base):
    __tablename__ = "injury_entries"
    id: Mapped[int] = mapped_column(primary_key=True)
    report_id: Mapped[int] = mapped_column(ForeignKey("injury_reports.id", ondelete="CASCADE"), index=True)
    game_date: Mapped[date] = mapped_column(Date, index=True)
    team_id: Mapped[str] = mapped_column(String(3))
    player_id: Mapped[str | None] = mapped_column(String(16), index=True)  # None if the name could not be matched
    player_name: Mapped[str] = mapped_column(String(96))
    status: Mapped[str] = mapped_column(String(16))  # Out | Doubtful | Questionable | Probable | Available
    reason: Mapped[str | None] = mapped_column(String(255))


# ------------------------------------------------------------------ odds (append-only, timestamped)
class OddsEvent(Base):
    __tablename__ = "odds_events"
    id: Mapped[str] = mapped_column(String(40), primary_key=True)  # The Odds API event id
    game_id: Mapped[str | None] = mapped_column(ForeignKey("games.id"), index=True)
    commence_utc: Mapped[datetime] = mapped_column(DateTime)
    home_name: Mapped[str] = mapped_column(String(64))
    away_name: Mapped[str] = mapped_column(String(64))


class OddsSnapshot(Base):
    __tablename__ = "odds_snapshots"
    id: Mapped[int] = mapped_column(primary_key=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime, index=True)  # when WE observed the price
    source: Mapped[str] = mapped_column(String(24), default="the-odds-api")
    historical: Mapped[bool] = mapped_column(Boolean, default=False)  # loaded from a historical snapshot endpoint
    event_id: Mapped[str] = mapped_column(String(40), index=True)
    game_id: Mapped[str | None] = mapped_column(String(16), index=True)
    bookmaker: Mapped[str] = mapped_column(String(32))
    market: Mapped[str] = mapped_column(String(24))  # moneyline|spread|total|points|rebounds|assists|pra|threes
    player_id: Mapped[str | None] = mapped_column(String(16))
    player_name: Mapped[str | None] = mapped_column(String(96))
    selection: Mapped[str] = mapped_column(String(8))  # home|away|over|under
    line: Mapped[float | None]
    price: Mapped[float]  # decimal odds
    book_updated_at: Mapped[datetime | None] = mapped_column(DateTime)

    __table_args__ = (Index("ix_odds_lookup", "game_id", "market", "player_id", "bookmaker", "fetched_at"),)


class ApiUsage(Base):
    __tablename__ = "api_usage"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    source: Mapped[str] = mapped_column(String(24))
    endpoint: Mapped[str] = mapped_column(String(128))
    credits: Mapped[int] = mapped_column(Integer, default=0)
    remaining: Mapped[int | None]


# ------------------------------------------------------------------ models & predictions
class ModelVersion(Base):
    __tablename__ = "model_versions"
    id: Mapped[int] = mapped_column(primary_key=True)
    family: Mapped[str] = mapped_column(String(16))  # player | game
    algo: Mapped[str] = mapped_column(String(16))  # lightgbm | xgboost
    version: Mapped[str] = mapped_column(String(40), unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    train_start: Mapped[date | None] = mapped_column(Date)
    train_end: Mapped[date | None] = mapped_column(Date)
    n_train: Mapped[int] = mapped_column(Integer, default=0)
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    features: Mapped[list[Any]] = mapped_column(JSON, default=list)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    data_hash: Mapped[str] = mapped_column(String(40), default="")
    code_sha: Mapped[str] = mapped_column(String(40), default="")
    artifact_path: Mapped[str] = mapped_column(String(255))
    active: Mapped[bool] = mapped_column(Boolean, default=False, index=True)


class Prediction(Base):
    """One stored, reproducible probability distribution."""
    __tablename__ = "predictions"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    model_version_id: Mapped[int] = mapped_column(ForeignKey("model_versions.id"))
    game_id: Mapped[str] = mapped_column(ForeignKey("games.id"), index=True)
    market: Mapped[str] = mapped_column(String(24))  # points|rebounds|assists|pra|threes|margin|total
    player_id: Mapped[str | None] = mapped_column(String(16), index=True)
    team_id: Mapped[str | None] = mapped_column(String(3))
    mean: Mapped[float]
    dist: Mapped[dict[str, Any]] = mapped_column(JSON)  # {"family": "negbin"|"normal"|"t", params...}
    features: Mapped[dict[str, Any]] = mapped_column(JSON)  # exact model inputs
    inputs_hash: Mapped[str] = mapped_column(String(40))
    data_quality: Mapped[float]  # 0..1
    quality_flags: Mapped[list[Any]] = mapped_column(JSON, default=list)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)  # False => disabled for betting
    is_latest: Mapped[bool] = mapped_column(Boolean, default=True, index=True)


# ------------------------------------------------------------------ paper betting
class Bet(Base):
    __tablename__ = "bets"
    id: Mapped[int] = mapped_column(primary_key=True)
    placed_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    mode: Mapped[str] = mapped_column(String(8), default="paper")  # paper only; no real-money execution exists
    kind: Mapped[str] = mapped_column(String(8))  # single | parlay
    game_ids: Mapped[list[Any]] = mapped_column(JSON)
    legs: Mapped[list[Any]] = mapped_column(JSON)  # [{game_id, market, player_id, selection, line, price, bookmaker, model_prob, ...}]
    description: Mapped[str] = mapped_column(Text)
    stake: Mapped[float]
    price: Mapped[float]
    model_prob: Mapped[float]
    fair_book_prob: Mapped[float | None]
    ev: Mapped[float]
    edge: Mapped[float | None]
    confidence: Mapped[float | None]
    kelly_full: Mapped[float | None]
    status: Mapped[str] = mapped_column(String(8), default="open", index=True)  # open|won|lost|push|void
    pnl: Mapped[float | None]
    settled_at: Mapped[datetime | None] = mapped_column(DateTime)
    closing_price: Mapped[float | None]
    clv: Mapped[float | None]  # price / closing_fair_price - 1
    prediction_ids: Mapped[list[Any]] = mapped_column(JSON, default=list)


class LedgerEntry(Base):
    __tablename__ = "bankroll_ledger"
    id: Mapped[int] = mapped_column(primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow, index=True)
    delta: Mapped[float]
    balance: Mapped[float]
    reason: Mapped[str] = mapped_column(String(32))  # deposit | stake | payout | refund | reset
    bet_id: Mapped[int | None] = mapped_column(ForeignKey("bets.id"))


class Suggestion(Base):
    """A suggested bet as it was shown before tip-off, settled against the real result afterwards."""
    __tablename__ = "suggestions"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    kind: Mapped[str] = mapped_column(String(10))  # prop | moneyline
    game_id: Mapped[str] = mapped_column(String(16), index=True)
    player_id: Mapped[str | None] = mapped_column(String(16))
    team_id: Mapped[str | None] = mapped_column(String(3))
    market: Mapped[str] = mapped_column(String(24))
    selection: Mapped[str] = mapped_column(String(8))
    line: Mapped[float | None]
    raw_prob: Mapped[float]  # model probability before the learned correction
    prob: Mapped[float]  # probability shown to the user (after correction)
    mu: Mapped[float | None]
    l10: Mapped[float | None]
    status: Mapped[str] = mapped_column(String(8), default="open", index=True)  # open|won|lost|void
    actual: Mapped[float | None]
    settled_at: Mapped[datetime | None] = mapped_column(DateTime)


# ------------------------------------------------------------------ operations
class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(48), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow, onupdate=utcnow)


class SourceStatus(Base):
    __tablename__ = "source_status"
    source: Mapped[str] = mapped_column(String(24), primary_key=True)
    last_attempt: Mapped[datetime | None] = mapped_column(DateTime)
    last_success: Mapped[datetime | None] = mapped_column(DateTime)
    last_error: Mapped[str | None] = mapped_column(Text)
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class BacktestRun(Base):
    __tablename__ = "backtest_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    name: Mapped[str] = mapped_column(String(64))
    config: Mapped[dict[str, Any]] = mapped_column(JSON)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON)
    code_sha: Mapped[str] = mapped_column(String(40), default="")

    __table_args__ = (UniqueConstraint("name", "created_at"),)


# ------------------------------------------------------------------ engine
_engine = None
_Session: sessionmaker[Session] | None = None


def get_engine():
    global _engine, _Session
    if _engine is None:
        url = get_settings().database_url
        kw: dict[str, Any] = {"connect_args": {"check_same_thread": False, "timeout": 60}} if url.startswith("sqlite") else {"pool_pre_ping": True}
        _engine = create_engine(url, **kw)
        _Session = sessionmaker(_engine, expire_on_commit=False)
    return _engine


# columns added after the first release: create_all() does not alter existing tables
_ADDED_COLUMNS = {"games": {"line_score": "JSON"}}


def init_db() -> None:
    from sqlalchemy import inspect, text
    eng = get_engine()
    Base.metadata.create_all(eng)
    insp = inspect(eng)
    with eng.begin() as con:
        for table, cols in _ADDED_COLUMNS.items():
            have = {c["name"] for c in insp.get_columns(table)}
            for name, typ in cols.items():
                if name not in have:
                    con.execute(text(f"ALTER TABLE {table} ADD COLUMN {name} {typ}"))


def reset_engine() -> None:
    """Drop the cached engine (tests switch databases)."""
    global _engine, _Session
    if _engine is not None:
        _engine.dispose()
    _engine, _Session = None, None


@contextmanager
def session_scope() -> Iterator[Session]:
    get_engine()
    assert _Session is not None
    s = _Session()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


def get_session() -> Iterator[Session]:  # FastAPI dependency
    with session_scope() as s:
        yield s
