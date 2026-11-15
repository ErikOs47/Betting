import { expect, test } from "@playwright/test";

const PAGES: [string, string][] = [
  ["/", "Übersicht"], ["/value", "Value Finder"], ["/games", "Spielanalyse"], ["/builder", "Bet Builder"],
  ["/performance", "Performance"], ["/backtest", "Backtesting"], ["/settings", "Einstellungen"],
];

test("all seven pages render without client errors", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  for (const [url, title] of PAGES) {
    await page.goto(url);
    await expect(page.getByRole("heading", { level: 1, name: title })).toBeVisible();
    await expect(page.getByText("Fehler:")).toHaveCount(0);
  }
  expect(errors).toEqual([]);
});

test("no provider key or token is exposed to the browser", async ({ page, request }) => {
  const s = await (await request.get("/api/settings")).json();
  expect(JSON.stringify(s)).not.toMatch(/apiKey|api_key"\s*:\s*"[^"]+/i);
  expect(typeof s.providers.odds_api_key_set).toBe("boolean");
  await page.goto("/settings");
  expect(await page.content()).not.toContain("NBI_ODDS_API_KEY=");
});

test("manual quote: EV equals probability x odds - 1 and feeds the bet builder", async ({ page, request }) => {
  const games = (await (await request.get("/api/games")).json()).games.filter((g: any) => g.model && g.status !== "final");
  test.skip(games.length === 0, "no upcoming game with a prediction");
  await page.goto(`/games/${games[0].id}`);
  await page.getByTestId("eval-market").selectOption("moneyline");
  await page.getByLabel("Quote", { exact: true }).fill("2.50");
  await page.getByLabel("Gegenquote (optional)").fill("1.55");
  const [resp] = await Promise.all([page.waitForResponse((r) => r.url().includes("/api/evaluate")), page.getByRole("button", { name: "Berechnen" }).click()]);
  const r = await resp.json();
  expect(r.ev).toBeCloseTo(r.p_win * 2.5 - 1, 3);
  expect(r.fair_odds).toBeCloseTo(1 / r.model_prob, 1);
  await expect(page.getByText("Modell-Wahrsch.")).toBeVisible();
  await page.getByRole("button", { name: "+ Bet Slip" }).click();
  await page.goto("/builder");
  await expect(page.getByText("Bet Slip (1)")).toBeVisible();
  await page.getByRole("button", { name: "Leeren" }).click();
  await expect(page.getByText("Bet Slip (0)")).toBeVisible();
});

test("kill switch blocks paper bets", async ({ page, request }) => {
  page.on("dialog", (d) => d.accept());
  await page.goto("/");
  await page.getByRole("button", { name: "Kill Switch", exact: true }).click();
  await expect(page.getByText("Kill Switch aktiv", { exact: true })).toBeVisible();
  const games = (await (await request.get("/api/games")).json()).games;
  const res = await request.post("/api/bets", { data: { legs: [{ game_id: games[0]?.id ?? "x", market: "moneyline", selection: "home", price: 2.0 }] } });
  expect(res.status()).toBe(422);
  await page.getByRole("button", { name: "Kill Switch aufheben" }).click();
  await expect(page.getByText("Wetten erlaubt (Paper)")).toBeVisible();
});

test("settings reject limits outside the safe range", async ({ request }) => {
  expect((await request.put("/api/settings/risk", { data: { max_bet_pct: 0.5 } })).status()).toBe(422);
  expect((await request.put("/api/settings/risk", { data: { max_bet_pct: 0.01 } })).status()).toBe(200);
});

test("hand-entered bookmaker quotes appear in the value finder and can be deleted", async ({ page, request }) => {
  const games = (await (await request.get("/api/games")).json()).games.filter((g: any) => g.model && g.status !== "final" && new Date(g.tip_utc) > new Date());
  test.skip(games.length === 0, "no upcoming game with a prediction");
  const id = games[0].id;
  await page.goto(`/games/${id}`);
  await page.getByLabel("Buchmacher", { exact: true }).fill("e2e-test");
  await page.getByLabel("Moneyline Heim").fill("1,95");
  await page.getByLabel("Moneyline Auswärts").fill("1,95");
  await page.getByRole("button", { name: "Quoten speichern" }).click();
  await expect(page.getByText("1 Märkte gespeichert (e2e-test)")).toBeVisible();
  const cands = (await (await request.get("/api/value-bets?all=true")).json()).candidates.filter((c: any) => c.bookmaker === "e2e-test");
  expect(cands.length).toBe(2);
  expect(cands[0].ev).toBeCloseTo(cands[0].p_win * cands[0].price_net - 1, 3);
  await page.goto("/value");
  await page.getByLabel("auch abgelehnte Kandidaten zeigen").check();
  await expect(page.getByText("e2e-test").first()).toBeVisible();
  await expect(page.getByRole("heading", { name: "Faire Linien des Modells (ohne Quoten nutzbar)" })).toBeVisible();
  await page.goto(`/games/${id}`);
  await page.getByRole("button", { name: "Quoten von e2e-test löschen" }).click();
  await expect(page.getByRole("button", { name: "Quoten von e2e-test löschen" })).toHaveCount(0);
  expect((await (await request.get(`/api/odds/${id}`)).json()).quotes.filter((q: any) => q.bookmaker === "e2e-test")).toEqual([]);
});
