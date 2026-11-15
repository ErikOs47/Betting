"""Team reference data and name normalisation shared by all sources."""
from __future__ import annotations

import re
import unicodedata

# (Basketball Reference abbr, NBA abbr, full name)
TEAMS = [
    ("ATL", "ATL", "Atlanta Hawks"), ("BOS", "BOS", "Boston Celtics"), ("BRK", "BKN", "Brooklyn Nets"),
    ("CHO", "CHA", "Charlotte Hornets"), ("CHI", "CHI", "Chicago Bulls"), ("CLE", "CLE", "Cleveland Cavaliers"),
    ("DAL", "DAL", "Dallas Mavericks"), ("DEN", "DEN", "Denver Nuggets"), ("DET", "DET", "Detroit Pistons"),
    ("GSW", "GSW", "Golden State Warriors"), ("HOU", "HOU", "Houston Rockets"), ("IND", "IND", "Indiana Pacers"),
    ("LAC", "LAC", "Los Angeles Clippers"), ("LAL", "LAL", "Los Angeles Lakers"), ("MEM", "MEM", "Memphis Grizzlies"),
    ("MIA", "MIA", "Miami Heat"), ("MIL", "MIL", "Milwaukee Bucks"), ("MIN", "MIN", "Minnesota Timberwolves"),
    ("NOP", "NOP", "New Orleans Pelicans"), ("NYK", "NYK", "New York Knicks"), ("OKC", "OKC", "Oklahoma City Thunder"),
    ("ORL", "ORL", "Orlando Magic"), ("PHI", "PHI", "Philadelphia 76ers"), ("PHO", "PHX", "Phoenix Suns"),
    ("POR", "POR", "Portland Trail Blazers"), ("SAC", "SAC", "Sacramento Kings"), ("SAS", "SAS", "San Antonio Spurs"),
    ("TOR", "TOR", "Toronto Raptors"), ("UTA", "UTA", "Utah Jazz"), ("WAS", "WAS", "Washington Wizards"),
]
BY_NBA = {n: b for b, n, _ in TEAMS}
BY_NAME = {name: b for b, _, name in TEAMS}
BY_NAME["LA Clippers"] = "LAC"
NAME_OF = {b: name for b, _, name in TEAMS}
NBA_OF = {b: n for b, n, _ in TEAMS}
BY_SQUASHED = {re.sub(r"\s+", "", name): b for name, b in BY_NAME.items()}  # "DenverNuggets" (injury PDF)

_SUFFIX = re.compile(r"(jr|sr|ii|iii|iv|v)$")


def norm_name(name: str) -> str:
    """'Kristaps Porziņģis' -> 'kristapsporzingis'; suffixes (Jr., III) are dropped."""
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    parts = [re.sub(r"[^a-z]", "", p) for p in s.split()]
    parts = [p for p in parts if p]
    if len(parts) > 2 and _SUFFIX.fullmatch(parts[-1]):
        parts = parts[:-1]
    return "".join(parts)


def norm_last_first(name: str) -> str:
    """Injury-report style 'HolmesII,DaRon' / 'PierreJr.,Jaron' -> 'daronholmes'."""
    last, _, first = name.partition(",")
    last = re.sub(r"(Jr\.?|Sr\.?|II|III|IV)$", "", last.strip())
    return norm_name(f"{first} {last}")
