import { defineConfig, devices } from "@playwright/test";

/**
 * Playwright e2e config. The smoke spec (e2e/smoke.spec.ts) exercises
 * login → dashboard → pause agent → open conversation.
 *
 * NOTE: e2e requires BOTH the Next dev server and the FastAPI backend (with a
 * seeded DB + Redis) running. `webServer` boots the frontend; the API must be
 * up separately (or the specs that hit live data are skipped). Run with:
 *   npm run test:e2e
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: !!process.env.CI,
  retries: process.env.CI ? 2 : 0,
  reporter: "list",
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3001",
    trace: "on-first-retry",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
  webServer: {
    command: "npm run dev",
    url: "http://localhost:3001",
    reuseExistingServer: !process.env.CI,
    timeout: 120_000,
  },
});
