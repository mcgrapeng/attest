import assert from "node:assert";
import test from "node:test";

import { add, subtract } from "./calc.ts";

test("add returns the sum", () => {
  assert.strictEqual(add(2, 3), 5);
});

test("subtract returns the difference", () => {
  assert.strictEqual(subtract(5, 3), 2);
});
