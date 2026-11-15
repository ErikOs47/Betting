"""Official NBA Injury Report (PDF published several times per day on official.nba.com)."""
from __future__ import annotations

import io
import re
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pdfplumber

from app.sources.http import SourceError, fetch
from app.teams import BY_NBA, BY_SQUASHED

INDEX_URL = "https://official.nba.com/nba-injury-report-{y1}-{y2}-season/"
PDF_RE = re.compile(r"https://ak-static\.cms\.nba\.com/referee/injury/Injury-Report_(\d{4}-\d{2}-\d{2})_(\d{2})(?:_(\d{2}))?([AP]M)\.pdf")
STATUSES = ("Out", "Doubtful", "Questionable", "Probable", "Available")
ET = ZoneInfo("America/New_York")

_LINE = re.compile(
    r"^(?:(?P<date>\d{2}/\d{2}/\d{4})\s+)?(?:(?P<time>\d{2}:\d{2})\(ET\)\s+)?(?:(?P<away>[A-Z]{3})@(?P<home>[A-Z]{3})\s+)?"
    r"(?:(?P<team>" + "|".join(sorted(BY_SQUASHED, key=len, reverse=True)) + r")\s+)?"
    r"(?P<rest>.*)$"
)
_PLAYER = re.compile(r"^(?P<name>[^\s,]+,[^\s,]+)\s+(?P<status>" + "|".join(STATUSES) + r")(?:\s+(?P<reason>.*))?$")


def list_reports(season_end_year: int) -> list[tuple[str, datetime]]:
    """All published report PDFs of a season as (url, report time UTC), oldest first."""
    url = INDEX_URL.format(y1=season_end_year - 1, y2=str(season_end_year)[2:])
    html, _ = fetch(url, timeout=30)
    out = {}
    for m in PDF_RE.finditer(html):  # type: ignore[arg-type]
        d, hh, mm, ap = m.groups()
        local = datetime.strptime(f"{d} {hh}:{mm or '00'} {ap}", "%Y-%m-%d %I:%M %p").replace(tzinfo=ET)
        out[m.group(0)] = local.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)
    if not out:
        raise SourceError(f"no injury report links found on {url}")
    return sorted(out.items(), key=lambda kv: kv[1])


def parse_report_text(text: str) -> list[dict]:
    rows: list[dict] = []
    gdate: date | None = None
    team: str | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith(("Injury Report:", "GameDate", "Page")):
            continue
        m = _LINE.match(line)
        if not m:
            continue
        if m["date"]:
            gdate = datetime.strptime(m["date"], "%m/%d/%Y").date()
        if m["team"]:
            team = BY_SQUASHED[m["team"]]
        elif m["home"] and not m["team"]:
            team = None
        p = _PLAYER.match(m["rest"] or "")
        if p and gdate and team:
            rows.append({"game_date": gdate, "team_id": team, "player_name": p["name"], "status": p["status"],
                         "reason": (p["reason"] or None)})
        elif rows and m["rest"] and not (m["date"] or m["time"] or m["home"] or m["team"]) and "NOTYETSUBMITTED" not in m["rest"]:
            # wrapped reason text belongs to the nearest player row; keep it only if that row has no reason yet
            if rows[-1]["reason"] is None:
                rows[-1]["reason"] = m["rest"]
    return rows


def fetch_report(url: str) -> list[dict]:
    body, _ = fetch(url, binary=True, timeout=60)
    with pdfplumber.open(io.BytesIO(body)) as pdf:  # type: ignore[arg-type]
        text = "\n".join((p.extract_text() or "") for p in pdf.pages)
    rows = parse_report_text(text)
    if not rows:
        raise SourceError(f"injury report {url} parsed to zero rows")
    return rows


__all__ = ["list_reports", "fetch_report", "parse_report_text", "BY_NBA"]
