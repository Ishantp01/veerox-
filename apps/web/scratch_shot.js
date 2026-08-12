const { chromium } = require("playwright");
const path = require("path");

const outDir = "C:/Users/LOQ/AppData/Local/Temp/claude/d--adrsproject-veerox-/2b6e5824-5693-4699-90f0-985fc7043658/scratchpad";

(async () => {
  const browser = await chromium.launch();
  const context = await browser.newContext({ viewport: { width: 1440, height: 900 } });
  const token = process.argv[2] || process.env.SESSION_TOKEN;

  await context.addInitScript((t) => {
    localStorage.setItem("veerox_session_token", t);
    localStorage.setItem("veerox_auth_mode", "session");
  }, token);

  const page = await context.newPage();
  page.on("requestfailed", (req) => console.log("[reqfail]", req.url(), req.failure()?.errorText));
  page.on("response", (res) => {
    if (res.url().includes("localhost:8002")) console.log("[resp]", res.status(), res.url());
  });

  await page.goto("http://localhost:3001/");
  await page.waitForSelector("nav", { timeout: 15000 }).catch(() => console.log("nav wait timed out"));
  await page.waitForTimeout(1500);
  console.log("final url:", page.url());
  await page.screenshot({ path: path.join(outDir, "dashboard-full.png"), fullPage: false });

  const nav = await page.$("nav");
  if (nav) {
    await nav.screenshot({ path: path.join(outDir, "sidebar.png") });
  } else {
    console.log("no <nav> found");
  }
  await browser.close();
})();
