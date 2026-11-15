"""Render the latest backtest as a Markdown block (used for the README results section)."""
from __future__ import annotations

from sqlalchemy import func, select

from app.db import BacktestRun, Game, session_scope

M = {"points": "Punkte", "rebounds": "Rebounds", "assists": "Assists", "threes": "Dreier", "pra": "PRA"}


def render() -> str:
    with session_scope() as db:
        r = db.scalars(select(BacktestRun).order_by(BacktestRun.id.desc()).limit(1)).first()
        n_final = db.scalar(select(func.count()).select_from(Game).where(Game.status == "final"))
        seasons = list(db.scalars(select(Game.season).where(Game.status == "final").distinct().order_by(Game.season)))
        m, cfg = r.metrics, r.config
    p = m["period"]
    n_rows = f"{p['n_player_rows']:,}".replace(",", " ")
    out = [f"Stand: Lauf `{r.name}` vom {r.created_at:%d.%m.%Y}, Code `{r.code_sha[:7]}`. Datenbasis: {n_final} gespielte Partien "
           f"(Saisons {', '.join(f'{s - 1}-{str(s)[2:]}' for s in seasons)}), Quelle Basketball Reference.",
           "",
           f"Walk-forward, expandierendes Fenster, {p['n_folds']} Blöcke à {cfg['step_days']} Tage. Out-of-sample-Zeitraum "
           f"{p['start']} bis {p['end']}: **{n_rows} Spieler-Spiele, {p['n_games']} Partien**.",
           "", "### Spieler-Props (out-of-sample)", "",
           "| Markt | MAE LightGBM | MAE XGBoost | MAE Ø letzte 10 | MAE Saison-Ø | Abdeckung 50/80/90 % | Brier (Ensemble) | Brier Basisrate | ECE |",
           "|---|---|---|---|---|---|---|---|---|"]
    for k, name in M.items():
        a, x, e = m["player"]["lightgbm"][k], m["player"].get("xgboost", {}).get(k), m["player"]["ensemble"][k]["event"]
        c = a["coverage"]
        out.append(f"| {name} | {a['mae']:.3f} | {x['mae']:.3f} | {a['mae_baseline_l10']:.3f} | {a['mae_baseline_season']:.3f} | "
                   f"{c['0.5'] * 100:.0f} / {c['0.8'] * 100:.0f} / {c['0.9'] * 100:.0f} | {e['brier']:.4f} | {a['event']['brier_base_rate']:.4f} | {e['ece'] * 100:.1f} % |")
    g = m["game"]
    if g:
        out += ["", "### Spielmärkte (out-of-sample)", "",
                "| Modell | Moneyline Brier | Log Loss | Trefferquote | ECE | Margin-MAE | Total-MAE |", "|---|---|---|---|---|---|---|"]
        for k, name in (("lightgbm", "LightGBM"), ("xgboost", "XGBoost"), ("ensemble", "Ensemble"), ("elo_baseline", "Elo-Baseline"), ("home_rate_baseline", "konstante Heimquote")):
            if k not in g:
                continue
            ml = g[k].get("moneyline", {})
            acc = f"{ml['accuracy'] * 100:.1f} %" if ml.get("accuracy") is not None else "–"
            ece = f"{ml['ece'] * 100:.1f} %" if ml.get("ece") is not None else "–"
            sp = f"{g[k]['spread']['mae']:.2f}" if "mae" in g[k].get("spread", {}) else ("%.2f" % g["lightgbm"]["spread"]["mae_elo"] if k == "elo_baseline" else "–")
            to = f"{g[k]['total']['mae']:.2f}" if "mae" in g[k].get("total", {}) else ("%.2f (Pace×Effizienz)" % g["lightgbm"]["total"]["mae_baseline"] if k == "elo_baseline" else "–")
            out.append(f"| {name} | {ml['brier']:.4f} | {ml['log_loss']:.4f} | {acc} | {ece} | {sp} | {to} |")
    c = m["correlations"]
    out += ["", "### Geschätzte Korrelationen (Auszug)", "",
            f"Gleicher Spieler Punkte↔Assists {c['same|assists|points']:+.2f}, Punkte↔Rebounds {c['same|points|rebounds']:+.2f}, "
            f"Punkte↔Dreier {c['same|points|threes']:+.2f}; Mitspieler Punkte↔Punkte {c['mate|points|points']:+.2f}; "
            + (f"Punkte↔Margin des eigenen Teams {c['margin|points']:+.2f}, Punkte↔Gesamtpunkte {c['total|points']:+.2f}." if "margin|points" in c else ""),
            "", "### Wett-Simulation (ROI, Yield, CLV, Drawdown)", "",
            ("Verfügbar – siehe Dashboard." if m["odds"]["available"] else
             "**Nicht verfügbar:** Es sind keine historischen Quoten gespeichert, daher werden keine ROI-/CLV-Werte ausgewiesen.")]
    return "\n".join(out)


INTERPRETATION = """
### Einordnung

- **Spieler-Props:** Beide Modelle sind in jedem Markt genauer als die naiven Baselines (Ø letzte 10 Spiele, Saison-Ø),
  und die Prognoseintervalle treffen ihre Sollabdeckung (50/80/90 %) nahezu exakt – die Verteilungen sind also brauchbar
  kalibriert. Der Vorsprung im MAE ist klein (wenige Prozent); das ist bei Zählstatistiken mit hoher Zufallsstreuung normal.
- **Spielmärkte:** Das Boosting-Modell liegt bei Moneyline und Handicap **praktisch gleichauf mit einer einfachen
  Elo-Wertung** (siehe Tabelle; die Abstände liegen im Bereich des Stichprobenrauschens). Ein belastbarer Modellvorteil
  ist dort nicht belegt. Nur bei Gesamtpunkten ist das Modell klar besser als die einfache Pace-x-Effizienz-Schätzung.
- **Wichtig:** Diese Zahlen messen Prognosegüte gegen echte Ergebnisse, **nicht** Profitabilität gegen Buchmacher.
  Buchmacherlinien enthalten mehr Information als jede hier verwendete Baseline. Ob ein Vorteil gegenüber dem Markt
  besteht, lässt sich erst mit historischen Quoten bzw. über den CLV gesammelter Paper-Wetten beurteilen.
"""


def update_readme(path) -> None:
    text = path.read_text(encoding="utf-8")
    a, b = "<!-- RESULTS -->", "<!-- /RESULTS -->"
    block = f"{a}\n{render()}\n{INTERPRETATION}{b}"
    if b in text:
        text = text[:text.index(a)] + block + text[text.index(b) + len(b):]
    else:
        text = text.replace(a, block)
    path.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    from app.config import REPO_ROOT
    update_readme(REPO_ROOT / "README.md")
    print(render())
