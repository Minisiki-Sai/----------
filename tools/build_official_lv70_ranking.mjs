import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const root = path.resolve("Z:/dragon_nest_build_app");
const inputPath = path.join(root, "exports", "skill_sim_dps_60L_all_jobs_v3_23.json");
const officialDir = path.join(root, "exports", "official");
const outputPath = path.join(officialDir, "排行表正式版LV70v1.xlsx");
const dataPath = path.join(root, "dragon_nest_builder", "data", "modular", "tables", "team_extreme_coefficients_lv70_v1.json");
const exportedJsonPath = path.join(officialDir, "extreme_team_coefficients_lv70_v1.json");

const payload = JSON.parse(await fs.readFile(inputPath, "utf8"));
const summary = payload.summary ?? [];
const details = payload.details ?? [];
const meanDps = summary.reduce((total, row) => total + Number(row.dps_total ?? 0), 0) / Math.max(1, summary.length);
const sortedSummary = [...summary].sort((a, b) => Number(b.dps_total ?? 0) - Number(a.dps_total ?? 0));

function finalCoeff(row) {
  const raw = Number(row.dps_total ?? 0) / meanDps;
  return (row.second_job === "曜" ? raw * 0.3 : raw);
}

const coefficientRows = sortedSummary.map((row, index) => ({
  rank: index + 1,
  first_job: row.first_job,
  second_job: row.second_job,
  dps_total: Number(row.dps_total ?? 0),
  mean_coefficient: Number(row.dps_total ?? 0) / meanDps,
  extreme_team_coefficient: finalCoeff(row),
}));

const coefficientPayload = {
  version: "LV70v1",
  source: "排行表正式版LV70v1",
  generated_from: "exports/skill_sim_dps_60L_all_jobs_v3_23.json",
  baseline: "建议系数(均值=1)",
  notes: [
    "系数=职业DPS总和/全职业DPS总和均值。",
    "曜的极限配队系数=建议系数(均值=1)*0.3，用于体现实战通常打坐。",
  ],
  mean_dps: meanDps,
  coefficients: Object.fromEntries(coefficientRows.map((row) => [
    row.second_job,
    Number(row.extreme_team_coefficient.toFixed(6)),
  ])),
  rows: coefficientRows.map((row) => ({
    ...row,
    dps_total: Number(row.dps_total.toFixed(6)),
    mean_coefficient: Number(row.mean_coefficient.toFixed(6)),
    extreme_team_coefficient: Number(row.extreme_team_coefficient.toFixed(6)),
  })),
};

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

function setWidths(sheet, widths) {
  widths.forEach((width, index) => {
    sheet.getRange(`${colLetter(index)}:${colLetter(index)}`).format.columnWidth = width;
  });
}

function styleHeader(sheet, row, colCount) {
  const range = sheet.getRange(rangeAddr(row, 0, row, colCount - 1));
  range.format = {
    fill: "#1F6F78",
    font: { bold: true, color: "#FFFFFF" },
    horizontalAlignment: "center",
    verticalAlignment: "center",
  };
  range.format.rowHeight = 25;
}

function styleTable(sheet, startRow, startCol, rowCount, colCount) {
  const range = sheet.getRange(rangeAddr(startRow, startCol, startRow + rowCount - 1, startCol + colCount - 1));
  range.format.borders = { preset: "all", style: "thin", color: "#D8E4E8" };
  range.format.verticalAlignment = "center";
}

const workbook = Workbook.create();
const ranking = workbook.worksheets.add("DPS排序");
ranking.showGridLines = false;
ranking.getRange("A1:V1").merge();
ranking.getRange("A1").values = [["排行表正式版LV70v1"]];
ranking.getRange("A1").format = {
  fill: "#E6F4F1",
  font: { bold: true, color: "#113C43", size: 16 },
  horizontalAlignment: "left",
  verticalAlignment: "center",
};
ranking.getRange("A2:V2").merge();
ranking.getRange("A2").values = [[`来源：60L装备、全L卡片、一键纹章、一键石板、其他页一键全选且技能buff清空；极限配队系数使用均值=1，曜额外乘0.3。全职业DPS均值=${meanDps.toFixed(3)}。`]];
ranking.getRange("A2").format = { fill: "#F7FAFC", font: { color: "#334155" }, wrapText: true };

const headers = [
  "排名", "一转职业", "二转职业", "攻击类型", "主属性", "属性职业",
  "等效面板", "物攻均值", "魔攻均值", "最终%", "致命%", "爆伤%",
  "火攻%", "水攻%", "光攻%", "暗攻%", "DPS总和(%/s)", "期望DPS/s",
  "建议系数(中位数=1)", "建议系数(均值=1)", "建议系数(最高=1)", "极限配队系数",
];
const summaryRows = sortedSummary.map((row, index) => [
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
  null,
]);
ranking.getRange("A3:V3").values = [headers];
if (summaryRows.length) {
  const last = 3 + summaryRows.length;
  ranking.getRange(rangeAddr(4, 0, last, headers.length - 1)).values = summaryRows;
  ranking.getRange(`S4:S${last}`).formulas = summaryRows.map((_, i) => [`=Q${i + 4}/MEDIAN($Q$4:$Q$${last})`]);
  ranking.getRange(`T4:T${last}`).formulas = summaryRows.map((_, i) => [`=Q${i + 4}/AVERAGE($Q$4:$Q$${last})`]);
  ranking.getRange(`U4:U${last}`).formulas = summaryRows.map((_, i) => [`=Q${i + 4}/MAX($Q$4:$Q$${last})`]);
  ranking.getRange(`V4:V${last}`).formulas = summaryRows.map((_, i) => [`=IF(C${i + 4}="曜",T${i + 4}*0.3,T${i + 4})`]);
  styleTable(ranking, 3, 0, summaryRows.length + 1, headers.length);
  ranking.getRange(`G4:I${last}`).format.numberFormat = "#,##0";
  ranking.getRange(`J4:Q${last}`).format.numberFormat = "0.000";
  ranking.getRange(`R4:R${last}`).format.numberFormat = "#,##0";
  ranking.getRange(`S4:V${last}`).format.numberFormat = "0.000";
}
styleHeader(ranking, 3, headers.length);
setWidths(ranking, [7, 11, 13, 10, 9, 10, 13, 12, 12, 9, 9, 9, 9, 9, 9, 9, 15, 14, 18, 16, 16, 15]);
ranking.freezePanes.freezeRows(3);

const coeffSheet = workbook.worksheets.add("极限配队系数");
coeffSheet.showGridLines = false;
coeffSheet.getRange("A1:F1").merge();
coeffSheet.getRange("A1").values = [["一键极限优化配队默认系数"]];
coeffSheet.getRange("A1").format = {
  fill: "#EAF2FF",
  font: { bold: true, color: "#1E3A5F", size: 15 },
};
const coeffHeaders = ["排名", "一转职业", "二转职业", "DPS总和(%/s)", "建议系数(均值=1)", "极限配队系数"];
coeffSheet.getRange("A3:F3").values = [coeffHeaders];
coeffSheet.getRange(rangeAddr(4, 0, 3 + coefficientRows.length, coeffHeaders.length - 1)).values = coefficientRows.map((row) => [
  row.rank,
  row.first_job,
  row.second_job,
  row.dps_total,
  row.mean_coefficient,
  row.extreme_team_coefficient,
]);
styleHeader(coeffSheet, 3, coeffHeaders.length);
styleTable(coeffSheet, 3, 0, coefficientRows.length + 1, coeffHeaders.length);
coeffSheet.getRange(`D4:D${3 + coefficientRows.length}`).format.numberFormat = "0.000";
coeffSheet.getRange(`E4:F${3 + coefficientRows.length}`).format.numberFormat = "0.000";
setWidths(coeffSheet, [8, 12, 14, 16, 18, 16]);
coeffSheet.freezePanes.freezeRows(3);

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

const config = workbook.worksheets.add("配置和公式");
config.showGridLines = false;
config.getRange("A1:D1").merge();
config.getRange("A1").values = [["配置和公式"]];
config.getRange("A1").format = {
  fill: "#F8FAFC",
  font: { bold: true, color: "#0F172A", size: 15 },
};
const configRows = [
  ["项目", "说明", "公式/口径", "备注"],
  ["装备", payload.config?.equipment ?? "", "", "主副强化12，防具10"],
  ["卡片", payload.config?.cards ?? "", "", "全L"],
  ["纹章", payload.config?.glyphs ?? "", "", "A纹章使用对应攻击额外属性"],
  ["石板", "一键石板", "", "包含普通石板和源铸石板"],
  ["其他", payload.config?.misc ?? "", "", "一键全选后清空技能buff"],
  ["属性攻", payload.config?.element_attack ?? "", "", "全属性攻击统一为该配装最大值"],
  ["DPS总和", payload.config?.skill_sim ?? "", "求和属性前DPS%/s", "属性职业排序不使用括号内属性后DPS"],
  ["建议系数(均值=1)", "职业DPS总和/全职业DPS均值", `全职业DPS均值=${meanDps.toFixed(6)}`, "作为极限配队基础系数"],
  ["曜", "建议系数额外乘0.3", "极限配队系数=建议系数(均值=1)*0.3", "体现实战通常打坐"],
];
config.getRange(rangeAddr(3, 0, 2 + configRows.length, 3)).values = configRows;
styleHeader(config, 3, 4);
styleTable(config, 3, 0, configRows.length, 4);
setWidths(config, [20, 48, 48, 42]);
config.getRange(`A3:D${2 + configRows.length}`).format.wrapText = true;

await fs.mkdir(officialDir, { recursive: true });
await fs.writeFile(dataPath, JSON.stringify(coefficientPayload, null, 2), "utf8");
await fs.writeFile(exportedJsonPath, JSON.stringify(coefficientPayload, null, 2), "utf8");

const inspectSummary = await workbook.inspect({
  kind: "table",
  sheetId: "DPS排序",
  range: "A3:V12",
  include: "values,formulas",
  tableMaxRows: 10,
  tableMaxCols: 22,
  maxChars: 5000,
});
console.log(inspectSummary.ndjson);

const errorScan = await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 100 },
  summary: "formula error scan",
});
console.log(errorScan.ndjson);

if (process.env.RENDER_PREVIEW === "1") {
  for (const sheetName of ["DPS排序", "极限配队系数", "配置和公式"]) {
    const preview = await workbook.render({ sheetName, autoCrop: "all", scale: 1, format: "png" });
    await fs.writeFile(path.join(officialDir, `${sheetName}.png`), new Uint8Array(await preview.arrayBuffer()));
  }
}

const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);
console.log(`SAVED ${outputPath}`);
console.log(`SAVED ${dataPath}`);
console.log(`SAVED ${exportedJsonPath}`);
