import assert from "node:assert";
import test from "node:test";

import { memoriesTable, readMemory, upsertMemory } from "./memories.ts";

test("memoriesTable declares the memories table", () => {
  assert.strictEqual(memoriesTable.name, "memories");
});

test("upsertMemory writes the derived copy", async () => {
  const written: unknown[] = [];
  await upsertMemory(
    {
      upsert: async (record: unknown) => {
        written.push(record);
      },
    },
    { id: "m1", tenantId: "acme-prod", body: "hello" },
  );
  assert.strictEqual(written.length, 1);
});

test("readMemory returns the row", async () => {
  const row = await readMemory(
    {
      get: async () => ({
        id: "m1",
        tenantId: "acme-prod",
        body: "hello",
      }),
    },
    "m1",
  );
  assert.strictEqual(row?.body, "hello");
});
