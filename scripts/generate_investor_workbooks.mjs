/** Generate formatted XLSX equivalents of the synthetic investor registers. */

import fs from "node:fs/promises";
import path from "node:path";
import { SpreadsheetFile, Workbook } from "@oai/artifact-tool";


const root = process.cwd();
const previewDir = path.join(root, "tmp", "workbook-build");
const fontFamily = "Arial";

const jobs = [
  {
    csv: "data/evals/investor_register.csv",
    xlsx: "data/evals/investor_register.xlsx",
    preview: "eval-investor-register.png",
    tableName: "EvaluationInvestorRegister",
  },
  {
    csv: "data/demo/northstar_growth_fund_ii/investor_register.csv",
    xlsx: "data/demo/northstar_growth_fund_ii/investor_register.xlsx",
    preview: "northstar-investor-register.png",
    tableName: "NorthstarInvestorRegister",
  },
];

const numericHeaders = new Set([
  "commitment_amount",
  "drawn_to_date_before_call",
  "remaining_commitment_amount",
  "capital_call_amount",
  "capital_call_percentage",
  "call_number",
  "management_fee",
]);
const dateHeaders = new Set(["call_date", "due_date"]);
const moneyHeaders = new Set([
  "commitment_amount",
  "drawn_to_date_before_call",
  "remaining_commitment_amount",
  "capital_call_amount",
  "management_fee",
]);

function columnName(index) {
  let result = "";
  let value = index + 1;
  while (value > 0) {
    const remainder = (value - 1) % 26;
    result = String.fromCharCode(65 + remainder) + result;
    value = Math.floor((value - 1) / 26);
  }
  return result;
}

async function buildWorkbook(job) {
  const csvPath = path.join(root, job.csv);
  const outputPath = path.join(root, job.xlsx);
  const csvText = await fs.readFile(csvPath, "utf8");
  const workbook = await Workbook.fromCSV(csvText, { sheetName: "LP Register" });
  const sheet = workbook.worksheets.getItem("LP Register");
  const used = sheet.getUsedRange();
  const values = used.values;
  const headers = values[0].map((value) => String(value));
  const rowCount = values.length;
  const columnCount = headers.length;

  for (let columnIndex = 0; columnIndex < columnCount; columnIndex += 1) {
    const header = headers[columnIndex];
    if (!numericHeaders.has(header) && !dateHeaders.has(header)) continue;
    const typed = values.slice(1).map((row) => {
      const raw = row[columnIndex];
      if (raw === null || raw === undefined || String(raw).trim() === "") return [null];
      if (dateHeaders.has(header)) return [new Date(`${String(raw)}T00:00:00Z`)];
      return [Number(raw)];
    });
    sheet.getRangeByIndexes(1, columnIndex, rowCount - 1, 1).values = typed;
  }

  const finalColumn = columnName(columnCount - 1);
  const tableRange = `A1:${finalColumn}${rowCount}`;
  sheet.showGridLines = false;
  sheet.freezePanes.freezeRows(1);
  sheet.freezePanes.freezeColumns(3);
  sheet.tabColor = "#17365D";

  sheet.getRange(tableRange).format.font = { name: fontFamily, size: 10, color: "#172033" };
  sheet.getRange(`A1:${finalColumn}1`).format = {
    fill: "#17365D",
    font: { name: fontFamily, size: 10, bold: true, color: "#FFFFFF" },
    horizontalAlignment: "center",
    verticalAlignment: "center",
    wrapText: true,
    rowHeight: 34,
    borders: { preset: "inside", style: "thin", color: "#FFFFFF" },
  };
  if (rowCount > 1) {
    sheet.getRange(`A2:${finalColumn}${rowCount}`).format.verticalAlignment = "center";
    sheet.getRange(`A2:${finalColumn}${rowCount}`).format.rowHeight = 19;
  }

  headers.forEach((header, index) => {
    const letter = columnName(index);
    const range = sheet.getRange(`${letter}2:${letter}${rowCount}`);
    if (moneyHeaders.has(header)) {
      range.format.numberFormat = '#,##0.00;(#,##0.00);-';
      range.format.horizontalAlignment = "right";
    } else if (header === "capital_call_percentage") {
      range.format.numberFormat = "0.00%";
      range.format.horizontalAlignment = "right";
    } else if (header === "call_number") {
      range.format.numberFormat = "0";
      range.format.horizontalAlignment = "right";
    } else if (dateHeaders.has(header)) {
      range.format.numberFormat = "yyyy-mm-dd";
      range.format.horizontalAlignment = "center";
    }
  });

  const widths = [18, 10, 16, 33, 40, 18, 22, 24, 20, 20, 12, 14, 14, 11, 25, 18, 18, 14];
  widths.slice(0, columnCount).forEach((width, index) => {
    sheet.getRange(`${columnName(index)}:${columnName(index)}`).format.columnWidth = width;
  });

  const table = sheet.tables.add(tableRange, true, job.tableName);
  table.style = "TableStyleMedium2";
  table.showBandedRows = true;
  table.showFilterButton = true;

  const summary = await workbook.inspect({
    kind: "table",
    range: `LP Register!A1:${finalColumn}${Math.min(rowCount, 8)}`,
    include: "values,formulas",
    tableMaxRows: 8,
    tableMaxCols: columnCount,
    maxChars: 9000,
  });
  console.log(summary.ndjson);
  const errors = await workbook.inspect({
    kind: "match",
    searchTerm: "#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A|#NUM!|#NULL!|#SPILL!|#CALC!",
    options: { useRegex: true, maxResults: 100 },
    summary: `formula error scan: ${job.xlsx}`,
  });
  console.log(errors.ndjson);

  await fs.mkdir(path.dirname(outputPath), { recursive: true });
  const output = await SpreadsheetFile.exportXlsx(workbook);
  await output.save(outputPath);

  const preview = await workbook.render({
    sheetName: "LP Register",
    range: `A1:${finalColumn}${rowCount}`,
    scale: 0.85,
    format: "png",
  });
  await fs.mkdir(previewDir, { recursive: true });
  await fs.writeFile(path.join(previewDir, job.preview), new Uint8Array(await preview.arrayBuffer()));
  console.log(job.xlsx);
}

for (const job of jobs) {
  await buildWorkbook(job);
}
