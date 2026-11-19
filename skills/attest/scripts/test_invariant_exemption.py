#!/usr/bin/env python3
"""不变式豁免机制的回归。

背景：`不变式有判定点` 这条判据查的是「每条不变式有没有判定点认领它」。
复合不变式（如「跨池同文本合法共存」）在判定点粒度下**天然**没有认领者 ——
同一条文本在不同池各自成立，不是一个判定点 ⇒ 判据只能报 warn ⇒ 永久噪音。

出口：计划里写明「这条不变式不需要判定点认领，理由是……」，判据认这个出口
（但**没有理由**的不算豁免，仍报）。

写法沿用仓库既有的豁免注释约定（`<!-- attest:waive … -->`）：

    <!-- attest:invariant-exempt I3 reason="跨池同文本合法共存：不是同一个不变式" -->
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

import readiness  # noqa: E402
import req_plan  # noqa: E402

FAILURES: list[str] = []


def expect(cond: bool, label: str) -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}")
    if not cond:
        FAILURES.append(label)


def mk_module(tmp: Path) -> readiness.Module:
    src = tmp / "src"
    src.mkdir(parents=True, exist_ok=True)
    (src / "a.ts").write_text("export function f() { return 1; }\n", encoding="utf-8")
    (src / "a.test.ts").write_text("test('x', () => {});\n", encoding="utf-8")
    return readiness.Module(root=tmp, path="src", base=src,
                            sources=[src / "a.ts"], tests=[src / "a.test.ts"])


def plan_text(rows: str, extra: str = "") -> str:
    return f"""# 测试计划

目标模块：`src`

## 不变式清单

| # | 不变式 | 钉在哪 |
| --- | --- | --- |
{rows}

## 核心逻辑细节

| 维度 | DP ID | 判定点 | 归属的不变式 | 落点（文件::用例名） | 豁免理由 |
| --- | --- | --- | --- | --- | --- |
| 错误路径 | src/a.ts#insertRow#drop | 缺 id 报错 | I1 | src/a.test.ts::rejects a row |  |
{extra}
"""


def main() -> int:
    tmp = Path(tempfile.mkdtemp())
    m = mk_module(tmp)

    print("\n标记能被解析（req_plan 侧）")
    p_parse = tmp / "plan-parse.md"
    p_parse.write_text(plan_text(
        "| I1 | 缺 id 必须报错 | src/a.test.ts::rejects a row |\n"
        "| I3 | 跨池同文本合法共存 | src/a.test.ts::rejects a row |\n",
        '<!-- attest:invariant-exempt I3 reason="跨池同文本合法共存：不是同一个不变式" -->',
    ), encoding="utf-8")
    plan = req_plan.parse_plan(p_parse)
    expect(plan.invariant_exemptions.get("I3") == "跨池同文本合法共存：不是同一个不变式",
           "attest:invariant-exempt 标记被解析成不变式豁免")
    expect("I1" not in plan.invariant_exemptions, "没标的不变式不在豁免表里")

    print("\n没有判定点认领的不变式：默认报 warn")
    p = tmp / "plan.md"
    p.write_text(plan_text(
        "| I1 | 缺 id 必须报错 | src/a.test.ts::rejects a row |\n"
        "| I3 | 跨池同文本合法共存 | src/a.test.ts::rejects a row |\n",
    ), encoding="utf-8")
    chk = readiness.check_invariant_coverage(m, str(p))
    expect(chk.status == "warn", "I3 没有豁免 ⇒ 报 warn（孤儿不变式）")
    expect("I3" in " ".join(chk.evidence or []), "证据里点名了 I3")

    print("\n写了豁免理由：不再算孤儿")
    p2 = tmp / "plan2.md"
    p2.write_text(plan_text(
        "| I1 | 缺 id 必须报错 | src/a.test.ts::rejects a row |\n"
        "| I3 | 跨池同文本合法共存 | src/a.test.ts::rejects a row |\n",
        '<!-- attest:invariant-exempt I3 reason="跨池同文本合法共存：不是同一个不变式" -->',
    ), encoding="utf-8")
    chk2 = readiness.check_invariant_coverage(m, str(p2))
    expect(chk2.status != "warn", "有豁免理由 ⇒ 不再报孤儿 warn")
    expect("豁免" in (chk2.detail or "") or "豁免" in " ".join(chk2.evidence or []),
           "豁免在报告里看得见（不静默）")

    print("\n豁免没给理由：不算豁免")
    p3 = tmp / "plan3.md"
    p3.write_text(plan_text(
        "| I1 | 缺 id 必须报错 | src/a.test.ts::rejects a row |\n"
        "| I3 | 跨池同文本合法共存 | src/a.test.ts::rejects a row |\n",
        '<!-- attest:invariant-exempt I3 reason="" -->',
    ), encoding="utf-8")
    chk3 = readiness.check_invariant_coverage(m, str(p3))
    expect(chk3.status == "warn", "豁免没理由 ⇒ 仍算孤儿")

    print("\n豁免理由太笼统（只写「不适用」）")
    p4 = tmp / "plan4.md"
    p4.write_text(plan_text(
        "| I1 | 缺 id 必须报错 | src/a.test.ts::rejects a row |\n"
        "| I3 | 跨池同文本合法共存 | src/a.test.ts::rejects a row |\n",
        '<!-- attest:invariant-exempt I3 reason="不适用" -->',
    ), encoding="utf-8")
    chk4 = readiness.check_invariant_coverage(m, str(p4))
    expect(chk4.status == "warn", "理由笼统 ⇒ 仍算孤儿")

    print("\nreq_plan check：豁免指向不存在的不变式 ⇒ 报红")
    p_stale = tmp / "plan-stale.md"
    p_stale.write_text(plan_text(
        "| I1 | 缺 id 必须报错 | src/a.test.ts::rejects a row |\n",
        '<!-- attest:invariant-exempt I9 reason="写错了编号" -->',
    ), encoding="utf-8")
    out = subprocess.run(
        [sys.executable, str(HERE / "req_plan.py"), "check", str(p_stale)],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "PYTHONUTF8": "1"},
    )
    expect("不变式豁免 I9 不在不变式清单里" in out.stdout,
           "豁免指向不存在的不变式 ⇒ 报红")

    print("\nreq_plan check：豁免没给理由 ⇒ 报红")
    p_noreason = tmp / "plan-noreason.md"
    p_noreason.write_text(plan_text(
        "| I1 | 缺 id 必须报错 | src/a.test.ts::rejects a row |\n",
        '<!-- attest:invariant-exempt I1 reason="" -->',
    ), encoding="utf-8")
    out2 = subprocess.run(
        [sys.executable, str(HERE / "req_plan.py"), "check", str(p_noreason)],
        capture_output=True, text=True, encoding="utf-8",
        env={**os.environ, "PYTHONUTF8": "1"},
    )
    expect("不变式豁免 I1 没给理由" in out2.stdout, "豁免没理由 ⇒ 报红")
    print("\n5. 全部通过" if not FAILURES else f"\n{len(FAILURES)} 项失败")
    for f in FAILURES:
        print("  -", f)
    return 1 if FAILURES else 0


if __name__ == "__main__":
    raise SystemExit(main())
