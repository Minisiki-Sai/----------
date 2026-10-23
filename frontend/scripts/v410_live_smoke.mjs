import { chromium } from "playwright-core";

const baseUrl = process.env.V410_URL || "http://127.0.0.1:1420/";
const replayPath = process.env.V410_REPLAY;
const edgePath = process.env.EDGE_PATH || "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
if (!replayPath) throw new Error("V410_REPLAY is required");

const browser = await chromium.launch({ executablePath: edgePath, headless: true });
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });
const errors = [];
page.on("console", message => { if (message.type() === "error") errors.push(`console: ${message.text()}`); });
page.on("pageerror", error => errors.push(`page: ${error.message}`));

async function tab(name) {
  await page.getByRole("button", { name, exact: true }).first().click();
  await page.waitForTimeout(120);
}

try {
  // The live page polls capture state continuously, so networkidle is not a
  // reachable readiness condition.  DOM readiness plus the first visible
  // navigation button is deterministic for both browser and Tauri builds.
  await page.goto(baseUrl, { waitUntil: "domcontentloaded", timeout: 30000 });
  await page.getByRole("button", { name: "实战统计", exact: true }).first().waitFor({ timeout: 30000 });
  await tab("实战统计");
  await page.getByTestId("replay-file-input").setInputFiles(replayPath);
  await page.locator(".stat-strip.six-columns").waitFor({ timeout: 120000 });

  await tab("全队技能时间轴");
  if (await page.locator(".live-timeline-explorer").count() !== 1) throw new Error("全队技能时间轴未渲染");
  if (await page.getByRole("button", { name: "走位平面图", exact: true }).count() !== 0) throw new Error("走位平面图前端入口仍然存在");
  await tab("Boss实时状态");
  if (await page.getByRole("button", { name: "状态总览", exact: true }).count() !== 1) throw new Error("Boss 状态总览缺失");
  await page.getByRole("button", { name: "状态明细", exact: true }).click();
  await tab("实时增益");
  if (await page.getByRole("button", { name: "增益总览", exact: true }).count() !== 1) throw new Error("实时增益总览缺失");
  await page.getByRole("button", { name: "增益明细", exact: true }).click();
  await tab("队伍 / 查看信息");
  await tab("Boss统计");
  for (const name of ["技能构成", "伤害曲线", "Boss实时状态", "实时增益", "全队技能时间轴", "Boss动作时间轴"]) {
    await page.getByRole("button", { name, exact: true }).last().click();
    await page.waitForTimeout(80);
  }

  await page.screenshot({ path: `${process.env.TEMP || "."}\\v410-live-complete.png`, fullPage: true });
  if (errors.length) throw new Error(errors.join("\n"));
  process.stdout.write("V4.10 live statistics smoke test passed.\n");
} finally {
  await browser.close();
}
