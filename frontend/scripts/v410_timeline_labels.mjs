import { chromium } from "playwright-core";

const browser = await chromium.launch({ executablePath: "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe", headless: true });
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
try {
  await page.goto(process.env.V410_URL || "http://127.0.0.1:1420/", { waitUntil: "networkidle", timeout: 30000 });
  await page.getByRole("button", { name: "实战统计", exact: true }).first().click();
  await page.getByTestId("replay-file-input").setInputFiles(process.env.V410_REPLAY);
  await page.locator(".stat-strip.six-columns").waitFor({ timeout: 120000 });
  await page.getByRole("button", { name: "全队技能时间轴", exact: true }).first().click();
  await page.locator(".timeline-label-layer").first().waitFor();
  const metrics = await page.locator(".native-timeline-frame").first().evaluate(frame => ({
    blocks: frame.querySelectorAll(".true-duration-block").length,
    labels: frame.querySelectorAll(".timeline-label-layer text").length,
    minBlock: Math.min(...[...frame.querySelectorAll(".true-duration-block")].map(node => Number(node.getAttribute("width")))),
    height: frame.querySelector("svg")?.getBoundingClientRect().height || 0,
  }));
  console.log(JSON.stringify(metrics));
  await page.screenshot({ path: `${process.env.TEMP || "."}\\v410-real-block-label-layer.png`, fullPage: true });
} finally {
  await browser.close();
}
