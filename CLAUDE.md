# NBA Betting Intelligence – Übergabe für Claude-Sitzungen

Analyse-Plattform für NBA-Wetten (Value Bets, Paper Betting). **Keine Gewinnversprechen, keine erfundenen
Daten, keine automatischen Echtgeldwetten.** Der Nutzer wettet manuell bei Tipico und nutzt fast nur die
Handy-Seite. Antworten an den Nutzer: Deutsch, ohne Einleitung, mit Begründung/Quellen; Irrtümer des
Nutzers sachlich korrigieren.

Vertiefung: `docs/ARCHITEKTUR.md`, `docs/METHODIK.md`, `docs/HANDY.md`, `docs/API_KOSTEN.md`, `README.md`.

## 1. Was wirklich läuft (Produktivpfad)

```
GitHub Actions (.github/workflows/update.yml, 10:15/10:38 + 15:05/15:28 UTC, je doppelt; auch manuell)
  └─ scripts/scheduled_update.sh
       1. Zustand vom Branch "data" holen (state/nbi.db.gz, state/artifacts/*.pkl.gz)
       2. app.cli scheduled-update: Ergebnisse → Injury Report → (Odds) → Training falls fällig
          → Prognosen → Tipps abrechnen/speichern → phone/data.json
       3. phone/build.py → phone/dist/index.html (+ data.json)
       4. Zustand + fertige Seite als EIN Commit per force-push auf Branch "data"
       5. abgerechnete Tipps als Monatsdateien auf Branch "history" (nur anhängen, nie force)
Claude-Aufgabe "NBA Update" (Trigger trig_01CLETnGpLupJ4XvLENTvpNb, Sonnet, 10:50 + 15:50 UTC)
  └─ scripts/publish_prep.sh  (holt phone/* vom Branch "data"; >3 h alt → rechnet selbst, ohne Sichern)
  └─ Artifact publish → https://claude.ai/artifact/2HRgzDTKyAJiNLFcoTUfrE  (private Handy-Seite)
```

- Standard-Branch ist `main` (seit 17.11.2026; geplante Workflows laufen nur vom Standard-Branch).
- Branches: `main` und `claude/nba-betting-platform-neo6i9` immer **beide** pushen
  (`git push origin HEAD:claude/nba-betting-platform-neo6i9 HEAD:main`). `data` nie von Hand bearbeiten.
- Die Handy-Seite ist statisches HTML (`phone/index.src.html` + eingebettetes `phone/nbmath.js`), lädt
  `data.json` relativ und rechnet Wahrscheinlichkeiten/EV/Kelly im Browser.
- Das volle Dashboard (FastAPI + Next.js + Postgres, `docker-compose.yml`) existiert und ist getestet,
  wird vom Nutzer aber nicht betrieben (kein Server). Nicht ohne Auftrag ausbauen.

## 2. Architektur (backend/app)

| Bereich | Dateien | Inhalt |
|---|---|---|
| Quellen | `sources/bref.py`, `injuries.py`, `odds_api.py`, `sportsdataio.py`, `http.py` | Basketball Reference (Spielplan, Boxscores, Viertel), offizielle NBA-Injury-PDFs, The Odds API (ohne Key, live ungetestet) |
| Ingest | `ingest/pipeline.py` | Upsert in DB, Aufholen verpasster Monate/Saisons, manuelle Quoten |
| DB | `db.py`, `settings_store.py` | SQLAlchemy; SQLite lokal/im Workflow, Postgres in Docker; neue Spalten über `_ADDED_COLUMNS` |
| Features | `features/build.py` | Leckagefrei: `shift(1)`-Rolling, As-of-Joins |
| Modelle | `ml/models.py`, `dist.py`, `registry.py`, `predict.py` | LightGBM + XGBoost; Spieler: Poisson-Mittel + NegBin-Dispersion; Spiel: Normalverteilung für Differenz/Summe mit Elo-Prior; Ensemble = Mittel |
| Wetten | `betting/probability.py`, `value.py`, `risk.py`, `odds_math.py`, `parlay.py`, `paper.py` | EV = p·Quote − 1, Edge, ¼ Kelly mit Limits, Wettsteuer, Paper Betting |
| Abgeleitete Märkte | `betting/doubles.py`, `betting/periods.py` | Double/Triple-Double (Gauß-Copula), Viertel/Halbzeit/Team-Punkte/HZ-Endstand (Regression auf OOS-Prognosen) |
| Tipps | `betting/suggestions.py` | Erzeugen, speichern, abrechnen, Statistik; lernt je Spielermarkt eine Logit-Steigung k |
| Backtest | `backtest/*` | Walk-forward ohne Leakage; speichert Tipp-Prior und Periodenmodell |
| Export | `export/phone.py` | baut `data.json` |
| Jobs | `tasks/jobs.py`, `scheduler.py`, `cli.py` | `scheduled-update` ist der Einstieg des Produktivpfads |
| API | `api/main.py` | nur fürs Dashboard |

Tipps: Spieler nur **Punkte, Double-Double, Triple-Double** (mehr bietet Tipico nicht). Team: Sieger,
Handicap, Über/Unter, Team-Punkte, 1. Halbzeit, Viertel, Halbzeit/Endstand.

## 3. Befehle

```bash
cd backend && ../.venv/bin/python -m pytest -q          # 61 Tests, ~100 s (braucht node)
python3 phone/build.py phone/data.json                   # Seite bauen → phone/dist, phone/preview
(cd phone/preview && python3 -m http.server 8090)        # Vorschau; mit Playwright bei 390 px prüfen
NBI_SKIP_RESTORE=1 bash scripts/scheduled_update.sh      # voller Lauf mit lokaler DB (pusht "data"!)
bash scripts/publish_prep.sh                             # gesicherte Seite holen
```

Veröffentlichen: Artifact-Tool, `url` wie oben, `file_path` `phone/dist/index.html`,
`files {"data.json": "phone/dist/data.json"}`. Vorher `read` (Seite und `data.json`); wird der Publish
abgelehnt, denselben Aufruf unverändert wiederholen. Danach `ArtifactComments watch on:false`.

## 4. Konventionen

- **Doppelte Mathematik:** `phone/nbmath.js` ist ein Port von Python (`ml/dist.py`,
  `betting/probability.py`, `odds_math.py`, `risk.py`, `periods.derived_prob`). Jede Änderung auf beiden
  Seiten machen; `tests/test_phone_math.py` prüft die Gleichheit.
- **Team-Tipps doppelt:** `suggestions.team_candidates` spiegelt den Team-Teil von `renderTips` in
  `phone/index.src.html` (gleiche Linienregel, `_jsround` = `Math.round`). Ändert man eine Seite, die andere
  mitziehen, sonst wird etwas anderes abgerechnet als angezeigt.
- Kein Leakage: Features nur aus Daten vor dem Spiel; neue Features brauchen einen Leakage-Test
  (`tests/test_features.py`).
- Fehlende/alte Daten deaktivieren Prognosen, statt geschätzt zu werden. Nie Platzhalterwerte erfinden.
- Secrets nur über `NBI_*`-Umgebungsvariablen im Backend, nie im Frontend oder in `data.json`.
- Basketball Reference: höchstens 20 Anfragen/Minute (3,3 s Abstand in `http.py`), 429 nie wiederholen.
- Code und Kommentare englisch, UI-Texte deutsch (Zahlen mit Komma, `de-DE`). Kommentare nur für das
  Warum. Handy-Seite: nur Farb-Tokens aus `:root`, Dark-first, kein horizontales Scrollen bei 390 px.
- Commits: englisch, kurzer Betreff + Begründung; keine Modellnamen in Code oder Doku.
- Vor jedem Push Tests laufen lassen; UI-Änderungen in der Vorschau mit Playwright ansehen.

## 5. Bekannte Schwachstellen

1. **Sieger-Modell ≈ Elo-Baseline.** Im Walk-forward kein Vorteil; Spieler-Punkte nur leicht besser
   (MAE 4,49 vs. 4,70). Ein Vorteil gegen Buchmacherquoten ist **nicht** nachgewiesen.
2. **Keine echten Quoten.** Ohne Odds-API-Key gibt es kein automatisches EV/CLV; „ab x,xx“ ist nur die
   Mindestquote laut Modell. Der Odds-API-Client ist nie live gelaufen.
3. **Team-Tipps sind keine Value-Tipps** und die Linien stammen vom Modell, nicht von Tipico. Die Bilanz
   misst dort Kalibrierung an Modell-Linien, nicht Gewinn.
4. **Abgeleitete Märkte** (Viertel, Halbzeit, Team-Punkte, HZ/Endstand): einfache lineare Skalierung mit
   Normalverteilung, n = 1.348 Spiele, nicht gegen Marktquoten geprüft. Double/Triple-Double: feste
   Korrelationsmatrix für alle Spieler.
5. **Ergebnisse erst gegen 11 Uhr deutscher Zeit** (BRef-Batch ~5 Uhr ET). Der Workflow-Zeitplan ist UTC
   und verschiebt sich in der Sommerzeit um eine Stunde; GitHub-Cron kann sich zusätzlich verspäten.
6. **Zustand = ein force-gepushter Commit** auf `data` (~26 MB, DB wächst; GitHub-Limit 100 MB/Datei).
   Nur die Tipp-Bilanz ist dauerhaft gesichert (Branch `history`, Wiederherstellung mit
   `app.cli history-import`); DB und Modelle haben kein Backup. Zwei gleichzeitige Läufe würden sich
   überschreiben (Workflow hat `concurrency`, ein manueller Lauf aus einer Sitzung nicht).
   GitHub-Cron ist unzuverlässig (am 16.11. einen Termin ausgelassen) → jeder Termin doppelt.
7. **Claude-Aufgabe kann nicht pushen** (kein Repo angebunden, 403). Ihr Fallback-Lauf speichert nichts.
8. **Team-Tipps vom 15.11.2026 sind nachgetragen** (gleiche Regel, Prognose von vor Spielbeginn), nicht
   live gespeichert.
9. `stats.nba.com` und `cdn.nba.com` sind aus Rechenzentren gesperrt → BRef ist die einzige
   Ergebnisquelle (Single Point of Failure, Scraping).
10. Team-Tipps haben keine gelernte Korrektur; Spieler-k stützt sich noch fast nur auf den Backtest-Prior.
11. Docker-Stack wurde nur teilweise in Containern getestet (Docker-Hub-Limit in der Cloud-Umgebung).

## 6. Offene To-dos

- [ ] Ersten **geplanten** Workflow-Lauf und den folgenden Claude-Publish prüfen (bisher nur manuell
      ausgelöst getestet; bis 17.11. startete kein einziger geplanter Lauf. Danach Standard-Branch von
      `claude/...` auf `main` umgestellt und Workflow aus-/eingeschaltet. Falls es weiter nicht startet:
      Claude-Aufgabe startet den Workflow per API mit Fine-grained Token, nur Actions read/write).
- [ ] Sommerzeit: Cron-Zeiten des Workflows und des Triggers anpassen oder zwei Zeitfenster vorsehen.
- [ ] Team-Tipps: nach einigen hundert Ergebnissen Kalibrierung je Kategorie auswerten, dann Korrektur
      wie bei Spielern (`learned_k`) einführen.
- [ ] Bilanz um Zeitfenster erweitern (z. B. 7/30 Tage) und Verlauf anzeigen.
- [ ] Größe der DB beobachten; bei Bedarf alte Prognosen/Quoten-Snapshots ausdünnen.
- [ ] Zweite Ergebnisquelle als Fallback für BRef evaluieren.
- [ ] Optional, nur auf Wunsch: Odds-API-Key einbinden und Client live testen (Kosten: `docs/API_KOSTEN.md`).

## 7. Nicht tun

- Keine echten Wetten platzieren oder automatisieren, kein Martingale, Limits (¼ Kelly, 1 %/Wette,
  2 %/Spiel, 3 % Tagesverlust) nicht aufweichen.
- Tests nicht abschalten, um grün zu werden.
- Zeitplan, Modell der Claude-Aufgabe oder Kosten verursachende Dienste nicht ohne Rückfrage ändern
  (stündliche Läufe hat der Nutzer als zu teuer abgelehnt).
- Keine Pull Requests ohne ausdrückliche Bitte.
