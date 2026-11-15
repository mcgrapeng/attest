import assert from "node:assert";
import test from "node:test";

import { closeClient, getClient } from "./a.ts";

test("getClient returns the same instance", () => {
  assert.strictEqual(getClient(), getClient());
});

test("closeClient closes the shared client", () => {
  closeClient();
  assert.strictEqual(getClient().closed, true);
});
