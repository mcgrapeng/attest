#!/usr/bin/env python3
"""检查：生产代码不得**整份继承** os.environ。

    python3 scripts/check_env_inheritance.py

为什么需要它：SkillSpector 的 E2（Env Variable Harvesting）规则太粗 ——
它把每一处 {**os.environ} 都算成 HIGH，而我们的 allowlist 是**规则级**的
（不依赖位置，改代码不会误红）。那就需要一条**精确**的检查来真正把关：

- 子进程环境必须走白名单（`proc_env.child_env`，`readiness` / `selfcheck` /
  `decision_points` / `mutation_check` 共用同一份）—— 白名单里的键才透传；
- 整份继承（`**os.environ` / `dict(os.environ)` / `os.environ.copy()`）
  在**生产代码**里一律不允许。

测试文件不在检查范围内：测试脚手架要把环境传给被测的子进程，那是刻意的
（真正的过滤发生在被测工具的 child_env 里）。

用 tokenize 判定"这是代码而不是注释/字符串"—— 否则注释里写一句
"之前是 {**os.environ}" 就会被当成违规。

退出码：0 = 干净；1 = 有整份继承。
"""
from __future__ import annotations

import io
import re
import sys
import tokenize
from pathlib import Path

HERE = Path(__file__).resolve().parent
PATTERNS = (
    # tokenize 会把表达式拆成多个 token 再用空格拼回 ⇒ 模式要容忍空白
    (re.compile(r"\*\*\s*os\s*\.\s*environ"), "**os.environ"),
    (re.compile(r"dict\s*\(\s*os\s*\.\s*environ"), "dict(os.environ)"),
    (re.compile(r"os\s*\.\s*environ\s*\.\s*copy\s*\(\s*\)"), "os.environ.copy()"),
)


def code_lines(src: str) -> list[tuple[int, str]]:
    """(行号, 该行的代码) —— 去掉注释与字符串字面量。

    同一个表达式会被拆成多个 token（`**os.environ` ⇒ `**` + `os` + `.` + `environ`），
    所以要**按行拼回**再匹配，否则永远匹配不到。
    """
    per_line: dict[int, list[str]] = {}
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type in (tokenize.COMMENT, tokenize.STRING):
                continue
            per_line.setdefault(tok.start[0], []).append(tok.string)
    except (tokenize.TokenError, IndentationError):
        return [(1, src)]
    return [(ln, " ".join(parts)) for ln, parts in sorted(per_line.items())]


def scan(path: Path) -> list[str]:
    src = path.read_text(encoding="utf-8", errors="replace")
    bad: list[str] = []
    for line_no, text in code_lines(src):
        for pat, label in PATTERNS:
            if pat.search(text):
                bad.append(f"{path.name}:{line_no} 整份继承 os.environ（{label}）")
                break
    return bad


def main() -> int:
    targets = sorted(p for p in HERE.glob("*.py")
                     if not p.name.startswith("test_"))
    bad: list[str] = []
    for p in targets:
        bad += scan(p)
    if bad:
        print("生产代码里出现整份继承 os.environ：")
        for b in bad:
            print(f"  {b}")
        print("\n子进程环境必须走白名单（child_env / _child_env）—— "
              "白名单里的键才透传。")
        return 1
    print(f"OK：{len(targets)} 个生产脚本都没有整份继承 os.environ")
    return 0


if __name__ == "__main__":
    sys.exit(main())
