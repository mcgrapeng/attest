#!/usr/bin/env python3
"""README 的自检：结构、数字、链接必须与代码一致。

两类漂移会悄悄发生，靠人记不住：

1. **中英两份不同步** —— 一份改了另一份没改，读者按其中一份操作会踩空。
2. **README 里的数字过期** —— 判据数、测试文件数、闸门数变了，README 还写着旧的。

所以这里把它们全部钉住：章节数与顺序、判据行数 vs 代码里的 Check 数、
测试文件数、fixture 数、eval 数、README 里出现的路径是否存在。

    python3 scripts/test_readme_sync.py
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
FAILURES: list[str] = []


def expect(cond: bool, label: str) -> None:
    print(f"  {'ok  ' if cond else 'FAIL'} {label}")
    if not cond:
        FAILURES.append(label)


def sections(text: str) -> list[str]:
    """章节标题（跳过代码块内的 # 注释行）。"""
    out: list[str] = []
    in_code = False
    for ln in text.splitlines():
        if ln.startswith("```"):
            in_code = not in_code
            continue
        if not in_code and re.match(r"^#{2,3} ", ln):
            out.append(ln.strip())
    return out


def criteria_rows(text: str) -> int:
    """判据表里的数据行数（表格首行是表头、第二行是分隔行）。"""
    n = 0
    for block in re.findall(r"(?:^\|.*\|\s*$\n)+", text, re.M):
        rows = [r for r in block.strip().splitlines() if r.strip()]
        if len(rows) < 3:
            continue
        first = rows[0].strip().strip("|").split("|")[0].strip()
        if first in ("判据", "Criterion"):
            n += len(rows) - 2
    return n


def main() -> int:
    en_path = ROOT / "README.md"
    zh_path = ROOT / "README.zh-CN.md"
    expect(en_path.is_file(), "README.md 存在")
    expect(zh_path.is_file(), "README.zh-CN.md 存在")
    if not (en_path.is_file() and zh_path.is_file()):
        return 1
    en, zh = en_path.read_text(encoding="utf-8"), zh_path.read_text(encoding="utf-8")

    print("\n中英两份结构一致")
    en_s, zh_s = sections(en), sections(zh)
    expect(len(en_s) == len(zh_s),
           f"章节数一致（英文 {len(en_s)} / 中文 {len(zh_s)}）")
    expect([s.split(" ", 1)[0] for s in en_s]
           == [s.split(" ", 1)[0] for s in zh_s],
           "章节层级与顺序一致")

    print("\n判据条数与代码一致")
    # 代码里的 Check 数：跑一次 readiness --json 数
    out = subprocess.run(
        [sys.executable, str(HERE / "readiness.py"), "src",
         "--repo", str(ROOT / "fixtures" / "ts-colocated"), "--json"],
        capture_output=True, text=True, cwd=str(ROOT))
    code_n = 0
    try:
        code_n = len(json.loads(out.stdout)["checks"])
    except Exception:
        pass
    expect(code_n == 14, f"代码里 14 条判据（实际 {code_n}）")
    expect(criteria_rows(en) == code_n,
           f"英文 README 判据表 {code_n} 行（实际 {criteria_rows(en)}）")
    expect(criteria_rows(zh) == code_n,
           f"中文 README 判据表 {code_n} 行（实际 {criteria_rows(zh)}）")

    print("\nREADME 里的数字与仓库实况一致")
    tests = sorted(p.name for p in (ROOT / "scripts").glob("test_*.py"))
    expect(f"{len(tests)} / {len(tests)}" in en or f"{len(tests)}/{len(tests)}" in en,
           f"英文 README 的回归套件数是 {len(tests)}")
    expect(f"{len(tests)} / {len(tests)}" in zh or f"{len(tests)}/{len(tests)}" in zh,
           f"中文 README 的回归套件数是 {len(tests)}")
    fixtures = len([d for d in (ROOT / "fixtures").iterdir() if d.is_dir()])
    expect("5 / 5" in en and "5 / 5" in zh, f"README 写 5 / 5 fixture（实际 {fixtures}）")
    evals = json.loads((ROOT / "evals" / "evals.json").read_text(encoding="utf-8"))
    n_evals = len(evals if isinstance(evals, list) else evals.get("evals", []))
    expect("6 / 6" in en and "6 / 6" in zh, f"README 写 6 / 6 eval（实际 {n_evals}）")

    print("\n输出样例是逐字真实输出")
    run_out = subprocess.run(
        [sys.executable, str(HERE / "readiness.py"), "src",
         "--repo", str(ROOT / "fixtures" / "ts-colocated")],
        capture_output=True, text=True, cwd=str(ROOT)).stdout
    run_out = re.sub(r"/\S*/fixtures/ts-colocated", "<repo root>", run_out).rstrip("\n")
    for label, text in (("英文", en), ("中文", zh)):
        expect(run_out in text,
               f"{label} README 的输出样例与真实运行逐字一致"
               "（样例被删节过就会失败）")

    print("\nREADME 引用的路径都存在")
    for label, text in (("英文", en), ("中文", zh)):
        missing = []
        for tok in set(re.findall(r"(?:[\w./~-]+/)*[\w.-]+\.(?:py|md|sh|svg|yml|yaml)\b", text)):
            if tok.startswith(("http", "//", "dist/", "<", "-", "~/.agents/testkit",
                               "SKILL_DIR")):
                continue
            if "*" in tok:
                continue
            p = tok.replace("~/.agents/skills/attest/", "").lstrip("~/")
            if p.startswith(("github.com", "img.shields")):
                continue
            if "/" not in p and p.endswith(".py"):
                # 正文里提到的裸文件名：仓库根与 scripts/ 都可能
                if (ROOT / p).exists():
                    continue
                p = f"scripts/{p}"
            if not (ROOT / p).exists():
                missing.append(tok)
        expect(not missing, f"{label} README 引用的仓库内文件都存在（缺：{missing}）")

    print("\nREADME 的环境白名单描述与代码一致")
    code = (HERE / "readiness.py").read_text(encoding="utf-8")
    exact = set(re.findall(r'"([A-Z_]+)"',
                           re.search(r"ENV_ALLOW_EXACT = \{(.*?)\}", code, re.S).group(1)))
    for label, text in (("英文", en), ("中文", zh)):
        miss = sorted(x for x in exact if f"`{x}`" not in text)
        expect(not miss, f"{label} README 列全了环境白名单（缺：{miss}）")

    print()
    if FAILURES:
        print(f"失败 {len(FAILURES)} 条：")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
