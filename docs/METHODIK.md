# Methodik

Dieses Dokument beschreibt, **was** berechnet wird und **warum**. Alle Formeln sind im Code als reine Funktionen
umgesetzt und durch Tests abgedeckt (`backend/tests`).

## 1. Features (ohne Data Leakage)

Datei: `app/features/build.py`. Jede Zeile (Spieler × Spiel bzw. Spiel) verwendet ausschließlich Informationen aus Spielen
**vor** dem jeweiligen Spiel: gleitende Fenster werden nach `shift(1)` gebildet, Elo ist der Stand vor dem Spiel, Ausfälle
werden per As-of-Join ohne exakte Treffer bewertet.

| Gruppe | Merkmale |
|---|---|
| Form | Mittel der letzten 5/10/20 Spiele und Saisonmittel für Minuten, Punkte, Rebounds, Assists, Dreier, PRA, Wurfversuche, Freiwürfe, Usage Rate, Per-36-Raten; exponentiell gewichtete Mittel; Streuung der letzten 10 Spiele |
| Rolle | Starter-Quote der letzten 5 Spiele, zuletzt gestartet, Minuten-Trend (L5 − L20), Teamwechsel, verpasste Spiele, Tage seit letztem Einsatz |
| Spielplan | Heim/Auswärts, Ruhetage, Back-to-back (Spieler und Team, auch Gegner), Spiele in 7 Tagen |
| Team | Offensive Rating, Pace, Punkte (L10, Saison) |
| Gegner | Defensive Rating (L10/L20/Saison), Pace, zugelassene Punkte/Rebounds/Assists/Dreier/eFG, Offensiv-Rebound-Quote, Ausfälle |
| Verletzungen | Summe der Minuten/Punkte/Assists (L10) der ausfallenden Mitspieler, getrennt nach „frisch“ (≤ 21 Tage) und gesamt; erwarteter Rollen-Zuwachs = frische Ausfall-Minuten × eigener Minutenanteil |
| Matchup | Elo-Differenz inkl. Heimvorteil (Blowout-Risiko), erwartete Pace |
| Spielmodell | je Team Net/Off/Def-Rating, Pace, Four Factors (L5/L10/L20/Saison), Elo, Ruhetage, Ausfälle; Differenzen und Summen |

**Ausfälle im Training:** Für gespielte Spiele stammt die Ausfallliste aus dem Boxscore (offiziell inaktiv bzw. nicht
eingesetzt mit Grund ≠ Trainerentscheidung). Diese Liste steht in der Realität kurz vor Tip-off fest. Für kommende
Spiele wird stattdessen der aktuelle Injury Report verwendet (Status Out/Doubtful). Das ist die einzige Stelle, an der
Trainings- und Live-Information nicht identisch sind: Wer Stunden vor dem Spiel wettet, kennt späte Ausfälle noch nicht.
Der Effekt ist begrenzt, weil die Confidence bei „Questionable“-Status und veraltetem Report sinkt – er sollte aber bei
der Interpretation des Backtests bedacht werden.

**Nachweis:** `tests/test_features.py` verändert alle Ergebnisse ab einem Stichtag und prüft, dass sämtliche Features
bis einschließlich dieses Stichtags bitgleich bleiben.

## 2. Modelle und Verteilungen

Datei: `app/ml/models.py`, `app/ml/dist.py`.

**Spieler-Props** (Punkte, Rebounds, Assists, Dreier, PRA – je ein eigenes Modell):

- Erwartungswert μ: Gradient Boosting mit Poisson-Zielfunktion (LightGBM als Hauptmodell, XGBoost als Zweitmodell).
- Verteilung: Negative Binomialverteilung mit Var = μ + α·μ², wobei α = exp(a + b·ln μ). Die zwei Parameter werden per
  Maximum Likelihood auf einem zeitlich nachgelagerten Kalibrierblock geschätzt (letzte 15 % des Trainingsfensters), also
  auf Daten, die das Mittelwertmodell nicht gesehen hat. Danach wird das Mittelwertmodell auf dem gesamten Fenster neu
  angepasst (Baumanzahl proportional skaliert).
- Daraus folgen P(Over), P(Under) und P(Push) für jede Linie exakt aus der Verteilungsfunktion.
- Prognosen gelten bedingt darauf, dass der Spieler spielt (Buchmacher stornieren Props bei Nichteinsatz).

**Spielmärkte** (Moneyline, Handicap, Over/Under):

- Zwei Regressionsmodelle für Margin (Heim − Auswärts) und Gesamtpunkte.
- Verteilung: Normalverteilung um die Prognose, σ = RMSE der Out-of-sample-Residuen des Kalibrierblocks, mit
  Stetigkeitskorrektur für ganzzahlige Linien (Push-Wahrscheinlichkeit).
- P(Heimsieg) = P(Margin > 0), ohne Unentschieden-Masse.

**Ensemble:** Die Modellwahrscheinlichkeit einer Wette ist der Mittelwert aus LightGBM und XGBoost; die Differenz der
beiden geht als Unsicherheitsmaß in die Confidence ein.

**Reproduzierbarkeit:** feste Seeds, deterministische Trainingsparameter; jede Prognose speichert Modellversion, exakte
Eingabewerte und einen Hash (`predictions.inputs_hash`). Jede Modellversion speichert Trainingszeitraum, Parameter,
Feature-Liste, Daten-Hash und Git-Commit.

## 3. Value-Bet-Engine

Datei: `app/betting/value.py`, `app/betting/odds_math.py`.

Für jede Seite jedes Zwei-Wege-Markts:

| Größe | Definition |
|---|---|
| Implizite Wahrscheinlichkeit | 1 / Quote |
| Buchmacher-Marge (Overround) | Σ 1/Quote − 1 |
| Faire Buchmacher-Wahrscheinlichkeit | margenbereinigt (multiplikativ; Power-Methode verfügbar) |
| Marktkonsens | Mittel der fairen Wahrscheinlichkeiten aller Buchmacher mit derselben Linie |
| Modellwahrscheinlichkeit p | P(Gewinn) / (P(Gewinn) + P(Verlust)) aus der Verteilung |
| Faire Quote | 1 / p |
| **EV** | p × Quote − 1 (bei möglichem Push: P(Gewinn) × Quote + P(Push) − 1) |
| **Edge** | p − Marktkonsens |
| Beste Quote | höchster Preis über alle Buchmacher (Line Shopping) |

Einseitige Märkte (nur Over ohne Under) werden verworfen, weil die Marge nicht entfernt werden kann.

**Wettsteuer:** Mit einem Steuersatz t auf die Auszahlung wird überall die Netto-Quote = Quote × (1 − t) verwendet
(EV, Kelly, Quotenbereich, Abrechnung). Der CLV vergleicht dagegen Brutto-Quoten, weil er die Marktbewegung misst.

**Mindestquote ohne Marktdaten:** Eine angebotene Quote enthält die Buchmacher-Marge, die faire Marktwahrscheinlichkeit
ist also höchstens 1/Quote. Bei Quote ≥ 1 / (p − Mindest-Edge) / (1 − t) ist der Edge-Filter deshalb auch ohne Kenntnis
der Gegenquote sicher erfüllt (konservative Schranke).

**Von Hand erfasste Quoten** (Quelle `manual`, z. B. Tipico) durchlaufen exakt dieselbe Auswertung wie API-Quoten;
sie gelten 180 Minuten als aktuell (einstellbar) statt 15 Minuten.

**Confidence (0–100)** – gewichtete Summe aus fünf nachvollziehbaren Komponenten:

| Gewicht | Komponente | Berechnung |
|---|---|---|
| 35 % | Datenqualität | Stichprobe (Spiele/20), Aktualität (Tage seit letztem Einsatz), Minutenstabilität, Rollenstabilität, Aktualität des Injury Reports; × 0,7 bei „Questionable“ |
| 20 % | Kalibrierung | 1 − ECE/5 % des Markts im letzten Walk-forward-Backtest |
| 20 % | Modell-Übereinstimmung | 1 − \|p_LightGBM − p_XGBoost\| / 8 Prozentpunkte |
| 15 % | Markt-Plausibilität | 1 bis 6 Punkte Edge, linear auf 0 bis zum Maximal-Edge (Standard 20 Punkte) |
| 10 % | Markttiefe | Anzahl Buchmacher mit dieser Linie / 4 |

Sehr große Edges sind in effizienten Märkten fast immer ein Zeichen fehlender Information (späte Ausfallmeldung,
veraltete Quote), deshalb senken sie die Confidence und werden oberhalb des Maximal-Edge ganz ausgefiltert.

**Standardfilter** (konfigurierbar): Edge ≥ 3 %, Confidence ≥ 70, EV > 0, Datenqualität ≥ 60 %, Quote 1,30–6,00,
Quote nicht älter als 15 Minuten, Prognose nicht deaktiviert.

## 4. Kombinationswetten

Datei: `app/betting/parlay.py`.

- Randverteilungen: die gespeicherten Verteilungen der einzelnen Legs.
- Abhängigkeit: Gauß-Copula. Die Korrelationsmatrix wird aus paarweisen Korrelationen zusammengesetzt, die im
  Walk-forward-Backtest aus den Normal-Scores der Out-of-sample-PIT-Werte geschätzt werden – getrennt nach
  „gleicher Spieler“, „Mitspieler“, „Gegenspieler“, „Spielerwert ↔ Margin des eigenen Teams“, „Spielerwert ↔ Gesamtpunkte“
  und „Margin ↔ Gesamtpunkte“. Legs aus verschiedenen Spielen sind unabhängig.
- 100 000 Ziehungen mit festem, aus den Legs abgeleitetem Seed (reproduzierbar); ausgewiesen werden gemeinsame
  Wahrscheinlichkeit, Simulationsfehler, Vergleich mit dem naiven Produkt und die faire Kombiquote.
- Ohne geschätzte Korrelationen wird Unabhängigkeit angenommen und deutlich gewarnt.

## 5. Risikomanagement

Datei: `app/betting/risk.py`, `app/betting/paper.py`.

1. Unsicherheitsabschlag: p_Einsatz = Markt + w × (Modell − Markt), Standard w = 0,5.
2. Kelly: f = (p·b − q) / b mit b = Quote − 1; Einsatz = ¼ × f × Bankroll.
3. Harte Limits (Bezug: Eigenkapital = Kontostand + offene Einsätze):
   - maximal 1 % pro Wette,
   - maximal 2 % pro Partie (alle Wetten auf ein Spiel teilen sich das Budget – das begrenzt korrelierte Risiken),
   - maximal 3 % Tagesverlust, gerechnet als **Worst Case**: bereits realisierter Tagesverlust + heute noch offene
     Einsätze + neuer Einsatz. (Eine Regel nur auf realisierte Verluste würde nie greifen, weil die Wetten eines
     Abends vor der ersten Abrechnung platziert werden.)
4. Kein Martingale: Der Einsatz hängt nur von Edge und aktueller Bankroll ab und sinkt nach Verlusten.
5. Kill Switch und deaktivierte Prognosen blockieren jede Wette. Wetten mit EV ≤ 0 werden abgelehnt.

## 6. Backtesting

Datei: `app/backtest/walkforward.py`, `app/backtest/betsim.py`.

- Expandierendes Fenster, strikt chronologisch: Für jeden Block (Standard 30 Tage) wird nur mit Spielen vor dem Block
  trainiert; ausgewertet wird nur der Block. Eine Assertion bricht ab, falls Trainingsdaten nicht vor Testdaten liegen.
- Prognosegüte (immer verfügbar, nur echte Ergebnisse): MAE gegen naive Baselines, negative Log-Likelihood,
  Intervallabdeckung (PIT), Brier Score, Log Loss, Kalibrierkurve und ECE. Als Ereignisschwelle dient mangels
  historischer Linien die eigene Ø-Leistung des Spielers (L10) bzw. eine Elo-/Pace-Schwelle für Spiele.
- Wett-Simulation (nur mit gespeicherten historischen Quoten): Entscheidungsquote ist der erste Snapshot vor Tip-off,
  Schlussquote der letzte; Quoten nach Tip-off werden nie verwendet. Gleiche Filter und gleiche Einsatzlogik wie live.
  Kennzahlen: ROI, Yield, Trefferquote, CLV, maximaler Drawdown.
- **Closing Line Value** = Wettquote × faire Schlusswahrscheinlichkeit − 1 (gleicher Buchmacher, gleiche Linie).

## 7. Was das System ausdrücklich nicht behauptet

- Keine Gewinngarantie. NBA-Hauptmärkte sind sehr effizient; ein Modell aus öffentlichen Boxscore-Daten schlägt die
  Schlussquote dort erfahrungsgemäß nicht dauerhaft. Wenn überhaupt, sind Vorteile eher bei Spieler-Props und durch
  Preisvergleich zwischen Buchmachern zu erwarten.
- Ohne historische Quoten gibt es keinen ROI-Nachweis. Eine gute Kalibrierung ist notwendig, aber nicht hinreichend
  für Profitabilität – entscheidend ist ein positiver CLV über viele Paper-Wetten.
