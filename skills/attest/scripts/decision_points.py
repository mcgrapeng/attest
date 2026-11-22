#!/usr/bin/env python3
"""判定点（Decision Point）推导引擎 —— 数据模型、DP ID 规则、L2 词法推导。

三件必须记住的事：

1. **只判定，不代写期望值。** 本模块只回答「源码里哪里有会改变行为的选择」；
   该断言什么，永远由人给。
2. **DP ID 不含行号。** 形态是 `file#anchor#kind[#ordinal]`；代码增删几行，ID 不漂
   （规格 §2.3.1）。行号只是报告里的一个字段。
3. **L2 是启发式。** 词法扫描有误报漏报，用它的报告必须标明「词法推导，可能有误报」。

DP 的种类（规格 §2.2）与高危档（规格 §3.5）：
高危四类 `error-path` / `resource` / `drop` / `state` 的覆盖缺口才逐条报红，
其余四类只汇总计数 —— 否则这张清单会变成噪音机。
"""
from __future__ import annotations

import ast
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

DP_KINDS: tuple[str, ...] = (
    "branch", "boundary", "error-path", "resource", "state", "drop",
    "boundary-io", "concurrency",
)

HIGH_RISK_KINDS: frozenset[str] = frozenset(
    {"error-path", "resource", "drop", "state"})

# 附录 A 的初始词表（按「真实出现的标识符形态」写，不是按英文词根写）。
RESOURCE_VERBS: frozenset[str] = frozenset({
    "create", "close", "open", "acquire", "release", "dispose", "destroy",
    "shutdown", "free", "unref", "end", "stop", "init", "setup", "teardown",
})

IO_WORDS: frozenset[str] = frozenset({
    "fetch", "request", "query", "select", "insert", "update", "delete",
    "upsert", "read", "write", "send", "post", "put", "patch", "connect",
    "exec", "commit", "rollback",
})

STATE_WORDS: frozenset[str] = frozenset({
    "status", "state", "phase", "stage", "step", "flag", "mode",
})

# `all` 不在表里 —— 它是 `Promise.all` 的一部分，整词匹配时根本不是独立标识符，
# 而子串匹配会把 `call` / `small` / `install` 全部误命中。
CONCURRENCY_WORDS: frozenset[str] = frozenset({
    "lock", "unlock", "mutex", "semaphore", "wait", "waitFor", "acquire",
    "promise", "allSettled", "race", "concurrent", "atomic", "transaction",
})

# §3.4：drop 的判定点是 filter / reject / slice 的谓词。
DROP_WORDS: frozenset[str] = frozenset({"filter", "reject", "slice"})

TOPLEVEL = "<toplevel>"


def _norm(word: str) -> str:
    """变体归一化：camelCase / snake_case / kebab-case → 小写无分隔。

    词表写 `teardown`，代码写 `tearDown` / `tear_down` 都要命中（附录 A 口径 2）。
    """
    return re.sub(r"[^a-z0-9]", "", word.lower())


_RESOURCE_NORM = frozenset(_norm(w) for w in RESOURCE_VERBS)
_IO_NORM = frozenset(_norm(w) for w in IO_WORDS)
_STATE_NORM = frozenset(_norm(w) for w in STATE_WORDS)
_CONCURRENCY_NORM = frozenset(_norm(w) for w in CONCURRENCY_WORDS)
_DROP_NORM = frozenset(_norm(w) for w in DROP_WORDS)


@dataclass
class DecisionPoint:
    """一个判定点。`dp_id` 由 `DpAnalysis.assign_ids()` 填，别手写。"""

    file: str
    anchor: str
    kind: str
    line: int
    detail: str = ""
    ordinal: int = 1
    dp_id: str = ""


@dataclass
class DpAnalysis:
    """一次推导的结果。`level` 决定这份清单有多可信，报告里必须写出来。"""

    level: str = "L1"
    backend: str = "none"
    backend_source: str = "none"
    note: str = ""
    points: list[DecisionPoint] = field(default_factory=list)

    def assign_ids(self) -> None:
        """按 `(file, anchor, kind)` 分组，组内按行号编号。

        组内只有一个时**省略 ordinal 段**（`x#f#drop`）；多个时从 1 起编号
        （`x#f#drop#1`）—— 同一符号里同类判定点会撞 ID（规格 §2.3.2）。
        """
        groups: dict[tuple[str, str, str], list[DecisionPoint]] = {}
        for p in self.points:
            groups.setdefault((p.file, p.anchor, p.kind), []).append(p)
        for (file, anchor, kind), pts in groups.items():
            pts.sort(key=lambda p: p.line)
            for i, p in enumerate(pts, start=1):
                p.ordinal = i
                p.dp_id = (f"{file}#{anchor}#{kind}" if len(pts) == 1
                           else f"{file}#{anchor}#{kind}#{i}")

    def by_id(self) -> dict[str, DecisionPoint]:
        return {p.dp_id: p for p in self.points}

    def by_kind(self) -> dict[str, list[DecisionPoint]]:
        out: dict[str, list[DecisionPoint]] = {}
        for p in self.points:
            out.setdefault(p.kind, []).append(p)
        return out


def normalize_dp_id(raw: str) -> str:
    """`#` 分段、每段去空白、**只把 kind 段小写**（规格 §2.3.2 的归一化）。"""
    parts = [p.strip() for p in raw.split("#")]
    if len(parts) >= 3:
        parts[2] = parts[2].lower()
    return "#".join(parts)


_DP_ID_RE = re.compile(r"^[^#]+#[^#]+#[a-z-]+(#\d+)?$")


def dp_key(raw: str) -> tuple[str, str, str, int] | None:
    """解析成可比键 `(file, anchor, kind, ordinal)`；不合法 ⇒ None。

    ordinal 段可写可不写，缺省当 1 —— `x#f#drop` 与 `x#f#drop#1` 是同一个。
    合法性判据 = 规格 §4.3 的正则。
    """
    norm = normalize_dp_id(raw)
    if not _DP_ID_RE.match(norm):
        return None
    parts = norm.split("#")
    ordinal = 1
    if len(parts) == 4:
        if not parts[3].isdigit():
            return None
        ordinal = int(parts[3])
    return (parts[0], parts[1], parts[2], ordinal)


# ------------------------------------------------------------------ L2 词法

# 字符串与注释清空走共享实现（code_strip）：以前这里与 mutation_check 各有一份
# 逐字相同的 JS stripper，docstring 里自己写着「改一处要改两处」。
from code_strip import strip_js_code  # noqa: E402
import proc_env  # noqa: E402


# 判定特征的正则。整词匹配靠 `_norm` + 词表，不靠正则魔法（附录 A 口径 1）。
# `else if` 算 **2 条**（else 一条 + if 一条）—— 规格 §3.4 说「每个 then/else
# 分支各一条」，L3 AST 后端也是这么数的（else 关键字一条 + 内层 if 一条）。
_RE_BRANCH = re.compile(r"\bif\b|\belse\b|\bcase\b|\bdefault\s*:|\?(?![.?:])")
_RE_BOUNDARY = re.compile(r"===|!==|==|!=|<=|>=|(?<![=!<>])[<>]")
_RE_ERROR = re.compile(r"\b(?:throw|raise|catch)\b")
_RE_AWAIT = re.compile(r"\bawait\b")
# `client.close()` / `client.close?.()` / `client.close!()` 都算调用
_RE_MEMBER_CALL = re.compile(
    r"([A-Za-z_$][\w$]*)\s*\.\s*([A-Za-z_$][\w$]*)\s*(?:[?!]\s*\.?)?\s*\(")
# `a.b?.c()` 的被调方是 c（前面是 `?.`，不是标识符）
_RE_OPT_CALL = re.compile(r"\?\.\s*([A-Za-z_$][\w$]*)\s*\(")
# 链式调用的续行：行首的 `.filter(`（接收者在上一行）
_RE_LEADING_DOT = re.compile(r"^[ \t]*\.[ \t]*([A-Za-z_$][\w$]*)[ \t]*\(")
_RE_PLAIN_CALL = re.compile(r"(?<![.\w$])([A-Za-z_$][\w$]*)\s*\(")
_RE_ASSIGN = re.compile(
    r"(?<![=!<>])([A-Za-z_$][\w$]*(?:\s*\.\s*[A-Za-z_$][\w$]*)*)"
    r"\s*(?:\*\*|<<|>>|[+\-*/%|&^])?=(?!=)")

# 作用域名识别：块之前那段文本长什么样，才算一个具名作用域。
_CONTROL_HEADS = frozenset({
    "if", "else", "for", "while", "switch", "catch", "try", "finally", "do",
    "with", "return", "case", "default", "function", "class", "const", "let",
    "var", "new", "typeof", "delete", "void", "await", "yield", "throw",
    "import", "export", "from", "as", "in", "of", "instanceof", "this",
    "super", "extends", "implements", "interface", "type", "enum", "namespace",
    "declare", "abstract", "static", "public", "private", "protected",
    "readonly", "async",
})

_RE_CLASS = re.compile(r"\bclass\s+([A-Za-z_$][\w$]*)")
_RE_FUNCTION = re.compile(r"\bfunction\s*\*?\s*([A-Za-z_$][\w$]*)")
_RE_ARROW_ASSIGN = re.compile(
    r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=\s*(?:async\s+)?"
    r"(?:function\b|\([^()]*\)\s*=>|[A-Za-z_$][\w$]*\s*=>)")
_RE_PROP_ARROW = re.compile(
    r"(?:^|[{;,(])\s*([A-Za-z_$][\w$]*)\s*:\s*(?:async\s+)?"
    r"(?:function\b|\([^()]*\)\s*=>|[A-Za-z_$][\w$]*\s*=>)")
# 类字段上的箭头函数：`private handler = () => {}`。必须带显式修饰符，
# 否则会和对象字面量里的同名写法混淆。
_RE_CLASS_FIELD = re.compile(
    r"\b(?:public|private|protected|readonly|static|abstract|override|declare)"
    r"\s+(?:readonly\s+)?([A-Za-z_$][\w$]*)\s*=\s*(?:async\s+)?"
    r"(?:function\b|\([^()]*\)\s*=>|[A-Za-z_$][\w$]*\s*=>)")
# `NAME(...)` / `NAME(...): Type` —— 方法（类方法与对象字面量里的方法）与函数声明
_RE_METHOD = re.compile(r"([A-Za-z_$][\w$]*)\s*\([^()]*\)\s*(?::[^;{}]*)?$")
# 顶层常量赋的对象字面量：`export const memoryInjectionNode: ChatNode = {`。
# 不给它名字 ⇒ 对象里的方法（`run`）成了孤儿作用域，而调用方 import 的是
# `memoryInjectionNode` ⇒ 两边对不上 ⇒ 变异点被作用域过滤滤光。
_RE_CONST_OBJECT = re.compile(
    r"\b(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*(?::[^=]*)?=\s*$")


@dataclass
class _Scope:
    depth: int
    name: str | None
    is_type: bool = False   # 返回类型里的对象字面量：闭合后紧跟的 `{` 是函数体


def _cut_head(text: str) -> str:
    """块之前那段文本 ⇒ 去掉上一条语句的尾巴（`;` / `}` 之后的部分）。"""
    cut = max(text.rfind(";"), text.rfind("}"))
    if cut >= 0:
        return text[cut + 1:].strip()
    return text.strip()


def _is_type_position(head: str) -> bool:
    """`{` 前面是 `:` ⇒ 这是类型位置（返回类型的对象字面量），不是代码块。"""
    return _cut_head(head).endswith(":")


def _match_scope_name(text: str) -> str | None:
    """从一段文本判断它引出的块属于哪个具名作用域；判不出 ⇒ None（匿名块）。"""
    text = _cut_head(text)
    if not text:
        return None
    # 顺序即优先级：`const f = function g() {}` 的锚点是 **f**（变量名），
    # 不是 g —— L3 AST 后端的 VariableDeclaration 规则也是这么定的（规格 §3.4）。
    for pattern in (_RE_CLASS, _RE_ARROW_ASSIGN, _RE_CLASS_FIELD, _RE_FUNCTION,
                    _RE_PROP_ARROW):
        m = pattern.search(text)
        if m:
            return m.group(1)
    m = _RE_METHOD.search(text)
    if m and m.group(1) not in _CONTROL_HEADS:
        return m.group(1)
    return None


def _folded_scope_name(lines: list[str], lineno: int, col: int) -> str | None:
    """签名折行时，头里只剩 `): Type` ⇒ 往上找配平的左括号，取它前面的标识符。"""
    head = lines[lineno - 1][:col]
    unmatched = head.count(")") - head.count("(")
    if unmatched <= 0:
        return None
    acc = ""
    for i in range(lineno - 2, max(-1, lineno - 12), -1):
        acc = lines[i] + "\n" + acc
        if acc.count("(") - acc.count(")") >= unmatched:
            break
    else:
        return None
    need = unmatched
    for idx in range(len(acc) - 1, -1, -1):
        ch = acc[idx]
        if ch == ")":
            need += 1
        elif ch == "(":
            if need == 1:
                m = re.search(r"([A-Za-z_$][\w$]*)\s*$", acc[:idx])
                return m.group(1) if m else None
            need -= 1
    return None


def _scope_name_for_brace(lines: list[str], lineno: int, col: int) -> str | None:
    """`{` 之前的那段文本 → 作用域名；本行判不出就往上找折行的签名。"""
    head = lines[lineno - 1][:col]
    name = _match_scope_name(head)
    if name:
        return name
    return _folded_scope_name(lines, lineno, col)


def _const_object_name(head: str) -> str | None:
    """`const X = {` 里的 X（顶层对象字面量 ⇒ 它的导出名就是 X）。"""
    m = _RE_CONST_OBJECT.search(_cut_head(head))
    return m.group(1) if m else None


def _innermost(stack: list[_Scope]) -> str:
    """最内层的**具名**作用域；没有 ⇒ `<toplevel>`（匿名块不改变归属）。"""
    for s in reversed(stack):
        if s.name:
            return s.name
    return TOPLEVEL


def _generic_positions(line: str) -> set[int]:
    """泛型参数表里的 `<` / `>` 位置 —— 它们不是比较运算符。

    判据：`<` 紧贴前面的标识符（`Promise<`）⇒ 泛型；再往右找配平的 `>`。
    shortcut: 手写且没空格的比较（`a<b`）会被当成泛型 ⇒ 漏报。
    升级条件：L3 AST 后端上线后用它替掉这条启发式。
    """
    positions: set[int] = set()
    for m in re.finditer(r"[A-Za-z_$][\w$]*<", line):
        start = m.end() - 1
        if start > 0 and line[start - 1] in ")]":
            continue
        depth = 0
        for i in range(start, len(line)):
            if line[i] == "<":
                depth += 1
            elif line[i] == ">":
                depth -= 1
                if depth == 0:
                    positions.add(start)
                    positions.add(i)
                    break
    return positions


def _call_tail(line: str, open_idx: int) -> str:
    """参数表闭合之后是什么（用来分辨「声明」和「调用」）。"""
    depth = 0
    for i in range(open_idx, len(line)):
        ch = line[i]
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return line[i + 1:].lstrip()
    return ""


def _line_events(line: str) -> list[tuple[int, str, str]]:
    """一行里的判定点：[(列, kind, 详情), ...]。"""
    events: list[tuple[int, str, str]] = []

    def add(col: int, kind: str, detail: str) -> None:
        events.append((col, kind, detail[:80]))

    def add_callee(col: int, name: str, detail: str) -> None:
        """按附录 A 的词表给一个被调方定类。"""
        n = _norm(name)
        if n in _RESOURCE_NORM:
            add(col, "resource", detail)
        if n in _IO_NORM:
            add(col, "boundary-io", detail)
        if n in _CONCURRENCY_NORM:
            add(col, "concurrency", detail)
        if n in _DROP_NORM:
            add(col, "drop", detail)

    for m in _RE_BRANCH.finditer(line):
        add(m.start(), "branch", m.group(0).strip())
    generic = _generic_positions(line)
    for m in _RE_BOUNDARY.finditer(line):
        if m.start() in generic:
            continue          # 泛型参数表不是比较运算符
        add(m.start(), "boundary", m.group(0))
    for m in _RE_ERROR.finditer(line):
        add(m.start(), "error-path", m.group(0))
    for m in _RE_AWAIT.finditer(line):
        add(m.start(), "concurrency", "await")

    for m in _RE_MEMBER_CALL.finditer(line):
        obj, prop = m.group(1), m.group(2)
        col = m.start()
        detail = f"{obj}.{prop}()"
        add_callee(col, prop, detail)
        if obj == "Promise" and _norm(prop) == "all":
            add(col, "concurrency", detail)

    for m in _RE_OPT_CALL.finditer(line):
        add_callee(m.start(), m.group(1), f"{m.group(1)}()")

    m = _RE_LEADING_DOT.match(line)
    if m:
        add_callee(m.start(), m.group(1), f".{m.group(1)}()")

    for m in _RE_PLAIN_CALL.finditer(line):
        tail = _call_tail(line, m.end() - 1)
        if tail.startswith("{") or tail.startswith(":"):
            continue          # 方法/函数声明：不是调用
        add_callee(m.start(), m.group(1), f"{m.group(1)}()")

    for m in _RE_ASSIGN.finditer(line):
        target = m.group(1)
        last = re.split(r"[.\s]+", target.strip())[-1]
        if _norm(last) in _STATE_NORM:
            add(m.start(), "state", f"{target.strip()} =")

    events.sort(key=lambda e: (e[0], e[1]))
    return events


def _scan_lines(lines: list[str],
               stack_sink: list[tuple[str, ...]] | None = None,
               ) -> tuple[list[_Scope], list[tuple[int, str, str, str]]]:
    """扫一遍源码行 ⇒ (最终作用域栈, [(行号, kind, 锚点, 详情), ...])。

    作用域靠花括号深度跟踪，不是「往上看找最近一个函数」——
    后者会把已经闭合的函数里的调用算到那个函数头上。

    `stack_sink` 给了就把**每一行开始处**的具名作用域栈（外 → 内）记下来，
    下标 = 行号 - 1。逐行问作用域时用这个，别逐行重扫（那是 O(n²)）。
    """
    stack: list[_Scope] = []
    hits: list[tuple[int, str, str, str]] = []
    depth = 0
    for lineno, line in enumerate(lines, start=1):
        if stack_sink is not None:
            stack_sink.append(tuple(s.name for s in stack if s.name))
        events = _line_events(line)
        braces = [(i, ch) for i, ch in enumerate(line) if ch in "{}"]
        merged = sorted(
            [(col, "hit", kind, detail) for col, kind, detail in events]
            + [(i, "open" if ch == "{" else "close", "", "") for i, ch in braces],
            key=lambda e: e[0],
        )
        pending: str | None = None   # `} {`：类型字面量闭合后紧跟的函数体继承名字
        for col, kind, _kind, detail in merged:
            if kind == "hit":
                hits.append((lineno, _kind, _innermost(stack), detail))
            elif kind == "open":
                depth += 1
                name = _scope_name_for_brace(lines, lineno, col)
                if name is None and depth == 1:
                    # 顶层 `const X = {…}`：对象字面量本身是导出的那个符号
                    name = _const_object_name(lines[lineno - 1][:col])
                if name is None:
                    name = pending
                pending = None
                stack.append(_Scope(depth=depth, name=name,
                                     is_type=_is_type_position(lines[lineno - 1][:col])))
            else:
                closed = None
                if stack and stack[-1].depth == depth:
                    closed = stack.pop()
                depth = max(0, depth - 1)
                if (closed is not None and closed.is_type and closed.name
                        and re.match(r"\s*\{", line[col + 1:])):
                    pending = closed.name
    return stack, hits


def _anchor_at(lines: list[str], lineno: int) -> str:
    """第 `lineno` 行**开始处**所在的最内层具名作用域；不在任何函数内 ⇒ `<toplevel>`。"""
    stack, _ = _scan_lines(lines[:max(lineno - 1, 0)])
    return _innermost(stack)


def scope_stacks(lines: list[str]) -> list[tuple[str, ...]]:
    """每一行开始处的具名作用域栈（外 → 内）；下标 = 行号 - 1。

    只看**最内层**会漏掉「工厂返回的对象字面量里的方法」：
    `createMilvusMemoryStore` 里的 `upsert`，锚点是 `upsert`，
    而调用方 import 的是 `createMilvusMemoryStore` ⇒ 作用域过滤全落空
    ⇒ 钉被判成「没有可判的变异点」（假的）。
    """
    sink: list[tuple[str, ...]] = []
    _scan_lines(lines, sink)
    return sink


def lexical_analyze(file: str, source: str) -> list[DecisionPoint]:
    """L2 词法推导：不需要任何后端，任何机器上都能跑。

    有误报漏报（启发式），报告里必须标明「词法推导，可能有误报」。
    """
    code = strip_js_code(source)
    lines = code.split("\n")
    _, hits = _scan_lines(lines)
    return [DecisionPoint(file=file, anchor=anchor, kind=kind, line=lineno,
                          detail=detail, ordinal=1)
            for lineno, kind, anchor, detail in hits]


# --------------------------------------------------------------- TS AST 后端
#
# L3 推导走**仓库自己那份 typescript**（规格 §3.3.2）：不引第三方 Python 包，
# 也不联网。后端两种：
#   B1 = TypeScript 5 经典 API（`ts.createSourceFile` + `ts.forEachChild`）
#   B2 = TypeScript 7 `typescript/unstable/{ast,sync}`（快照 + 远端节点）
# 两者 SyntaxKind 名字一致 ⇒ 一套分类器 + 一层薄适配。

@dataclass
class TsBackend:
    """一份**真的能解析代码**的 TypeScript。"""

    kind: str          # "B1"（TS5 经典 API）/ "B2"（TS7 unstable）
    label: str
    source: str        # "repo-local"（仓库里的）/ "global-npm"（全局装的）
    package_dir: str   # typescript 包目录，绝对路径


# 遍历时跳过的目录：依赖树、构建产物、缓存。
_SKIP_DIRS = frozenset({
    "node_modules", ".git", "dist", "build", "out", "coverage", ".next",
    ".nuxt", ".turbo", ".cache", ".venv", "venv", "__pycache__",
    ".pytest_cache", ".ruff_cache", ".mypy_cache", "target", ".idea",
    ".vscode", "vendor", "Pods",
})


def _node_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """子进程环境白名单（实现在 `proc_env`，与 readiness 同一份）。

    本模块不 import readiness —— 将来 readiness 要 import 本模块，会成环。
    """
    return proc_env.child_env(extra)


def _scan_repo(root: Path) -> tuple[list[tuple[Path, str]], bool]:
    """扫一遍目标树 ⇒ (typescript 包候选, 有没有 Node/TS 项目的痕迹)。

    候选包含 pnpm 布局（`node_modules/.pnpm/typescript@*/node_modules/typescript`）；
    软链会 `resolve()` 到真实目录（同一份包只算一次）。
    """
    candidates: list[tuple[Path, str]] = []
    seen: set[Path] = set()
    node_project = False

    def add(cand: Path) -> None:
        try:
            real = cand.resolve()
        except OSError:
            return
        if real in seen or not (real / "package.json").is_file():
            return
        seen.add(real)
        candidates.append((real, "repo-local"))

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames
                             if d not in _SKIP_DIRS and not d.startswith("."))
        if "package.json" in filenames:
            node_project = True
        if any(f.endswith((".ts", ".tsx")) for f in filenames):
            node_project = True
        base = Path(dirpath) / "node_modules"
        add(base / "typescript")
        pnpm = base / ".pnpm"
        if pnpm.is_dir():
            for entry in sorted(pnpm.glob("typescript@*/node_modules/typescript")):
                add(entry)
    return candidates, node_project


def _npm_global_roots() -> list[Path]:
    """`npm root -g` ⇒ 全局包目录（每个能找到的 npm 各问一次）。"""
    roots: list[Path] = []
    seen: set[Path] = set()
    npm_bins: list[str] = []
    which = shutil.which("npm")
    if which:
        npm_bins.append(which)
    for extra in ("/opt/homebrew/bin/npm", "/usr/local/bin/npm", "/usr/bin/npm"):
        if extra not in npm_bins and os.path.exists(extra):
            npm_bins.append(extra)
    for npm in npm_bins:
        try:
            proc = subprocess.run([npm, "root", "-g"], capture_output=True,
                                  text=True, encoding="utf-8",
                                  errors="replace", timeout=60)
        except (OSError, subprocess.SubprocessError):
            continue
        if proc.returncode != 0:
            continue
        for ln in proc.stdout.splitlines():
            ln = ln.strip()
            if not ln:
                continue
            try:
                real = Path(ln).resolve()
            except OSError:
                continue
            if real in seen:
                continue
            seen.add(real)
            roots.append(real)
    return roots


def _global_ts_candidates() -> list[Path]:
    """机器上全局装的 typescript ⇒ 绝对路径列表。

    `npm root -g` 只反映**当前那个 npm**（fnm / nvm / Volta 各有自己的全局
    目录）⇒ 只问它一个会「明明装了 typescript 却找不到」，整个 L3 路径
    永远走不到。所以除了它，还看几个常见的全局前缀。
    """
    prefixes = [Path("/opt/homebrew/lib/node_modules"),
                Path("/usr/local/lib/node_modules"),
                Path("/usr/lib/node_modules"),
                Path.home() / ".npm-global/lib/node_modules"]
    env_prefix = os.environ.get("NPM_CONFIG_PREFIX")
    if env_prefix:
        prefixes.append(Path(env_prefix) / "lib/node_modules")
    out: list[Path] = []
    seen: set[Path] = set()
    for d in _npm_global_roots() + prefixes:
        cand = d / "typescript"
        try:
            real = cand.resolve()
        except OSError:
            continue
        if real in seen or not (real / "package.json").is_file():
            continue
        seen.add(real)
        out.append(real)
    return out


def _backend_label(pkg_dir: Path) -> str:
    try:
        meta = json.loads((pkg_dir / "package.json").read_text(encoding="utf-8"))
        version = str(meta.get("version") or "?")
    except (OSError, ValueError):
        version = "?"
    return f"typescript@{version}"


def _run_ts_script(script: str, mode: str, pkg_dir: str, payload: dict,
                   cwd: Path, timeout: int = 180) -> tuple[int, str, str]:
    """在**临时目录**里跑一段 .mjs（绝不写进用户仓库）⇒ (退出码, stdout, stderr)。

    stdout/stderr 落**文件**而不是管道：macOS 上管道写是异步的，脚本写完
    就 `process.exit()` ⇒ 超过管道缓冲（64KB）的尾巴当场丢掉。真实模块的
    判定点 JSON 早就超过 64KB ⇒ AST 推导会静默退回 L2，整套判定点引擎
    在真实仓库上失效。文件写是同步的 ⇒ 写完即落盘，丢不了。
    """
    node = shutil.which("node")
    if not node:
        return 127, "", "找不到 node"
    extra: dict[str, str] = {}
    if payload.get("nodePath"):
        extra["NODE_PATH"] = str(payload["nodePath"])
    env = _node_env(extra)
    with tempfile.TemporaryDirectory(prefix="attest-ts-") as td:
        tdir = Path(td)
        (tdir / "attest-ts.mjs").write_text(script, encoding="utf-8")
        (tdir / "payload.json").write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        out_file = tdir / "stdout.txt"
        err_file = tdir / "stderr.txt"
        try:
            with out_file.open("wb") as fo, err_file.open("wb") as fe:
                proc = subprocess.run(
                    [node, str(tdir / "attest-ts.mjs"), mode, pkg_dir,
                     str(tdir / "payload.json")],
                    cwd=str(cwd), stdout=fo, stderr=fe,
                    timeout=timeout, env=env)
        except subprocess.TimeoutExpired:
            return 124, "", f"node 超时（{timeout}s）"
        except (OSError, ValueError) as exc:
            return 127, "", str(exc)
        out = out_file.read_bytes().decode("utf-8", errors="replace")
        err = err_file.read_bytes().decode("utf-8", errors="replace")
    return proc.returncode, out, err


def _probe_backend(kind: str, pkg_dir: Path) -> tuple[bool, str]:
    """真跑一遍合成代码 ⇒ 这份 typescript 能不能用。

    判据是**能力**不是版本号也不是路径存在（规格 §3.3.2）。
    """
    code, out, err = _run_ts_script(_probe_script(), "probe", str(pkg_dir),
                                    {"kind": kind}, cwd=pkg_dir.parent,
                                    timeout=90)
    if code != 0:
        return False, (err.strip() or out.strip())[:200]
    try:
        data = json.loads(out.strip().splitlines()[-1])
    except (ValueError, IndexError):
        return False, "探测输出不是 JSON"
    if not data.get("ok"):
        return False, str(data.get("error", ""))[:200]
    return True, str(data.get("kind", kind))


def detect_backend(root: Path, prefer: str = "auto") -> TsBackend | None:
    """找出能真解析代码的 TypeScript 后端；没有 ⇒ None（调用方退回 L2）。

    `prefer`：`auto`（B1 优先再 B2）/ `b1` / `b2` / `l2`（不探测，直接 None）。
    第一个探测成功的候选胜出。
    """
    if prefer == "l2":
        return None
    kinds = ("B1", "B2") if prefer == "auto" else (prefer.upper(),)
    candidates, node_project = _scan_repo(Path(root))
    if node_project:
        # 全局 npm 的 typescript 只在目标真的是 Node/TS 项目时才看 ——
        # 空目录不该因为机器上装了全局 typescript 就「有后端」。
        for g in _global_ts_candidates():
            candidates.append((g, "global-npm"))
    for kind in kinds:
        for pkg_dir, source in candidates:
            ok, _info = _probe_backend(kind, pkg_dir)
            if ok:
                return TsBackend(kind=kind, label=_backend_label(pkg_dir),
                                 source=source, package_dir=str(pkg_dir))
    return None


def _parse_ts_json(out: str) -> tuple[dict | None, bool]:
    """解析 TS 后端的 stdout ⇒ (数据, 输出是否被截断)。

    后端约定「一行一个 JSON」。有一行以 `{` 开头却解析不了 ⇒ 输出被截断
    （大模块的 JSON 会被管道缓冲截在 64 KiB）—— 这时要说清楚，不能把
    半截 JSON 当成「后端跑不起来」糊弄过去。
    """
    text = out.strip()
    if not text:
        return None, False
    truncated = False
    for line in reversed(text.splitlines()):
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            data = json.loads(line)
        except ValueError:
            truncated = True
            continue
        if isinstance(data, dict):
            return data, truncated
    return None, truncated


def ts_parses(backend: TsBackend, abs_path: Path, text: str,
              cwd: Path) -> tuple[bool | None, str]:
    """用 AST 后端真解析一遍 ⇒ (能不能解析, 说明)；None = 判不了。

    B1 从内存里的文本解析（不落盘）；B2 只能从磁盘读 ⇒ 先把文本写进那个
    路径，finally 里还原（内容没变就不动磁盘）。
    """
    payload: dict = {"kind": backend.kind, "abs": str(abs_path),
                     "cwd": str(cwd)}
    original: str | None = None
    try:
        if backend.kind == "B1":
            payload["text"] = text
        else:
            try:
                current = abs_path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                current = None
            if current != text:
                original = current
                abs_path.write_text(text, encoding="utf-8")
        code, out, err = _run_ts_script(_TS_SCRIPT, "parse",
                                        backend.package_dir, payload, cwd=cwd,
                                        timeout=120)
    finally:
        if original is not None:
            try:
                abs_path.write_text(original, encoding="utf-8")
            except OSError:
                pass
    if code != 0:
        return None, (err.strip() or out.strip())[:200]
    data, _truncated = _parse_ts_json(out)
    if not data or not data.get("ok"):
        return None, str((data or {}).get("error", ""))[:200]
    if data.get("parses"):
        return True, ""
    return False, "; ".join(data.get("errors") or [])[:200]


def ast_analyze(root: Path, files: list[str],
                backend: TsBackend | None) -> DpAnalysis:
    """用 TS AST 后端推导 L3 判定点。

    后端不可用或跑不起来 ⇒ 空结果（level=L1）+ note，调用方据此退回 L2。
    """
    if backend is None:
        return DpAnalysis(level="L1", backend="none", backend_source="none",
                          note="没有可用的 TypeScript AST 后端（退回 L2 词法推导）")
    payload_files: list[dict[str, str]] = []
    for rel in files:
        p = Path(root) / rel
        if p.is_file():
            payload_files.append({"abs": str(p.resolve()), "rel": rel})
    label = f"后端 {backend.label}（{backend.kind}，{backend.source}）"
    if not payload_files:
        return DpAnalysis(level="L3", backend=backend.kind,
                          backend_source=backend.source,
                          note=label + "；没有可解析的文件")
    payload = {
        "kind": backend.kind,
        "cwd": str(Path(root).resolve()),
        "files": payload_files,
        "nodePath": str(Path(backend.package_dir).parent),
        "words": {
            "resource": sorted(RESOURCE_VERBS),
            "io": sorted(IO_WORDS),
            "state": sorted(STATE_WORDS),
            "concurrency": sorted(CONCURRENCY_WORDS),
            "drop": sorted(DROP_WORDS),
        },
    }
    code, out, err = _run_ts_script(_probe_script(), "analyze",
                                    backend.package_dir, payload, Path(root))
    data, truncated = _parse_ts_json(out)
    if code != 0 or not data or not data.get("ok"):
        if truncated:
            reason = (f"后端输出被截断（只拿到 {len(out)} 字节，"
                      "JSON 不完整）")
        else:
            reason = str((data or {}).get("error") or err.strip() or out.strip())[:200]
        return DpAnalysis(level="L1", backend="none", backend_source="none",
                          note=f"{label}；后端跑不起来：{reason}")
    points = [
        DecisionPoint(file=str(p.get("file", "")),
                      anchor=str(p.get("anchor") or TOPLEVEL),
                      kind=str(p.get("kind", "")),
                      line=int(p.get("line") or 0),
                      detail=str(p.get("detail", "")))
        for p in data.get("points", [])
    ]
    an = DpAnalysis(level="L3", backend=backend.kind,
                    backend_source=backend.source, note=label, points=points)
    an.assign_ids()
    return an


def structural_points(root: Path, files: list[str],
                      backend: TsBackend | None) -> list[dict]:
    """L3 结构变异点（**事实**：算子 / 区间 / 符号，不含替换文本）。

    替换文本由 Python 算 —— 这样替换规则不用装 TypeScript 也能单测。
    后端不可用或跑不起来 ⇒ 空列表（调用方据此退回 L2）。
    """
    if backend is None:
        return []
    payload_files: list[dict[str, str]] = []
    for rel in files:
        p = Path(root) / rel
        if p.is_file():
            payload_files.append({"abs": str(p.resolve()), "rel": rel})
    if not payload_files:
        return []
    payload = {"kind": backend.kind, "cwd": str(Path(root).resolve()),
               "files": payload_files,
               "nodePath": str(Path(backend.package_dir).parent)}
    code, out, _err = _run_ts_script(_probe_script(), "mutate",
                                     backend.package_dir, payload, Path(root))
    data, _truncated = _parse_ts_json(out)
    if code != 0 or not data or not data.get("ok"):
        return []
    return [p for p in data.get("points", []) if isinstance(p, dict)]


_TS_SCRIPT = r"""
// attest 的 TypeScript AST 驱动脚本。跑在临时目录里，绝不写进用户仓库。
//   node attest-ts.mjs probe   <pkgDir> <payload.json>
//   node attest-ts.mjs analyze <pkgDir> <payload.json>
import { createRequire } from "node:module";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const [mode, pkgDir, payloadPath] = process.argv.slice(2);
const payload = JSON.parse(fs.readFileSync(payloadPath, "utf8"));
const req = createRequire(path.join(pkgDir, "package.json"));

const TOPLEVEL = "<toplevel>";
const BOUNDARY_OPS = new Set([">", "<", ">=", "<=", "==", "!=", "===", "!=="]);
const ASSIGN_OPS = new Set([
  "=", "+=", "-=", "*=", "/=", "%=", "**=", "<<=", ">>=", ">>>=",
  "&=", "|=", "^=", "&&=", "||=", "??=",
]);

// 运算符 token → 文本。**必须用正向查找**（名字 → 编号）：TypeScript 的
// SyntaxKind 里 `FirstAssignment` / `LastAssignment` 这类别名与具体 token
// 同值，反向映射 `SyntaxKind[80]` 拿到的是 "FirstAssignment" 而不是
// "EqualsToken" —— 运算符文本会整个查不到。
const OP_TOKENS = [
  ["EqualsToken", "="], ["PlusEqualsToken", "+="], ["MinusEqualsToken", "-="],
  ["AsteriskEqualsToken", "*="], ["AsteriskAsteriskEqualsToken", "**="],
  ["SlashEqualsToken", "/="], ["PercentEqualsToken", "%="],
  ["LessThanLessThanEqualsToken", "<<="],
  ["GreaterThanGreaterThanEqualsToken", ">>="],
  ["GreaterThanGreaterThanGreaterThanEqualsToken", ">>>="],
  ["BarBarEqualsToken", "||="], ["AmpersandAmpersandEqualsToken", "&&="],
  ["QuestionQuestionEqualsToken", "??="], ["BarEqualsToken", "|="],
  ["AmpersandEqualsToken", "&="], ["CaretEqualsToken", "^="],
  ["LessThanToken", "<"], ["GreaterThanToken", ">"],
  ["LessThanEqualsToken", "<="], ["GreaterThanEqualsToken", ">="],
  ["EqualsEqualsToken", "=="], ["ExclamationEqualsToken", "!="],
  ["EqualsEqualsEqualsToken", "==="], ["ExclamationEqualsEqualsToken", "!=="],
];

// 分类器用到的节点种类。探测阶段会逐个验存在 —— 少一个就换后端，
// 不能悄悄少判一类。
const REQUIRED_KINDS = [
  "IfStatement", "ConditionalExpression", "CaseClause", "DefaultClause",
  "ThrowStatement", "CatchClause", "AwaitExpression", "BinaryExpression",
  "VariableDeclaration", "VariableStatement", "CallExpression", "NewExpression",
  "FunctionDeclaration", "ClassDeclaration", "ClassExpression",
  "MethodDeclaration", "GetAccessor", "SetAccessor", "Constructor",
  "PropertyDeclaration", "PropertyAssignment", "PropertySignature",
  "ArrowFunction", "FunctionExpression", "Identifier", "PropertyAccessExpression",
  "ImportDeclaration",
];

const norm = (s) => String(s == null ? "" : s).toLowerCase().replace(/[^a-z0-9]/g, "");
const text = (n) => (n && typeof n.text === "string" ? n.text : null);

function buildOpMap(syntaxKind) {
  const out = {};
  for (const pair of OP_TOKENS) {
    const v = syntaxKind[pair[0]];
    if (typeof v === "number") out[v] = pair[1];
  }
  return out;
}

function missingKinds(syntaxKind) {
  const missing = [];
  for (const name of REQUIRED_KINDS) {
    if (typeof syntaxKind[name] !== "number") missing.push(name);
  }
  return missing;
}

// 后缀 → ScriptKind（.tsx/.jsx 要分开，否则 JSX 语法解析不了）。
function scriptKindOf(ts, f) {
  if (f.endsWith(".tsx")) return ts.ScriptKind.TSX;
  if (f.endsWith(".jsx")) return ts.ScriptKind.JSX;
  if (/\.(js|mjs|cjs)$/.test(f)) return ts.ScriptKind.JS;
  return ts.ScriptKind.TS;
}

// 诊断消息可能是链（DiagnosticMessageChain）⇒ 拍平成一行。
function diagText(d) {
  const m = d && d.messageText;
  if (m == null) return String(d);
  if (typeof m === "string") return m;
  if (typeof m.messageText === "string") return m.messageText;
  if (Array.isArray(m.next)) {
    return m.next.map((x) => diagText({ messageText: x })).join(" ");
  }
  return String(m);
}

function openB1(files) {
  const ts = req(pkgDir);
  if (!ts || typeof ts.createSourceFile !== "function") {
    throw new Error("B1: 这份 typescript 没有经典 API（createSourceFile）");
  }
  const sourceFiles = files.map((f) =>
    ts.createSourceFile(f.abs, fs.readFileSync(f.abs, "utf8"),
                        ts.ScriptTarget.Latest, true, scriptKindOf(ts, f.abs)));
  return {
    syntaxKind: ts.SyntaxKind,
    opMap: buildOpMap(ts.SyntaxKind),
    forEachChild: (n, cb) => ts.forEachChild(n, cb),
    nodeFlags: ts.NodeFlags,
    sourceFiles,
    close: () => {},
  };
}

function openB2(files, cwd) {
  const astMod = req(req.resolve("typescript/unstable/ast"));
  const syncMod = req(req.resolve("typescript/unstable/sync"));
  const API = syncMod.API;
  if (typeof API !== "function") throw new Error("B2: unstable/sync 没有 API");
  const api = new API({ cwd });
  const abs = files.map((f) => path.resolve(f.abs));
  const snap = api.updateSnapshot({ openFiles: abs });
  const sourceFiles = [];
  for (const a of abs) {
    const proj = snap.getDefaultProjectForFile(a);
    if (!proj) throw new Error("B2: " + a + " 没有所属项目");
    const sf = proj.program.getSourceFile(a);
    if (!sf) throw new Error("B2: " + a + " 没解析出源文件");
    sourceFiles.push(sf);
  }
  return {
    syntaxKind: astMod.SyntaxKind,
    opMap: buildOpMap(astMod.SyntaxKind),
    forEachChild: (n, cb) => n.forEachChild(cb),
    nodeFlags: astMod.NodeFlags,
    sourceFiles,
    close: () => { try { api.close(); } catch (e) { /* 关不掉就算了 */ } },
  };
}

function makeWords() {
  const w = payload.words || {};
  const set = (k) => new Set((w[k] || []).map(norm));
  return { resource: set("resource"), io: set("io"), state: set("state"),
           concurrency: set("concurrency"), drop: set("drop") };
}

function isFnExpr(SK, n) {
  if (!n) return false;
  return n.kind === SK.ArrowFunction || n.kind === SK.FunctionExpression;
}

// 锚点 = 最近的具名作用域（函数 / 类 / 方法）。
function anchorFor(SK, node, anchor) {
  if (node.kind === SK.FunctionDeclaration || node.kind === SK.ClassDeclaration ||
      node.kind === SK.ClassExpression || node.kind === SK.MethodDeclaration ||
      node.kind === SK.GetAccessor || node.kind === SK.SetAccessor ||
      node.kind === SK.Constructor) {
    return text(node.name) || anchor;
  }
  if (node.kind === SK.PropertyDeclaration ||
      node.kind === SK.PropertyAssignment ||
      node.kind === SK.PropertySignature) {
    return isFnExpr(SK, node.initializer) ? (text(node.name) || anchor) : anchor;
  }
  if (node.kind === SK.VariableDeclaration) {
    return isFnExpr(SK, node.initializer) ? (text(node.name) || anchor) : anchor;
  }
  return anchor;
}

// 谓词类方法：参数是「对每个元素成立的判断」⇒ 谓词取反有判定力。
const PREDICATE_METHODS = new Set([
  "filter", "find", "findIndex", "some", "every", "findLast", "findLastIndex",
]);

// catch 体清空前先做「声明-使用」检查：体里声明的名字在块外还被引用 ⇒
// 清空会让那个名字变成未定义 ⇒ 变异测的是语法而不是行为，放弃该点。
function catchBodySafe(backend, sf, block, startOf, endOf) {
  const SK = backend.syntaxKind;
  const bStart = startOf(block);
  const bEnd = endOf(block);
  const decls = new Map();
  const collect = (n) => {
    if (n.kind === SK.VariableDeclaration || n.kind === SK.FunctionDeclaration ||
        n.kind === SK.ClassDeclaration) {
      const nm = text(n.name);
      if (nm) decls.set(nm, n.name);
    }
    backend.forEachChild(n, collect);
  };
  backend.forEachChild(block, collect);
  if (!decls.size) return true;
  let usedOutside = false;
  const scan = (n) => {
    if (usedOutside) return;
    if (n.kind === SK.Identifier) {
      const declNode = decls.get(text(n));
      if (declNode && n !== declNode) {
        const p = n.parent;
        const isPropName = !!(p && p.kind === SK.PropertyAccessExpression &&
                              p.name === n);
        if (!isPropName) {
          const pos = startOf(n);
          if (pos < bStart || pos >= bEnd) { usedOutside = true; return; }
        }
      }
    }
    backend.forEachChild(n, scan);
  };
  backend.forEachChild(sf, scan);
  return !usedOutside;
}

// 结构变异点：只报**事实**（算子 / 区间 / 符号 / 少量上下文），
// 替换文本由 Python 算 —— 替换规则因此不用装 TypeScript 也能单测。
function collectStructural(backend, files) {
  const SK = backend.syntaxKind;
  const out = [];
  for (let i = 0; i < files.length; i++) {
    const sf = backend.sourceFiles[i];
    const rel = files[i].rel;
    const src = String(sf.text || "");
    const startOf = (n) => {
      try { const p = n.getStart(sf); if (typeof p === "number") return p; } catch (e) {}
      try { const p = n.getStart(); if (typeof p === "number") return p; } catch (e) {}
      return typeof n.pos === "number" ? n.pos : 0;
    };
    const endOf = (n) => {
      try { const p = n.getEnd(); if (typeof p === "number") return p; } catch (e) {}
      return typeof n.end === "number" ? n.end : 0;
    };
    const slice = (a, b) => src.slice(a, b);
    const lineOf = (n) => {
      const pos = startOf(n);
      try { return sf.getLineAndCharacterOfPosition(pos).line + 1; } catch (e) { return 0; }
    };
    const emit = (op, start, end, node, anchor, extra) => {
      const rec = { op: op, start: start, end: end, line: lineOf(node),
                    symbol: anchor, file: rel };
      if (extra) for (const k of Object.keys(extra)) rec[k] = extra[k];
      out.push(rec);
    };

    const visit = (node, anchor) => {
      const k = node.kind;
      if (k === SK.ThrowStatement) {
        const s = startOf(node);
        if (slice(s, s + 5) === "throw") emit("drop-throw", s, s + 5, node, anchor);
      } else if (k === SK.CallExpression || k === SK.NewExpression) {
        const parent = node.parent;
        const calleeNode = node.expression;
        const calleeKind = calleeNode ? calleeNode.kind : -1;
        const inMember = !!(parent && parent.kind === SK.PropertyAccessExpression &&
                            parent.expression === node);
        const inTag = !!(parent && parent.kind === SK.TaggedTemplateExpression &&
                         parent.tag === node);
        const inCallee = !!(parent && parent.kind === SK.CallExpression &&
                            parent.expression === node);
        const isSuper = calleeKind === SK.SuperKeyword;
        const isImport = calleeKind === SK.ImportKeyword;
        if (!inMember && !inTag && !inCallee && !isSuper && !isImport) {
          emit("drop-call", startOf(node), endOf(node), node, anchor);
        }
        if (node.arguments && node.arguments.length === 1 && calleeNode &&
            calleeNode.kind === SK.PropertyAccessExpression) {
          const mName = text(calleeNode.name);
          const arg = mName && PREDICATE_METHODS.has(mName) ? node.arguments[0] : null;
          if (arg && arg.kind === SK.ArrowFunction) {
            const params = arg.parameters || [];
            const p = params.length === 1 ? params[0] : null;
            const pname = p && p.name ? text(p.name) : null;
            const aStart = startOf(arg);
            const aText = slice(aStart, endOf(arg));
            const body = arg.body;
            const isAsync = slice(aStart, aStart + 6).indexOf("async") === 0;
            const hasComment = aText.indexOf("//") >= 0 || aText.indexOf("/*") >= 0;
            if (pname && body && body.kind !== SK.Block && !isAsync &&
                !hasComment && aText.indexOf(",") < 0) {
              emit("predicate-negate", startOf(body), endOf(body), node, anchor,
                   { arrow: aText, param: pname });
            }
          }
        }
      } else if (k === SK.CatchClause) {
        const block = node.block;
        if (block && catchBodySafe(backend, sf, block, startOf, endOf)) {
          emit("catch-drop", startOf(block), endOf(block), node, anchor);
        }
      }
      const child = anchorFor(SK, node, anchor);
      backend.forEachChild(node, (n) => visit(n, child));
    };

    visit(sf, TOPLEVEL);
  }
  return out;
}

function collectImports(backend, sf) {
  const SK = backend.syntaxKind;
  const names = new Set();
  const walk = (node) => {
    if (node.kind === SK.ImportDeclaration) {
      const clause = node.importClause;
      if (clause) {
        if (clause.name) names.add(text(clause.name));
        const b = clause.namedBindings;
        if (b) {
          if (b.elements) for (const el of b.elements) names.add(text(el.name));
          if (b.name) names.add(text(b.name));
        }
      }
    }
    backend.forEachChild(node, walk);
  };
  backend.forEachChild(sf, walk);
  return names;
}

function analyzeFiles(backend, files) {
  const SK = backend.syntaxKind;
  const words = makeWords();
  const out = [];

  for (let i = 0; i < files.length; i++) {
    const sf = backend.sourceFiles[i];
    const rel = files[i].rel;
    const imported = collectImports(backend, sf);

    const lineOf = (node) => {
      let pos = -1;
      try { pos = node.getStart(sf); } catch (e) {
        try { pos = node.getStart(); } catch (e2) { pos = node.pos; }
      }
      if (typeof pos !== "number" || pos < 0) return 0;
      try { return sf.getLineAndCharacterOfPosition(pos).line + 1; } catch (e) { return 0; }
    };

    const emit = (kind, node, detail, anchor) => {
      out.push({ file: rel, anchor: anchor, kind: kind,
                 line: lineOf(node), detail: String(detail || "").slice(0, 80) });
    };

    const opText = (n) => {
      const t = n.operatorToken;
      return t ? (backend.opMap[t.kind] || "") : "";
    };
    const targetName = (n) => {
      if (!n) return null;
      if (n.kind === SK.Identifier) return text(n);
      if (n.kind === SK.PropertyAccessExpression) return text(n.name);
      return null;
    };
    const callee = (node) => {
      const e = node.expression;
      if (!e) return { name: null, obj: null };
      if (e.kind === SK.Identifier) return { name: text(e), obj: null };
      if (e.kind === SK.PropertyAccessExpression) {
        const obj = e.expression.kind === SK.Identifier ? text(e.expression) : null;
        return { name: text(e.name), obj: obj };
      }
      return { name: null, obj: null };
    };
        const visit = (node, anchor) => {
      const k = node.kind;
      if (k === SK.IfStatement) {
        emit("branch", node, "if", anchor);
        if (node.elseStatement) emit("branch", node.elseStatement, "else", anchor);
      } else if (k === SK.ConditionalExpression) {
        emit("branch", node, "?:", anchor);
      } else if (k === SK.CaseClause) {
        emit("branch", node, "case", anchor);
      } else if (k === SK.DefaultClause) {
        emit("branch", node, "default", anchor);
      } else if (k === SK.ThrowStatement) {
        emit("error-path", node, "throw", anchor);
      } else if (k === SK.CatchClause) {
        emit("error-path", node, "catch", anchor);
      } else if (k === SK.AwaitExpression) {
        emit("concurrency", node, "await", anchor);
      } else if (k === SK.BinaryExpression) {
        const op = opText(node);
        if (BOUNDARY_OPS.has(op)) {
          emit("boundary", node, op, anchor);
        } else if (ASSIGN_OPS.has(op)) {
          const t = targetName(node.left);
          if (t && words.state.has(norm(t))) emit("state", node.left, t, anchor);
        }
      } else if (k === SK.VariableDeclaration) {
        const nm = text(node.name);
        if (nm && node.initializer && words.state.has(norm(nm))) {
          emit("state", node, nm, anchor);
        }
      } else if (k === SK.VariableStatement) {
        if (anchor === TOPLEVEL) {
          const flags = (node.declarationList && node.declarationList.flags) || 0;
          const isConst = !!(backend.nodeFlags && (flags & backend.nodeFlags.Const));
          if (!isConst) emit("concurrency", node, "module-level let/var", anchor);
        }
      } else if (k === SK.CallExpression || k === SK.NewExpression) {
        const c = callee(node);
        if (c.name) {
          const n = norm(c.name);
          const shown = c.obj ? c.obj + "." + c.name + "()" : c.name + "()";
          if (words.resource.has(n)) emit("resource", node, shown, anchor);
          if (words.io.has(n)) emit("boundary-io", node, shown, anchor);
          if (words.concurrency.has(n)) emit("concurrency", node, shown, anchor);
          if (words.drop.has(n)) emit("drop", node, shown, anchor);
          if (c.obj === "Promise" && n === "all") {
            emit("concurrency", node, "Promise.all()", anchor);
          }
          if (imported.has(c.name)) emit("boundary-io", node, shown, anchor);
        }
      }
      const child = anchorFor(SK, node, anchor);
      backend.forEachChild(node, (n) => visit(n, child));
    };

    visit(sf, TOPLEVEL);
  }
  return out;
}

function probeKind(kind, file, dir) {
  try {
    const backend = kind === "B1" ? openB1([{ abs: file }])
                                  : openB2([{ abs: file }], dir);
    const missing = missingKinds(backend.syntaxKind);
    if (missing.length) {
      backend.close();
      return { ok: false, error: "少这些 SyntaxKind：" + missing.join(",") };
    }
    if (!backend.nodeFlags || typeof backend.nodeFlags.Const !== "number") {
      backend.close();
      return { ok: false, error: "没有 NodeFlags.Const" };
    }
    let found = false;
    const walk = (n) => {
      if (n.kind === backend.syntaxKind.IfStatement) found = true;
      backend.forEachChild(n, walk);
    };
    for (const sf of backend.sourceFiles) walk(sf);
    backend.close();
    if (!found) return { ok: false, error: "解析了但没找到 IfStatement" };
    return { ok: true, kind: kind };
  } catch (e) {
    return { ok: false, error: String((e && e.message) || e) };
  }
}

// stdout 是**异步**管道（macOS）⇒ 写完不能立刻 exit：没刷完的数据会被丢掉
// （大模块的 JSON 截在 64 KiB ⇒ 解析失败 ⇒ 静默退回 L2，能力凭空少一档）。
// 退出放进写回调 ⇒ 数据落地后才退。
function emit(obj, code) {
  process.stdout.write(JSON.stringify(obj) + "\n", () => process.exit(code));
}

if (mode === "probe") {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "attest-ts-probe-"));
  const file = path.join(dir, "probe.ts");
  fs.writeFileSync(file, "if (x >= 1) { throw new Error('e'); }\n", "utf8");
  const r = probeKind(payload.kind, file, dir);
  emit(r, r.ok ? 0 : 1);
} else if (mode === "analyze") {
  try {
    const backend = payload.kind === "B1"
      ? openB1(payload.files)
      : openB2(payload.files, payload.cwd);
    const points = analyzeFiles(backend, payload.files);
    backend.close();
    emit({ ok: true, points: points }, 0);
  } catch (e) {
    emit({ ok: false, error: String((e && e.message) || e) }, 1);
  }
} else if (mode === "mutate") {
  try {
    const backend = payload.kind === "B1"
      ? openB1(payload.files)
      : openB2(payload.files, payload.cwd);
    const points = collectStructural(backend, payload.files);
    backend.close();
    emit({ ok: true, points: points }, 0);
  } catch (e) {
    emit({ ok: false, error: String((e && e.message) || e) }, 1);
  }
} else if (mode === "parse") {
  // 变异体能不能解析：B1 从内存里的文本解析，B2 只能从磁盘读
  // （Python 侧负责把文本写进去再还原）。
  try {
    const abs = payload.abs;
    let diags = [];
    if (payload.kind === "B1") {
      const ts = req(pkgDir);
      const sf = ts.createSourceFile(abs, payload.text, ts.ScriptTarget.Latest,
                                     true, scriptKindOf(ts, abs));
      diags = sf.parseDiagnostics || [];
    } else {
      const syncMod = req(req.resolve("typescript/unstable/sync"));
      const api = new syncMod.API({ cwd: payload.cwd });
      try {
        const snap = api.updateSnapshot({ openFiles: [abs] });
        const proj = snap.getDefaultProjectForFile(abs);
        if (!proj) throw new Error("B2: " + abs + " 没有所属项目");
        diags = proj.program.getSyntacticDiagnostics(abs) || [];
      } finally {
        try { api.close(); } catch (e) { /* 关不掉就算了 */ }
      }
    }
    emit({ ok: true, parses: diags.length === 0,
           errors: diags.slice(0, 3).map(diagText) }, 0);
  } catch (e) {
    emit({ ok: false, error: String((e && e.message) || e) }, 1);
  }
} else {
  emit({ ok: false, error: "未知模式 " + mode }, 1);
}
"""


def _probe_script() -> str:
    """探测/分析用的 .mjs 源码（一份，两个模式）。"""
    return _TS_SCRIPT


# ------------------------------------------------------------------ Python L3

# Python 的比较运算符与 TS 的边界运算符一一对应；`in` / `is` 不算边界
# （规格 §3.4 的边界运算符表里没有它们）。
_PY_BOUNDARY_OPS = (ast.Eq, ast.NotEq, ast.Lt, ast.LtE, ast.Gt, ast.GtE)


def _py_dotted(node: ast.AST) -> str:
    """`a.b.c` 还原成点号串；还原不了 ⇒ ""。"""
    parts: list[str] = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if isinstance(cur, ast.Name):
        parts.append(cur.id)
        return ".".join(reversed(parts))
    return ""


def _py_imported_names(tree: ast.Module) -> set[str]:
    """模块里被 import 进来的名字（`import a` → a；`from m import b as c` → c）。"""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                names.add(alias.asname or alias.name)
    return names


class _PyVisitor(ast.NodeVisitor):
    """Python 侧的判定点收集器。锚点 = 最近的具名作用域（函数 / 类）。"""

    def __init__(self, file: str, imported: set[str]) -> None:
        self.file = file
        self.imported = imported
        self.points: list[DecisionPoint] = []
        self._scopes: list[str] = []

    @property
    def anchor(self) -> str:
        return self._scopes[-1] if self._scopes else TOPLEVEL

    def _emit(self, kind: str, line: int, detail: str = "") -> None:
        self.points.append(DecisionPoint(file=self.file, anchor=self.anchor,
                                         kind=kind, line=line, detail=detail))

    # -- 作用域 --
    def _scope(self, node: ast.AST, name: str) -> None:
        self._scopes.append(name)
        self.generic_visit(node)
        self._scopes.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._scope(node, node.name)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._scope(node, node.name)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._scope(node, node.name)

    # -- 分支 --
    def visit_If(self, node: ast.If) -> None:
        self._emit("branch", node.lineno, "if")
        self.generic_visit(node)

    def visit_IfExp(self, node: ast.IfExp) -> None:
        self._emit("branch", node.lineno, "if 表达式")
        self.generic_visit(node)

    def visit_Match(self, node: ast.Match) -> None:
        for case in node.cases:
            # `ast.match_case` 没有 lineno ⇒ 借分支体第一行的行号。
            line = case.body[0].lineno if case.body else node.lineno
            self._emit("branch", line, "match 分支")
        self.generic_visit(node)

    # -- 边界与错误路径 --
    def visit_Compare(self, node: ast.Compare) -> None:
        if any(isinstance(op, _PY_BOUNDARY_OPS) for op in node.ops):
            self._emit("boundary", node.lineno, "比较")
        self.generic_visit(node)

    def visit_Raise(self, node: ast.Raise) -> None:
        self._emit("error-path", node.lineno, "raise")
        self.generic_visit(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        self._emit("error-path", node.lineno, "except")
        self.generic_visit(node)

    # -- 资源 --
    def visit_With(self, node: ast.With) -> None:
        self._emit("resource", node.lineno, "with")
        self.generic_visit(node)

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        self._emit("resource", node.lineno, "async with")
        self.generic_visit(node)

    # -- 状态 --
    def _state_target(self, target: ast.AST) -> None:
        name = None
        if isinstance(target, ast.Attribute):
            name = target.attr
        elif isinstance(target, ast.Name):
            name = target.id
        if name and _norm(name) in _STATE_NORM:
            self._emit("state", getattr(target, "lineno", 0), name)

    def visit_Assign(self, node: ast.Assign) -> None:
        for target in node.targets:
            self._state_target(target)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None:
            self._state_target(node.target)
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self._state_target(node.target)
        self.generic_visit(node)

    # -- 并发 --
    def visit_Await(self, node: ast.Await) -> None:
        self._emit("concurrency", node.lineno, "await")
        self.generic_visit(node)

    # -- 丢弃 --
    def _comprehension(self, node: ast.AST) -> None:
        # 过滤条件挂在每个 generator 上（`ast.comprehension.ifs`），不在
        # ListComp 节点上 —— 一个推导式记一条 drop（对上 TS 的 `filter()` 一次调用）。
        for gen in getattr(node, "generators", ()):
            if gen.ifs:
                self._emit("drop", gen.ifs[0].lineno, "推导式过滤")
        self.generic_visit(node)

    visit_ListComp = _comprehension
    visit_SetComp = _comprehension
    visit_GeneratorExp = _comprehension
    visit_DictComp = _comprehension

    # -- 调用 --
    def visit_Call(self, node: ast.Call) -> None:
        dotted = _py_dotted(node.func)
        last = dotted.rsplit(".", 1)[-1] if dotted else ""
        norm = _norm(last)
        shown = f"{dotted}()" if dotted else "<调用>()"
        if norm:
            # 一个名字可能同时命中几张表（如 `acquire` 既是资源动词又是并发词），
            # 跟 TS 侧一样**各自记一条**，不互斥。
            if norm in _RESOURCE_NORM:
                self._emit("resource", node.lineno, shown)
            if norm in _IO_NORM:
                self._emit("boundary-io", node.lineno, shown)
            if norm in _CONCURRENCY_NORM:
                self._emit("concurrency", node.lineno, shown)
            if norm in _DROP_NORM:
                self._emit("drop", node.lineno, shown)
        if dotted.endswith("gather"):
            self._emit("concurrency", node.lineno, shown)
        if last and last in self.imported:
            self._emit("boundary-io", node.lineno, shown)
        self.generic_visit(node)


def python_ast_analyze(file: str, source: str) -> DpAnalysis:
    """Python 源码的 L3 判定点 —— 标准库 `ast`，不依赖任何第三方包。

    解析不了就降级成 L1 并写明原因 —— 静默给一张空清单会被读成「这个文件
    没有判定点」，那是假绿。
    """
    an = DpAnalysis(level="L3", backend="py-ast", backend_source="stdlib")
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        an.level = "L1"
        an.note = f"Python 源码解析失败（第 {exc.lineno} 行）：{exc.msg}"
        return an
    visitor = _PyVisitor(file, _py_imported_names(tree))
    visitor.visit(tree)
    an.points = visitor.points
    an.assign_ids()
    return an


# ------------------------------------------------------- 一组文件的合并推导


def _read_source(root: Path, rel: str) -> str:
    try:
        return (Path(root) / rel).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _join_notes(notes: list[str]) -> str:
    """相同的说明只说一次，重复的带上次数。

    每个 Python 文件成功解析都会记一条「L3 Python ast 推导」—— 一百个文件
    就是一百句一模一样的话，报告里读不下去。
    """
    counts: dict[str, int] = {}
    order: list[str] = []
    for n in notes:
        if n not in counts:
            counts[n] = 0
            order.append(n)
        counts[n] += 1
    parts = [n if counts[n] == 1 else f"{n}（{counts[n]} 个文件）" for n in order]
    return "；".join(parts) or "没有可分析的源文件"


def analyze_files(root: Path, files: list[str],
                  prefer: str = "auto") -> DpAnalysis:
    """一组源文件（TS/JS + Python 混合）的判定点推导。

    等级取**各组里最低的那档**：TS 那边只要退回 L2，整体就只是 L2
    （规格 §7.4：L2 下新判据一律不硬判 ⇒ 缺能力绝不报绿）。
    """
    root = Path(root)
    ts_files = [f for f in files if not f.endswith(".py")]
    py_files = [f for f in files if f.endswith(".py")]
    points: list[DecisionPoint] = []
    notes: list[str] = []
    rank = 3
    backend = "none"
    backend_source = "none"

    if ts_files:
        be = detect_backend(root, prefer)
        an = ast_analyze(root, ts_files, be) if be else None
        if an is not None and an.points:
            points.extend(an.points)
            notes.append(f"L3 AST 推导（{an.note}）")
            backend, backend_source = an.backend, an.backend_source
        else:
            for rel in ts_files:
                points.extend(lexical_analyze(rel, _read_source(root, rel)))
            notes.append("L2 词法推导（启发式，可能有误报）；"
                         + (an.note if an is not None else "没有可用的 TS AST 后端"))
            rank = min(rank, 2)
            backend, backend_source = "lexical", "none"
    if py_files:
        parsed = False
        for rel in py_files:
            an = python_ast_analyze(rel, _read_source(root, rel))
            points.extend(an.points)
            if an.level == "L3":
                parsed = True
                notes.append("L3 Python ast 推导")
            else:
                notes.append(f"Python 源码解析失败（{an.note}）")
        if parsed:
            if backend == "none":
                backend, backend_source = "py-ast", "stdlib"
        else:
            rank = min(rank, 1)

    out = DpAnalysis(level=("L3" if rank == 3 else "L2" if rank == 2 else "L1"),
                     backend=backend, backend_source=backend_source,
                     note=_join_notes(notes),
                     points=points)
    out.assign_ids()
    return out
