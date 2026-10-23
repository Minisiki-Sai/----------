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

try {
  await page.goto(baseUrl, { waitUntil: "commit", timeout: 30000 });
  await page.getByRole("button", { name: "实战统计", exact: true }).first().waitFor({ timeout: 60000 });
  await page.getByRole("button", { name: "实战统计", exact: true }).first().click();
  await page.getByTestId("replay-file-input").setInputFiles(replayPath);
  await page.locator(".stat-strip.six-columns").waitFor({ timeout: 180000 });
  await page.getByRole("button", { name: "全队评分", exact: true }).click();
  await page.locator(".score-toolbar").waitFor({ timeout: 30000 });

  if (!await page.locator(".score-layout").count()) {
    const scoreAction = page.locator(".score-toolbar button").last();
    await scoreAction.waitFor({ timeout: 30000 });
    await scoreAction.click();
  }
  await page.locator(".score-layout").waitFor({ timeout: 600000 });
  await page.getByRole("button", { name: "输出时间轴", exact: true }).click();

  const timelinePane = page.locator('[data-score-detail-pane="timeline"]');
  const timeline = timelinePane.locator(".native-timeline-frame").first();
  await timeline.waitFor({ timeout: 120000 });
  const zoom = timeline.getByLabel("缩放");
  await zoom.evaluate(element => {
    element.value = "33";
    element.dispatchEvent(new Event("input", { bubbles: true }));
  });
  await timeline.getByRole("button", { name: "隐藏标签", exact: true }).click();
  const scroll = timeline.locator(".native-timeline-scroll");
  const expectedScroll = await scroll.evaluate(element => {
    element.scrollLeft = Math.min(640, Math.max(0, element.scrollWidth - element.clientWidth));
    element.dispatchEvent(new Event("scroll", { bubbles: true }));
    return element.scrollLeft;
  });

  await page.getByRole("button", { name: "输出明细", exact: true }).click();
  await page.locator('[data-score-detail-pane="output"]:visible').waitFor();
  if (!await timelinePane.count()) throw new Error("切换后输出时间轴实例被销毁");

  await page.getByRole("button", { name: "输出时间轴", exact: true }).click();
  await timelinePane.locator(".native-timeline-frame").waitFor();
  const restoredZoom = Number(await zoom.inputValue());
  const restoredScroll = await scroll.evaluate(element => element.scrollLeft);
  if (restoredZoom !== 33) throw new Error(`时间轴缩放未保留：${restoredZoom}`);
  if (Math.abs(restoredScroll - expectedScroll) > 2) {
    throw new Error(`时间轴横向位置未保留：${expectedScroll} -> ${restoredScroll}`);
  }
  if (!await timeline.getByRole("button", { name: "显示标签", exact: true }).count()) {
    throw new Error("时间轴标签显示状态未保留");
  }
  if (errors.length) throw new Error(errors.join("\n"));
  process.stdout.write("Score detail state persistence smoke test passed.\n");
} finally {
  await browser.close();
}
