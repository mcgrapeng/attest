#!/usr/bin/env python3
"""round-14 回归测试：细节维度按需求类型动态推导。"""
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


MATH_SRC = """export function add(a: number, b: number): number {
  if (a > 100) throw new Error('too big');
  return a + b;
}
"""
AUTH_SRC = """export function can(user: { role: string }, need: string): boolean {
  return user.role === need;
}
"""


def main() -> int:
    # C locale 下 stdout 默认 ascii，打中文会 UnicodeEncodeError
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    tmp = mkdtemp()
    write(tmp / "package.json", json.dumps({"scripts": {"test": "vitest"}}))
    write(tmp / "src" / "math" / "add.ts", MATH_SRC)
    write(tmp / "src" / "auth" / "perm.ts", AUTH_SRC)
    write(tmp / "src" / "math" / "add.test.ts", "test('add', () => {});\n")

    sys.path.insert(0, str(HERE))
    from req_plan import derive_dimensions  # noqa: E402

    print("推导：按代码特征挑维度")
    math_dims = derive_dimensions(tmp, ["src/math"])
    expect(set(math_dims) == {"边界", "错误路径"},
           f"纯计算模块只问边界/错误路径（实际 {sorted(math_dims)}）")
    auth_dims = derive_dimensions(tmp, ["src/auth"])
    expect(set(auth_dims) == {"权限"},
           f"纯权限模块只问权限（实际 {sorted(auth_dims)}）")

    print("\n生成：只生成推导出的维度行")
    r = run("new", "m", "--repo", str(tmp), "--module", "src/math")
    expect(r.returncode == 0, "new 成功")
    plan = tmp / "docs" / "m-test-plan.md"
    text = plan.read_text(encoding="utf-8")
    expect("| 边界 |" in text, "边界行在")
    expect("| 错误路径 |" in text, "错误路径行在")
    expect("| 权限 |" not in text, "权限行不在（不逼人填无关的）")
    expect("| 跨租户隔离 |" not in text, "跨租户隔离行不在")
    expect("触发证据" in text, "每行带触发证据")

    print("\n校验：只要求推导出的维度（不误报无关维度）")
    text = text.replace(
        '| 边界 | <待填：测试文件，或"不适用：…">（触发证据：src/math/add.ts:2 — 取值上下界） |',
        "| 边界 | src/math/add.test.ts |")
    text = text.replace(
        '| 错误路径 | <待填：测试文件，或"不适用：…">（触发证据：src/math/add.ts:2 — 错误分支） |',
        "| 错误路径 | 不适用：本需求只测正常加法 |")
    text = text.replace(
        "| I1 | <待填：这需求必须永远成立的性质> | <待填：src/x.test.ts::用例名> |",
        "| I1 | 和法正确 | src/math/add.test.ts::add |")
    text = text.replace(
        "| <待填：流程名> | <待填：src/x.test.ts> | <待填：单测> |",
        "| 加法 | src/math/add.test.ts::add | test |")
    plan.write_text(text, encoding="utf-8")
    r2 = run("check", str(plan), "--repo", str(tmp))
    expect("没考虑到的维度" not in r2.stdout,
           "推导出的维度都答了 ⇒ 不提醒漏维度")
    expect("权限" not in r2.stdout, "不因无关维度提醒")

    print("\n校验：维度行留着但没落点 ⇒ 报红")
    text2 = text.replace("| 错误路径 | 不适用：本需求只测正常加法 |",
                          "| 错误路径 |  |")
    plan2 = tmp / "docs" / "m2-test-plan.md"
    plan2.write_text(text2, encoding="utf-8")
    r3 = run("check", str(plan2), "--repo", str(tmp))
    expect("没有测试落点" in r3.stdout, "维度行没落点 ⇒ 报红")

    print("\n校验：维度行整行删掉 ⇒ 提醒漏维度")
    text2b = "\n".join(
        ln for ln in text.splitlines()
        if not ln.startswith("| 错误路径 |"))
    plan2b = tmp / "docs" / "m2b-test-plan.md"
    plan2b.write_text(text2b, encoding="utf-8")
    r3b = run("check", str(plan2b), "--repo", str(tmp))
    expect("没考虑到的维度" in r3b.stdout, "维度行被删掉 ⇒ 提醒漏维度")

    print("\n额外维度：计划里声明的也要答")
    text3 = text.replace("本需求额外涉及：<待填：没有就删掉这行；填了就多几个维度要答>",
                          "本需求额外涉及：资源上限")
    plan3 = tmp / "docs" / "m3-test-plan.md"
    plan3.write_text(text3, encoding="utf-8")
    r4 = run("check", str(plan3), "--repo", str(tmp))
    expect("资源上限" in r4.stdout and "没考虑到的维度" in r4.stdout,
           "声明的额外维度没答 ⇒ 提醒")

    print("\n数据对账：模块碰 ≥2 个存储 ⇒ 多一个维度")
    tmp2 = mkdtemp()
    write(tmp2 / "package.json", json.dumps({"scripts": {"test": "vitest"}}))
    write(tmp2 / "src" / "m.ts",
          "export const t = pgTable('t', {});\nexport const C = MilvusClient;\n")
    d2 = derive_dimensions(tmp2, ["src"])
    expect("数据对账" not in d2, "存储数判定在 new/check 里做（这里只查代码特征）")

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
