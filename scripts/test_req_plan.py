#!/usr/bin/env python3
"""req_plan.py 自测。

跑法: python3 ~/.agents/skills/req-test-plan/scripts/test_req_plan.py
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from req_plan import (  # noqa: E402
    Repo, check_plan, existing_test_files, lane_scripts, module_helpers,
    parse_plan, scan_repo,
)

FAILURES: list[str] = []


def expect(cond: bool, label: str) -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}")
    if not cond:
        FAILURES.append(label)


def main() -> int:
    # C locale 下 stdout 默认 ascii，打中文会 UnicodeEncodeError
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    tmp = Path(tempfile.mkdtemp())

    print("仓库扫描")
    pkg = tmp / "package.json"
    pkg.write_text(json.dumps({
        "scripts": {
            "test": "vitest run",
            "test:e2e": "playwright test",
            "test:integration:memory": "MEMORY_ITEST=1 node test",
            "build": "tsc",
            "dev": "vite",
        }
    }), encoding="utf-8")
    (tmp / "src").mkdir()
    (tmp / "src" / "a.test.ts").write_text("test('x', () => {})\n", encoding="utf-8")
    (tmp / "src" / "a.ts").write_text("export function doThing(): void {}\n", encoding="utf-8")
    repo = scan_repo(tmp)
    lanes = lane_scripts(repo)
    expect("test" in lanes, "scripts 里的 test 是车道")
    expect("test:e2e" in lanes, "test:e2e 是车道")
    expect("test:integration:memory" in lanes, "itest 车道认得出来")
    expect("build" not in lanes, "build 不是测试车道")
    expect("dev" not in lanes, "dev 不是测试车道")

    tests = existing_test_files(tmp)
    expect("src/a.test.ts" in tests, "现有测试文件被认出来")

    print("\n复用侦察（防重复造轮子）")
    (tmp / "src" / "helper.ts").write_text(
        "export function computeFingerprint(x: string): string { return x }\n"
        "export const MAX_LEN = 10;\n"
    , encoding="utf-8")
    (tmp / "src" / "b.test.ts").write_text(
        "export function helperOnlyForTest(): void {}\n"
    , encoding="utf-8")
    helpers = module_helpers(tmp, ["src"])
    names = " ".join(helpers.get("src", []))
    expect("computeFingerprint" in names, "已有 helper 被列出来")
    expect("MAX_LEN" in names, "已有常量被列出来")
    expect("helperOnlyForTest" not in names,
           "测试文件里的符号不算已有 helper（测试文件排除）")
    expect("doThing" in names, "生产文件里的导出算已有 helper")

    print("\n计划解析")
    plan_md = tmp / "docs" / "x-test-plan.md"
    plan_md.parent.mkdir()
    plan_md.write_text("""# 测试计划：x

## 已有能力

| 能力 | 位置 |
| --- | --- |
| 复用指纹计算 | src/helper.ts |

## 不变式清单

| # | 不变式 | 钉在哪 |
| --- | --- | --- |
| I1 | 指纹稳定 | src/helper.ts:2 |
| I2 | 长度不超限 | src/a.ts:1 |

## 测试车道

| 车道 | 命令 |
| --- | --- |
| test | vitest run |

## 覆盖矩阵

| 流程 | 测试落点 | 车道 |
| --- | --- | --- |
| 写入→召回 | src/a.test.ts | test |
""", encoding="utf-8")
    plan = parse_plan(plan_md)
    expect(len(plan.invariants) == 2, "两条不变式被解析出来")
    expect(plan.invariants[0][0] == "I1", "不变式编号解析正确")
    expect(len(plan.lanes) == 1, "一条车道被解析出来")
    expect(len(plan.matrix_rows) == 1, "矩阵一行被解析出来")

    print("\n校验：钉不到就报错")
    fs = check_plan(plan, repo)
    errs = [f for f in fs if f.level == "error"]
    expect(not errs, "全部钉都指向真实文件 ⇒ 无错误")

    print("\n校验：不存在的文件要报红")
    bad_md = tmp / "docs" / "bad-test-plan.md"
    bad_md.write_text("""# 测试计划：bad

## 不变式清单

| # | 不变式 | 钉在哪 |
| --- | --- | --- |
| I1 | 编出来的 | src/nonexistent.ts:9 |
| I2 | 没钉 | |
""", encoding="utf-8")
    bad = parse_plan(bad_md)
    bad_fs = check_plan(bad, repo)
    bad_errs = [f for f in bad_fs if f.level == "error"]
    expect(any("不存在" in f.detail for f in bad_errs),
           "钉指向不存在的文件 ⇒ 报红")
    expect(any("没有钉" in f.detail for f in bad_errs),
           "不变式没有钉 ⇒ 报红")

    print("\n校验：编出来的车道要提醒")
    fake_lane = tmp / "docs" / "fake-test-plan.md"
    fake_lane.write_text("""# 测试计划：fake

## 不变式清单

| # | 不变式 | 钉在哪 |
| --- | --- | --- |
| I1 | 有钉 | src/a.ts:1 |

## 测试车道

- `test:made-up-lane` — `不存在的命令`
""", encoding="utf-8")
    fl = check_plan(parse_plan(fake_lane), repo)
    expect(any(f.level == "warn" and "made-up" in f.item for f in fl),
           "编出来的车道（bullet 形态）⇒ 提醒")

    print("\n计划解析：bullet 形态的车道")
    bullet = tmp / "docs" / "bullet-test-plan.md"
    bullet.write_text("""# 测试计划：bullet

## 测试车道

- `test` — `vitest run`
- `test:e2e` — `playwright test`
""", encoding="utf-8")
    bp = parse_plan(bullet)
    expect(bp.lanes == ["test", "test:e2e"], "bullet 形态的车道被解析出来")

    shutil.rmtree(tmp, ignore_errors=True)
    print()
    if FAILURES:
        print(f"{len(FAILURES)} 项失败:")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
