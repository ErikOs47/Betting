# NBA Betting Intelligence

Statistische Analyseplattform für NBA-Wettmärkte: Wahrscheinlichkeitsmodelle für Spieler-Props (Punkte, Rebounds,
Assists, Dreier, PRA), Moneyline, Handicap und Over/Under, eine Value-Bet-Engine mit Margenbereinigung, Kombinations­wetten
mit Korrelationen, Risikomanagement (¼ Kelly mit harten Limits), Paper Betting und Walk-forward-Backtesting – als
Web-Dashboard.

> **Keine Gewinnversprechen.** Das System platziert keine Echtgeldwetten (nur Paper Betting). Ob die Modelle gegen echte
> Buchmacherquoten profitabel sind, ist **nicht** nachgewiesen – siehe [Ergebnisse](#ergebnisse-mit-echten-daten) und
> [Grenzen](#grenzen-und-nächste-schritte). Glücksspiel kann süchtig machen; Hilfe in Deutschland: 0800 1 37 27 00 (BZgA).

## Inhalt

- [Schnellstart mit Docker](#schnellstart-mit-docker)
- [Lokale Installation ohne Docker](#lokale-installation-ohne-docker)
- [Daten laden, trainieren, backtesten](#daten-laden-trainieren-backtesten)
- [Dashboard](#dashboard)
- [Ergebnisse mit echten Daten](#ergebnisse-mit-echten-daten)
- [Tests](#tests)
- [Grenzen und nächste Schritte](#grenzen-und-nächste-schritte)
- Weitere Dokumente: [Architektur](docs/ARCHITEKTUR.md) · [Methodik](docs/METHODIK.md) · [Datenquellen und Kosten](docs/API_KOSTEN.md)

## Schnellstart mit Docker

Voraussetzung: Docker mit Compose.

```bash
cp .env.example .env          # NBI_ODDS_API_KEY eintragen (optional, aber nötig für Buchmacherquoten)
docker compose up --build -d  # PostgreSQL, Redis, API, Celery-Worker/Beat, Web

# Erstbefüllung (einmalig, siehe unten zur Dauer)
docker compose exec api python -m app.cli ingest 2025 2026 2027
docker compose exec api python -m app.cli injuries
docker compose exec api python -m app.cli train
docker compose exec api python -m app.cli backtest
docker compose exec api python -m app.cli predict
```

Dashboard: http://localhost:3000 · API-Dokumentation: http://localhost:8000/docs

Danach hält der Worker alles selbst aktuell (Ergebnisse alle 30 min, Injury Report alle 15 min, Quoten nach
eingestelltem Intervall und Credit-Budget, Prognosen nach jeder Aktualisierung, Abrechnung der Paper-Wetten,
wöchentlicher Backtest).

## Lokale Installation ohne Docker

Voraussetzungen: Python ≥ 3.12, Node ≥ 22. Ohne weitere Konfiguration wird SQLite verwendet (`data/nbi.db`);
für PostgreSQL `NBI_DATABASE_URL=postgresql+psycopg://user:pass@host/db` setzen.

```bash
python -m venv .venv && .venv/bin/pip install -r backend/requirements.txt
cp .env.example .env

cd backend
../.venv/bin/python -m app.cli ingest 2025 2026 2027   # Saison-Endjahre
../.venv/bin/python -m app.cli injuries
../.venv/bin/python -m app.cli train
../.venv/bin/python -m app.cli backtest
../.venv/bin/python -m app.cli predict
../.venv/bin/uvicorn app.api.main:app --port 8000      # API

cd ../frontend
npm install && npm run build && npm start              # Dashboard auf :3000 (BACKEND_URL=http://localhost:8000)
```

Automatische Aktualisierung ohne Docker: `celery -A app.tasks.celery_app worker -B` (benötigt Redis) oder per Cron
`python -m app.cli refresh`.

## Daten laden, trainieren, backtesten

| Befehl (`python -m app.cli …`) | Zweck |
|---|---|
| `ingest 2025 2026 2027` | Spielplan + Boxscores der Saisons (Endjahr). Bereits geladene Spiele werden übersprungen. |
| `injuries` | neuesten offiziellen NBA Injury Report laden |
| `odds` | aktuellen Quoten-Snapshot speichern (The Odds API, verbraucht Credits) |
| `historical-odds 2025-10-21 2026-04-12 [--props]` | historische Quoten nachladen (bezahlter Tarif; 10 Credits je Markt und Region) |
| `train` | LightGBM- und XGBoost-Modelle für Spieler und Spiele trainieren und versionieren |
| `backtest` | Walk-forward-Backtest; speichert Metriken, Out-of-sample-Prognosen und Korrelationen |
| `predict` | Verteilungen für anstehende Spiele berechnen und speichern |
| `refresh` | ein kompletter Zyklus: Ergebnisse, Verletzungen, Quoten, ggf. Training, Prognosen, Abrechnung |

**Dauer der Erstbefüllung:** Basketball Reference erlaubt höchstens 20 Anfragen pro Minute; das System lädt deshalb
einen Boxscore alle 3,3 Sekunden. Eine komplette Saison (≈ 1 320 Spiele inkl. Playoffs) dauert ≈ 75 Minuten. Der
Download ist jederzeit abbrechbar und setzt beim nächsten Aufruf fort.

## Dashboard

| Seite | Inhalt |
|---|---|
| Übersicht | Spiele des Tages mit Modellprognose, Bankroll, ROI, Trefferquote, Top Value Bets, Warnungen zu Datenquellen |
| Value Finder | alle Kandidaten mit bester Quote, fairer Quote, Modell- und Marktwahrscheinlichkeit, Edge, EV, Confidence (inkl. Aufschlüsselung), Einsatzvorschlag; Paper-Wette per Klick |
| Spielanalyse | Team-Form, Matchup, direkte Duelle, Injury Report, Spielerprognosen mit Bandbreiten; **Quote prüfen**: Linie und Quote manuell eingeben → Wahrscheinlichkeit, faire Quote, Edge, EV, Einsatz, vollständige Verteilung |
| Bet Builder | Einzel- und Kombinationswetten (auch Same Game Parlays) mit simulierter gemeinsamer Wahrscheinlichkeit, Korrelationsfaktor und fairer Kombiquote |
| Performance | Gewinn/Verlust, ROI, Yield, Trefferquote, Drawdown, CLV, Bankroll-Verlauf, alle Wetten |
| Backtesting | Out-of-sample-Vergleich LightGBM / XGBoost / Baselines, Kalibrierkurven, Fold-Verlauf, geschätzte Korrelationen, Wett-Simulation (sobald historische Quoten vorliegen) |
| Einstellungen | Filter, Einsatzlimits, Automatisierung, Status der Datenquellen, Credit-Verbrauch, manuelle Jobs, Modellversionen |

Der Kill Switch (oben rechts) blockiert sofort alle neuen Wetten und Einsatzvorschläge.

## Ergebnisse mit echten Daten

<!-- RESULTS -->
Stand: Lauf `walkforward-2-saisons` vom 15.11.2026, Code `fd301b9`. Datenbasis: 1505 gespielte Partien (Saisons 2025-26, 2026-27), Quelle Basketball Reference.

Walk-forward  expandierendes Fenster  6 Blöcke à 30 Tage. Out-of-sample-Zeitraum 2026-02-24 bis 2026-11-13: **13 888 Spieler-Spiele, 568 Partien**.

### Spieler-Props (out-of-sample)

| Markt | MAE LightGBM | MAE XGBoost | MAE Ø letzte 10 | MAE Saison-Ø | Abdeckung 50/80/90 % | Brier (Ensemble) | Brier Basisrate | ECE |
|---|---|---|---|---|---|---|---|---|
| Punkte | 4.526 | 4.538 | 4.776 | 4.865 | 49 / 79 / 89 | 0.2346 | 0.2469 | 2.0 % |
| Rebounds | 1.852 | 1.861 | 1.927 | 1.936 | 50 / 80 / 90 | 0.2355 | 0.2465 | 1.0 % |
| Assists | 1.325 | 1.333 | 1.381 | 1.397 | 50 / 80 / 90 | 0.2298 | 0.2449 | 1.4 % |
| Dreier | 0.887 | 0.889 | 0.898 | 0.902 | 50 / 80 / 90 | 0.2150 | 0.2364 | 1.3 % |
| PRA | 5.914 | 5.926 | 6.321 | 6.468 | 50 / 80 / 89 | 0.2335 | 0.2473 | 1.8 % |

### Spielmärkte (out-of-sample)

| Modell | Moneyline Brier | Log Loss | Trefferquote | ECE | Margin-MAE | Total-MAE |
|---|---|---|---|---|---|---|
| LightGBM | 0.1834 | 0.5427 | 71.1 % | 6.1 % | 11.65 | 14.91 |
| XGBoost | 0.1847 | 0.5463 | 71.1 % | 6.7 % | 11.65 | 14.73 |
| Ensemble | 0.1835 | 0.5432 | – | 6.2 % | – | – |
| Elo-Baseline | 0.1796 | 0.5377 | 74.8 % | 4.4 % | 11.67 | 16.34 (Pace×Effizienz) |
| konstante Heimquote | 0.2445 | 0.6822 | – | – | – | – |

### Geschätzte Korrelationen (Auszug)

Gleicher Spieler Punkte↔Assists +0.19, Punkte↔Rebounds +0.29, Punkte↔Dreier +0.57; Mitspieler Punkte↔Punkte -0.03; Punkte↔Margin des eigenen Teams +0.12, Punkte↔Gesamtpunkte +0.13.

### Wett-Simulation (ROI, Yield, CLV, Drawdown)

**Nicht verfügbar:** Es sind keine historischen Quoten gespeichert, daher werden keine ROI-/CLV-Werte ausgewiesen.

### Einordnung

- **Spieler-Props:** Beide Modelle sind in jedem Markt genauer als die naiven Baselines (Ø letzte 10 Spiele, Saison-Ø),
  und die Prognoseintervalle treffen ihre Sollabdeckung (50/80/90 %) nahezu exakt – die Verteilungen sind also brauchbar
  kalibriert. Der Vorsprung im MAE ist klein (wenige Prozent); das ist bei Zählstatistiken mit hoher Zufallsstreuung normal.
- **Spielmärkte:** Das Boosting-Modell ist **nicht besser als eine einfache Elo-Wertung**. Die Unterschiede zu Elo liegen
  im Bereich des Stichprobenrauschens. Für Moneyline/Handicap gibt es damit keinen Beleg für einen Modellvorteil; das
  schlägt sich in einer niedrigen Kalibrierungs-Komponente der Confidence nieder, sodass diese Märkte den
  Standardfilter (Confidence >= 70) seltener passieren. Nur bei Gesamtpunkten ist das Modell klar besser als die
  einfache Pace-x-Effizienz-Schätzung.
- **Wichtig:** Diese Zahlen messen Prognosegüte gegen echte Ergebnisse, **nicht** Profitabilität gegen Buchmacher.
  Buchmacherlinien enthalten mehr Information als jede hier verwendete Baseline. Ob ein Vorteil gegenüber dem Markt
  besteht, lässt sich erst mit historischen Quoten bzw. über den CLV gesammelter Paper-Wetten beurteilen.
<!-- /RESULTS -->

## Tests

```bash
cd backend && ../.venv/bin/python -m pytest            # 49 Tests (SQLite)
NBI_TEST_DATABASE_URL=postgresql+psycopg://nbi:nbi@localhost/nbi ../.venv/bin/python -m pytest   # dieselben Tests gegen PostgreSQL

cd frontend && npm run typecheck && npm run e2e        # Playwright; benötigt laufende API (:8000) und Web (:3000)
```

Abgedeckt sind u. a.: EV/Kelly/Margenbereinigung, Verteilungen und Push-Logik, alle Einsatzlimits und der Kill Switch,
Parser (Boxscore, Spielplan, Injury Report, Odds-API-Format), **Leakage-Test** der Features, Value-Engine und Filter,
Korrelationen in Kombinationen, Paper-Wetten inkl. Abrechnung/Storno/CLV, Wett-Simulation ohne Nach-Tip-off-Quoten,
Reproduzierbarkeit gespeicherter Prognosen, Deaktivierung bei veralteten Daten, API-Validierung.

Die automatisierten Tests verwenden eine synthetische Testliga (`backend/tests/synth.py`). Diese Daten existieren nur
in den Tests; die Anwendung selbst arbeitet ausschließlich mit geladenen echten Daten.

## Grenzen und nächste Schritte

1. **Kein ROI-Nachweis ohne historische Quoten.** Historische Quoten gibt es bei The Odds API nur in bezahlten Tarifen.
   Solange keine vorliegen, weist der Backtest bewusst keine ROI-, Yield-, CLV- oder Drawdown-Werte aus. Nächster
   Schritt: Schlüssel eintragen, `historical-odds` für den Testzeitraum laden, `backtest` erneut ausführen.
2. **Quoten-Anbindung nicht live getestet.** In der Entwicklungsumgebung lag kein API-Schlüssel vor. Der Client folgt der
   v4-Dokumentation und ist gegen das dokumentierte Antwortformat getestet; der erste echte Abruf sollte beobachtet werden.
3. **Datenquelle Basketball Reference** ist für den privaten Gebrauch gedacht; die Nutzungsbedingungen untersagen Tools
   auf Basis gescrapter Daten ohne Erlaubnis. Für einen öffentlichen/kommerziellen Betrieb eine lizenzierte Quelle
   einsetzen (Details in [API_KOSTEN.md](docs/API_KOSTEN.md)).
4. **stats.nba.com** ist aus Rechenzentren nicht erreichbar und daher nur optionale Zweitquelle.
5. **Starting Lineups** werden nicht aus einem bestätigten Feed gelesen, sondern über die Starter-Historie angenähert.
6. **Späte Ausfälle:** Das Training kennt die endgültige Inaktiv-Liste, live gilt der jeweils aktuelle Injury Report
   (siehe [Methodik](docs/METHODIK.md), Abschnitt 1). Wetten kurz vor Tip-off sind dadurch besser abgesichert als frühe.
7. **Docker-Images** konnten in der Entwicklungsumgebung nicht vollständig gebaut werden (Build-Container ohne
   Netzwerk); die Compose-Datei ist validiert, die Dienste wurden nativ gegen PostgreSQL 16 getestet.
8. Empfohlene Reihenfolge vor jeder Überlegung zu echtem Geld: mehrere hundert Paper-Wetten sammeln und prüfen, ob der
   durchschnittliche **CLV positiv** ist. Ohne positiven CLV ist kein langfristiger Vorteil zu erwarten.
