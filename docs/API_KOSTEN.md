# Datenquellen- und Kostenanalyse

Stand der Prüfung: 15.11.2026. Erreichbarkeit wurde aus der Entwicklungsumgebung (Cloud-Rechenzentrum) real getestet.
Preise ändern sich – vor einem Abo die verlinkte Preisseite prüfen.

## Ergebnis in Kürze

| Quelle | Rolle im System | Kosten | Historie | Spieler-Props | Prüfergebnis |
|---|---|---|---|---|---|
| **Basketball Reference** | Primärquelle Spielplan + Boxscores (Basic, Advanced, Inactives) | kostenlos | Jahrzehnte | – (Statistiken, keine Quoten) | erreichbar; Limit 20 Anfragen/Minute |
| **Offizieller NBA Injury Report** | Verletzungen/Verfügbarkeit, mehrfach täglich | kostenlos | laufende Saison als PDFs | – | erreichbar, PDF wird geparst |
| **The Odds API** | Quoten: Moneyline, Spread, Total, Props; historische Snapshots | 0 – 249 $/Monat | Hauptmärkte ab Juni 2020, Props ab 3. Mai 2023 (nur bezahlte Tarife) | ja | Client implementiert; **kein Schlüssel vorhanden → nicht live getestet** |
| **stats.nba.com** | optionale Zweitquelle | kostenlos | ab 1996 | – | **aus dem Rechenzentrum nicht erreichbar** (Timeout) – blockiert üblicherweise Cloud-IPs |
| **SportsDataIO** | optional: bestätigte Starting Lineups, Projektionen | Odds-Tarif ca. 99 $/Monat laut Entwicklerseite; größere Pakete über Vertrieb | je nach Vertrag | ja (kostenpflichtig) | Client implementiert; Testzugang liefert **verfälschte („scrambled“) Daten** und ist für Modelle unbrauchbar |

## The Odds API – Kostenmodell

Tarife (Credits pro Monat): Starter kostenlos 500 · 20K für 30 $ · 100K für 59 $ · 5M für 119 $ · 15M für 249 $.

Credit-Kosten pro Aufruf:

- Event-Liste: kostenlos.
- Hauptmärkte (`/odds`): 1 Credit × Märkte × Regionen.
- Props (`/events/{id}/odds`): 1 Credit × Märkte × Regionen **pro Spiel**.
- Historisch: **10** Credits × Märkte × Regionen (Props zusätzlich pro Spiel); nur in bezahlten Tarifen.

Rechenbeispiele mit der Standardkonfiguration dieses Systems (2 Regionen `eu,us`; 3 Hauptmärkte; 5 Prop-Märkte):

| Vorgang | Rechnung | Credits |
|---|---|---|
| 1 Snapshot Hauptmärkte | 3 × 2 | 6 |
| Props für 1 Spiel | 5 × 2 | 10 |
| Ein Spieltag mit 8 Spielen, je 1 Prop-Abruf + 4 Hauptmarkt-Snapshots | 8 × 10 + 4 × 6 | 104 |
| Ein Monat (≈ 30 Spieltage) | 30 × 104 | ≈ 3 100 |
| Historie Hauptmärkte, 1 Snapshot/Spieltag, 1 Saison (≈ 170 Spieltage) | 170 × 3 × 2 × 10 | ≈ 10 200 |
| Historie Props, 1 Snapshot/Spiel, 1 Saison (≈ 1 230 Spiele) | 1 230 × 5 × 2 × 10 | ≈ 123 000 |

Folgerungen:

- Der **kostenlose Tarif (500 Credits)** reicht für wenige Tage Hauptmärkte, nicht für laufende Props. Das System
  erzwingt deshalb ein Monatsbudget (`NBI_ODDS_MONTHLY_CREDIT_BUDGET`) und verweigert Aufrufe darüber.
- Für den **Live-Betrieb mit Props** genügt der 20K-Tarif (30 $/Monat). Mit nur einer Region halbieren sich alle Kosten.
- Ein **Quoten-Backtest über 3 Saisons Props** (≈ 370 000 Credits) braucht einmalig den 5M-Tarif (119 $) für einen Monat;
  nur Hauptmärkte über 3 Saisons (≈ 31 000 Credits) passen in den 100K-Tarif (59 $).
- Props sind erst ab Mai 2023 historisch verfügbar – ein Props-Backtest kann also höchstens die Saisons ab 2023-24 abdecken.

## Basketball Reference – Nutzungsbedingungen

- Technisches Limit: mehr als 20 Anfragen pro Minute führen zur Sperre (laut aktueller Fassung bis zu einem Tag).
  Das System drosselt auf eine Anfrage alle 3,3 s; eine Saison (≈ 1 320 Boxscores) lädt daher ≈ 75 Minuten.
  Rohseiten werden komprimiert gecacht und nie doppelt geladen.
- **Rechtlicher Hinweis:** Die Datennutzungsseite von Sports Reference untersagt, ohne Erlaubnis Websites oder Tools
  auf Basis gescrapter Daten zu erstellen. Für eine private, nicht öffentliche Analyse ist das eine Grauzone; für einen
  öffentlichen oder kommerziellen Betrieb sollte eine lizenzierte Quelle verwendet werden (z. B. ein bezahlter
  Statistik-Feed oder stats.nba.com von einem privaten Anschluss). Der Quellcode kapselt die Quelle in
  `app/sources/bref.py`, ein Austausch betrifft nur dieses Modul.

## Was die Quellen nicht liefern

- **Bestätigte Starting Lineups vor Tip-off** gibt es kostenlos nicht verlässlich. Das Modell verwendet deshalb nur die
  Starter-Historie (Quote der letzten 5 Spiele) – echte Lineup-Feeds (SportsDataIO) wären eine Verbesserung.
- **Historische Injury Reports** älterer Saisons werden nicht rückwirkend geladen. Für das Training dienen die
  offiziellen Inaktiv-Listen der Boxscores (siehe Methodik, Abschnitt Leakage).

## Quellen

- The Odds API, Tarife: https://the-odds-api.com/
- The Odds API, historische Daten (Verfügbarkeit ab 2020 bzw. 3. Mai 2023, nur bezahlte Tarife): https://the-odds-api.com/historical-odds-data/
- The Odds API, v4-Dokumentation (Credit-Kosten): https://the-odds-api.com/liveapi/guides/v4/
- Sports Reference, Bot-Richtlinie (20 Anfragen/Minute): https://www.sports-reference.com/bot-traffic.html
- Sports Reference, Datennutzung: https://www.sports-reference.com/data_use.html
- SportsDataIO, Entwicklerseite (Preise, Scrambled-Testdaten): https://sportsdata.io/developers
- NBA Injury Report: https://official.nba.com/nba-injury-report-2026-27-season/

Hinweis zur Belegqualität: Die Preis- und Credit-Angaben stammen aus einer Websuche vom 15.11.2026, die die offiziellen
Seiten zitiert; die Seiten von The Odds API und Sports Reference selbst konnte ich aus dieser Umgebung nicht direkt
abrufen. Die SportsDataIO-Preise sind nur über die Suche belegt und widersprüchlich angegeben (99 $ vs. 25 $ Einstieg).
