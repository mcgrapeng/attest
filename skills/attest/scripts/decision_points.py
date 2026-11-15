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

import json
import os
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

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

_STRIP_RE = re.compile(
    r"'(?:\\.|[^'\\\n])*'"
    r'|"(?:\\.|[^"\\\n])*"'
    r"|`(?:\\.|[^`\\])*`"
    r"|//[^\n]*"
    r"|/\*.*?\*/",
    re.S,
)


def _blank(m: re.Match[str]) -> str:
    """把匹配段换成空格，但**保留换行** —— 行号是 DP 的报告字段，塌了就错。"""
    return re.sub(r"[^\n]", " ", m.group(0))


def _strip_js_code(src: str) -> str:
    """把 JS/TS 的字符串与注释换成空格（**保持偏移量与行号不变**）。

    与 `mutation_check._strip_js_code` 同口径（字符串在前 ⇒ 字符串里的 `//`
    不会被当注释），改一处要改两处。本模块不 import mutation_check ——
    免得形成循环依赖；两份都要保换行（mutation_check 那份原本会把多行字符串
    压成一行，`find_mutations` 的行号因此会算错）。
    """
    return _STRIP_RE.sub(_blank, src)


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


def _scan_lines(lines: list[str]) -> tuple[list[_Scope], list[tuple[int, str, str, str]]]:
    """扫一遍源码行 ⇒ (最终作用域栈, [(行号, kind, 锚点, 详情), ...])。

    作用域靠花括号深度跟踪，不是「往上看找最近一个函数」——
    后者会把已经闭合的函数里的调用算到那个函数头上。
    """
    stack: list[_Scope] = []
    hits: list[tuple[int, str, str, str]] = []
    depth = 0
    for lineno, line in enumerate(lines, start=1):
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


def lexical_analyze(file: str, source: str) -> list[DecisionPoint]:
    """L2 词法推导：不需要任何后端，任何机器上都能跑。

    有误报漏报（启发式），报告里必须标明「词法推导，可能有误报」。
    """
    code = _strip_js_code(source)
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
    """子进程环境白名单（口径与 `readiness.child_env` 一致）。

    本模块不 import readiness —— 将来 readiness 要 import 本模块，会成环。
    """
    allow_exact = frozenset({
        "PATH", "HOME", "LANG", "LANGUAGE", "TZ", "TMPDIR", "TEMP", "TMP",
        "SHELL", "USER", "LOGNAME", "TERM", "SYSTEMROOT", "COMSPEC", "CI",
    })
    allow_prefix = ("LC_", "NODE_", "NPM_", "PNPM_", "YARN_")
    env = {k: v for k, v in os.environ.items()
           if k in allow_exact or k.startswith(allow_prefix)}
    env["PYTHONUTF8"] = "1"
    if extra:
        env.update(extra)
    return env


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


def _global_ts_candidate() -> Path | None:
    """全局 npm 里的 typescript（`npm root -g`）⇒ 绝对路径。"""
    npm = shutil.which("npm")
    if not npm:
        return None
    try:
        proc = subprocess.run([npm, "root", "-g"], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    lines = [ln.strip() for ln in proc.stdout.splitlines() if ln.strip()]
    if not lines:
        return None
    cand = Path(lines[-1]) / "typescript"
    if not (cand / "package.json").is_file():
        return None
    try:
        return cand.resolve()
    except OSError:
        return None


def _backend_label(pkg_dir: Path) -> str:
    try:
        meta = json.loads((pkg_dir / "package.json").read_text(encoding="utf-8"))
        version = str(meta.get("version") or "?")
    except (OSError, ValueError):
        version = "?"
    return f"typescript@{version}"


def _run_ts_script(script: str, mode: str, pkg_dir: str, payload: dict,
                   cwd: Path, timeout: int = 180) -> tuple[int, str, str]:
    """在**临时目录**里跑一段 .mjs（绝不写进用户仓库）⇒ (退出码, stdout, stderr)。"""
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
        try:
            proc = subprocess.run(
                [node, str(tdir / "attest-ts.mjs"), mode, pkg_dir,
                 str(tdir / "payload.json")],
                cwd=str(cwd), capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=timeout, env=env)
        except subprocess.TimeoutExpired:
            return 124, "", f"node 超时（{timeout}s）"
        except (OSError, ValueError) as exc:
            return 127, "", str(exc)
    return proc.returncode, proc.stdout, proc.stderr


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
        g = _global_ts_candidate()
        if g is not None:
            candidates.append((g, "global-npm"))
    for kind in kinds:
        for pkg_dir, source in candidates:
            ok, _info = _probe_backend(kind, pkg_dir)
            if ok:
                return TsBackend(kind=kind, label=_backend_label(pkg_dir),
                                 source=source, package_dir=str(pkg_dir))
    return None


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
    data: dict | None = None
    if out.strip():
        try:
            data = json.loads(out.strip().splitlines()[-1])
        except ValueError:
            data = None
    if code != 0 or not data or not data.get("ok"):
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

function openB1(files) {
  const ts = req(pkgDir);
  if (!ts || typeof ts.createSourceFile !== "function") {
    throw new Error("B1: 这份 typescript 没有经典 API（createSourceFile）");
  }
  const scriptKind = (f) => {
    if (f.endsWith(".tsx")) return ts.ScriptKind.TSX;
    if (f.endsWith(".jsx")) return ts.ScriptKind.JSX;
    if (/\.(js|mjs|cjs)$/.test(f)) return ts.ScriptKind.JS;
    return ts.ScriptKind.TS;
  };
  const sourceFiles = files.map((f) =>
    ts.createSourceFile(f.abs, fs.readFileSync(f.abs, "utf8"),
                        ts.ScriptTarget.Latest, true, scriptKind(f.abs)));
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

    const isFnExpr = (n) => {
      if (!n) return false;
      return n.kind === SK.ArrowFunction || n.kind === SK.FunctionExpression;
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
    const anchorFor = (node, anchor) => {
      if (node.kind === SK.FunctionDeclaration || node.kind === SK.ClassDeclaration ||
          node.kind === SK.ClassExpression || node.kind === SK.MethodDeclaration ||
          node.kind === SK.GetAccessor || node.kind === SK.SetAccessor ||
          node.kind === SK.Constructor) {
        return text(node.name) || anchor;
      }
      if (node.kind === SK.PropertyDeclaration ||
          node.kind === SK.PropertyAssignment ||
          node.kind === SK.PropertySignature) {
        return isFnExpr(node.initializer) ? (text(node.name) || anchor) : anchor;
      }
      if (node.kind === SK.VariableDeclaration) {
        return isFnExpr(node.initializer) ? (text(node.name) || anchor) : anchor;
      }
      return anchor;
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
      const child = anchorFor(node, anchor);
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

if (mode === "probe") {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "attest-ts-probe-"));
  const file = path.join(dir, "probe.ts");
  fs.writeFileSync(file, "if (x >= 1) { throw new Error('e'); }\n", "utf8");
  const r = probeKind(payload.kind, file, dir);
  process.stdout.write(JSON.stringify(r) + "\n");
  process.exit(r.ok ? 0 : 1);
} else if (mode === "analyze") {
  try {
    const backend = payload.kind === "B1"
      ? openB1(payload.files)
      : openB2(payload.files, payload.cwd);
    const points = analyzeFiles(backend, payload.files);
    backend.close();
    process.stdout.write(JSON.stringify({ ok: true, points: points }) + "\n");
    process.exit(0);
  } catch (e) {
    process.stdout.write(JSON.stringify({
      ok: false, error: String((e && e.message) || e) }) + "\n");
    process.exit(1);
  }
} else {
  process.stdout.write(JSON.stringify({ ok: false, error: "未知模式 " + mode }) + "\n");
  process.exit(1);
}
"""


def _probe_script() -> str:
    """探测/分析用的 .mjs 源码（一份，两个模式）。"""
    return _TS_SCRIPT
