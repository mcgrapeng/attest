#!/usr/bin/env python3
"""测试强度（变异测试的轻量版）：把源码改坏，看**测试会不会红**。

为什么要有它：覆盖率只说明代码被执行过，不说明断言会失败。
一个"杀不死缺陷"的测试套件，覆盖率再高也不说明问题 ——
本模块回答的是：**这套测试能不能发现缺陷？**

做法：把目标仓库**复制**到临时目录（绝不改用户仓库），往副本里注入一个
**已知缺陷**（改比较运算符），再跑该模块自己的测试：

    测试红了 ⇒ 变异被杀死 ⇒ 这套测试能发现这类缺陷（证据）
    测试还绿 ⇒ **杀不死**   ⇒ 测试套件有洞（报出来）

七类算子（规格 §6.3）：

    L2 词法：cmp-negate（== → !=）、cmp-tighten（>= → >）、
             in-to-hasown（x in Y → Object.hasOwn(Y, x)）
    L3 结构：drop-throw（throw E → void E）、drop-call（f(a) → undefined）、
             predicate-negate（filter(P) → filter(x => !P(x))）、
             catch-drop（catch(e){BODY} → catch(e){}）

L2 只认比较运算符与 `in`，不碰语句边界；L3 需要 AST 后端
（`decision_points` 的 TypeScript 后端），结构算子只在 L3 路径下存在。
所有变异都是**表达式替换**，不删语句 —— 删语句多半变成编译错误，
测出来的结论不可靠。

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

import ast
import os
import re
import shutil
import subprocess
import sys
import tempfile
from bisect import bisect_right
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from code_strip import strip_js_code, strip_py_code  # noqa: E402
from decision_points import (  # noqa: E402
    _anchor_at, detect_backend, scope_stacks, structural_points)

#: 变异算子全集（规格 §6.3 的表，顺序即报告里的展示顺序）
OPERATORS: tuple[str, ...] = (
    "cmp-negate", "cmp-tighten", "in-to-hasown",
    "drop-throw", "drop-call", "predicate-negate", "catch-drop",
)

#: 词法算子（L2）：不碰语句边界，任何机器上都能跑
L2_OPERATORS: frozenset[str] = frozenset(
    {"cmp-negate", "cmp-tighten", "in-to-hasown"})

#: 结构算子（L3）：需要 AST 后端
L3_OPERATORS: frozenset[str] = frozenset(
    {"drop-throw", "drop-call", "predicate-negate", "catch-drop"})

#: 原运算符 → (改后, 叫什么)
_OP_MAP: dict[str, tuple[str, str]] = {
    "==": ("!=", "等号取反"),
    "!=": ("==", "不等号取反"),
    "===": ("!==", "严格等号取反"),
    "!==": ("===", "严格不等号取反"),
    ">=": (">", "下界收紧"),
    "<=": ("<", "上界收紧"),
}

#: 原运算符 → 算子名（比较运算符分两类：取反 / 收紧）
_OP_NAMES: dict[str, str] = {
    "==": "cmp-negate", "!=": "cmp-negate",
    "===": "cmp-negate", "!==": "cmp-negate",
    ">=": "cmp-tighten", "<=": "cmp-tighten",
}

#: 结构算子的中文说明
_STRUCT_KIND: dict[str, str] = {
    "drop-throw": "抛错换成空表达式",
    "drop-call": "调用换成 undefined",
    "predicate-negate": "谓词取反",
    "catch-drop": "catch 体清空",
}

#: 找运算符出现的位置。
#:   前一位不是 = ! < >   ⇒ 不碰 === 与 !==
#:   后一位不是 =         ⇒ 不碰 >= 的 = 与 >> 的第二个 >
#: 三字符的 === / !== 先试 ⇒ JS 的严格相等也进得来（很常见的变异点）。
_OP_RE = re.compile(r"(?<![=!<>])(===|!==|==|!=|>=|<=)(?![=])")

#: `in` 运算符：`x in Y` ⇒ `Object.hasOwn(Y, x)`。
#: 只认「两边都是标识符链」这种保守形状 —— 认不出的（调用结果、下标、
#: 字面量）宁可跳过，也别造出会崩的变异体。
_IN_EXPR_RE = re.compile(
    r"(?<![A-Za-z0-9_$\.\]\)])"
    r"([A-Za-z_$][\w$]*(?:\s*\.\s*[A-Za-z_$][\w$])*)"
    r"\s+(in)\s+"
    r"([A-Za-z_$][\w$]*(?:\s*\.\s*[A-Za-z_$][\w$])*)"
    r"(?![A-Za-z0-9_$\.\[\(])")

#: `in` 关键字（词边界）—— 用来认出 `for (x in y)` 头里那个**不是运算符**的 in
_IN_KW_RE = re.compile(r"(?<![A-Za-z0-9_$])in(?![A-Za-z0-9_$])")

#: 变异生效标记：副本里被改坏的文件会打出 `__ATTEST_MUTANT__:<相对路径>`，
#: 子进程输出里没有它 ⇒ 这次变异压根没生效（文件没被加载 / 函数没被调用）。
MUTANT_MARKER = "__ATTEST_MUTANT__"

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
    op: str = ""              # 算子名（规格 §6.3 的 7 个之一）
    symbol: str | None = None  # 所属具名符号（作用域限定用）
    #: 所在的**全部**具名作用域（外 → 内）。`symbol` 只是最内层那个 ——
    #: 调用方 import 的是外层符号（工厂）时，光看 `symbol` 会把整个文件滤空。
    scopes: tuple[str, ...] = ()

    def where(self) -> str:
        return f"{self.path.name}:{self.line}"


# ---------------------------------------------------------------- 找变异点


def _py_scopes(src: str) -> list[tuple[str, int, int]]:
    """Python 的具名作用域 ⇒ [(名字, 起始行, 结束行)]。

    `_anchor_at` 是按大括号找包围函数的 ⇒ 只对 JS/TS 有效；Python 恒返回
    `<toplevel>` ⇒ 作用域过滤会把 Python 的变异点全丢掉。
    """
    try:
        tree = ast.parse(src)
    except (SyntaxError, ValueError):
        return []
    out: list[tuple[str, int, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            end = getattr(node, "end_lineno", None) or node.lineno
            out.append((node.name, node.lineno, end))
    return out


def _py_scope_at(scopes: list[tuple[str, int, int]],
                 lineno: int) -> str | None:
    """第 `lineno` 行所在的最内层具名作用域（不在任何 def/class 内 ⇒ None）。"""
    best: str | None = None
    best_span: int | None = None
    for name, start, end in scopes:
        if not start <= lineno <= end:
            continue
        span = end - start
        if best_span is None or span < best_span:
            best, best_span = name, span
    return best


def _for_in_positions(code: str) -> set[int]:
    """`for (x in y)` 头里那个 `in` 的位置（它不是运算符，别去改它）。"""
    out: set[int] = set()
    for m in re.finditer(r"\bfor\s*\(", code):
        start = m.end()
        depth = 0
        end = len(code)
        for j in range(start, len(code)):
            ch = code[j]
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                if depth == 0:
                    end = j
                    break
                depth -= 1
            elif ch == ";" and depth == 0:
                end = j
                break
        header = code[start:end]
        for km in _IN_KW_RE.finditer(header):
            depth = 0
            for ch in header[:km.start()]:
                if ch in "([{":
                    depth += 1
                elif ch in ")]}":
                    depth -= 1
            if depth == 0:
                out.add(start + km.start())
                break
    return out


def _find_in_mutations(path: Path, code: str, lines: list[str],
                       limit: int | None,
                       stacks: list[tuple[str, ...]] | None = None,
                       ) -> list[Mutation]:
    """`x in Y` ⇒ `Object.hasOwn(Y, x)`（原型链语义不同 ⇒ 真实缺陷）。

    Python 不做：`Object.hasOwn` 是 JS 的 API。
    """
    if path.suffix == ".py":
        return []
    for_ins = _for_in_positions(code)
    out: list[Mutation] = []
    for mm in _IN_EXPR_RE.finditer(code):
        if mm.start(2) in for_ins:
            continue
        left, right = mm.group(1), mm.group(3)
        start = mm.start()
        line = code.count("\n", 0, start) + 1
        col = start - (code.rfind("\n", 0, start) + 1) + 1
        out.append(Mutation(
            path=path, line=line, col=col, offset=start,
            length=mm.end() - start,
            original=f"{left} in {right}",
            mutated=f"Object.hasOwn({right}, {left})",
            kind="in 换成 hasOwn（看不见原型链上的键）",
            op="in-to-hasown", symbol=_anchor_at(lines, line),
            scopes=((stacks[line - 1] if stacks and line - 1 < len(stacks)
                     else ()) or ())))
        if limit is not None and len(out) >= limit:
            break
    return out


def find_mutations(path: Path,
                   limit: int | None = DEFAULT_MAX_PER_FILE) -> list[Mutation]:
    """在文件里找可注入的变异点（只认代码位置，不碰字符串与注释）。

    比较运算符与 `in` **分两族各取上限** —— 否则一堆比较运算符会把
    `in` 挤掉（要什么算子由调用方按 `op` 过滤）。

    `limit=None` = 不设上限。调用方（`run_mutation_check`）要按**作用域**
    挑点时必须用不限的：先截断再过滤的话，作用域外的点会把名额占光，
    作用域内的点压根到不了 ⇒ 假的「不适用」。
    """
    try:
        src = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    if path.suffix == ".py":
        code = strip_py_code(src)
    else:
        code = strip_js_code(src)
    lines = code.split("\n")
    py_scopes = _py_scopes(src) if path.suffix == ".py" else None
    js_stacks = None if py_scopes is not None else scope_stacks(lines)

    def symbol_at(line: int) -> str | None:
        if py_scopes is not None:
            return _py_scope_at(py_scopes, line)
        return _anchor_at(lines, line)

    def scopes_at(line: int) -> tuple[str, ...]:
        if py_scopes is not None:
            name = _py_scope_at(py_scopes, line)
            return (name,) if name else ()
        idx = max(line - 1, 0)
        if js_stacks is None or idx >= len(js_stacks):
            return ()
        return js_stacks[idx]

    out: list[Mutation] = []
    for mm in _OP_RE.finditer(code):
        op = mm.group(1)
        mutated, kind = _OP_MAP[op]
        start = mm.start()
        line = code.count("\n", 0, start) + 1
        col = start - (code.rfind("\n", 0, start) + 1) + 1
        out.append(Mutation(path=path, line=line, col=col, offset=start,
                            length=len(op), original=op, mutated=mutated,
                            kind=kind, op=_OP_NAMES[op],
                            symbol=symbol_at(line),
                            scopes=scopes_at(line)))
        if limit is not None and len(out) >= limit:
            break
    out.extend(_find_in_mutations(path, code, lines, limit, js_stacks))
    return out


def _apply(text: str, mut: Mutation) -> str:
    """把一个变异应用到文本（按绝对偏移，偏移量与原文件一致）。"""
    return text[:mut.offset] + mut.mutated + text[mut.offset + mut.length:]


# ---------------------------------------------------------------- 结构算子
#
# 结构算子要语句与表达式的边界 ⇒ 只有走 TypeScript AST 后端才拿得到。
# 后端那边只吐**事实**（算子 / 区间 / 符号），替换文本在这儿算 ——
# 这样替换规则不用装 TypeScript 也能单测。

#: 结构算子的替换文本（catch-drop 换的是整个 catch 体 `{...}`）
_STRUCT_REPLACEMENT: dict[str, str] = {
    "drop-throw": "void",
    "drop-call": "undefined",
    "catch-drop": "{}",
}

#: TS 后端探测一次要起 node，慢 ⇒ 按项目根缓存
_BACKEND_CACHE: dict[str, object] = {}


def _project_root(path: Path) -> Path | None:
    """往上找带 package.json 的目录（TS AST 后端要在项目里跑）。"""
    for parent in path.parents:
        if (parent / "package.json").is_file():
            return parent
    return None


def _backend_for(root: Path):
    """项目根对应的 TS AST 后端（没有 ⇒ None，调用方退回 L2）。"""
    key = str(root)
    if key not in _BACKEND_CACHE:
        try:
            _BACKEND_CACHE[key] = detect_backend(root)
        except OSError:
            _BACKEND_CACHE[key] = None
    return _BACKEND_CACHE[key]


def _astral_u16_starts(src: str) -> list[int]:
    """星平面字符（emoji 之类）的 UTF-16 起始偏移（升序）。"""
    out: list[int] = []
    u = 0
    for ch in src:
        if ord(ch) > 0xFFFF:
            out.append(u)
            u += 2
        else:
            u += 1
    return out


def _u16_to_cp(off: int, starts: list[int]) -> int:
    """TypeScript 的 UTF-16 偏移 → Python 的码点下标。

    TS 的偏移是 UTF-16 码元，Python 字符串下标是码点 ——
    前面有一个 emoji，两者就差 1。
    """
    return off - bisect_right(starts, off - 2)


def find_structural_mutations(path: Path,
                              limit: int | None = DEFAULT_MAX_PER_FILE
                              ) -> list[Mutation]:
    """L3 结构算子（throw / 调用 / 谓词 / catch 体）。

    没有 TypeScript 后端 ⇒ 空列表（不是报错）—— 调用方据此退回 L2。

    `limit=None` = 不设上限（同 `find_mutations`：先截断再按作用域过滤
    会让作用域内的点压根到不了）。
    """
    if path.suffix not in (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"):
        return []
    root = _project_root(path)
    if root is None:
        return []
    backend = _backend_for(root)
    if backend is None:
        return []
    try:
        src = path.read_text(encoding="utf-8", errors="replace")
        rel = str(path.resolve().relative_to(root.resolve()))
    except (OSError, ValueError):
        return []
    starts = _astral_u16_starts(src)
    js_stacks = scope_stacks(strip_js_code(src).split("\n"))
    out: list[Mutation] = []
    for fact in structural_points(root, [rel], backend):
        op = str(fact.get("op") or "")
        if op not in L3_OPERATORS:
            continue
        try:
            start = _u16_to_cp(int(fact["start"]), starts)
            end = _u16_to_cp(int(fact["end"]), starts)
        except (KeyError, TypeError, ValueError):
            continue
        if not 0 <= start <= end <= len(src):
            continue
        mutated = _STRUCT_REPLACEMENT.get(op)
        if op == "predicate-negate":
            # 换掉谓词**体**：`(r) => r.ok` ⇒ `(r) => !((r) => r.ok)(r)`
            arrow = str(fact.get("arrow") or "")
            param = str(fact.get("param") or "")
            if not arrow or not param:
                continue
            mutated = f"!({arrow})({param})"
        if mutated is None:
            continue
        line = src.count("\n", 0, start) + 1
        col = start - (src.rfind("\n", 0, start) + 1) + 1
        out.append(Mutation(
            path=path, line=line, col=col, offset=start, length=end - start,
            original=src[start:end], mutated=mutated, kind=_STRUCT_KIND[op],
            op=op, symbol=str(fact.get("symbol") or "") or None,
            scopes=(js_stacks[line - 1] if line - 1 < len(js_stacks) else ())))
        if limit is not None and len(out) >= limit:
            break
    return out


# ---------------------------------------------------------------- 用例匹配


def match_case(case: str, full_path: str) -> bool:
    """用例名是否命中某个失败项（**严格**，只有三条规则）。

    1. 归一化（折叠空白、去首尾）后完全相等；
    2. pytest 的 node id（`tests/test_x.py::test_x`）取最后一段比；
    3. 否则按 `>` 切段，取 `full_path` 的**末尾 k 段**（k = case 的段数）
       与 case 逐段完全相等。

    不做「去前缀」「包含」「去标点」—— 那些宽松规则会把别的用例的失败
    算成命中（假杀死）。
    """
    a = " ".join(case.split())
    b = " ".join(full_path.split())
    if not a or not b:
        return False
    if a == b:
        return True
    if "::" in b:
        tail = b.rsplit("::", 1)[-1].strip()
        if tail == a:
            return True
        # 参数化用例：`test_x[param]` ⇒ 去掉参数段再比
        if tail.split("[", 1)[0].strip() == a:
            return True
    a_parts = [p.strip() for p in a.split(">")]
    b_parts = [p.strip() for p in b.split(">")]
    if len(b_parts) < len(a_parts):
        return False
    return b_parts[-len(a_parts):] == a_parts


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
                       timeout: int = 120, classify=None,
                       case_name: str | None = None,
                       scope_symbols: set[str] | None = None,
                       prefer_symbols: set[str] | None = None,
                       ops: tuple[str, ...] | None = None,
                       parse_check=None, inject=None) -> dict:
    """对目标模块跑一轮变异检查。

    返回：
        attempted   试了几个变异点
        killed      多少个被测试发现（测试红了）
        timeouts    其中多少个是超时（也算发现了，但值得看一眼）
        unknown     多少个**判不了**（环境没就绪 / 输出格式认不出 / 变异后解析不过）
        survivors   杀不死的（位置 + 改了什么）
        notes       说明与阻碍

    `ws_rel` 是工作区相对仓库根的路径（"." = 仓库根）。
    `source_rels` / `test_rels` 相对**工作区**。
    `classify(code, out, name) -> (失败用例, 未实测原因)` 可选：
    给了就按三分口径判（真失败⇒杀死、环境没就绪/输出认不出⇒**判不了**），
    不给就退回"退出码非 0 = 杀死" —— 那会把环境错误算成"杀死了"，是假绿。
    `ops` 要哪些算子（默认全部 L2 词法算子）；先按算子过滤**再**按文件
    取上限 —— 否则比较运算符会把点名要的其他算子挤掉。
    `scope_symbols` 只保留这些具名符号里的变异点。
    `prefer_symbols` 是**钉住那条用例真正提到的**符号 —— 抽样时优先挑它们，
    否则名额会被同一个文件里毫不相干的函数吃光 ⇒ 判成「杀不死」是假的。
    `case_name` 只让**这个用例**的失败算数（钉的用例有没有区分力）。
    `parse_check(path, text) -> bool` 判断变异后的代码还解析不解析得过；
    解析不过 ⇒ 记"判不了"，**不进分母**（跑了也测不出东西）。
    检查那一刻 `path` 指向的文件**还是原文**（变异还没写下去）——
    要判断"这次变异有没有把代码改坏"的校验器得能拿到原文。
    `inject(rel, text, mut) -> text` 可选：在副本里给改坏的文件打标记
    （`__ATTEST_MUTANT__:<rel>`）；跑完输出里没有标记 ⇒ 这次变异没生效 ⇒
    记**不适用**（不是存活），同样不进分母。
    """
    res: dict = {"attempted": 0, "killed": 0, "timeouts": 0, "unknown": 0,
                 "not_applicable": 0,
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

    wanted = set(ops) if ops is not None else set(L2_OPERATORS)
    want_l2 = wanted & L2_OPERATORS
    want_l3 = wanted & L3_OPERATORS

    candidates: list[tuple[str, Mutation]] = []
    # source_rels 是相对**工作区**的 ⇒ 得按工作区找，不是按仓库根
    base = repo if ws_rel in (".", "") else repo / ws_rel
    for rel in source_rels:
        src = base / rel
        if not src.is_file():
            continue
        res["scanned"] += 1
        if want_l2:
            # 不设上限：每文件的上限在**作用域过滤之后**才施加（见下），
            # 先截断会让作用域外的点把名额占光。
            for mut in find_mutations(src, None):
                if mut.op in want_l2:
                    candidates.append((rel, mut))
        if want_l3:
            for mut in find_structural_mutations(src, None):
                if mut.op in want_l3:
                    candidates.append((rel, mut))
    if scope_symbols:
        # 命中条件：变异点所在的**任一层**具名作用域被测试引用。
        # 只看最内层会滤空「工厂返回的对象字面量里的方法」——
        # 测试 import 的是工厂，方法的锚点是方法名，两边对不上。
        candidates = [(rel, mut) for rel, mut in candidates
                      if (mut.symbol and mut.symbol in scope_symbols)
                      or any(s in scope_symbols for s in mut.scopes)]
    if not candidates:
        res["notes"].append("源码里没找到可注入的变异点"
                            f"（要的算子：{'、'.join(sorted(wanted))}）")
        return res

    if prefer_symbols:
        # 钉住那条用例提到的符号排前面（稳定排序 ⇒ 同组内仍是文件/行序）。
        candidates.sort(
            key=lambda rm: not (
                rm[1].symbol in prefer_symbols
                or any(s in prefer_symbols for s in rm[1].scopes)))
        # 只排前面不够：同文件里另一个函数的变异点照样把名额吃光 ⇒ 判成
        # 「杀不死」是假的（docstring 里写过这句）。所以**用例提到的符号有
        # 候选时只取它们**；一个都没有 ⇒ 这轮变异点只能来自别的符号，
        # 说清「判不了」，别把「不在这条用例范围内」说成「这条钉没区分力」。
        picked_scope = [rm for rm in candidates
                        if rm[1].symbol in prefer_symbols
                        or any(s in prefer_symbols for s in rm[1].scopes)]
        if picked_scope:
            candidates = picked_scope
        else:
            res["outside_case_scope"] = True
            res["notes"].append(
                "钉住的那条用例没提到任何可变异符号 ⇒ 这轮变异点只能取自"
                "同文件（或同作用域）的别的符号 ⇒ 不能据此判这条钉没区分力")

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
            mutated_text = _apply(original, mut)
            if inject is not None:
                try:
                    mutated_text = inject(rel, mutated_text, mut)
                except Exception:      # noqa: BLE001 —— 注入器自己炸了也算判不了
                    res["unknown"] += 1
                    res["notes"].append(f"{rel}:{mut.line} 标记注入失败 ⇒ 判不了")
                    continue
            if parse_check is not None:
                try:
                    parses = bool(parse_check(target, mutated_text))
                except Exception:      # noqa: BLE001 —— 判卷器自己炸了也算判不了
                    parses = False
                if not parses:
                    res["unknown"] += 1
                    res["notes"].append(
                        f"{rel}:{mut.line} 变异后解析不过 ⇒ 判不了")
                    continue
            target.write_text(mutated_text, encoding="utf-8")
            try:
                code, out = _run_tests(runner, cwd, test_rels, env, timeout)
            finally:
                target.write_text(original, encoding="utf-8")
            if code == 127:
                res["notes"].append(out.strip()[:200] or "测试命令跑不起来")
                return res
            if inject is not None and f"{MUTANT_MARKER}:{rel}" not in out:
                # 标记没出现 ⇒ 改坏的那段压根没跑到 ⇒ 不适用（不是存活）
                res["not_applicable"] += 1
                res["notes"].append(
                    f"{rel}:{mut.line} 变异没生效（标记没出现）⇒ 不适用")
                continue
            res["attempted"] += 1
            if code == 124:
                res["timeouts"] += 1
                res["killed"] += 1
                continue
            if classify is not None:
                # 三分口径（与 readiness 的「跑不起来 ≠ 不达标」同一条）：
                #   真失败      ⇒ 杀死
                #   环境没就绪  ⇒ **判不了**（既不算杀死，也不算存活）
                #   输出认不出  ⇒ **判不了**（不能因为退出码非 0 就说"杀死了"）
                fails, setup = classify(code, out, f"变异 {rel}:{mut.line}")
                if case_name is None:
                    hit = bool(fails)
                else:
                    # 只认**钉的那个用例**的红：别的用例红了不算它有区分力
                    hit = any(match_case(case_name, f) for f in fails)
                if hit:
                    res["killed"] += 1
                elif fails:
                    # 别的用例红了、钉的用例没红 ⇒ 对这个用例来说没区分力
                    res["survivors"].append({
                        "where": f"{rel}:{mut.line}",
                        "kind": mut.kind,
                        "op": mut.op,
                        "change": f"{mut.original} → {mut.mutated}",
                    })
                elif setup:
                    res["unknown"] += 1
                    res["notes"].append(setup[0] if setup else "判不了")
                else:
                    res["survivors"].append({
                        "where": f"{rel}:{mut.line}",
                        "kind": mut.kind,
                        "op": mut.op,
                        "change": f"{mut.original} → {mut.mutated}",
                    })
                continue
            if code != 0:
                res["killed"] += 1
            else:
                res["survivors"].append({
                    "where": f"{rel}:{mut.line}",
                    "kind": mut.kind,
                    "op": mut.op,
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
        print(f"  {m.where()}  {m.op}  {m.kind}  {m.original!r} → {m.mutated!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
