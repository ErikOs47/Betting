/* Market probabilities, EV and stake sizing - a line-by-line port of the backend
   (app/ml/dist.py, app/betting/probability.py, app/betting/odds_math.py, app/betting/risk.py).
   Verified against the Python implementation by backend/tests/test_phone_math.py. */
(function (root) {
  "use strict";
  // log-gamma (Lanczos, g=7, n=9): ~15 significant digits
  const LG = [0.99999999999980993, 676.5203681218851, -1259.1392167224028, 771.32342877765313, -176.61502916214059,
    12.507343278686905, -0.13857109526572012, 9.9843695780195716e-6, 1.5056327351493116e-7];
  function lgamma(x) {
    if (x < 0.5) return Math.log(Math.PI / Math.sin(Math.PI * x)) - lgamma(1 - x);
    x -= 1;
    let a = LG[0];
    const t = x + 7.5;
    for (let i = 1; i < 9; i++) a += LG[i] / (x + i);
    return 0.5 * Math.log(2 * Math.PI) + (x + 0.5) * Math.log(t) - t + Math.log(a);
  }
  // negative binomial with mean mu and Var = mu + alpha*mu^2
  function nbPmf(k, mu, alpha) {
    const n = 1 / alpha, p = n / (n + mu);
    return Math.exp(lgamma(k + n) - lgamma(n) - lgamma(k + 1) + n * Math.log(p) + k * Math.log(1 - p));
  }
  function nbCdf(k, mu, alpha) {
    if (k < 0) return 0;
    let s = 0;
    for (let i = 0; i <= k; i++) s += nbPmf(i, mu, alpha);
    return Math.min(1, s);
  }
  function nbQuantile(q, mu, alpha) {
    let s = 0;
    for (let k = 0; k < 500; k++) { s += nbPmf(k, mu, alpha); if (s >= q) return k; }
    return 500;
  }
  // standard normal CDF via erfc (W. J. Cody rational approximation, |error| < 1e-14)
  function erfc(x) {
    const z = Math.abs(x), t = 1 / (1 + 0.5 * z);
    const r = t * Math.exp(-z * z - 1.26551223 + t * (1.00002368 + t * (0.37409196 + t * (0.09678418 + t * (-0.18628806 +
      t * (0.27886807 + t * (-1.13520398 + t * (1.48851587 + t * (-0.82215223 + t * 0.17087277)))))))));
    return x >= 0 ? r : 2 - r;
  }
  const normCdf = (x, mu, sd) => 0.5 * erfc(-(x - mu) / (sd * Math.SQRT2));
  const normSf = (x, mu, sd) => 1 - normCdf(x, mu, sd);

  function countProbs(mu, alpha, line) {
    const k = Math.floor(line), isInt = line === k;
    const over = 1 - nbCdf(k, mu, alpha), push = isInt ? nbPmf(k, mu, alpha) : 0;
    return { over, under: Math.max(0, 1 - over - push), push };
  }
  function normalProbs(mu, sd, t) {
    if (t === Math.floor(t)) {
      const over = normSf(t + 0.5, mu, sd), under = normCdf(t - 0.5, mu, sd);
      return { over, under, push: Math.max(0, 1 - over - under) };
    }
    const over = normSf(t, mu, sd);
    return { over, under: 1 - over, push: 0 };
  }
  function homeWinProb(mu, sd) {
    const win = normSf(0.5, mu, sd), lose = normCdf(-0.5, mu, sd);
    return win / (win + lose);
  }
  const PLAYER = ["points", "rebounds", "assists", "threes", "pra"];
  function one(d, market, sel, line) {
    if (PLAYER.includes(market)) {
      const p = countProbs(d.mu, d.alpha, line);
      return sel === "over" ? { win: p.over, lose: p.under, push: p.push } : { win: p.under, lose: p.over, push: p.push };
    }
    if (market === "moneyline") {
      const ph = homeWinProb(d.mu, d.sigma_ml || d.sigma), win = sel === "home" ? ph : 1 - ph;
      return { win, lose: 1 - win, push: 0 };
    }
    if (market === "spread") { // line = handicap of the selected team
      if (sel === "home") { const p = normalProbs(d.mu, d.sigma, -line); return { win: p.over, lose: p.under, push: p.push }; }
      const p = normalProbs(d.mu, d.sigma, line); return { win: p.under, lose: p.over, push: p.push };
    }
    if (market === "total") {
      const p = normalProbs(d.mu, d.sigma, line);
      return sel === "over" ? { win: p.over, lose: p.under, push: p.push } : { win: p.under, lose: p.over, push: p.push };
    }
    throw new Error("unknown market " + market);
  }
  /** Ensemble of both models; p = win / (win + lose), comparable with de-vigged bookmaker probabilities. */
  function marketProbability(d, market, sel, line) {
    const a = one(d, market, sel, line), out = { win: a.win, lose: a.lose, push: a.push };
    out.pPrimary = a.win / Math.max(1e-12, a.win + a.lose);
    out.pAlt = null;
    if (d.alt) {
      const b = one(Object.assign({}, d, d.alt), market, sel, line);
      out.pAlt = b.win / Math.max(1e-12, b.win + b.lose);
      for (const k of ["win", "lose", "push"]) out[k] = 0.5 * (a[k] + b[k]);
    }
    out.p = out.win / Math.max(1e-12, out.win + out.lose);
    return out;
  }
  function devig(prices) { const q = prices.map((x) => 1 / x), s = q.reduce((a, b) => a + b, 0); return q.map((x) => x / s); }
  const ev = (pWin, price, pPush) => pWin * price + (pPush || 0) - 1;
  function kelly(pWin, price, pPush) {
    const b = price - 1, pLose = 1 - pWin - (pPush || 0);
    if (b <= 0 || pWin <= 0) return 0;
    return Math.max(0, (pWin * b - pLose) / b);
  }
  /** Quarter-Kelly on the uncertainty-shrunk probability, capped per bet (same rules as the backend). */
  function stake({ pWin, pPush, pMarket, price, bankroll, cfg }) {
    const pStake = pMarket == null ? pWin : pMarket + cfg.shrink * (pWin - pMarket);
    const f = kelly(pStake, price, pPush);
    const raw = cfg.kelly * f * bankroll, cap = cfg.maxBet * bankroll;
    return { amount: Math.floor(Math.min(raw, cap) * 100) / 100, kellyFull: f, capped: raw > cap };
  }
  const api = { lgamma, nbPmf, nbCdf, nbQuantile, normCdf, countProbs, normalProbs, homeWinProb, marketProbability, devig, ev, kelly, stake, PLAYER };
  if (typeof module !== "undefined" && module.exports) module.exports = api; else root.NBMath = api;
})(typeof globalThis !== "undefined" ? globalThis : this);
