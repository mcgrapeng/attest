#!/usr/bin/env python3
"""测试强度（变异测试的轻量版）：把源码改坏，看**测试会不会红**。

为什么要有它：覆盖率只说明代码被执行过，不说明断言会失败。
一个"杀不死缺陷"的测试套件，覆盖率再高也不说明问题 ——
本模块回答的是：**这套测试能不能发现缺陷？**

做法：把目标仓库**复制**到临时目录（绝不改用户仓库），往副本里注入一个
**已知缺陷**（改比较运算符），再跑该模块自己的测试：

    测试红了 ⇒ 变异被杀死 ⇒ 这套测试能发现这类缺陷（证据）
    测试还绿 ⇒ **杀不死**   ⇒ 测试套件有洞（报出来）

只改这四种运算符：

    == → !=    != → ==    >= → >    <= → <

改完仍是同类语义、不会造成死循环，也不改常量/字符串/控制流 ——
那些变异容易变成"编译错误"或"改了无关的语义"，测出来的结论不可靠。

判定口径（故意不设阈值）：

    杀死了 0 个   ⇒ ✗ 这套测试发现不了这类缺陷
    杀死了部分   ⇒ · 有杀不死的点（列出位置）
    全部杀死     ⇒ ✓

"杀不杀得死多少算够"是项目的门槛，不是本工具能替项目定的 ——
所以只报事实与位置，不发明分数线。

单文件调试用：

    python3 scripts/mutation_check.py src/foo.ts
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

#: 变异算子：原运算符 → (改后, 叫什么)
_OP_MAP: dict[str, tuple[str, str]] = {
    "==": ("!=", "等号取反"),
    "!=": ("==", "不等号取反"),
    "===": ("!==", "严格等号取反"),
    "!==": ("===", "严格不等号取反"),
    ">=": (">", "下界收紧"),
    "<=": ("<", "上界收紧"),
}

#: 找运算符出现的位置。
#:   前一位不是 = ! < >   ⇒ 不碰 === 与 !==
#:   后一位不是 =         ⇒ 不碰 >= 的 = 与 >> 的第二个 >
#: 三字符的 === / !== 先试 ⇒ JS 的严格相等也进得来（很常见的变异点）。
_OP_RE = re.compile(r"(?<![=!<>])(===|!==|==|!=|>=|<=)(?![=])")

#: 复制副本时跳过的目录
SKIP_DIRS = (
    "node_modules", ".git", "dist", "build", "coverage", ".next", "out",
    "target", ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", ".turbo", ".cache", ".idea", ".vscode", "docs",
)

#: 单个文件最多找多少个变异点
DEFAULT_MAX_PER_FILE = 3


@dataclass
class Mutation:
    path: Path
    line: int
    col: int
    offset: int
    length: int
    original: str
    mutated: str
    kind: str

    def where(self) -> str:
        return f"{self.path.name}:{self.line}"


# ---------------------------------------------------------------- 找变异点


def _strip_py_code(src: str) -> str:
    """把 Python 的字符串与注释换成空格（**保持偏移量不变**）。

    tokenize 给的是 (行, 列)，不是绝对偏移 ⇒ 得自己换算，
    否则第 2 行往后的字符串会盖错位置（第一版就踩了这个）。
    """
    import io
    import tokenize
    try:
        toks = list(tokenize.generate_tokens(io.StringIO(src).readline))
    except (tokenize.TokenError, IndentationError, ValueError):
        return src
    starts = [0]
    for ln in src.splitlines(keepends=True):
        starts.append(starts[-1] + len(ln))
    out = list(src)
    for tok in toks:
        if tok.type not in (tokenize.STRING, tokenize.COMMENT):
            continue
        s = starts[tok.start[0] - 1] + tok.start[1]
        e = starts[tok.end[0] - 1] + tok.end[1]
        for i in range(max(0, s), min(e, len(out))):
            if out[i] != "\n":
                out[i] = " "
    return "".join(out)


_JS_STRIP_RE = re.compile(
    r"'(?:\\.|[^'\\\n])*'"
    r'|"(?:\\.|[^"\\\n])*"'
    r"|`(?:\\.|[^`\\])*`"
    r"|//[^\n]*"
    r"|/\*.*?\*/",
    re.S,
)


def _strip_js_code(src: str) -> str:
    """把 JS/TS 的字符串与注释换成空格（**保持偏移量不变**）。

    顺序要紧：字符串在前 ⇒ 字符串里的 ``//`` 不会被当成注释。
    """
    return _JS_STRIP_RE.sub(lambda m: " " * len(m.group(0)), src)


def find_mutations(path: Path, limit: int = DEFAULT_MAX_PER_FILE) -> list[Mutation]:
    """在文件里找可注入的变异点（只认代码位置，不碰字符串与注释）。"""
    try:
        src = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    if path.suffix == ".py":
        code = _strip_py_code(src)
    else:
        code = _strip_js_code(src)
    out: list[Mutation] = []
    for mm in _OP_RE.finditer(code):
        op = mm.group(1)
        mutated, kind = _OP_MAP[op]
        start = mm.start()
        line = code.count("\n", 0, start) + 1
        col = start - (code.rfind("\n", 0, start) + 1) + 1
        out.append(Mutation(path=path, line=line, col=col, offset=start,
                            length=len(op), original=op, mutated=mutated,
                            kind=kind))
        if len(out) >= limit:
            break
    return out


def _apply(text: str, mut: Mutation) -> str:
    """把一个变异应用到文本（按绝对偏移，偏移量与原文件一致）。"""
    return text[:mut.offset] + mut.mutated + text[mut.offset + mut.length:]


# ---------------------------------------------------------------- 副本


def _copy_repo(repo: Path, dst: Path) -> None:
    """把仓库复制到 dst（跳过依赖目录与构建产物）。"""
    ignore = shutil.ignore_patterns(*SKIP_DIRS, "*.pyc", "*.pyo", ".DS_Store")
    shutil.copytree(repo, dst, ignore=ignore, dirs_exist_ok=True,
                    symlinks=True)


def _link_deps(repo: Path, dst: Path, ws_rel: str) -> list[str]:
    """把依赖目录**软链**进副本（不复制体积）。

    不带依赖 ⇒ 副本里跑不动测试 ⇒ 这项就"没法测"。
    软链既能跑真测试，又不花时间与磁盘。
    """
    linked: list[str] = []
    roots = [(repo, dst)]
    if ws_rel not in (".", ""):
        roots.append((repo / ws_rel, dst / ws_rel))
    for src_root, dst_root in roots:
        for name in ("node_modules", ".venv", "venv"):
            s = src_root / name
            d = dst_root / name
            if not s.is_dir() or d.exists() or d.is_symlink():
                continue
            try:
                d.symlink_to(s, target_is_directory=True)
                linked.append(str(d.relative_to(dst)))
            except OSError:
                pass
    return linked


def _has_deps(repo: Path, ws_rel: str) -> bool:
    """仓库（或工作区）里有没有依赖目录 —— 没有就别提"挂不上依赖"。"""
    roots = [repo]
    if ws_rel not in (".", ""):
        roots.append(repo / ws_rel)
    return any((r / name).is_dir()
               for r in roots
               for name in ("node_modules", ".venv", "venv"))


# ---------------------------------------------------------------- 跑测试


def _child_env() -> dict[str, str]:
    """子进程环境：白名单透传，不是整个 os.environ（口径与 readiness 一致）。"""
    allow_exact = {
        "PATH", "HOME", "LANG", "LANGUAGE", "TZ", "TMPDIR", "TEMP", "TMP",
        "SHELL", "USER", "LOGNAME", "TERM", "SYSTEMROOT", "COMSPEC", "CI",
        "POSTGRES_URL", "DATABASE_URL", "MEMORY_ITEST",
        "MEMORY_RECONCILE_OUT_DIR", "REQ_PLAN_RECONCILE_OUT_DIR",
    }
    allow_prefix = ("LC_", "NODE_", "PYTHON", "NPM_", "PNPM_", "YARN_",
                    "PIP_", "UV_")
    env = {k: v for k, v in os.environ.items()
           if k in allow_exact or k.startswith(allow_prefix)}
    env["PYTHONUTF8"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    # 自检不该去连用户的库
    env["POSTGRES_URL"] = ""
    env["DATABASE_URL"] = ""
    return env


def _run_tests(runner: list[str], cwd: Path, test_rels: list[str],
               env: dict[str, str], timeout: int) -> tuple[int, str]:
    """跑一次测试 ⇒ (退出码, 输出尾部)。退出码 124 = 超时，127 = 跑不起来。"""
    cmd = list(runner) + list(test_rels)
    try:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                              encoding="utf-8", timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return 124, f"测试超时（{timeout}s）"
    except (FileNotFoundError, PermissionError) as e:
        return 127, f"测试命令跑不起来（{e}）"
    except OSError as e:
        return 127, f"测试命令跑不起来（{e}）"
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


# ---------------------------------------------------------------- 主流程


def run_mutation_check(repo: Path, ws_rel: str,
                       source_rels: list[str], test_rels: list[str],
                       runner: list[str], max_mutants: int = 6,
                       timeout: int = 120) -> dict:
    """对目标模块跑一轮变异检查。

    返回：
        attempted   试了几个变异点
        killed      多少个被测试发现（测试红了）
        timeouts    其中多少个是超时（也算发现了，但值得看一眼）
        survivors   杀不死的（位置 + 改了什么）
        notes       说明与阻碍

    `ws_rel` 是工作区相对仓库根的路径（"." = 仓库根）。
    `source_rels` / `test_rels` 相对**工作区**。
    """
    res: dict = {"attempted": 0, "killed": 0, "timeouts": 0,
                 "survivors": [], "notes": [], "scanned": 0}
    if not source_rels:
        res["notes"].append("没有可改的源码")
        return res
    if not test_rels:
        res["notes"].append("没有测试文件 ⇒ 无从判断测试能不能发现缺陷")
        return res
    if not runner:
        res["notes"].append("没认出测试命令")
        return res

    candidates: list[tuple[str, Mutation]] = []
    for rel in source_rels:
        src = repo / rel
        if not src.is_file():
            continue
        res["scanned"] += 1
        for mut in find_mutations(src):
            candidates.append((rel, mut))
    if not candidates:
        res["notes"].append("源码里没找到可注入的运算符变异点"
                            "（没有 == / != / >= / <= 这类比较）")
        return res

    per_file: Counter = Counter()
    picked: list[tuple[str, Mutation]] = []
    for rel, mut in candidates:
        if per_file[rel] >= DEFAULT_MAX_PER_FILE:
            continue
        per_file[rel] += 1
        picked.append((rel, mut))
        if len(picked) >= max_mutants:
            break

    with tempfile.TemporaryDirectory(prefix="attest-mutation-") as td:
        dst = Path(td) / "repo"
        try:
            _copy_repo(repo, dst)
        except OSError as e:
            res["notes"].append(f"复制仓库失败：{e}")
            return res
        linked = _link_deps(repo, dst, ws_rel)
        if not linked and _has_deps(repo, ws_rel):
            res["notes"].append("副本里没挂上依赖目录 ⇒ 测试可能跑不起来")
        cwd = dst if ws_rel in (".", "") else dst / ws_rel
        env = _child_env()
        for extra in (cwd / "node_modules" / ".bin", dst / "node_modules" / ".bin"):
            if extra.is_dir():
                env["PATH"] = f"{extra}{os.pathsep}{env.get('PATH', '')}"

        for rel, mut in picked:
            target = cwd / rel
            if not target.is_file():
                continue
            original = target.read_text(encoding="utf-8", errors="replace")
            target.write_text(_apply(original, mut), encoding="utf-8")
            try:
                code, out = _run_tests(runner, cwd, test_rels, env, timeout)
            finally:
                target.write_text(original, encoding="utf-8")
            if code == 127:
                res["notes"].append(out.strip()[:200] or "测试命令跑不起来")
                return res
            res["attempted"] += 1
            if code == 124:
                res["timeouts"] += 1
                res["killed"] += 1
                continue
            if code != 0:
                res["killed"] += 1
            else:
                res["survivors"].append({
                    "where": f"{rel}:{mut.line}",
                    "kind": mut.kind,
                    "change": f"{mut.original} → {mut.mutated}",
                })
    return res


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="列出一个文件里可注入的变异点")
    ap.add_argument("source", help="源码文件")
    ap.add_argument("--limit", type=int, default=10)
    args = ap.parse_args(argv)
    src = Path(args.source)
    muts = find_mutations(src, args.limit)
    print(f"{src}: 找到 {len(muts)} 个变异点")
    for m in muts:
        print(f"  {m.where()}  {m.kind}  {m.original!r} → {m.mutated!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
