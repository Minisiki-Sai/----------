import fs from "node:fs/promises";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";

const inputPath = "Z:/dragon_nest_build_app/outputs/skill_glyph_review/skill_glyphs_extracted.json";
const outputDir = "Z:/dragon_nest_build_app/outputs/skill_glyph_review";
const outputPath = `${outputDir}/龙之谷全技能纹章拆包提取表.xlsx`;
const data = JSON.parse(await fs.readFile(inputPath, "utf8"));

const workbook = Workbook.create();
const summary = workbook.worksheets.add("技能纹章汇总");
const detail = workbook.worksheets.add("技能纹章明细");
const notes = workbook.worksheets.add("字段说明");

const colors = {
  navy: "#17324D",
  blue: "#2F75B5",
  paleBlue: "#EAF3F8",
  paleGreen: "#EAF5EE",
  paleGold: "#FFF4D6",
  line: "#D8E1E8",
  text: "#243442",
  muted: "#5E7180",
  white: "#FFFFFF",
};

function titleBand(sheet, title, subtitle, lastColumn) {
  sheet.mergeCells(`A1:${lastColumn}1`);
  sheet.getRange("A1").values = [[title]];
  sheet.getRange(`A1:${lastColumn}1`).format = {
    fill: colors.navy,
    font: { bold: true, color: colors.white, size: 16 },
    verticalAlignment: "center",
  };
  sheet.getRange("1:1").format.rowHeight = 32;
  sheet.mergeCells(`A2:${lastColumn}2`);
  sheet.getRange("A2").values = [[subtitle]];
  sheet.getRange(`A2:${lastColumn}2`).format = {
    fill: colors.paleBlue,
    font: { color: colors.muted, size: 10 },
    verticalAlignment: "center",
    wrapText: true,
  };
  sheet.getRange("2:2").format.rowHeight = 30;
  sheet.showGridLines = false;
}

function styleHeader(range) {
  range.format = {
    fill: colors.blue,
    font: { bold: true, color: colors.white },
    horizontalAlignment: "center",
    verticalAlignment: "center",
    wrapText: true,
    borders: { preset: "outside", style: "thin", color: colors.blue },
  };
}

titleBand(
  summary,
  "龙之谷全技能纹章汇总",
  `拆包来源：G:/Resource00/resource/ext/glyphskilltable.dnt｜共 ${data.summary.length} 个技能；效果档位已去重。`,
  "F",
);
const summaryHeaders = ["职业页", "技能ID", "技能名称", "效果类型数", "档位记录数", "纹章效果汇总"];
const summaryRows = data.summary.map((row) => [
  row.job,
  row.skill_id,
  row.skill,
  row.option_count,
  row.grade_count,
  row.effects,
]);
summary.getRange(`A4:F${4 + summaryRows.length}`).values = [summaryHeaders, ...summaryRows];
styleHeader(summary.getRange("A4:F4"));
summary.getRange(`A5:F${4 + summaryRows.length}`).format = {
  font: { color: colors.text, size: 10 },
  verticalAlignment: "center",
  borders: { insideHorizontal: { style: "thin", color: colors.line } },
};
summary.getRange(`B5:E${4 + summaryRows.length}`).format.horizontalAlignment = "right";
summary.getRange(`B5:E${4 + summaryRows.length}`).format.numberFormat = "#,##0";
summary.getRange(`F5:F${4 + summaryRows.length}`).format.wrapText = true;
summary.getRange("A:A").format.columnWidth = 14;
summary.getRange("B:B").format.columnWidth = 11;
summary.getRange("C:C").format.columnWidth = 20;
summary.getRange("D:E").format.columnWidth = 12;
summary.getRange("F:F").format.columnWidth = 58;
summary.freezePanes.freezeRows(4);
const summaryTable = summary.tables.add(`A4:F${4 + summaryRows.length}`, true, "SkillGlyphSummaryTable");
summaryTable.style = "TableStyleMedium2";
summaryTable.showFilterButton = true;

titleBand(
  detail,
  "龙之谷全技能纹章明细",
  `共 ${data.detail.length} 条去重记录；“原始技能ID”保留一个纹章同时绑定多个技能的拆包写法。`,
  "O",
);
const detailHeaders = [
  "职业页", "职业ID", "技能ID", "原始技能ID", "技能名称", "效果类型", "档位", "数值", "完整效果描述",
  "纹章效果代码", "纹章效果值", "技能等级ID", "描述文本ID", "描述参数", "原始行ID",
];
const detailRows = data.detail.map((row) => [
  row.job,
  row.job_id,
  row.skill_id,
  row.source_skill_ids,
  row.skill,
  row.effect_type,
  row.grade,
  row.value,
  row.effect_text,
  row.glyph_effect_code,
  row.glyph_effect_value,
  row.skill_level_id,
  row.summary_id,
  row.summary_params,
  row.row_id,
]);
detail.getRange(`A4:O${4 + detailRows.length}`).values = [detailHeaders, ...detailRows];
styleHeader(detail.getRange("A4:O4"));
detail.getRange(`A5:O${4 + detailRows.length}`).format = {
  font: { color: colors.text, size: 9 },
  verticalAlignment: "center",
  borders: { insideHorizontal: { style: "thin", color: colors.line } },
};
detail.getRange(`B5:D${4 + detailRows.length}`).format.numberFormat = "#,##0";
detail.getRange(`H5:H${4 + detailRows.length}`).format.numberFormat = "0.00";
detail.getRange(`J5:L${4 + detailRows.length}`).format.numberFormat = "0.######";
detail.getRange(`O5:O${4 + detailRows.length}`).format.numberFormat = "0";
detail.getRange(`B5:D${4 + detailRows.length}`).format.horizontalAlignment = "right";
detail.getRange(`H5:O${4 + detailRows.length}`).format.horizontalAlignment = "right";
detail.getRange(`I5:I${4 + detailRows.length}`).format.wrapText = true;
const detailWidths = [13, 9, 10, 16, 20, 13, 9, 10, 44, 14, 13, 12, 15, 30, 14];
detailWidths.forEach((width, index) => {
  detail.getRangeByIndexes(0, index, 4 + detailRows.length, 1).format.columnWidth = width;
});
detail.freezePanes.freezeRows(4);
detail.freezePanes.freezeColumns(5);
const detailTable = detail.tables.add(`A4:O${4 + detailRows.length}`, true, "SkillGlyphDetailTable");
detailTable.style = "TableStyleMedium2";
detailTable.showFilterButton = true;

titleBand(
  notes,
  "字段说明与使用建议",
  "这份表用于人工确认技能纹章数据，尚未直接接入模拟器。建议优先在汇总页筛职业/技能，再到明细页核对描述与拆包字段。",
  "D",
);
const noteRows = [
  ["字段", "含义", "处理规则", "备注"],
  ["职业页", "技能所属技能树页面", "由技能表的 NeedJob 映射", "未能映射的记录标为“未匹配”"],
  ["技能ID", "skilltable_character.dnt 中的技能行 ID", "数值保留", "可用于后续关联技能树/伤害数据"],
  ["原始技能ID", "glyphskilltable.dnt 的原始 SkillID", "多 ID 记录按分号拆分为多行", "原值仍保留，便于追溯"],
  ["效果类型", "从中文描述归纳的用途", "伤害、冷却、耗蓝、持续时间、致命等", "“其他”需要人工确认"],
  ["档位", "同技能同描述模板下的相对档位", "按数值划分低/中/高档", "只有一个数值时标为唯一档"],
  ["数值", "从描述参数或百分比文本提取", "以数值保存，不带百分号", "复杂描述请以完整效果描述为准"],
  ["完整效果描述", "UI 文本模板替换参数后的中文说明", "去掉颜色控制码并合并换行", "这是人工确认的首要字段"],
  ["原始行ID", "纹章表中的 RowId", "完整保留", "用于回查原始 DNT"],
  ["数据源", "G:/Resource00/resource/ext/glyphskilltable.dnt", "技能映射：skilltable_character.dnt", "文本映射：resource/uistring/uistring.xml"],
];
notes.getRange(`A4:D${3 + noteRows.length}`).values = noteRows;
styleHeader(notes.getRange("A4:D4"));
notes.getRange(`A5:D${3 + noteRows.length}`).format = {
  font: { color: colors.text, size: 10 },
  verticalAlignment: "top",
  wrapText: true,
  borders: { insideHorizontal: { style: "thin", color: colors.line } },
};
notes.getRange(`A5:A${3 + noteRows.length}`).format = { fill: colors.paleGreen, font: { bold: true, color: colors.text } };
notes.getRange("A:A").format.columnWidth = 18;
notes.getRange("B:B").format.columnWidth = 32;
notes.getRange("C:C").format.columnWidth = 38;
notes.getRange("D:D").format.columnWidth = 42;
notes.getRange(`5:${3 + noteRows.length}`).format.rowHeight = 34;
notes.freezePanes.freezeRows(4);

await fs.mkdir(outputDir, { recursive: true });
const checks = [];
checks.push((await workbook.inspect({
  kind: "table",
  range: "技能纹章汇总!A1:F14",
  include: "values,formulas",
  tableMaxRows: 14,
  tableMaxCols: 6,
})).ndjson);
checks.push((await workbook.inspect({
  kind: "match",
  searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",
  options: { useRegex: true, maxResults: 50 },
  summary: "final formula error scan",
})).ndjson);

for (const [sheetName, range, fileName] of [
  ["技能纹章汇总", "A1:F24", "preview_summary.png"],
  ["技能纹章明细", "A1:O18", "preview_detail.png"],
  ["字段说明", "A1:D13", "preview_notes.png"],
]) {
  const preview = await workbook.render({ sheetName, range, scale: 1.2, format: "png" });
  await fs.writeFile(`${outputDir}/${fileName}`, new Uint8Array(await preview.arrayBuffer()));
}

const output = await SpreadsheetFile.exportXlsx(workbook);
await output.save(outputPath);
console.log(JSON.stringify({ outputPath, checks }, null, 2));
