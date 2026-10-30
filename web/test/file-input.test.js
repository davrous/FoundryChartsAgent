import assert from "node:assert/strict";
import test from "node:test";
import { encodeFile, MAX_FILE_BYTES } from "../src/file-input.js";
import { drillRequest } from "../src/chart-data.js";

test("CSV/XLSX upload preserves bytes in standard Responses file_data", async () => {
  for (const name of ["metrics.csv", "metrics.XLSX"]) {
    const bytes = new Uint8Array([0, 128, 255, 65, 66]);
    const file = { name, size: bytes.length, arrayBuffer: async () => bytes.buffer };
    const result = await encodeFile(file);
    assert.equal(result.filename, name);
    assert.deepEqual(Buffer.from(result.file_data.split(",")[1], "base64"), Buffer.from(bytes));
  }
});

test("client upload budgets reject empty, oversized and unsupported files", async () => {
  for (const file of [
    { name: "test.csv", size: 0 },
    { name: "test.csv", size: MAX_FILE_BYTES + 1 },
    { name: "test.xls", size: 1 },
    { name: "../test.csv", size: 1 },
  ]) await assert.rejects(encodeFile(file));
});

test("uploaded columns never receive fictional sales drill-downs", () => {
  assert.throws(() => drillRequest({
    request: { source: "file", query: { group_by: "region" } },
    rows: [{ x: "Europe", y: 1 }],
  }, "Europe"), /follow-up question/);
});
