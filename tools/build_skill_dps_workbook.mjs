import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const root = path.resolve("Z:/dragon_nest_build_app");
const inputPath = path.join(root, "exports", "skill_sim_dps_60L_all_jobs_v3_23.json");
const outputDir = path.join(root, "outputs", "skill_dps_export");
const outputPath = path.join(outputDir, "skill_sim_dps_60L_all_jobs_v3_23.xlsx");

const payload = JSON.parse(await fs.readFile(inputPath, "utf8"));
const summary = payload.summary ?? [];
const details = payload.details ?? [];
const errors = payload.errors ?? [];

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
    fill: "#1F6F78",
    font: { bold: true, color: "#FFFFFF" },
    horizontalAlignment: "center",
    verticalAlignment: "center",
  };
}

function styleTable(sheet, startRow, startCol, rowCount, colCount) {
  const range = sheet.getRange(rangeAddr(startRow, startCol, startRow + rowCount - 1, startCol + colCount - 1));
  range.format.borders = { preset: "all", style: "thin", color: "#D8E4E8" };
  range.format.verticalAlignment = "center";
}

function setWidths(sheet, widths) {
  widths.forEach((width, index) => {
    sheet.getRange(`${colLetter(index)}:${colLetter(index)}`).format.columnWidth = width;
  });
}

const workbook = Workbook.create();

const summarySheet = workbook.worksheets.add("DPS排序");
summarySheet.showGridLines = false;
summarySheet.getRange("A1:U1").merge();
summarySheet.getRange("A1").values = [["60L全职业技能模拟 DPS 排序"]];
summarySheet.getRange("A1").format = {
  fill: "#E6F4F1",
  font: { bold: true, color: "#113C43", size: 16 },
  horizontalAlignment: "left",
  verticalAlignment: "center",
};

const summaryHeaders = [
  "排名", "一转职业", "二转职业", "攻击类型", "主属性", "属性职业",
  "等效面板", "物攻均值", "魔攻均值", "最终%", "致命%", "爆伤%",
  "火攻%", "水攻%", "光攻%", "暗攻%", "DPS总和(%/s)", "期望DPS/s",
  "建议系数(中位数=1)", "建议系数(均值=1)", "建议系数(最高=1)",
];
const summaryRows = summary.map((row, index) => [
  index + 1,
  row.first_job,
  row.second_job,
  row.attack_type,
  row.main_stat,
  row.element,
  row.effective_panel,
  row.physical_average,
  row.magic_average,
  row.final_percent,
  row.crit_percent,
  row.crit_damage_percent,
  row.fire_attack,
  row.water_attack,
  row.light_attack,
  row.dark_attack,
  row.dps_total,
  row.expected_dps,
  null,
  null,
  null,
]);
summarySheet.getRange("A3:U3").values = [summaryHeaders];
if (summaryRows.length) {
  summarySheet.getRange(rangeAddr(4, 0, 3 + summaryRows.length, summaryHeaders.length - 1)).values = summaryRows;
  const last = 3 + summaryRows.length;
  summarySheet.getRange(`S4:S${last}`).formulas = summaryRows.map((_, i) => [`=Q${i + 4}/MEDIAN($Q$4:$Q$${last})`]);
  summarySheet.getRange(`T4:T${last}`).formulas = summaryRows.map((_, i) => [`=Q${i + 4}/AVERAGE($Q$4:$Q$${last})`]);
  summarySheet.getRange(`U4:U${last}`).formulas = summaryRows.map((_, i) => [`=Q${i + 4}/MAX($Q$4:$Q$${last})`]);
  styleTable(summarySheet, 3, 0, summaryRows.length + 1, summaryHeaders.length);
  summarySheet.getRange(`G4:I${last}`).format.numberFormat = "#,##0";
  summarySheet.getRange(`J4:Q${last}`).format.numberFormat = "0.000";
  summarySheet.getRange(`R4:R${last}`).format.numberFormat = "#,##0";
  summarySheet.getRange(`S4:U${last}`).format.numberFormat = "0.000";
}
styleHeader(summarySheet, 3, summaryHeaders.length);
setWidths(summarySheet, [7, 11, 13, 10, 9, 10, 13, 12, 12, 9, 9, 9, 9, 9, 9, 9, 15, 14, 18, 16, 16]);
summarySheet.freezePanes.freezeRows(3);

const detailSheet = workbook.worksheets.add("技能模拟明细");
detailSheet.showGridLines = false;
const detailHeaders = [
  "一转职业", "二转职业", "序号", "技能", "等级", "所需等级", "冷却(s)",
  "基础倍率", "灼烧手", "计算倍率", "属性", "等效倍率%", "属性倍率",
  "DPS%/s", "属性后DPS%/s", "灼烧手显示", "技能数值显示", "等效倍率显示", "DPS显示",
];
const detailRows = details.map((row) => [
  row.first_job,
  row.second_job,
  row.row,
  row.skill,
  row.level,
  row.required_level,
  row.cooldown,
  row.base_multiplier,
  row.burning_hand_bonus,
  row.display_multiplier,
  row.element,
  row.effective_percent,
  row.element_multiplier,
  row.dps_percent,
  row.dps_after_element,
  row.burning_hand_text,
  row.skill_value_text,
  row.effective_text,
  row.dps_text,
]);
detailSheet.getRange("A1:S1").values = [detailHeaders];
if (detailRows.length) {
  detailSheet.getRange(rangeAddr(2, 0, 1 + detailRows.length, detailHeaders.length - 1)).values = detailRows;
  styleTable(detailSheet, 1, 0, detailRows.length + 1, detailHeaders.length);
  detailSheet.getRange(`G2:O${detailRows.length + 1}`).format.numberFormat = "0.000";
}
styleHeader(detailSheet, 1, detailHeaders.length);
setWidths(detailSheet, [11, 13, 7, 18, 7, 10, 9, 9, 9, 9, 8, 12, 10, 11, 14, 11, 36, 36, 36]);
detailSheet.freezePanes.freezeRows(1);

const configSheet = workbook.worksheets.add("配置和公式");
configSheet.showGridLines = false;
configSheet.getRange("A1:D1").merge();
configSheet.getRange("A1").values = [["导出配置与系数公式"]];
configSheet.getRange("A1").format = {
  fill: "#EAF2FF",
  font: { bold: true, color: "#1E3A5F", size: 15 },
};
const configRows = [
  ["项目", "说明", "公式/口径", "备注"],
  ["装备", payload.config.equipment, "", "主副强化12，防具10，使用模拟器一键配装规则"],
  ["卡片", payload.config.cards, "", "仅卡片等级全部设为L"],
  ["纹章", payload.config.glyphs, "", "A纹章不能选择同名额外属性时保留无"],
  ["其他", payload.config.misc, "", "保留收集/称号/专精/时装，技能buff清空"],
  ["属性攻击", payload.config.element_attack, "", "用自由输入的属性百分比补齐"],
  ["DPS总和", payload.config.skill_sim, "'=SUM(10个技能的属性前DPS%/s)", "技能排序使用当前模拟器一键模拟逻辑；属性后DPS保留在明细列"],
  ["建议系数(中位数=1)", "推荐观察口径之一", "'=本职业DPS总和 / 全职业DPS总和中位数", "中位职业约为1，对极端值不太敏感"],
  ["建议系数(均值=1)", "整体平均归一", "'=本职业DPS总和 / 全职业DPS总和平均值", "平均职业=1"],
  ["建议系数(最高=1)", "最高职业归一", "'=本职业DPS总和 / 全职业DPS总和最大值", "最高职业=1"],
  ["注意", "该表用于比较技能模拟DPS，不直接等同实战职业强度。", "未计入命中率、输出窗口、操作难度、站位损失、技能空转等。", ""],
];
configSheet.getRange(rangeAddr(3, 0, 2 + configRows.length, 3)).values = configRows;
styleHeader(configSheet, 3, 4);
styleTable(configSheet, 3, 0, configRows.length, 4);
setWidths(configSheet, [24, 52, 48, 42]);
configSheet.getRange(`A3:D${2 + configRows.length}`).format.wrapText = true;

if (errors.length) {
  const errorSheet = workbook.worksheets.add("错误");
  errorSheet.showGridLines = false;
  errorSheet.getRange("A1:C1").values = [["一转职业", "二转职业", "错误"]];
  errorSheet.getRange(rangeAddr(2, 0, 1 + errors.length, 2)).values = errors.map((row) => [row.first_job, row.second_job, row.error]);
  styleHeader(errorSheet, 1, 3);
  styleTable(errorSheet, 1, 0, errors.length + 1, 3);
  setWidths(errorSheet, [14, 14, 70]);
}

await fs.mkdir(outputDir, { recursive: true });

const inspectSummary = await workbook.inspect({
  kind: "table",
  sheetId: "DPS排序",
  range: "A3:U12",
  include: "values,formulas",
  tableMaxRows: 10,
  tableMaxCols: 21,
  maxChars: 5000,
});
console.log(inspectSummary.ndjson);

const errorsScan = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 100 },
  summary: "formula error scan",
});
console.log(errorsScan.ndjson);

for (const sheetName of ["DPS排序", "技能模拟明细", "配置和公式"]) {
  const preview = await workbook.render({ sheetName, autoCrop: "all", scale: 1, format: "png" });
  await fs.writeFile(path.join(outputDir, `${sheetName}.png`), new Uint8Array(await preview.arrayBuffer()));
}

const output = await SpreadsheetFile.exportXlsx(workbook);
try {
  await output.save(outputPath);
  console.log(`SAVED ${outputPath}`);
} catch (error) {
  if (error?.code !== "EBUSY") {
    throw error;
  }
  const stamp = new Date().toISOString().replace(/[-:T]/g, "").slice(0, 14);
  const fallbackPath = path.join(outputDir, `skill_sim_dps_60L_all_jobs_${stamp}.xlsx`);
  await output.save(fallbackPath);
  console.log(`SAVED ${fallbackPath}`);
}
