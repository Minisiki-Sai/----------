import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const root = path.resolve("Z:/dragon_nest_build_app");
const outputDir = path.join(root, "outputs", "skill_cast_time_review");
const inputPath = path.join(outputDir, "skill_cast_time_review_rows.json");
const outputPath = path.join(outputDir, "全职业技能施放时间确认表.xlsx");

const payload = JSON.parse(await fs.readFile(inputPath, "utf8"));
const rows = payload.rows ?? [];

function colLetter(index) {
  let n = index + 1;
  let s = "";
  while (n > 0) {
    const mod = (n - 1) % 26;
    s = String.fromCharCode(65 + mod) + s;
    n = Math.floor((n - mod) / 26);
  }
  return s;
}

function rangeAddr(row1, col1, row2, col2) {
  return `${colLetter(col1)}${row1}:${colLetter(col2)}${row2}`;
}

function styleHeader(sheet, row, colCount) {
  const range = sheet.getRange(rangeAddr(row, 0, row, colCount - 1));
  range.format = {
    fill: "#285E61",
    font: { bold: true, color: "#FFFFFF" },
    horizontalAlignment: "center",
    verticalAlignment: "center",
  };
  range.format.rowHeight = 26;
}

function styleTable(sheet, startRow, startCol, rowCount, colCount) {
  const range = sheet.getRange(rangeAddr(startRow, startCol, startRow + rowCount - 1, startCol + colCount - 1));
  range.format.borders = { preset: "all", style: "thin", color: "#D6E3E7" };
  range.format.verticalAlignment = "center";
}

function setWidths(sheet, widths) {
  widths.forEach((width, index) => {
    sheet.getRange(`${colLetter(index)}:${colLetter(index)}`).format.columnWidth = width;
  });
}

const workbook = Workbook.create();

const sheet = workbook.worksheets.add("施放时间确认");
sheet.showGridLines = false;
sheet.getRange("A1:W1").merge();
sheet.getRange("A1").values = [["全职业技能施放时间确认表"]];
sheet.getRange("A1").format = {
  fill: "#E9F5F2",
  font: { bold: true, color: "#143C3D", size: 16 },
  verticalAlignment: "center",
};
sheet.getRange("A2:W2").merge();
sheet.getRange("A2").values = [["说明：当前技能数据没有可靠的逐技能施放时间字段，表中“施放时间(s)-待确认”留空。请按实战完整动作时间填写，后续可接入技能模拟DPS。"]];
sheet.getRange("A2").format = { fill: "#F8FBFA", font: { color: "#31595A" }, wrapText: true };

const headers = [
  "一转职业", "二转职业", "技能ID", "技能名称", "等级", "需求等级",
  "原始/修正CD(s)", "当前等效CD(s)", "CD来源", "等效CD来源",
  "攻击类型", "属性", "原始技能面板", "当前倍率", "灼烧手基础加成",
  "倍率后技能面板", "施放时间(s)-待确认", "前摇/后摇/完整动作备注",
  "是否计入施放时间修正", "网页DelayTime(s)", "描述持续时间(s)", "描述摘要", "显示名称",
];

const matrix = rows.map((row) => headers.map((key) => row[key] ?? ""));
sheet.getRange("A4:W4").values = [headers];
if (matrix.length) {
  sheet.getRange(rangeAddr(5, 0, 4 + matrix.length, headers.length - 1)).values = matrix;
  styleTable(sheet, 4, 0, matrix.length + 1, headers.length);
  sheet.getRange(`G5:H${4 + matrix.length}`).format.numberFormat = "0.000";
  sheet.getRange(`N5:O${4 + matrix.length}`).format.numberFormat = "0.000";
  sheet.getRange(`Q5:Q${4 + matrix.length}`).format.numberFormat = "0.00";
}
styleHeader(sheet, 4, headers.length);
setWidths(sheet, [10, 13, 8, 18, 7, 9, 13, 13, 18, 16, 12, 8, 24, 10, 14, 26, 18, 28, 18, 14, 14, 70, 18]);
sheet.getRange(`A4:W${Math.max(5, 4 + matrix.length)}`).format.wrapText = true;
sheet.freezePanes.freezeRows(4);
sheet.freezePanes.freezeColumns(4);

const byJobSheet = workbook.worksheets.add("按职业统计");
byJobSheet.showGridLines = false;
const counts = new Map();
for (const row of rows) {
  const first = row["一转职业"] ?? "";
  const second = row["二转职业"] ?? "";
  const key = `${first}\u0000${second}`;
  counts.set(key, (counts.get(key) ?? 0) + 1);
}
const statHeaders = ["一转职业", "二转职业", "技能数量", "已填写施放时间数量", "待填写数量"];
const statRows = Array.from(counts.entries()).map(([key, count]) => {
  const [first, second] = key.split("\u0000");
  return [first, second, count, 0, count];
});
byJobSheet.getRange("A1:E1").values = [statHeaders];
if (statRows.length) {
  byJobSheet.getRange(rangeAddr(2, 0, 1 + statRows.length, statHeaders.length - 1)).values = statRows;
  styleTable(byJobSheet, 1, 0, statRows.length + 1, statHeaders.length);
}
styleHeader(byJobSheet, 1, statHeaders.length);
setWidths(byJobSheet, [12, 14, 10, 18, 12]);
byJobSheet.freezePanes.freezeRows(1);

const notes = workbook.worksheets.add("说明");
notes.showGridLines = false;
const noteRows = [
  ["项目", "说明"],
  ["表格范围", payload.meta?.scope ?? "当前模拟器技能模拟下拉框内的非EX伤害技能"],
  ["为什么施放时间留空", "当前 JSON 只有 _DelayTime 等冷却/持续字段，没有可靠的逐技能动作耗时字段。"],
  ["后续接入口径", "DPS不再只按 技能等效面板 / CD 计算，而应结合技能施放时间，限制同一时间轴内可释放技能数量。"],
  ["建议填写方式", "优先填完整实战动作时间；特殊持续输出技能可以在备注里写触发节奏、持续时间、可提前取消等。"],
  ["当前等效CD", "已包含模拟器现有的内部等效CD规则、固定CD和特殊双飞腿规则，未包含外部鸡尾酒/精神爆发/时间加速。"],
];
notes.getRange("A1:B6").values = noteRows;
styleHeader(notes, 1, 2);
styleTable(notes, 1, 0, noteRows.length, 2);
setWidths(notes, [22, 92]);
notes.getRange("A1:B6").format.wrapText = true;

await fs.mkdir(outputDir, { recursive: true });

const inspectSummary = await workbook.inspect({
  kind: "region",
  sheetId: "施放时间确认",
  range: "A1:W10",
  maxChars: 4000,
});
console.log(inspectSummary.ndjson);

const errorScan = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 100 },
  summary: "formula error scan",
});
console.log(errorScan.ndjson);

const previewRanges = {
  "施放时间确认": "A1:W22",
  "按职业统计": "A1:E32",
  "说明": "A1:B6",
};
for (const sheetName of ["施放时间确认", "按职业统计", "说明"]) {
  const preview = await workbook.render({ sheetName, range: previewRanges[sheetName], autoCrop: "all", scale: 1, format: "png" });
  await fs.writeFile(path.join(outputDir, `${sheetName}.png`), new Uint8Array(await preview.arrayBuffer()));
}

const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);
console.log(`SAVED ${outputPath}`);
