import { chromium } from "playwright-core";

const edgePath = process.env.EDGE_PATH || "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe";
const browser = await chromium.launch({ executablePath: edgePath, headless: true });
const errors = [];

const damageSeries = Array.from({ length: 60 }, (_, index) => [index, Math.round(550000 + Math.sin(index / 4) * 260000 + (index % 9) * 40000)]);
const payload = {
  state: "running", status: "测试中", elapsedSeconds: 59, selfSourceId: "p1",
  players: [
    { source_id: "p1", job: "冰灵", name: "冰灵", damage: 42000000, dps: 720000, damage_series: damageSeries, attack_types: ["magic"], elements: ["water", "fire"] },
    { source_id: "p4", job: "圣徒", name: "月光泡泡", damage: 1000000, dps: 18000, damage_series: [], attack_types: ["magic"], elements: ["light"] },
  ],
  buffs: [
    { key: "p1|精神爆发", catalog_key: "精神爆发", skill_name: "精神爆发", target_id: "p1", source_id: "p2", job: "风行者", start_seconds: 52, end_seconds: 66, duration_seconds: 14, display_order: 1 },
    { key: "p1|鸡尾酒", catalog_key: "鸡尾酒", skill_name: "鸡尾酒", target_id: "p1", source_id: "p3", job: "药剂师", start_seconds: 45, end_seconds: 75, duration_seconds: 30, display_order: 2 },
  ],
  debuffs: [
    { key: "damage:magic", category: "damage", mode: "magic", label: "魔法增伤", current_value: 40, next_value: 20, end_seconds: 64, duration_seconds: 10 },
    { key: "resistance:water", category: "resistance", mode: "water", label: "减水抗", current_value: 30, next_value: 15, end_seconds: 62, duration_seconds: 8 },
    { key: "freeze", category: "freeze", mode: "freeze", label: "叠冰", current_value: 2, next_value: 1, end_seconds: 61, duration_seconds: 4 },
  ],
  cooldowns: [{ key: "p2|精神爆发", catalog_key: "精神爆发", skill_name: "精神爆发", source_id: "p2", source_job: "风行者", cast_seconds: 52, cooldown_seconds: 60, ready_seconds: 112, remaining_seconds: 53 }],
  playerHealth: [{ target_id: "p4", target_name: "月光泡泡", target_job: "圣徒", current_hp: 68000, max_hp: 250000, current_percent: 27.2, exact_percent: true, max_hp_source: "profile" }],
  buffOptions: [
    { key: "精神爆发", skill: "精神爆发", label: "动作速度", category: "速度/冷却", scope: "team", fullDurationTeam: true, provider: "风行者", displayOrder: 1, providerOrder: 1 },
    { key: "鸡尾酒", skill: "鸡尾酒", label: "四维与冷却", category: "速度/冷却", scope: "team", fullDurationTeam: true, provider: "药剂师", displayOrder: 2, providerOrder: 2 },
    { key: "无情打击", skill: "无情打击", label: "团队攻击", category: "攻击", scope: "team", fullDurationTeam: true, provider: "圣徒", displayOrder: 3, providerOrder: 3 },
    { key: "光明之呼吸", skill: "光明之呼吸", label: "团队光攻", category: "属性", scope: "team", fullDurationTeam: true, provider: "圣徒", displayOrder: 4, providerOrder: 3 },
  ],
};
const settings = {
  selectedBuffs: ["精神爆发", "鸡尾酒"], selectedCooldownBuffs: ["精神爆发"],
  selectedEffectCurves: ["damage:magic", "resistance:water", "freeze:freeze"],
  selectedDpsPlayers: ["p1"], selectedMissingBuffs: ["无情打击", "光明之呼吸"], buffOrder: ["精神爆发", "鸡尾酒"],
  showBuffOverlay: true, showDebuffOverlay: true,
  showEffectCurveOverlay: true, showPersonalDpsOverlay: true,
  showCooldownOverlay: true,
  showClearedBuffAlert: true,
  showMissingBuffAlert: true, showTeamMissingBuffs: true, showLowHealthAlert: true,
  curveHistorySeconds: 60, dpsWindowSeconds: 10, lowHealthThreshold: 35,
  damageMode: "both", resistanceMode: "all", defenseMode: "both",
};

async function openOverlay(view, viewport, payloadState = { current: payload }) {
  const page = await browser.newPage({ viewport });
  page.on("pageerror", error => errors.push(`${view}: ${error.message}`));
  page.on("console", message => { if (message.type() === "error") errors.push(`${view}: ${message.text()}`); });
  await page.route("**/api/capture/buff-overlay", route => route.fulfill({
  status: 200,
  contentType: "application/json",
  body: JSON.stringify(payloadState.current),
  }));
  await page.goto(`http://127.0.0.1:1420/?view=${view}`, { waitUntil: "domcontentloaded" });
  await page.evaluate(value => {
    localStorage.setItem("dragon-nest-v420-buff-overlay-enabled", "1");
    localStorage.setItem("dragon-nest-v420-buff-overlay-settings", JSON.stringify(value));
  }, settings);
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.waitForTimeout(1200);
  return page;
}

const statusPage = await openOverlay("buff-overlay", { width: 760, height: 500 });
const statusPanels = await statusPage.locator(".overlay-panel").count();
const statusRows = await statusPage.locator(".buff-row").count();
if (statusPanels !== 1 || statusRows !== 5) throw new Error(`状态页数量异常：panels=${statusPanels}, rows=${statusRows}`);
if (await statusPage.getByText("常驻", { exact: true }).count()) throw new Error("有限时长状态被错误显示为常驻");
await statusPage.screenshot({ path: `${process.env.TEMP || "."}\\v421-overlay-status.png`, fullPage: true });

const curvePage = await openOverlay("effect-overlay", { width: 520, height: 520 });
const curvePanels = await curvePage.locator(".curve-panel").count();
if (curvePanels !== 2) throw new Error(`曲线页数量异常：${curvePanels}`);
await curvePage.screenshot({ path: `${process.env.TEMP || "."}\\v421-overlay-curves.png`, fullPage: true });

const alertPage = await openOverlay("alert-overlay", { width: 520, height: 420 });
const alertPanels = await alertPage.locator(".compact-panel").count();
if (alertPanels !== 1) throw new Error(`关键 Buff CD 页数量异常：${alertPanels}`);
await alertPage.screenshot({ path: `${process.env.TEMP || "."}\\v421-overlay-alerts.png`, fullPage: true });

const missingBuffPage = await openOverlay("missing-buff-overlay", { width: 560, height: 300 });
const missingPanels = await missingBuffPage.locator(".missing-buff-panel").count();
const missingRows = await missingBuffPage.locator(".missing-buff-rows article").count();
if (missingPanels !== 1 || missingRows !== 3) throw new Error(`缺少 Buff 双模式行数异常：panels=${missingPanels}, rows=${missingRows}`);
const selectedMissingRows = missingBuffPage.locator(".missing-buff-rows article.missing-buff");
if (await selectedMissingRows.count() !== 2) throw new Error("每个自选 Buff 没有独占一行");
const missingText = await selectedMissingRows.allInnerTexts();
if (!missingText.every(text => text.includes("冰灵") && text.includes("月光泡泡"))) throw new Error(`自选 Buff 没有列出所有缺少角色：${missingText.join(" | ")}`);
const automaticMissingText = await missingBuffPage.locator(".missing-buff-rows article.missing-player").innerText();
if (!automaticMissingText.includes("月光泡泡") || !automaticMissingText.includes("精神爆发") || !automaticMissingText.includes("鸡尾酒")) throw new Error(`全队 Buff 没有按角色合并：${automaticMissingText}`);
await missingBuffPage.screenshot({ path: `${process.env.TEMP || "."}\\v442-overlay-missing-buff.png`, fullPage: true });

const lowHealthPage = await openOverlay("low-health-overlay", { width: 520, height: 160 });
const lowHealthText = await lowHealthPage.locator(".low-health-alert-line").innerText();
if (!lowHealthText.includes("月光泡泡") || lowHealthText.includes("圣徒 27%")) throw new Error(`低血量提醒没有优先显示角色名：${lowHealthText}`);

const clearPayloadState = { current: { ...payload, buffClearRevision: 0, buffClearEvents: [] } };
const clearedBuffPage = await openOverlay("cleared-buff-overlay", { width: 520, height: 160 }, clearPayloadState);
clearPayloadState.current = {
  ...payload,
  buffClearRevision: 1,
  buffClearEvents: [{
    key: "clear-1", revision: 1, timestamp_seconds: 59.5,
    source_id: "boss", source_name: "Boss", skill_id: "clear", skill_name: "清除",
    targets: [{ target_id: "p4", target_name: "月光泡泡", target_job: "圣徒" }],
  }],
};
await clearedBuffPage.waitForTimeout(700);
const clearedBuffText = await clearedBuffPage.locator(".cleared-alert-line").innerText();
if (!clearedBuffText.includes("月光泡泡") || clearedBuffText.includes("Buff 被清除：圣徒")) throw new Error(`Buff 清除提醒没有优先显示角色名：${clearedBuffText}`);

if (errors.length) throw new Error(errors.join("\n"));
process.stdout.write(`[OK] status=${statusPanels}/${statusRows} curves=${curvePanels} alerts=${alertPanels} missing=${missingPanels}/${missingRows} actor-names=2/2\n`);
await browser.close();
