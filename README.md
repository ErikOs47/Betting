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
