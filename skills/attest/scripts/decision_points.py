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

import re
from dataclasses import dataclass, field

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
_RE_BRANCH = re.compile(r"\belse\s+if\b|\bif\b|\belse\b|\bcase\b|\bdefault\s*:"
                        r"|\?(?![.?:])")
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
    for pattern in (_RE_CLASS, _RE_FUNCTION, _RE_ARROW_ASSIGN, _RE_PROP_ARROW):
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
