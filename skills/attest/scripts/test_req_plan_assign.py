#!/usr/bin/env python3
"""assign 回归：判定点归属的机器接口（拉清单 / 校验并写回 / 重跑不丢）。"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
SCRIPT = HERE / "req_plan.py"
FAILURES: list[str] = []
TMPDIRS: list[Path] = []


def expect(cond: bool, label: str) -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}")
    if not cond:
        FAILURES.append(label)


def mkdtemp() -> Path:
    p = Path(tempfile.mkdtemp())
    TMPDIRS.append(p)
    return p


def write(p: Path, text: str) -> Path:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True, encoding="utf-8",
                          env={**os.environ, "PYTHONUTF8": "1"})


SRC = """export function check(n: number): number {
  if (n > 100) throw new Error('too big');
  if (n < 0) throw new Error('too small');
  return n;
}

export function branchy(n: number): number {
  if (n === 1) return 1;
  return 2;
}
"""

TEST_SRC = """import { it } from 'node:test';
import assert from 'node:assert/strict';
import { check } from './a.ts';

it('rejects overflow', () => {
  assert.throws(() => check(101));
});

it('uses default for empty', () => {
  assert.equal(check(0), 0);
});
"""

V1_PLAN = """# 测试计划：assign 夹具

## 不变式清单

| # | 不变式 | 钉在哪 |
| --- | --- | --- |
| I1 | 越界要拒收 | src/a.test.ts::rejects overflow |
| I2 | 空值当默认值 | src/a.test.ts::uses default for empty |

## 覆盖矩阵

| 流程 | 落点 | 车道 |
| --- | --- | --- |
| 写入 | src/a.test.ts::rejects overflow | unit |

## 核心逻辑细节

| 维度 | 细节 | 落点（文件::用例名） |
| --- | --- | --- |
| 错误路径 | 超限要抛错 | src/a.test.ts::rejects overflow |
"""

V2_PLAN = """# 测试计划：assign 夹具

## 不变式清单

| # | 不变式 | 钉在哪 |
| --- | --- | --- |
| I1 | 越界要拒收 | src/a.test.ts::rejects overflow |
| I2 | 空值当默认值 | src/a.test.ts::uses default for empty |

## 覆盖矩阵

| 流程 | 落点 | 车道 |
| --- | --- | --- |
| 写入 | src/a.test.ts::rejects overflow | unit |

## 核心逻辑细节

| 维度 | DP ID | 判定点 | 归属的不变式 | 落点（文件::用例名） | 豁免理由 |
| --- | --- | --- | --- | --- | --- |
| 错误路径 | src/a.ts#check#error-path | throw new Error('too big'); |  |  |  |
| 错误路径 | src/a.ts#other#error-path | throw new Error('too small'); |  |  |  |
| 分支 | src/a.ts#branchy#branch | if (n === 1) return 1; |  |  |  |
"""


def make_repo(plan_text: str) -> tuple[Path, Path]:
    repo = mkdtemp()
    write(repo / "package.json", json.dumps({"scripts": {"test": "vitest"}}))
    write(repo / "src" / "a.ts", SRC)
    write(repo / "src" / "a.test.ts", TEST_SRC)
    plan = write(repo / "docs" / "p-test-plan.md", plan_text)
    return repo, plan


def jsonl(text: str) -> list[dict]:
    return [json.loads(ln) for ln in text.splitlines() if ln.strip()]


def main() -> int:
    print("== assign --list ==")
    repo, plan = make_repo(V2_PLAN)
    r = run("assign", str(plan), "--repo", str(repo))
    expect(r.returncode == 0, "assign --list 退出码 0")
    rows = jsonl(r.stdout)
    meta = rows[0] if rows else {}
    expect("_invariants" in meta, "第一行是元数据（带不变式清单）")
    expect([i["id"] for i in meta.get("_invariants", [])] == ["I1", "I2"],
           "元数据里的不变式编号与计划一致")
    tasks = rows[1:]
    expect(len(tasks) == 3, f"待填清单有 3 条（实际 {len(tasks)}）")
    expect(all(t["high_risk"] for t in tasks[:2]), "高危判定点排在前面")
    expect(tasks[0]["dp_id"] == "src/a.ts#check#error-path",
           "按文件/锚点排序，第一条是 check#error-path")
    expect(tasks[0]["code"] == "throw new Error('too big');",
           "清单带上源码那一句")
    expect("src/a.test.ts" in tasks[0]["candidates"],
           "候选测试文件认得出来（源码里提到该符号的测试文件）")

    r = run("assign", str(plan), "--repo", str(repo), "--kinds", "branch")
    tasks = jsonl(r.stdout)[1:]
    expect(len(tasks) == 1 and tasks[0]["kind"] == "branch",
           "--kinds 只列该类型的判定点")

    r = run("assign", str(plan), "--repo", str(repo), "--limit", "1")
    tasks = jsonl(r.stdout)[1:]
    expect(len(tasks) == 1, "--limit 分批")

    print("== v1 计划：没有判定点表 ==")
    repo1, plan1 = make_repo(V1_PLAN)
    r = run("assign", str(plan1), "--repo", str(repo1))
    expect(r.returncode == 1, "v1 计划上 assign 退出码 1")
    expect("upgrade" in (r.stderr + r.stdout),
           "v1 计划上提示先跑 upgrade")

    print("== assign --apply ==")
    repo2, plan2 = make_repo(V2_PLAN)
    ans = write(repo2 / "answers.jsonl", "\n".join([
        json.dumps({"dp_id": "src/a.ts#check#error-path", "invariant": "I1",
                    "pin": "src/a.test.ts::rejects overflow"}),
        json.dumps({"dp_id": "src/a.ts#other#error-path", "invariant": "I2",
                    "pin": "src/a.test.ts::uses default for empty"}),
        json.dumps({"dp_id": "src/a.ts#branchy#branch",
                    "waiver": "纯分支选择，行为由上面两条不变式覆盖"}),
    ]) + "\n")
    r = run("assign", str(plan2), "--repo", str(repo2), "--apply", str(ans))
    expect(r.returncode == 0, "全部合法 ⇒ 退出码 0")
    text = plan2.read_text(encoding="utf-8")
    expect("| I1 | src/a.test.ts::rejects overflow |" in text,
           "归属的不变式与落点写回了")
    expect("纯分支选择" in text, "豁免理由写回了")
    expect("高危缺口还剩 0 条" in r.stdout, "高危缺口归零")

    r2 = run("check", str(plan2), "--repo", str(repo2))
    errs_before = len(r2.stdout.split("个错误 /")[0].splitlines()[-1].split())
    expect("个错误" in r2.stdout, "check 能跑")

    print("== apply 会挡住写错的答案 ==")
    repo3, plan3 = make_repo(V2_PLAN)
    before = plan3.read_text(encoding="utf-8")
    bad = write(repo3 / "bad.jsonl", "\n".join([
        # 归属的不变式不在清单里
        json.dumps({"dp_id": "src/a.ts#check#error-path", "invariant": "I9",
                    "pin": "src/a.test.ts::rejects overflow"}),
        # 落点用例不存在
        json.dumps({"dp_id": "src/a.ts#check#error-path", "invariant": "I1",
                    "pin": "src/a.test.ts::没有这个用例"}),
        # 豁免理由太笼统
        json.dumps({"dp_id": "src/a.ts#check#error-path",
                    "waiver": "不适用"}),
        # 计划里没有这个判定点
        json.dumps({"dp_id": "src/a.ts#ghost#error-path", "waiver": "不存在"}),
        # 既没归属也没豁免
        json.dumps({"dp_id": "src/a.ts#check#error-path"}),
    ]) + "\n")
    r = run("assign", str(plan3), "--repo", str(repo3), "--apply", str(bad))
    expect(r.returncode == 1, "有写错的答案 ⇒ 退出码 1")
    expect(plan3.read_text(encoding="utf-8") == before,
           "被挡下的答案一个字都没写进计划")
    expect("不在不变式清单里" in r.stderr, "报出「不在不变式清单里」")
    expect("里不存在" in r.stderr, "报出「用例不存在」")
    expect("太笼统" in r.stderr, "报出「豁免理由太笼统」")
    expect("计划里没有这个判定点" in r.stderr, "报出「计划里没有这个判定点」")

    print("== 重跑 upgrade 不丢已填的归属 ==")
    repo4, plan4 = make_repo(V2_PLAN)
    ans4 = write(repo4 / "a.jsonl", json.dumps(
        {"dp_id": "src/a.ts#check#error-path", "invariant": "I1",
         "pin": "src/a.test.ts::rejects overflow"}) + "\n")
    r = run("assign", str(plan4), "--repo", str(repo4), "--apply", str(ans4))
    expect(r.returncode == 0, "先填一条")
    before = plan4.read_text(encoding="utf-8")
    r = run("upgrade", str(plan4), "--repo", str(repo4), "--module", "src")
    expect(r.returncode == 0, "重跑 upgrade 成功")
    after = plan4.read_text(encoding="utf-8")
    expect("| I1 | src/a.test.ts::rejects overflow |" in after,
           "重跑 upgrade 后填好的归属还在")
    expect("| I1 | src/a.test.ts::rejects overflow |" in before,
           "（改之前那一份里也有）")

    print()
    for d in TMPDIRS:
        shutil.rmtree(d, ignore_errors=True)
    if FAILURES:
        print(f"{len(FAILURES)} 项失败:")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
