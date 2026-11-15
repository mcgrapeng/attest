import assert from "node:assert";
import test from "node:test";

import { closeClient, getClient } from "./a.ts";

test("getClient returns a live connection", () => {
  assert.strictEqual(getClient("pg").closed, false);
});

test("closeClient closes the connection", () => {
  closeClient("pg");
  assert.strictEqual(getClient("pg").closed, true);
});
