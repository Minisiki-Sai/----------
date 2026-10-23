import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const root = "Z:/dragon_nest_build_app";
const curated = process.env.BURST_RULE_CURATED === "1";
const inputPath = path.join(
  root,
  "outputs/burst_rules",
  curated ? "skill_burst_rule_candidates_curated.json" : "skill_burst_rule_candidates.json",
);
const outputDir = path.join(root, "outputs/burst_rules");
const outputPath = path.join(outputDir, curated ? "爆发加速模拟_职业机制候选规则表_精简版.xlsx" : "爆发加速模拟_职业机制候选规则表.xlsx");
const previewPath = path.join(outputDir, curated ? "burst_rule_candidates_curated_preview.png" : "burst_rule_candidates_preview.png");

const payload = JSON.parse(await fs.readFile(inputPath, "utf8"));
await fs.mkdir(outputDir, { recursive: true });

const workbook = Workbook.create();
const guide = workbook.worksheets.add("使用说明");
const summary = workbook.worksheets.add("职业摘要");
const sheet = workbook.worksheets.add("候选规则");

guide.showGridLines = false;
summary.showGridLines = false;
sheet.showGridLines = false;

guide.getRange("A1:F1").merge();
guide.getRange("A1").values = [["爆发/加速模拟职业机制候选规则表"]];
guide.getRange("A1").format = {
  fill: "#243B53",
  font: { bold: true, color: "#FFFFFF", size: 16 },
};
guide.getRange("A3:F3").merge();
guide.getRange("A3").values = [[payload.rule_note]];
guide.getRange("A3").format = { fill: "#E0F2FE", font: { bold: true, color: "#0F172A" }, wrapText: true };
guide.getRange("A5:F10").values = [
  ["怎么用", "在「候选规则」表里筛选职业和候选类型，重点看“描述摘要”和“处理建议”。", "", "", "", ""],
  ["确认状态", "建议你把确认为正确的行改为“保留”，错误的改为“删除”，需要我再判断的改为“复核”。", "", "", "", ""],
  ["接入原则", "确认后我会把规则接入爆发/加速模拟：先计算机制前置、再按冷却/状态窗口/连招优先级排技能。", "", "", "", ""],
  ["CD口径", "描述里减少技能CD，按目标技能总CD折算：例如加速下20s技能实际6s，减少2s只等效减少0.6s。", "", "", "", ""],
  ["特别提醒", "这张表是候选，不代表全部都会接入；需要你确认哪些符合实战。", "", "", "", ""],
  ["下一步", "你确认后，我会生成 skill_burst_rules.json 并改调度器读取它。", "", "", "", ""],
];
guide.getRange("A5:A10").format = { fill: "#DBEAFE", font: { bold: true, color: "#1E3A8A" } };
guide.getRange("B5:F10").merge(true);
guide.getRange("B5:F10").format = { wrapText: true };
guide.getRange("A:F").format.autofitColumns();
guide.getRange("B:B").format.columnWidthPx = 760;

const summaryHeaders = ["职业", "候选数量", "数据源", "备注"];
const summaryRows = payload.summaries.map((item) => [
  item.job,
  item.candidate_count,
  item.file,
  (item.notes || []).join("；"),
]);
summary.getRangeByIndexes(0, 0, 1, summaryHeaders.length).values = [summaryHeaders];
summary.getRangeByIndexes(1, 0, summaryRows.length, summaryHeaders.length).values = summaryRows;
summary.getRangeByIndexes(0, 0, 1, summaryHeaders.length).format = {
  fill: "#334155",
  font: { bold: true, color: "#FFFFFF" },
};
summary.getRangeByIndexes(0, 0, summaryRows.length + 1, summaryHeaders.length).format.borders = {
  preset: "all",
  style: "thin",
  color: "#CBD5E1",
};
summary.getRange("A:D").format.autofitColumns();
summary.freezePanes.freezeRows(1);
summary.tables.add(`A1:D${summaryRows.length + 1}`, true, "JobSummaryTable");

const headers = [
  "职业",
  "页签",
  "技能ID",
  "技能名",
  "技能等级",
  "需求等级",
  "原始CD",
  "候选类型",
  "关联技能",
  "提取数值",
  "描述摘要",
  "处理建议",
  "确认状态",
  "筛选说明",
  "数据源",
];
const rows = payload.rows.map((row) => headers.map((header) => row[header] ?? ""));
sheet.getRangeByIndexes(0, 0, 1, headers.length).values = [headers];
if (rows.length) {
  sheet.getRangeByIndexes(1, 0, rows.length, headers.length).values = rows;
}
const usedRows = rows.length + 1;
sheet.getRangeByIndexes(0, 0, 1, headers.length).format = {
  fill: "#0F766E",
  font: { bold: true, color: "#FFFFFF" },
};
sheet.getRangeByIndexes(0, 0, usedRows, headers.length).format.borders = {
  preset: "all",
  style: "thin",
  color: "#D1D5DB",
};
sheet.getRangeByIndexes(1, 10, Math.max(rows.length, 1), 2).format = { wrapText: true };
sheet.getRangeByIndexes(1, 12, Math.max(rows.length, 1), 1).dataValidation = {
  rule: { type: "list", values: ["待确认", "保留", "删除", "复核"] },
};
sheet.getRange("A:O").format.autofitColumns();
const widths = {
  A: 90,
  B: 90,
  C: 70,
  D: 130,
  E: 70,
  F: 70,
  G: 70,
  H: 160,
  I: 180,
  J: 160,
  K: 520,
  L: 250,
  M: 80,
  N: 260,
  O: 120,
};
for (const [col, px] of Object.entries(widths)) {
  sheet.getRange(`${col}:${col}`).format.columnWidthPx = px;
}
sheet.freezePanes.freezeRows(1);
sheet.freezePanes.freezeColumns(4);
sheet.tables.add(`A1:O${usedRows}`, true, "BurstRuleCandidateTable");

const statusRange = sheet.getRange(`M2:M${usedRows}`);
statusRange.conditionalFormats.add("containsText", {
  text: "保留",
  format: { fill: "#DCFCE7", font: { color: "#166534", bold: true } },
});
statusRange.conditionalFormats.add("containsText", {
  text: "删除",
  format: { fill: "#FEE2E2", font: { color: "#991B1B", bold: true } },
});
statusRange.conditionalFormats.add("containsText", {
  text: "复核",
  format: { fill: "#FEF3C7", font: { color: "#92400E", bold: true } },
});

const inspect = await workbook.inspect({
  kind: "table",
  sheetId: "候选规则",
  range: "A1:O8",
  include: "values",
  tableMaxRows: 8,
  tableMaxCols: 15,
  maxChars: 4000,
});
console.log(inspect.ndjson);
const errors = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 20 },
  summary: "formula error scan",
});
console.log(errors.ndjson);

const preview = await workbook.render({
  sheetName: "候选规则",
  range: "A1:O18",
  scale: 1,
  format: "png",
});
await fs.writeFile(previewPath, new Uint8Array(await preview.arrayBuffer()));

const xlsx = await SpreadsheetFile.exportXlsx(workbook);
await xlsx.save(outputPath);
console.log(outputPath);
