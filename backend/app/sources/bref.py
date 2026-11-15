"""Basketball Reference: schedules and full box scores (basic + advanced + inactives).

Used as the primary historical source because stats.nba.com blocks most cloud/datacenter IPs.
Rate limit: Sports Reference bans clients above 20 req/min, so every call is throttled.
"""
from __future__ import annotations

import re
import time
from datetime import date, datetime
from pathlib import Path

from selectolax.parser import HTMLParser

from app.config import get_settings
from app.sources.http import SourceError, fetch

BASE = "https://www.basketball-reference.com"
MONTHS = ["october", "november", "december", "january", "february", "march", "april", "may", "june"]

INT_STATS = ["fg", "fga", "fg3", "fg3a", "ft", "fta", "orb", "drb", "trb", "ast", "stl", "blk", "tov", "pf", "pts"]
ADV_STATS = ["ts_pct", "efg_pct", "orb_pct", "drb_pct", "trb_pct", "ast_pct", "stl_pct", "blk_pct", "tov_pct",
             "usg_pct", "off_rtg", "def_rtg"]


def _cache(kind: str, name: str) -> Path:
    return get_settings().raw_dir / "bref" / kind / f"{name}.html.gz"


def _get(path: str, cache: Path | None) -> str:
    body, _ = fetch(BASE + path, min_interval=get_settings().bref_min_interval_s, cache_path=cache)
    return body  # type: ignore[return-value]


def _uncomment(html: str) -> str:
    # Sports Reference ships secondary tables inside HTML comments.
    return html.replace("<!--", "").replace("-->", "")


def _num(txt: str | None) -> float | None:
    if txt is None:
        return None
    txt = txt.strip()
    if txt in ("", "-"):
        return None
    try:
        return float(txt)
    except ValueError:
        return None


def season_label(end_year: int) -> str:
    return f"{end_year - 1}-{str(end_year)[2:]}"


def season_end_year(d: date) -> int:
    return d.year + 1 if d.month >= 9 else d.year


# ---------------------------------------------------------------- schedule

def parse_schedule(html: str, end_year: int) -> list[dict]:
    tree = HTMLParser(html)
    table = tree.css_first("table#schedule")
    if table is None:
        return []
    out: list[dict] = []
    playoffs = False
    for tr in table.css("tbody tr"):
        if "thead" in (tr.attributes.get("class") or ""):
            if "Playoffs" in tr.text():
                playoffs = True
            continue
        cells = {c.attributes.get("data-stat"): c for c in tr.css("th,td")}
        dcell = cells.get("date_game")
        if dcell is None or not dcell.text(strip=True):
            continue
        csk = dcell.attributes.get("csk") or ""
        m = re.match(r"(\d{4})(\d{2})(\d{2})0([A-Z]{3})", csk)
        if not m:
            continue
        gdate = date(int(m[1]), int(m[2]), int(m[3]))
        home = m[4]

        def abbr(key: str) -> str | None:
            a = cells[key].css_first("a") if key in cells else None
            mm = re.search(r"/teams/([A-Z]{3})/", a.attributes.get("href", "")) if a else None
            return mm[1] if mm else None

        box = cells["box_score_text"].css_first("a") if "box_score_text" in cells else None
        start = cells["game_start_time"].text(strip=True) if "game_start_time" in cells else ""
        out.append({
            "game_id": csk,
            "season": end_year,
            "date": gdate,
            "start_et": parse_start_time(start),
            "home": abbr("home_team_name") or home,
            "away": abbr("visitor_team_name"),
            "home_name": cells["home_team_name"].text(strip=True),
            "away_name": cells["visitor_team_name"].text(strip=True),
            "home_pts": _int(cells.get("home_pts")),
            "away_pts": _int(cells.get("visitor_pts")),
            "has_box": box is not None,
            "playoffs": playoffs,
        })
    return out


def _int(cell) -> int | None:
    v = _num(cell.text() if cell is not None else None)
    return int(v) if v is not None else None


def parse_start_time(txt: str) -> str | None:
    """'7:30p' / '7:30 PM' -> '19:30' (US Eastern)."""
    m = re.match(r"(\d{1,2}):(\d{2})\s*([ap])", txt.strip().lower())
    if not m:
        return None
    h = int(m[1]) % 12 + (12 if m[3] == "p" else 0)
    return f"{h:02d}:{m[2]}"


def fetch_schedule(end_year: int, *, refresh_months: set[str] | None = None) -> list[dict]:
    """All games of a season. Months in `refresh_months` bypass the cache (for the live season)."""
    games: dict[str, dict] = {}
    for month in MONTHS:
        cache = _cache("schedule", f"{end_year}-{month}")
        if refresh_months and month in refresh_months and cache.exists():
            cache.unlink()
        missing = cache.with_suffix(".404")
        if missing.exists() and time.time() - missing.stat().st_mtime < 86400:
            continue  # known to have no games (re-checked once a day)
        try:
            html = _get(f"/leagues/NBA_{end_year}_games-{month}.html", cache)
        except SourceError as e:
            if "404" in str(e):
                missing.parent.mkdir(parents=True, exist_ok=True)
                missing.touch()
                continue  # month has no games in this season
            raise
        for g in parse_schedule(html, end_year):
            games[g["game_id"]] = g
    return sorted(games.values(), key=lambda g: (g["date"], g["game_id"]))


# ---------------------------------------------------------------- box score

def _mp_seconds(cell) -> int:
    if cell is None:
        return 0
    csk = cell.attributes.get("csk")
    if csk and csk.isdigit():
        return int(csk)
    m = re.match(r"(\d+):(\d+)", cell.text(strip=True))
    return int(m[1]) * 60 + int(m[2]) if m else 0


def parse_boxscore(html: str, game_id: str) -> dict:
    tree = HTMLParser(_uncomment(html))
    teams = [m for m in re.findall(r'id="box-([A-Z]{3})-game-basic"', html)]
    teams = list(dict.fromkeys(teams))
    if len(teams) != 2:
        raise SourceError(f"{game_id}: expected 2 team tables, found {teams}")
    away, home = teams  # Basketball Reference lists the visitor first
    if home != game_id[-3:]:
        raise SourceError(f"{game_id}: home team mismatch ({home})")

    players: list[dict] = []
    team_tot: dict[str, dict] = {}
    for team in teams:
        basic = tree.css_first(f"table#box-{team}-game-basic")
        adv = tree.css_first(f"table#box-{team}-game-advanced")
        adv_rows: dict[str, dict] = {}
        if adv is not None:
            for tr in adv.css("tbody tr"):
                th = tr.css_first("th")
                pid = th.attributes.get("data-append-csv") if th else None
                if pid:
                    adv_rows[pid] = {c.attributes.get("data-stat"): _num(c.text()) for c in tr.css("td")}
        starters_done = False
        order = 0
        for tr in basic.css("tbody tr"):
            if "thead" in (tr.attributes.get("class") or ""):
                starters_done = True
                continue
            th = tr.css_first("th")
            pid = th.attributes.get("data-append-csv") if th else None
            if not pid:
                continue
            cells = {c.attributes.get("data-stat"): c for c in tr.css("td")}
            order += 1
            row = {"player_id": pid, "name": th.text(strip=True), "team": team, "opp": home if team == away else away,
                   "starter": not starters_done, "order": order}
            if "reason" in cells:  # DNP / not with team / suspended
                row.update({"played": False, "dnp_reason": cells["reason"].text(strip=True), "seconds": 0})
            else:
                row.update({"played": True, "dnp_reason": None, "seconds": _mp_seconds(cells.get("mp"))})
                for k in INT_STATS:
                    v = _num(cells[k].text()) if k in cells else None
                    row[k] = int(v) if v is not None else 0
                row["plus_minus"] = _num((cells["plus_minus"].text() if "plus_minus" in cells else "").replace("+", ""))
                a = adv_rows.get(pid, {})
                for k in ADV_STATS:
                    row[k] = a.get(k)
            players.append(row)
        foot = {c.attributes.get("data-stat"): _num(c.text()) for c in basic.css("tfoot td")}
        afoot = {c.attributes.get("data-stat"): _num(c.text()) for c in adv.css("tfoot td")} if adv is not None else {}
        team_tot[team] = {**{k: foot.get(k) for k in INT_STATS}, 
                          "off_rtg": afoot.get("off_rtg"), "def_rtg": afoot.get("def_rtg"),
                          "ts_pct": afoot.get("ts_pct"), "efg_pct": afoot.get("efg_pct"),
                          "tov_pct": afoot.get("tov_pct"), "orb_pct": afoot.get("orb_pct")}

    ff = tree.css_first("table#four_factors")
    if ff is not None:
        for tr in ff.css("tbody tr"):
            th = tr.css_first("th")
            t = th.text(strip=True) if th else None
            if t in team_tot:
                c = {x.attributes.get("data-stat"): _num(x.text()) for x in tr.css("td")}
                team_tot[t]["pace"] = c.get("pace")
                team_tot[t]["ft_rate"] = c.get("ft_rate")

    periods = 4
    ls = tree.css_first("table#line_score")
    if ls is not None:
        first = ls.css_first("tbody tr")
        if first is not None:
            periods = max(4, len(first.css("td")) - 1)  # minus total column

    inactive: dict[str, list[dict]] = {away: [], home: []}
    for strong in tree.css("strong"):
        if not strong.text().strip().startswith("Inactive:"):
            continue
        cur = None
        for node in strong.parent.iter(include_text=False):
            if node.tag == "span":
                cur = node.text().strip()
            elif node.tag == "a" and cur in inactive:
                m = re.search(r"/players/\w/(\w+)\.html", node.attributes.get("href", ""))
                if m:
                    inactive[cur].append({"player_id": m[1], "name": node.text(strip=True)})
        break

    tip = None
    meta = tree.css_first("div.scorebox_meta div")
    if meta is not None:
        try:
            tip = datetime.strptime(meta.text(strip=True), "%I:%M %p, %B %d, %Y").isoformat()
        except ValueError:
            tip = None

    for t in teams:
        if not team_tot[t].get("pts"):
            raise SourceError(f"{game_id}: no team totals for {t}")
    return {"game_id": game_id, "home": home, "away": away, "tip_et": tip, "periods": periods,
            "teams": team_tot, "players": players, "inactive": inactive}


def fetch_boxscore(game_id: str) -> dict:
    return parse_boxscore(_get(f"/boxscores/{game_id}.html", _cache("box", game_id)), game_id)
