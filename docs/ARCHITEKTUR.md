# Architektur

## 1. Überblick

```
                ┌────────────────────────── Browser ──────────────────────────┐
                │  Next.js-Dashboard (React, TypeScript, Tailwind, Dark Mode) │
                └──────────────────────────────┬──────────────────────────────┘
                                               │  /api/* (nur eigener Server)
                         ┌─────────────────────▼─────────────────────┐
                         │ Next.js Route-Handler (Server-Proxy)      │  fügt optional das API-Token hinzu;
                         └─────────────────────┬─────────────────────┘  keine Schlüssel im Browser
                                               │
┌──────────────┐   Celery-Tasks   ┌────────────▼────────────┐
│ Celery Beat  ├─────────────────►│ FastAPI (app.api.main)  │
│ + Worker     │                  └────────────┬────────────┘
└──────┬───────┘                               │
       │            ┌──────────────────────────┼─────────────────────────────┐
       ▼            ▼                          ▼                             ▼
  app.tasks.jobs  app.ingest ──► app.features ──► app.ml ──► app.betting ──► app.backtest
       │            │   (Quellen)   (leakage-frei) (Modelle)  (Value, Risiko,   (Walk-forward,
       │            │                                          Paper, Kombi)     Wett-Simulation)
       │            ▼
       │   Basketball Reference · NBA Injury Report · The Odds API · SportsDataIO · stats.nba.com
       ▼
  PostgreSQL (alle Fakten, Quoten-Snapshots, Prognosen, Wetten)   Artefakte (Modelle, OOS-Prognosen)   optional Redis (nur für Celery)
```

Betrieb: Standardmäßig läuft der Zeitplaner im Backend-Prozess (`NBI_SCHEDULER=internal`, `app/tasks/scheduler.py`),
sodass ein Server nur drei Container braucht (PostgreSQL, Backend, Web; optional Caddy für HTTPS). Bei leerer
Datenbank lädt er zuerst drei Saisons und trainiert. Celery + Redis (`app/tasks/celery_app.py`) bleiben als
Alternative für größere Installationen erhalten. Die Web-App ist eine installierbare PWA mit optionalem
Passwort-Login (`frontend/proxy.ts`, Cookie mit HMAC, 90 Tage gültig).

Datenfluss eines Aktualisierungszyklus (`app.tasks.jobs.refresh_all`):

1. **Ergebnisse** – Spielplan + neue Boxscores (Basketball Reference).
2. **Verletzungen** – neuester offizieller NBA Injury Report (PDF), jede Fassung bleibt mit Zeitstempel gespeichert.
3. **Quoten** – Snapshot aller Märkte (The Odds API), append-only mit `fetched_at`.
4. **Training** – nur wenn das aktive Modell älter als 7 Tage Spielbetrieb ist.
5. **Prognosen** – Verteilungen für alle anstehenden Spiele, versioniert gespeichert.
6. **Abrechnung** – offene Paper-Wetten gegen echte Boxscores, CLV gegen den letzten Snapshot vor Tip-off.

Jeder Schritt läuft isoliert; ein Ausfall wird in `source_status` protokolliert und im Dashboard angezeigt.

## 2. Ordnerstruktur

```
backend/
  app/
    config.py            Einstellungen aus Umgebungsvariablen (NBI_*)
    db.py                SQLAlchemy-Schema + Sessions
    settings_store.py    zur Laufzeit änderbare Filter/Limits/Kill Switch (mit harten Grenzen)
    teams.py             Team-Referenz, Namensnormalisierung
    sources/             http.py (Rate-Limit, Retry, Cache) · bref.py · injuries.py · odds_api.py · sportsdataio.py · nba_stats.py
    ingest/              pipeline.py (Upserts, Quellen-Health, Credit-Budget) · download_bref.py (Massendownload)
    features/build.py    leakage-freies Feature-Engineering (Training und Live über denselben Code)
    ml/                  dist.py (Verteilungen) · models.py (LightGBM/XGBoost) · registry.py · predict.py
    betting/             odds_math.py · probability.py · value.py · risk.py · parlay.py · paper.py
    backtest/            walkforward.py · betsim.py · metrics.py
    tasks/               jobs.py · celery_app.py (Beat-Zeitplan)
    api/main.py          REST-API
    cli.py               Kommandozeile
  tests/                 Pytest (Mathematik, Parser, Leakage, Value, Kombi, Paper, Backtest, API)
frontend/
  app/                   7 Seiten + /api/[...path] Server-Proxy
  components/            UI-Bausteine, SVG-Charts
  e2e/                   Playwright
docs/                    Architektur, API-/Kostenanalyse, Methodik, Entwicklungsplan
docker-compose.yml       db, api (mit Zeitplaner), web, optional caddy (HTTPS, Profil `https`)
```

## 3. Datenbankschema (PostgreSQL)

| Tabelle | Inhalt | Schlüssel |
|---|---|---|
| `teams`, `players` | Referenzdaten (IDs von Basketball Reference) | `id` |
| `games` | Spielplan + Ergebnis, Tip-off (UTC), Status | `id` |
| `team_game_stats` | Team-Boxscore inkl. Pace, Off/Def-Rating, Four Factors | (`game_id`,`team_id`) |
| `player_game_stats` | Spieler-Boxscore inkl. Sekunden, Starter, Usage, DNP-Grund | (`game_id`,`player_id`) |
| `game_inactives` | offiziell inaktive Spieler je gespieltem Spiel | (`game_id`,`player_id`) |
| `injury_reports`, `injury_entries` | jede Fassung des Injury Reports mit Veröffentlichungszeit | `id` |
| `odds_events` | Zuordnung Odds-API-Event ↔ Spiel | `id` |
| `odds_snapshots` | **append-only** Quoten: Buchmacher, Markt, Spieler, Seite, Linie, Preis, `fetched_at`, `historical` | `id`, Index `ix_odds_lookup` |
| `api_usage` | verbrauchte Credits je Aufruf (Budgetkontrolle) | `id` |
| `model_versions` | Version, Algorithmus, Trainingszeitraum, Parameter, Features, Metriken, Daten-Hash, Code-SHA, Artefaktpfad | `id`, `version` unique |
| `predictions` | gespeicherte Verteilung je Spiel/Markt/Spieler, exakte Eingaben, Hash, Datenqualität, `enabled`, `is_latest` | `id` |
| `bets` | Paper-Wetten (Legs, Einsatz, Quote, Modell-p, EV, Edge, Confidence, Status, G/V, CLV) | `id` |
| `bankroll_ledger` | lückenloses Kontobuch (Einsatz, Auszahlung, Erstattung) | `id` |
| `settings` | Filter, Limits, Kill Switch, Automatisierung | `key` |
| `source_status` | letzter Versuch/Erfolg/Fehler je Datenquelle | `source` |
| `backtest_runs` | Konfiguration + alle Out-of-sample-Metriken | `id` |

Grundsätze: Quoten und Prognosen werden nie überschrieben (Historie bleibt), alle Zeitstempel sind UTC, Fremdschlüssel
werden von PostgreSQL erzwungen (die Testsuite läuft gegen SQLite **und** PostgreSQL).

## 4. Sicherheit und Betrieb

- API-Schlüssel nur als Server-Umgebungsvariablen (`.env`, nicht im Repository). Das Frontend spricht ausschließlich
  den eigenen `/api`-Proxy an; `GET /api/settings` liefert nur `…_key_set: true/false`.
- Optionales `NBI_API_TOKEN` schützt alle schreibenden Endpunkte; der Web-Container setzt es serverseitig.
- Kill Switch (`POST /api/kill-switch`): blockiert sofort jede neue Wette und jeden Einsatzvorschlag.
- Es existiert kein Codepfad für Echtgeldwetten (`bets.mode` ist immer `paper`).
- Einstellungen haben harte Obergrenzen (z. B. max. 5 % pro Wette), damit ein Tippfehler das Risikomanagement nicht aushebelt.
- Datenqualität: fehlende Ergebnisse vergangener Spiele deaktivieren alle Prognosen; ein veralteter Injury Report senkt
  Datenqualität und Confidence; veraltete Quoten (> 15 min) fallen durch den Filter.

## 5. Entwicklungsplan

| Phase | Inhalt | Status |
|---|---|---|
| 1 | Architektur, Schema, Quellen- und Kostenanalyse | erledigt |
| 2 | Ingestion (Boxscores, Injury Report, Odds-Client, Health/Budget) | erledigt, mit echten Daten geprüft (Odds-Client nur gegen dokumentiertes Format getestet – kein Schlüssel vorhanden) |
| 3 | Leakage-freie Features, Verteilungsmodelle (LightGBM + XGBoost), Registry | erledigt |
| 4 | Value-Engine, Confidence, Risiko, Paper Betting, CLV | erledigt |
| 5 | Monte-Carlo/Copula für Kombinationen, Korrelationsschätzung | erledigt |
| 6 | Walk-forward-Backtest, Kalibrierung, Wett-Simulation | erledigt; ROI/CLV-Teil braucht historische Quoten |
| 7 | API, Dashboard, Docker, Tests (Pytest, Playwright) | erledigt |
| 8 | Nächste Schritte | siehe README „Grenzen und nächste Schritte“ |
