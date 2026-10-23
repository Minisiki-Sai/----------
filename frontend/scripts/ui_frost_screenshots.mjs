import { chromium } from "playwright-core";
import { mkdirSync } from "node:fs";

const out = "C:/Users/yaoxi/Documents/GitHub/dnact/outputs/verification/ui-frost";
mkdirSync(out, { recursive: true });

const browser = await chromium.launch({
  executablePath: "C:/Program Files/Google/Chrome/Application/chrome.exe",
  headless: true,
});
const errors = [];

const page = await browser.newPage({ viewport: { width: 1280, height: 800 }, deviceScaleFactor: 1.5 });
page.on("console", msg => { if (msg.type() === "error") errors.push(msg.text()); });
page.on("pageerror", err => errors.push(String(err)));

await page.goto("http://127.0.0.1:1420/#live", { waitUntil: "load" });
await page.waitForTimeout(3500);
await page.screenshot({ path: `${out}/01-main-light.png` });

await page.click(".frost-theme-toggle");
await page.waitForTimeout(400);
await page.screenshot({ path: `${out}/02-main-dark.png` });
await page.click(".frost-theme-toggle");

await page.click(".frost-rail-item");
await page.waitForTimeout(600);
await page.screenshot({ path: `${out}/03-npcap.png` });

await page.click(".frost-back");
await page.waitForTimeout(400);
await page.click(".frost-entry");
await page.waitForTimeout(900);
await page.screenshot({ path: `${out}/04-overlay-settings.png` });
await page.keyboard.press("Escape");
await page.click(".overlay-settings-dialog .icon-button").catch(() => {});

for (const [name, view, size] of [
  ["05-buff-overlay", "buff-overlay", { width: 360, height: 240 }],
  ["06-skill-overlay", "skill-countdown-overlay", { width: 360, height: 240 }],
]) {
  const overlay = await browser.newPage({ viewport: { width: 460, height: 640 }, deviceScaleFactor: 2 });
  await overlay.goto(`http://127.0.0.1:1420/?view=${view}&layoutPreview=1`, { waitUntil: "load" });
  await overlay.waitForTimeout(1200);
  const shell = overlay.locator(".buff-overlay-shell");
  await shell.screenshot({ path: `${out}/${name}.png`, omitBackground: true });
  await overlay.close();
}

console.log("errors:", JSON.stringify(errors.slice(0, 8), null, 2));
await browser.close();
