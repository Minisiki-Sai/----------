import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const root = path.resolve("Z:/dragon_nest_build_app");
const baseDir = path.join(root, "outputs", "skill_cast_time_review");
const rowsPath = path.join(baseDir, "skill_cast_time_review_rows.json");
const segmentsPath = path.join(baseDir, "video_segment_estimates.json");
const outputPath = path.join(baseDir, "全职业技能施放时间视频估算表.xlsx");

const payload = JSON.parse(await fs.readFile(rowsPath, "utf8"));
const segmentPayload = JSON.parse(await fs.readFile(segmentsPath, "utf8"));
const rows = payload.rows ?? [];
const segments = segmentPayload.segments ?? [];

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
    fill: "#254E5A",
    font: { bold: true, color: "#FFFFFF" },
    horizontalAlignment: "center",
    verticalAlignment: "center",
  };
  range.format.rowHeight = 26;
}

function styleTable(sheet, startRow, startCol, rowCount, colCount) {
  const range = sheet.getRange(rangeAddr(startRow, startCol, startRow + rowCount - 1, startCol + colCount - 1));
  range.format.borders = { preset: "all", style: "thin", color: "#D7E2E5" };
  range.format.verticalAlignment = "center";
}

function setWidths(sheet, widths) {
  widths.forEach((width, index) => {
    sheet.getRange(`${colLetter(index)}:${colLetter(index)}`).format.columnWidth = width;
  });
}

const workbook = Workbook.create();

const skillSheet = workbook.worksheets.add("技能施放时间");
skillSheet.showGridLines = false;
skillSheet.getRange("A1:AA1").merge();
skillSheet.getRange("A1").values = [["全职业技能施放时间视频估算表"]];
skillSheet.getRange("A1").format = {
  fill: "#E8F4F2",
  font: { bold: true, color: "#143C3D", size: 16 },
  verticalAlignment: "center",
};
skillSheet.getRange("A2:AA2").merge();
skillSheet.getRange("A2").values = [["四个核心列：采用施放时间、起手到伤害出现、起手到可接下个技能、起手到最后一hit/完整打完。当前先生成可维护表格和视频片段估算，逐技能数据需要按视频逐帧核对后填入。"]];
skillSheet.getRange("A2").format = { fill: "#F7FAF9", font: { color: "#31595A" }, wrapText: true };

const headers = [
  "一转职业", "二转职业", "技能ID", "技能名称", "等级", "需求等级",
  "采用施放时间(s)", "起手到伤害出现(s)", "起手到可接下个技能(s)", "起手到最后一hit/完整打完(s)",
  "帧率", "开始帧", "伤害帧", "可接技能帧", "完整结束帧",
  "视频来源", "视频时间段", "置信度", "备注",
  "原始/修正CD(s)", "当前等效CD(s)", "攻击类型", "属性", "原始技能面板",
  "当前倍率", "倍率后技能面板", "描述摘要",
];
const matrix = rows.map((row) => [
  row["一转职业"], row["二转职业"], row["技能ID"], row["技能名称"], row["等级"], row["需求等级"],
  "", "", "", "", "", "", "", "", "", "", "", "待测", "",
  row["原始/修正CD(s)"], row["当前等效CD(s)"], row["攻击类型"], row["属性"], row["原始技能面板"],
  row["当前倍率"], row["倍率后技能面板"], row["描述摘要"],
]);
skillSheet.getRange("A4:AA4").values = [headers];
if (matrix.length) {
  skillSheet.getRange(rangeAddr(5, 0, 4 + matrix.length, headers.length - 1)).values = matrix;
  styleTable(skillSheet, 4, 0, matrix.length + 1, headers.length);
  skillSheet.getRange(`G5:J${4 + matrix.length}`).format.numberFormat = "0.00";
  skillSheet.getRange(`T5:U${4 + matrix.length}`).format.numberFormat = "0.000";
  skillSheet.getRange(`Y5:Y${4 + matrix.length}`).format.numberFormat = "0.000";
}
styleHeader(skillSheet, 4, headers.length);
setWidths(skillSheet, [10, 13, 8, 18, 7, 9, 14, 15, 16, 20, 8, 9, 9, 10, 10, 24, 18, 10, 30, 13, 13, 12, 8, 24, 10, 26, 70]);
skillSheet.getRange(`A4:AA${Math.max(5, 4 + matrix.length)}`).format.wrapText = true;
skillSheet.freezePanes.freezeRows(4);
skillSheet.freezePanes.freezeColumns(6);

const segmentSheet = workbook.worksheets.add("视频片段估算");
segmentSheet.showGridLines = false;
const segHeaders = [
  "片段", "开始(s)", "结束(s)", "片段时长(s)", "估算动作开始(s)", "估算伤害/效果(s)",
  "估算可接技能(s)", "估算完整结束(s)", "采用施放时间(s)", "起手到伤害出现(s)",
  "起手到可接技能(s)", "起手到完整结束(s)", "置信度", "来源", "缩略图文件",
];
const segRows = segments.map((seg) => [
  seg.segment_index, seg.start_s, seg.end_s, seg.duration_s, seg.estimated_action_start_s, seg.estimated_first_effect_s,
  seg.estimated_cancel_ready_s, seg.estimated_full_end_s, seg.cast_time_adopt_s, seg.startup_to_damage_s,
  seg.startup_to_cancel_s, seg.startup_to_full_end_s, seg.confidence, seg.source,
  `outputs/skill_cast_time_review/frame_review/segments/segment_${String(seg.segment_index).padStart(3, "0")}.jpg`,
]);
segmentSheet.getRange("A1:O1").values = [segHeaders];
if (segRows.length) {
  segmentSheet.getRange(rangeAddr(2, 0, 1 + segRows.length, segHeaders.length - 1)).values = segRows;
  styleTable(segmentSheet, 1, 0, segRows.length + 1, segHeaders.length);
  segmentSheet.getRange(`B2:L${1 + segRows.length}`).format.numberFormat = "0.000";
}
styleHeader(segmentSheet, 1, segHeaders.length);
setWidths(segmentSheet, [7, 9, 9, 11, 14, 14, 14, 14, 15, 16, 16, 17, 9, 30, 66]);
segmentSheet.freezePanes.freezeRows(1);

const notes = workbook.worksheets.add("口径说明");
notes.showGridLines = false;
const noteRows = [
  ["项目", "说明"],
  ["采用施放时间", "后续接入模拟器时用于DPS计算的主值，原则上优先用“起手到可接下个技能”，持续/召唤类技能可手动改为更合理口径。"],
  ["起手到伤害出现", "从技能起手帧到首次明显伤害/效果出现的时间。"],
  ["起手到可接下个技能", "从技能起手帧到角色可输入/衔接下一个主要技能的时间。"],
  ["起手到最后一hit/完整打完", "从技能起手帧到最后一段伤害或主要持续效果结束的时间。"],
  ["当前状态", "已下载并分析 BV1akZhYZEsj；该视频是职业合集，不是每技能硬切，不能全自动匹配技能名。"],
  ["下一步", "按职业/技能逐个找更清晰的视频或用合集逐帧人工核对，把四个时间列填进“技能施放时间”表。"],
];
notes.getRange("A1:B7").values = noteRows;
styleHeader(notes, 1, 2);
styleTable(notes, 1, 0, noteRows.length, 2);
setWidths(notes, [24, 110]);
notes.getRange("A1:B7").format.wrapText = true;

await fs.mkdir(baseDir, { recursive: true });
const inspect = await workbook.inspect({ kind: "region", sheetId: "技能施放时间", range: "A1:AA10", maxChars: 3000 });
console.log(inspect.ndjson);
const scan = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 100 },
});
console.log(scan.ndjson);
for (const [sheetName, range] of [["技能施放时间", "A1:AA22"], ["视频片段估算", "A1:O34"], ["口径说明", "A1:B7"]]) {
  const preview = await workbook.render({ sheetName, range, autoCrop: "all", scale: 1, format: "png" });
  await fs.writeFile(path.join(baseDir, `${sheetName}.png`), new Uint8Array(await preview.arrayBuffer()));
}
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);
console.log(`SAVED ${outputPath}`);
