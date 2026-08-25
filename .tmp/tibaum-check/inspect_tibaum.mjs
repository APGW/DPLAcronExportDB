import { FileBlob, SpreadsheetFile } from "@oai/artifact-tool";

const sourcePath = "C:/Users/alexandru.paraschi/Downloads/2025-02-06 DPL_PWK HAM-Am Tibaum (BF) (SH).xlsx";
const input = await FileBlob.load(sourcePath);
const workbook = await SpreadsheetFile.importXlsx(input);
const overview = await workbook.inspect({
  kind: "workbook,sheet",
  include: "id,name",
  maxChars: 10000,
});
const metadata = workbook.worksheets.getItem("Info und Historie AnlagenDPL");
const analog = workbook.worksheets.getItem("PWK_Analogwerte");
const result = {
  overview: overview.ndjson,
  metadata: metadata.getRange("A1:F10").values,
  analogHeader: analog.getRange("A1:BF8").values,
};
console.log(JSON.stringify(result, null, 2));
