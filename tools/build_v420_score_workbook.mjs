import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const ROOT = "E:/新建文件夹/龙之谷模拟器版本/龙之谷模拟器4.20";
const OUTPUT_DIR = `${ROOT}/outputs/v420_score_batch_20260819`;
const DATA_PATH = `${OUTPUT_DIR}/workbook_data.json`;
const OUTPUT_PATH = `${OUTPUT_DIR}/龙之谷V4.20抓包全队评分汇总_20260819.xlsx`;
const PREVIEW_DIR = `${OUTPUT_DIR}/previews`;

const data = JSON.parse(await fs.readFile(DATA_PATH, "utf8"));
const workbook = Workbook.create();

const colors = {
  ink: "#102A43",
  teal: "#0B7285",
  tealDark: "#07525F",
  tealLight: "#DFF3F5",
  blueLight: "#EAF2F8",
  grid: "#C9D8E3",
  muted: "#526D82",
  white: "#FFFFFF",
  green: "#D9F2E6",
  amber: "#FFF0C2",
  red: "#FBE0E0",
};

function colName(index) {
  let value = index + 1;
  let name = "";
  while (value > 0) {
    value -= 1;
    name = String.fromCharCode(65 + (value % 26)) + name;
    value = Math.floor(value / 26);
  }
  return name;
}

function styleHeader(range) {
  range.format.fill = colors.teal;
  range.format.font = { bold: true, color: colors.white, size: 10 };
  range.format.verticalAlignment = "center";
  range.format.horizontalAlignment = "center";
  range.format.wrapText = true;
  range.format.rowHeight = 30;
  range.format.borders = { preset: "all", style: "thin", color: colors.grid };
}

function addDataSheet(name, headers, rows, options = {}) {
  const sheet = workbook.worksheets.add(name);
  sheet.showGridLines = false;
  const matrix = [headers, ...rows];
  const endCol = colName(Math.max(0, headers.length - 1));
  const endRow = Math.max(1, matrix.length);
  sheet.getRange(`A1:${endCol}${endRow}`).values = matrix;
  styleHeader(sheet.getRange(`A1:${endCol}1`));
  if (rows.length) {
    const body = sheet.getRange(`A2:${endCol}${endRow}`);
    body.format.font = { color: colors.ink, size: 9 };
    body.format.verticalAlignment = "center";
    body.format.borders = { preset: "all", style: "thin", color: colors.grid };
    body.format.wrapText = false;
  }
  sheet.freezePanes.freezeRows(1);
  if (options.freezeColumns) sheet.freezePanes.freezeColumns(options.freezeColumns);
  (options.widths || []).forEach((width, index) => {
    if (!width) return;
    sheet.getRangeByIndexes(0, index, endRow, 1).format.columnWidth = width;
  });
  for (const [index, format] of Object.entries(options.numberFormats || {})) {
    if (!rows.length) continue;
    sheet.getRangeByIndexes(1, Number(index), rows.length, 1).format.numberFormat = format;
  }
  for (const index of options.wrapColumns || []) {
    if (!rows.length) continue;
    sheet.getRangeByIndexes(1, index, rows.length, 1).format.wrapText = true;
  }
  if (options.colorGrades && rows.length) {
    const gradeIndex = headers.indexOf("等级");
    if (gradeIndex >= 0) {
      const gradeColors = {
        SSS: "#BCE7E8", SS: "#CFEDE6", S: "#D9F2E6", A: "#E6F4D7",
        B: "#FFF0C2", C: "#FCE4C2", D: "#FBE0E0", E: "#F3D4D4",
      };
      rows.forEach((row, rowIndex) => {
        const grade = String(row[gradeIndex] ?? "");
        const fill = gradeColors[grade] || colors.blueLight;
        const cell = sheet.getCell(rowIndex + 1, gradeIndex);
        cell.format.fill = fill;
        cell.format.font = { bold: true, color: colors.ink, size: 10 };
        cell.format.horizontalAlignment = "center";
      });
    }
  }
  return sheet;
}

// Overview first so the workbook opens on a concise control page.
const overview = workbook.worksheets.add("概览");
overview.showGridLines = false;
overview.getRange("A1:H2").merge();
overview.getRange("A1").values = [["龙之谷 V4.20 抓包全队评分汇总"]];
overview.getRange("A1:H2").format.fill = colors.tealDark;
overview.getRange("A1:H2").format.font = { bold: true, color: colors.white, size: 20 };
overview.getRange("A1:H2").format.horizontalAlignment = "left";
overview.getRange("A1:H2").format.verticalAlignment = "center";

overview.getRange("A4:B4").values = [["统计项目", "结果"]];
styleHeader(overview.getRange("A4:B4"));
const scoreCount = Math.max(1, data.rows.scores.length + 1);
const captureCount = Math.max(1, data.rows.captures.length + 1);
overview.getRange("A5:A14").values = [
  ["模拟器版本"], ["评分规则版本"], ["抓包文件数"], ["唯一抓包数"], ["已评分记录数"],
  ["角色评分行数"], ["角色平均总分"], ["角色平均输出分"], ["角色平均辅助分"], ["生存扣分合计"],
];
overview.getRange("B5:B8").values = [["4.20"], [20260819], [data.meta.file_count], [data.meta.unique_capture_count]];
overview.getRange("B9").formulas = [[`=COUNTIF('记录索引'!C2:C${captureCount},"scored")`]];
overview.getRange("B10").formulas = [[`=COUNTA('角色评分'!A2:A${scoreCount})`]];
overview.getRange("B11").formulas = [[`=AVERAGE('角色评分'!K2:K${scoreCount})`]];
overview.getRange("B12").formulas = [[`=AVERAGE('角色评分'!L2:L${scoreCount})`]];
overview.getRange("B13").formulas = [[`=AVERAGE('角色评分'!M2:M${scoreCount})`]];
overview.getRange("B14").formulas = [[`=SUM('角色评分'!P2:P${scoreCount})`]];
overview.getRange("A5:B14").format.borders = { preset: "all", style: "thin", color: colors.grid };
overview.getRange("A5:A14").format.fill = colors.tealLight;
overview.getRange("A5:A14").format.font = { bold: true, color: colors.ink, size: 10 };
overview.getRange("B5:B14").format.font = { color: colors.ink, size: 10 };
overview.getRange("B11:B14").format.numberFormat = "0.00";

overview.getRange("D4:H4").merge();
overview.getRange("D4").values = [["处理与上传说明"]];
styleHeader(overview.getRange("D4:H4"));
overview.getRange("D5:H10").merge();
const upload = data.meta.upload || {};
const uploadText = [
  `抓包按 SHA-256 去重；同一数据改名不会重复计算。`,
  `评分规则版本：20260819。旧版本已上传的同一抓包会按新版重新上传。`,
  `云端状态：${upload.status || upload.result || "以批处理日志为准"}。`,
  `输出技能明细 ${data.meta.skill_record_count} 行；辅助明细 ${data.meta.support_record_count} 行。`,
  `未评分记录主要为校准、TCP/UDP、个人档案或无有效战斗数据的抓包。`,
].join("\n");
overview.getRange("D5").values = [[uploadText]];
overview.getRange("D5:H10").format.fill = colors.blueLight;
overview.getRange("D5:H10").format.font = { color: colors.ink, size: 10 };
overview.getRange("D5:H10").format.wrapText = true;
overview.getRange("D5:H10").format.verticalAlignment = "top";
overview.getRange("D5:H10").format.borders = { preset: "all", style: "thin", color: colors.grid };

overview.getRange("A16:L16").values = [data.headers.summary];
styleHeader(overview.getRange("A16:L16"));
if (data.rows.summary.length) {
  overview.getRange(`A17:L${16 + data.rows.summary.length}`).values = data.rows.summary;
  overview.getRange(`A17:L${16 + data.rows.summary.length}`).format.borders = { preset: "all", style: "thin", color: colors.grid };
  overview.getRange(`A17:L${16 + data.rows.summary.length}`).format.font = { color: colors.ink, size: 9 };
  overview.getRange(`D17:L${16 + data.rows.summary.length}`).format.numberFormat = "0.00";
}
overview.getRange("A:A").format.columnWidth = 25;
overview.getRange("B:B").format.columnWidth = 16;
overview.getRange("C:C").format.columnWidth = 12;
overview.getRange("D:L").format.columnWidth = 16;
overview.freezePanes.freezeRows(2);

addDataSheet("角色评分", data.headers.scores, data.rows.scores, {
  freezeColumns: 4,
  colorGrades: true,
  widths: [25, 18, 18, 15, 14, 18, 12, 13, 26, 8, 10, 10, 10, 10, 11, 11, 10, 10, 10, 14, 14, 14, 12, 12, 16, 16, 16, 14, 12, 10, 10, 10, 12, 14, 45],
  numberFormats: { 10: "0.00", 11: "0.00", 12: "0.00", 13: "0.00", 14: "0.00", 15: "0.00", 16: "0.0%", 17: "0.0%", 18: "0.0%", 20: "0.00", 21: "0.00", 22: "0.00", 23: "0.00", 24: "#,##0", 25: "#,##0", 26: "#,##0", 27: "#,##0", 28: "#,##0", 31: "0.0%", 32: "0.0", 33: "#,##0" },
  wrapColumns: [8, 34],
});

addDataSheet("输出技能明细", data.headers.skills, data.rows.skills, {
  freezeColumns: 5,
  widths: [24, 18, 14, 14, 12, 20, 10, 10, 16, 16, 10, 9, 12, 12, 12, 12, 10, 10],
  numberFormats: { 8: "#,##0", 9: "#,##0", 10: "0.00", 12: "0.00", 13: "0.00", 14: "0.0%", 15: "0.0%", 16: "0.0%" },
});

addDataSheet("辅助技能明细", data.headers.supports, data.rows.supports, {
  freezeColumns: 5,
  widths: [24, 18, 14, 14, 12, 20, 20, 13, 10, 12, 12, 10, 11, 12, 12, 12, 10, 15, 15, 36, 36],
  numberFormats: { 8: "0.00", 9: "0.0%", 10: "0.0%", 11: "0.00", 12: "0.00", 13: "0.00", 14: "0.00", 15: "0.00", 17: "0.0%", 18: "0.0%" },
  wrapColumns: [19, 20],
});

addDataSheet("生存明细", data.headers.survival, data.rows.survival, {
  freezeColumns: 5,
  widths: [24, 18, 14, 14, 12, 11, 14, 9, 10, 10, 10, 10, 10, 10, 10, 10, 12, 10, 9, 12, 14, 16, 11, 12, 11, 12, 12, 11, 12, 11, 14, 16, 48],
  numberFormats: { 5: "0.00", 6: "#,##0", 14: "0.00", 15: "0.00", 16: "0.00", 17: "0.00", 19: "#,##0", 20: "#,##0", 21: "0.0%", 22: "0.00", 24: "0.00", 25: "0.00", 26: "0.0%", 27: "0.00", 29: "0.00", 30: "0.00", 31: "0.00" },
  wrapColumns: [32],
});

addDataSheet("Boss阶段", data.headers.bosses, data.rows.bosses, {
  freezeColumns: 4,
  widths: [24, 18, 14, 25, 14, 15, 10, 10, 11, 11, 32, 35, 12, 13, 9, 12, 12, 13, 10, 10, 10, 10],
  numberFormats: { 4: "#,##0", 5: "#,##0", 8: "0.000", 9: "0.000", 15: "#,##0", 16: "#,##0", 17: "#,##0", 18: "0.0", 19: "0.0", 20: "0.0", 21: "0.0" },
  wrapColumns: [10, 11],
});

addDataSheet("记录索引", data.headers.captures, data.rows.captures, {
  freezeColumns: 2,
  widths: [28, 20, 12, 14, 9, 9, 12, 12, 16, 14, 15, 13, 12, 12, 48],
  numberFormats: { 6: "0.000", 7: "0.000", 8: "#,##0", 9: "#,##0", 10: "#,##0", 11: "0.00", 12: "0" },
  wrapColumns: [14],
});

addDataSheet("未评分记录", data.headers.unscored, data.rows.unscored, {
  widths: [30, 20, 14, 16, 15, 13, 16, 10, 60],
  numberFormats: { 4: "#,##0", 5: "0.000", 6: "#,##0" },
  wrapColumns: [8],
});

addDataSheet("重复文件", data.headers.duplicates, data.rows.duplicates, {
  widths: [36, 36, 22, 15],
});

await fs.mkdir(OUTPUT_DIR, { recursive: true });
await fs.mkdir(PREVIEW_DIR, { recursive: true });
const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(OUTPUT_PATH);

const overviewPreview = await workbook.render({ sheetName: "概览", range: "A1:L34", scale: 1.2, format: "png" });
await fs.writeFile(`${PREVIEW_DIR}/概览.png`, new Uint8Array(await overviewPreview.arrayBuffer()));
const scorePreview = await workbook.render({ sheetName: "角色评分", range: "A1:P18", scale: 1, format: "png" });
await fs.writeFile(`${PREVIEW_DIR}/角色评分.png`, new Uint8Array(await scorePreview.arrayBuffer()));

const inspection = await workbook.inspect({
  kind: "workbook,sheet,formula",
  maxChars: 10000,
  tableMaxRows: 5,
  tableMaxCols: 8,
  options: { maxResults: 80 },
});
await fs.writeFile(`${OUTPUT_DIR}/workbook_inspection.ndjson`, inspection.ndjson || String(inspection), "utf8");
console.log(JSON.stringify({ output: OUTPUT_PATH, previews: PREVIEW_DIR, sheets: 9 }, null, 2));
