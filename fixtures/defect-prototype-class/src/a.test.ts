import assert from "node:assert";
import test from "node:test";

import { lookup } from "./a.ts";

test("lookup returns the role of an admin", () => {
  assert.strictEqual(lookup("admin"), "admin");
});

test("lookup returns undefined for an unknown role", () => {
  assert.strictEqual(lookup("ghost"), undefined);
});
