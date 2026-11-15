import assert from "node:assert";
import test from "node:test";

import { insertRow } from "./a.ts";

test("insertRow keeps a well-formed row", () => {
  const out = insertRow([{ id: "1", text: "hello" }], { id: "2", text: "world" });
  assert.strictEqual(out.length, 2);
});

test("insertRow drops a row without an id", () => {
  const out = insertRow([{ id: "", text: "hello" }], { id: "2", text: "world" });
  assert.strictEqual(out.length, 1);
});
