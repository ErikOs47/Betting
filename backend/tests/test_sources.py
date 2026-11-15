from datetime import date, datetime

from app.sources import bref, injuries, odds_api
from app.teams import norm_last_first, norm_name


def _row(pid, name, mp, pts, extra=""):
    return (f'<tr><th data-append-csv="{pid}" data-stat="player"><a>{name}</a></th><td data-stat="mp" csk="{mp}">x</td>'
            f'<td data-stat="fg">3</td><td data-stat="fga">7</td><td data-stat="fg3">1</td><td data-stat="fg3a">3</td><td data-stat="ft">2</td>'
            f'<td data-stat="fta">2</td><td data-stat="orb">1</td><td data-stat="drb">4</td><td data-stat="trb">5</td><td data-stat="ast">6</td>'
            f'<td data-stat="stl">1</td><td data-stat="blk">0</td><td data-stat="tov">2</td><td data-stat="pf">3</td>'
            f'<td data-stat="pts">{pts}</td><td data-stat="plus_minus">+4</td>{extra}</tr>')


def _team(abbr, pts):
    basic = (f'<table id="box-{abbr}-game-basic"><tbody>{_row(abbr.lower() + "a01", "Starter One", 2100, 20)}'
             f'<tr class="thead"><th>Reserves</th></tr>{_row(abbr.lower() + "b01", "Bench Guy", 600, 9)}'
             f'<tr><th data-append-csv="{abbr.lower()}c01" data-stat="player"><a>Dnp Guy</a></th><td data-stat="reason">Did Not Play</td></tr>'
             f'</tbody><tfoot><tr><th>Team Totals</th><td data-stat="mp">240</td><td data-stat="fg">40</td><td data-stat="fga">85</td>'
             f'<td data-stat="fg3">12</td><td data-stat="fg3a">33</td><td data-stat="ft">18</td><td data-stat="fta">22</td><td data-stat="orb">9</td>'
             f'<td data-stat="drb">33</td><td data-stat="trb">42</td><td data-stat="ast">25</td><td data-stat="stl">7</td><td data-stat="blk">4</td>'
             f'<td data-stat="tov">12</td><td data-stat="pf">19</td><td data-stat="pts">{pts}</td></tr></tfoot></table>')
    adv = (f'<table id="box-{abbr}-game-advanced"><tbody><tr><th data-append-csv="{abbr.lower()}a01"></th><td data-stat="usg_pct">28.8</td>'
           f'<td data-stat="off_rtg">120</td><td data-stat="def_rtg">110</td></tr></tbody><tfoot><tr><th></th><td data-stat="off_rtg">112.5</td>'
           f'<td data-stat="def_rtg">104.1</td></tr></tfoot></table>')
    return basic + adv


BOX = ('<html><div class="scorebox_meta"><div>7:30 PM, November 1, 2024</div></div>'
       '<!-- <table id="line_score"><tbody><tr><th>BOS</th><td>30</td><td>30</td><td>25</td><td>25</td><td>8</td><td>118</td></tr></tbody></table>'
       '<table id="four_factors"><tbody><tr><th>BOS</th><td data-stat="pace">99.1</td><td data-stat="ft_rate">.2</td></tr>'
       '<tr><th>CHO</th><td data-stat="pace">99.1</td><td data-stat="ft_rate">.25</td></tr></tbody></table> -->'
       + _team("BOS", 118) + _team("CHO", 110) +
       '<div><strong>Inactive:&nbsp;</strong><span><strong>BOS</strong>&nbsp;</span><a href="/players/h/horfoal01.html">Al Horford</a>'
       '<span><strong>CHO</strong>&nbsp;</span><a href="/players/m/millebr02.html">Brandon Miller</a>, <a href="/players/w/willima07.html">Mark Williams</a></div></html>')


def test_parse_boxscore():
    b = bref.parse_boxscore(BOX, "202411010CHO")
    assert (b["away"], b["home"], b["periods"]) == ("BOS", "CHO", 5)  # 4 quarters + 1 OT
    assert b["line_score"] is None  # fixture lists only one team row
    assert b["tip_et"] == "2024-11-01T19:30:00"
    assert b["teams"]["BOS"]["pts"] == 118 and b["teams"]["CHO"]["pace"] == 99.1 and b["teams"]["BOS"]["off_rtg"] == 112.5
    p = {x["player_id"]: x for x in b["players"]}
    assert p["bosa01"]["starter"] and p["bosa01"]["seconds"] == 2100 and p["bosa01"]["usg_pct"] == 28.8 and p["bosa01"]["plus_minus"] == 4
    assert not p["bosb01"]["starter"] and p["bosb01"]["played"]
    assert not p["bosc01"]["played"] and p["bosc01"]["dnp_reason"] == "Did Not Play"
    assert [x["player_id"] for x in b["inactive"]["CHO"]] == ["millebr02", "willima07"]
    assert [x["player_id"] for x in b["inactive"]["BOS"]] == ["horfoal01"]


def test_parse_boxscore_rejects_wrong_home_team():
    import pytest
    from app.sources.http import SourceError
    with pytest.raises(SourceError):
        bref.parse_boxscore(BOX, "202411010BOS")


SCHEDULE = '''<table id="schedule"><tbody>
<tr><th data-stat="date_game" csk="202411010CHO">Fri</th><td data-stat="game_start_time">7:00p</td>
<td data-stat="visitor_team_name"><a href="/teams/BOS/2025.html">Boston Celtics</a></td><td data-stat="visitor_pts">124</td>
<td data-stat="home_team_name"><a href="/teams/CHO/2025.html">Charlotte Hornets</a></td><td data-stat="home_pts">109</td>
<td data-stat="box_score_text"><a href="/boxscores/202411010CHO.html">Box</a></td></tr>
<tr class="thead"><th>Playoffs</th></tr>
<tr><th data-stat="date_game" csk="202504200BOS">Sun</th><td data-stat="game_start_time">3:30p</td>
<td data-stat="visitor_team_name"><a href="/teams/ORL/2025.html">Orlando Magic</a></td><td data-stat="visitor_pts"></td>
<td data-stat="home_team_name"><a href="/teams/BOS/2025.html">Boston Celtics</a></td><td data-stat="home_pts"></td>
<td data-stat="box_score_text"></td></tr></tbody></table>'''


def test_parse_schedule():
    s = bref.parse_schedule(SCHEDULE, 2025)
    assert len(s) == 2
    assert s[0]["date"] == date(2024, 11, 1) and s[0]["home"] == "CHO" and s[0]["away"] == "BOS" and s[0]["has_box"]
    assert s[0]["start_et"] == "19:00" and s[0]["home_pts"] == 109 and not s[0]["playoffs"]
    assert s[1]["playoffs"] and not s[1]["has_box"] and s[1]["home_pts"] is None and s[1]["start_et"] == "15:30"
    assert bref.season_end_year(date(2024, 11, 1)) == 2025 and bref.season_end_year(date(2025, 4, 1)) == 2025


INJ = """Injury Report: 11/14/26 09:45 PM
GameDate GameTime Matchup Team PlayerName CurrentStatus Reason
11/14/2026 07:00(ET) DEN@NOP DenverNuggets HolmesII,DaRon Out GLeague-OnAssignment
Strawther,Julian Questionable Injury/Illness-LeftKneeSprain
NewOrleansPelicans Murray,Dejounte Out Injury/Illness-LeftAnkleSprain
Injury/Illness-BilateralPatellar
MurphyIII,Trey Doubtful
Tendinopathy
07:30(ET) DAL@CLE DallasMavericks NOTYETSUBMITTED
ClevelandCavaliers Mobley,Evan Probable Injury/Illness-Illness
Page1of9
11/15/2026 06:00(ET) LAL@BOS LosAngelesLakers James,LeBron Available Injury/Illness-Rest
"""


def test_parse_injury_report():
    rows = injuries.parse_report_text(INJ)
    got = [(str(r["game_date"]), r["team_id"], r["player_name"], r["status"]) for r in rows]
    assert got == [("2026-11-14", "DEN", "HolmesII,DaRon", "Out"), ("2026-11-14", "DEN", "Strawther,Julian", "Questionable"),
                   ("2026-11-14", "NOP", "Murray,Dejounte", "Out"), ("2026-11-14", "NOP", "MurphyIII,Trey", "Doubtful"),
                   ("2026-11-14", "CLE", "Mobley,Evan", "Probable"), ("2026-11-15", "LAL", "James,LeBron", "Available")]
    assert rows[3]["reason"] == "Tendinopathy"


def test_report_timestamp_regex_and_names():
    m = injuries.PDF_RE.search("https://ak-static.cms.nba.com/referee/injury/Injury-Report_2026-11-14_09_45PM.pdf")
    assert m.groups() == ("2026-11-14", "09", "45", "PM")
    assert norm_last_first("HolmesII,DaRon") == norm_name("DaRon Holmes II") == "daronholmes"
    assert norm_last_first("PierreJr.,Jaron") == norm_name("Jaron Pierre Jr.")
    assert norm_name("Kristaps Porziņģis") == "kristapsporzingis"
    assert norm_last_first("deLarrea,Sergio") == norm_name("Sergio de Larrea")


EVENT = {"id": "e1", "commence_time": "2026-11-15T00:10:00Z", "home_team": "Boston Celtics", "away_team": "Dallas Mavericks",
         "bookmakers": [{"key": "pinnacle", "last_update": "2026-11-14T20:00:00Z", "markets": [
             {"key": "h2h", "outcomes": [{"name": "Boston Celtics", "price": 1.5}, {"name": "Dallas Mavericks", "price": 2.7}]},
             {"key": "spreads", "outcomes": [{"name": "Boston Celtics", "price": 1.91, "point": -5.5}, {"name": "Dallas Mavericks", "price": 1.91, "point": 5.5}]},
             {"key": "totals", "outcomes": [{"name": "Over", "price": 1.9, "point": 228.5}, {"name": "Under", "price": 1.92, "point": 228.5}]},
             {"key": "player_points", "outcomes": [{"name": "Over", "description": "Jayson Tatum", "price": 1.87, "point": 27.5},
                                                   {"name": "Under", "description": "Jayson Tatum", "price": 1.95, "point": 27.5},
                                                   {"name": "Over", "description": "Broken", "price": 1.0, "point": 3.5}]},
             {"key": "unknown_market", "outcomes": [{"name": "x", "price": 2.0}]}]}]}


def test_flatten_odds_event():
    rows = odds_api.flatten_event(EVENT)
    assert len(rows) == 8  # invalid price and unknown market dropped
    by = {(r["market"], r["selection"]): r for r in rows}
    assert by[("moneyline", "home")]["price"] == 1.5 and by[("moneyline", "away")]["line"] is None
    assert by[("spread", "home")]["line"] == -5.5 and by[("spread", "away")]["line"] == 5.5
    assert by[("points", "over")]["player_name"] == "Jayson Tatum" and by[("points", "under")]["line"] == 27.5
    assert by[("total", "over")]["book_updated_at"] == datetime(2026, 11, 14, 20, 0)
    assert odds_api.estimate_cost(3) == 3 * odds_api.n_regions() and odds_api.estimate_cost(5, 2, historical=True) == 100 * odds_api.n_regions()
