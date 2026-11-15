/** 与被测模块无关的测试 —— 用来演示「钉对目标模块零覆盖」。 */

import assert from "node:assert";
import test from "node:test";

function shout(text: string): string {
  return `${text}!`;
}

test("unrelated helper keeps its input", () => {
  assert.strictEqual(shout("hi"), "hi!");
});
