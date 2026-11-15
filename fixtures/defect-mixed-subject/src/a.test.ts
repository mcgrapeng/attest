import assert from "node:assert";
import test from "node:test";

import { exemptFirstPerson, isThirdParty } from "./a.ts";

test("isThirdParty recognises a third-party sentence", () => {
  assert.strictEqual(isThirdParty("张三来开会"), true);
});

test("exemptFirstPerson keeps a third-party sentence", () => {
  assert.strictEqual(exemptFirstPerson("张三来开会"), "张三来开会");
});
