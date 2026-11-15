import { defineConfig } from "@playwright/test";

// Expects the stack to be running (API on :8000 with predictions, web on :3000).
export default defineConfig({
  testDir: "e2e", timeout: 60_000, workers: 1, reporter: "list",
  use: { baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000", trace: "off",
    launchOptions: process.env.PW_CHROMIUM_PATH ? { executablePath: process.env.PW_CHROMIUM_PATH, args: ["--no-sandbox"] } : {} },
});
