/**
 * 一个还没有任何测试的模块 —— fixture 的被测模块。
 *
 * 刻意**没有导出、没有测试文件**：这正是评审里复现出来的那个形状 ——
 * 退出码 0、结论「达到交付条件」、一行「✓ 测试全绿 — 没有失败用例」。
 */

function internalHelper(value: number): number {
  return value * 2;
}

void internalHelper;
