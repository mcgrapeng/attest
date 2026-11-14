#!/usr/bin/env python3
"""oracle 独立性启发式检查：期望值是不是取自被测对象自己。

    python3 scripts/oracle_check.py <测试文件或目录>... --repo <项目根> [--json]

Howden 1968 的 oracle 问题：你凭什么知道这次测试的期望值是多少？判据来自被测对象
自身就是同义反复（tautology），测试再多也不产生信息。

本脚本扫测试文件，标出三类"期望值取自被测对象自身"的形态：

  1. 自比较       —— 同一个断言里，被测对象的同一个调用出现两次，两边比相等。
  2. 期望值取自被测对象 —— 先把被测对象的输出存进变量，再拿它当期望值比较。
  3. 快照自比对   —— 同一个值既做了基线（toMatchSnapshot）又做了被测比较。

被测对象调用怎么认：不试图做调用图分析。只用保守启发式 —— 调用目标是本仓库源码里
导出的符号（`export function foo` / `export const foo` / Python 的公开 `def foo` /
Rust 的 `pub fn foo` / Go 的 `func Foo`），且该调用出现在断言上下文里。

判定是**提醒**，永远不阻塞：启发式会误报，真问题不能被噪声淹没。
只读：不改用户仓库的任何文件。

已知边界（宁漏勿噪的代价，说清楚免得被当成全覆盖）：

  - 只认**裸标识符调用**（`calc(1)`）。成员调用（`api.calc(1)`）一律不认 ——
    期望值来自外部 API 的情况占多数，认了就是噪声。
  - "自比较"要求两侧是**字面完全相同**的调用（`calc(1)` vs `calc(1)`）。
    `calc(a)` vs `calc(b)` 不算：那种比较有时是合法的性质测试。
  - "期望值取自被测对象"要求变量的**最近一次赋值**取自被测对象；变量被重新
    赋成字面量后就不再算。
  - 跨文件的期望值传递（helper 文件里造的期望值）认不出来。
  - 幂等 / 不变性 / 确定性这类断言（"同样的输入给同样的输出"）会被报出来 ——
    它们按定义就是把被测对象的输出当期望值。人可以逐条驳回。

参考：docs/testing-philosophy-review.md 标尺 2 与 R3。
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import sys
from pathlib import Path

# ------------------------------------------------------------------ 常量

SKIP_DIRS = {"node_modules", ".git", "dist", "build", "coverage", "out",
             "target", "vendor", ".venv", "venv", "__pycache__", ".next",
             ".turbo", ".cache"}

# 认作测试文件的文件名（默认扫描用）
TEST_FILE_PATTERNS = ("*.test.*", "*.spec.*", "*.itest.*",
                      "test_*.py", "*_test.py", "*_test.go", "*_test.rs")

JS_EXT = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}
CODE_EXT = JS_EXT | {".py", ".rs", ".go"}

# 通用名 —— 到处都有的函数名，不足以当"被测对象"的标志
GENERIC_NAMES = {
    "error", "errormsg", "errormessage", "helper", "helpers", "util",
    "utils", "config", "options", "result", "results", "data", "value",
    "values", "item", "items", "name", "names", "type", "types", "id",
    "ids", "key", "keys", "index", "main", "default", "init", "create",
    "update", "delete", "get", "set", "list", "run", "start", "stop",
    "test", "check", "parse", "format", "build", "load", "save", "handle",
    "process", "execute", "map", "filter", "reduce", "clone", "copy",
    "move", "sort", "find", "search", "print", "println", "printf",
    "log", "logger", "assert", "expect", "should", "describe", "it",
    "before", "after", "beforeeach", "aftereach", "new", "len", "size",
    "str", "int", "bool", "float", "dict", "set_", "tuple", "min", "max",
    "abs", "round", "all", "any", "some", "none", "wrap", "unwrap",
    "tostring", "to_string",
}

# 导出符号的识别
_JS_EXPORT_RE = re.compile(
    r"^[ \t]*export\s+(?:default\s+)?(?:async\s+)?"
    r"(?:function\*?|const|let|var|class)\s+([A-Za-z_$][\w$]*)", re.M)
_JS_EXPORT_LIST_RE = re.compile(r"^[ \t]*export\s*\{([^}]*)\}", re.M)
_PY_EXPORT_RE = re.compile(
    r"^(?:async\s+)?(?:def|class)\s+([A-Za-z_]\w*)", re.M)
_RUST_EXPORT_RE = re.compile(
    r"^[ \t]*pub(?:\([^)]*\))?\s+(?:async\s+)?(?:fn|struct|enum|trait)\s+"
    r"([A-Za-z_]\w*)", re.M)
_GO_EXPORT_RE = re.compile(
    r"^func\s+(?:\([^)]*\)\s*)?([A-Za-z_]\w*)\s*[\(\[]", re.M)

# 调用：标识符紧跟左括号（成员调用 `api.foo()` 不算 —— 那是别的来源）
_CALL_RE = re.compile(r"(?<![\w$.])([A-Za-z_$][\w$]*)(\s*)\(")

# 断言的关键字
_JS_EXPECT_RE = re.compile(r"(?<![\w$.])expect\s*\(")
_JS_MATCHER_RE = re.compile(
    r"(?:\s*\.\s*(?:not|resolves|rejects|soft))*"
    r"\s*\.\s*(toBe|toEqual|toStrictEqual|toDeepEqual|"
    r"toMatchSnapshot|toMatchInlineSnapshot)\s*\(")
_JS_ASSERT_CALL_RE = re.compile(
    r"(?<![\w$.])(?:self\.)?assert\."
    r"(?:strictEqual|equal|notStrictEqual|notEqual|deepEqual|"
    r"deepStrictEqual|notDeepEqual)\s*\(")
_PY_ASSERT_CALL_RE = re.compile(
    r"(?<![\w$.])(?:self\.)?"
    r"(?:assertEqual|assertNotEqual|assertAlmostEqual)\s*\(")
_RUST_ASSERT_RE = re.compile(r"(?<![\w$])(assert_eq!|assert_ne!)\s*\(")
_PY_ASSERT_STMT_RE = re.compile(r"(?m)^[ \t]*assert[ \t]+")
_GO_IF_RE = re.compile(r"(?<![\w$.])if[ \t]+")

# 赋值：变量 <- 被测对象输出
_ASSIGN_RES = (
    # JS/TS：const expected = calc(a);
    re.compile(r"(?<![\w$.])(?:const|let|var)[ \t]+([A-Za-z_$][\w$]*)[ \t]*="
               r"[ \t]*(.+?);?[ \t]*$", re.M),
    # Python/Go：expected = calc(a) / expected := calc(a)
    re.compile(r"(?m)^[ \t]*([A-Za-z_][\w]*)[ \t]*:?=[ \t]*(.+?)[ \t]*$"),
    # Rust：let mut expected = calc(a);（普通 let 已被上面那条覆盖）
    re.compile(r"(?<![\w$.])let[ \t]+mut[ \t]+([A-Za-z_][\w]*)[ \t]*="
               r"[ \t]*(.+?);?[ \t]*$", re.M),
)

# 说明文本（提醒级，人话）
WHY_SELF = "期望值取自被测对象自己的输出 —— 断言恒成立，测不出任何东西"
WHY_FROM_SUT = "期望值取自被测对象自己的输出 —— 等于拿被测对象和自己比"
WHY_SNAPSHOT = "快照基线由被测对象自己的输出生成 —— 基线不是独立来源"

KIND_SELF = "自比较"
KIND_FROM_SUT = "期望值取自被测对象"
KIND_SNAPSHOT = "快照自比对"


# ------------------------------------------------------------ 文本工具


def _scan_strings(text: str) -> bytearray:
    """标出字符串字面量的内容位置（不含引号本身）。

    字符串里的括号、逗号、关键字都不能当代码结构看。
    """
    n = len(text)
    mask = bytearray(n)
    i = 0
    while i < n:
        ch = text[i]
        if ch in "\"'`":
            j = i + 1
            while j < n:
                if text[j] == "\\":
                    j += 2
                    continue
                if text[j] == ch:
                    break
                if text[j] == "\n" and ch != "`":
                    break            # 没闭合，别把后面整行都吃掉
                j += 1
            end = min(j, n)
            for k in range(i + 1, end):
                mask[k] = 1
            i = end + 1
            continue
        i += 1
    return mask


def _mask_comments(text: str, smask: bytearray) -> str:
    """把注释内容替换成等长空格 —— 行号列号不变，注释里的代码不算数。"""
    out = list(text)
    n = len(text)
    i = 0
    while i < n:
        if smask[i]:
            i += 1
            continue
        ch = text[i]
        if ch == "/" and i + 1 < n and text[i + 1] == "/":
            j = i
            while j < n and text[j] != "\n":
                out[j] = " "
                j += 1
            i = j
            continue
        if ch == "/" and i + 1 < n and text[i + 1] == "*":
            j = i
            while j < n and not (text[j] == "*" and j + 1 < n
                                 and text[j + 1] == "/"):
                if text[j] != "\n":
                    out[j] = " "
                j += 1
            for k in range(i, min(j + 2, n)):
                if out[k] != "\n":
                    out[k] = " "
            i = j + 2
            continue
        if ch == "#":
            j = i
            while j < n and text[j] != "\n":
                out[j] = " "
                j += 1
            i = j
            continue
        i += 1
    return "".join(out)


def _norm(text: str, smask: bytearray, start: int = 0,
          end: int | None = None) -> str:
    """归一化表达式文本 —— 去掉字符串外的空白，用来判断两次调用是不是同一个。"""
    if end is None:
        end = len(text)
    buf: list[str] = []
    for i in range(start, end):
        if smask[i] and text[i].isspace():
            continue
        if not smask[i] and text[i].isspace():
            continue
        buf.append(text[i])
    return "".join(buf)


def _match_close(text: str, i: int, smask: bytearray) -> int:
    """text[i] 是 '(' —— 返回配对的 ')' 下标；找不到返回 -1。"""
    depth = 0
    n = len(text)
    j = i
    while j < n:
        if not smask[j]:
            ch = text[j]
            if ch in "([{":
                depth += 1
            elif ch in ")]}":
                depth -= 1
                if depth == 0:
                    return j
        j += 1
    return -1


def _split_top_spans(text: str, smask: bytearray, start: int, end: int,
                     sep: str = ",") -> list[tuple[int, int]]:
    """按顶层分隔符切分，返回 [(起点, 长度)]（首尾空白已去掉）。"""
    spans: list[tuple[int, int]] = []
    seg_start = start
    depth = 0
    for i in range(start, end):
        if smask[i]:
            continue
        ch = text[i]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif depth == 0 and ch == sep:
            spans.append((seg_start, i))
            seg_start = i + 1
    spans.append((seg_start, end))
    out: list[tuple[int, int]] = []
    for s, e in spans:
        while s < e and text[s].isspace():
            s += 1
        while e > s and text[e - 1].isspace():
            e -= 1
        out.append((s, e - s))
    return out


def _trim_span(text: str, start: int, end: int) -> tuple[int, int]:
    """去掉片段首尾空白，返回 (起点, 长度)。"""
    while start < end and text[start].isspace():
        start += 1
    while end > start and text[end - 1].isspace():
        end -= 1
    return start, end - start


def _split_compare(text: str, smask: bytearray, start: int = 0,
                   end: int | None = None) -> tuple[int, int, str] | None:
    """在顶层找比较运算符，返回 (左起, 右起, 运算符)；没有则 None。"""
    if end is None:
        end = len(text)
    depth = 0
    i = start
    while i < end:
        if smask[i]:
            i += 1
            continue
        ch = text[i]
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        elif depth == 0:
            three = text[i:i + 3]
            two = text[i:i + 2]
            if three in ("===", "!=="):
                return start, i, three
            if two in ("==", "!="):
                return start, i, two
            if two in ("<=", ">="):
                i += 2
                continue
        i += 1
    return None


def _strip_outer_parens(text: str, smask: bytearray, start: int,
                        end: int) -> tuple[int, int]:
    """剥掉最外层的一对括号（Python 的 assert (a == b)）。"""
    while end - start >= 2 and text[start] == "(" and text[end - 1] == ")":
        inner = _match_close(text, start, smask)
        if inner != end - 1:
            break
        start += 1
        end -= 1
    return start, end


def _line_of(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def _snippet_of(raw_lines: list[str], line: int, limit: int = 120) -> str:
    s = raw_lines[line - 1].strip() if 1 <= line <= len(raw_lines) else ""
    if len(s) > limit:
        s = s[:limit] + "…"
    return s


# ------------------------------------------------- 被测对象（导出符号）


def _is_test_name(name: str) -> bool:
    return any(fnmatch.fnmatch(name, pat) for pat in TEST_FILE_PATTERNS)


def _iter_source_files(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in filenames:
            if Path(fn).suffix.lower() not in CODE_EXT:
                continue
            if _is_test_name(fn):
                continue
            yield Path(dirpath) / fn


def collect_sut_names(root: Path) -> set[str]:
    """本仓库源码里导出的符号 —— 调用这些名字的函数视为"被测对象调用"。"""
    names: set[str] = set()
    for path in _iter_source_files(root):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        ext = path.suffix.lower()
        if ext in JS_EXT:
            for m in _JS_EXPORT_RE.finditer(text):
                names.add(m.group(1))
            for m in _JS_EXPORT_LIST_RE.finditer(text):
                for part in m.group(1).split(","):
                    part = part.strip()
                    if not part:
                        continue
                    part = re.sub(r"^type\s+", "", part)
                    if " as " in part:
                        part = part.split(" as ")[-1].strip()
                    if re.fullmatch(r"[A-Za-z_$][\w$]*", part):
                        names.add(part)
        elif ext == ".py":
            for m in _PY_EXPORT_RE.finditer(text):
                names.add(m.group(1))
        elif ext == ".rs":
            for m in _RUST_EXPORT_RE.finditer(text):
                names.add(m.group(1))
        elif ext == ".go":
            for m in _GO_EXPORT_RE.finditer(text):
                names.add(m.group(1))
    return {n for n in names
            if n not in GENERIC_NAMES and not n.startswith("_")}


# ------------------------------------------------------------ 调用识别


def _find_sut_calls(text: str, sut: set[str], smask: bytearray,
                    start: int = 0, end: int | None = None
                    ) -> list[tuple[str, str]]:
    """范围内的被测对象调用，返回 [(调用名, 归一化调用文本)]。

    只认裸标识符调用（`calc(1)`），不认成员调用（`api.calc(1)`）——
    后者通常来自别的来源（外部 API、fixture 助手）。
    """
    if end is None:
        end = len(text)
    out: list[tuple[str, str]] = []
    for m in _CALL_RE.finditer(text, start, end):
        if smask[m.start()]:
            continue
        name = m.group(1)
        if name not in sut:
            continue
        before = text[max(start, m.start() - 12):m.start()].rstrip()
        if before.endswith("new") or before.endswith("function"):
            continue
        popen = m.end() - 1
        pclose = _match_close(text, popen, smask)
        if pclose < 0:
            continue
        out.append((name, _norm(text, smask, m.start(), pclose + 1)))
    return out


def _resolve_alias(var: str, aliases: dict[str, tuple[str | None, int]],
                   depth: int = 4) -> str | None:
    """变量 <- 被测对象调用？返回被测调用的归一化文本。"""
    if depth <= 0 or var not in aliases:
        return None
    call, _off = aliases[var]
    if call is None:
        return None               # 最近一次赋值不是取自被测对象
    if re.fullmatch(r"[A-Za-z_$][\w$]*", call):
        return _resolve_alias(call, aliases, depth - 1)
    return call


def _expr_has_sut(text: str, sut: set[str], smask: bytearray, start: int,
                  end: int, aliases: dict[str, tuple[str | None, int]]
                  ) -> bool:
    """表达式是否牵扯到被测对象（直接调用，或变量来自被测对象输出）。"""
    if _find_sut_calls(text, sut, smask, start, end):
        return True
    v = text[start:end].strip()
    if re.fullmatch(r"[A-Za-z_$][\w$]*", v):
        return _resolve_alias(v, aliases) is not None
    return False


# --------------------------------------------------------- 断言抽取


class _Assertion:
    """一条比较断言（带操作数在原文中的位置）。"""

    __slots__ = ("offset", "line", "left_off", "left_len",
                 "right_off", "right_len")

    def __init__(self, offset: int, line: int, left_off: int, left_len: int,
                 right_off: int, right_len: int) -> None:
        self.offset = offset
        self.line = line
        self.left_off = left_off
        self.left_len = left_len
        self.right_off = right_off
        self.right_len = right_len


class _Snapshot:
    __slots__ = ("offset", "line", "expr_off", "expr_len")

    def __init__(self, offset: int, line: int, expr_off: int,
                 expr_len: int) -> None:
        self.offset = offset
        self.line = line
        self.expr_off = expr_off
        self.expr_len = expr_len


def _collect_assignments(clean: str, smask: bytearray, sut: set[str]
                         ) -> list[tuple[int, str, str | None]]:
    """收集变量赋值：[(下标, 变量名, 被测调用文本 | None)]。

    None 表示"这次赋值不是取自被测对象" —— 变量被重新赋值后，旧的
    "来自被测对象"绑定必须失效，否则会拿更早的一次赋值误判后面的断言。
    """
    out: list[tuple[int, str, str | None]] = []
    for rx in _ASSIGN_RES:
        for m in rx.finditer(clean):
            var, rhs = m.group(1), m.group(2)
            if var in GENERIC_NAMES:
                continue
            if rhs.lstrip().startswith("="):
                continue                # a == b，不是赋值
            rstart = m.start(2)
            calls = _find_sut_calls(clean, sut, smask, rstart,
                                    rstart + len(rhs))
            call = calls[0][1] if len(calls) == 1 else None
            out.append((m.start(), var, call))
    # 按位置排序 —— "最后一次赋值说了算"必须按位置算，不能按正则命中顺序算
    return sorted(out, key=lambda x: x[0])


def _aliases_before(assigns, offset: int) -> dict[str, tuple[str | None, int]]:
    """某个位置之前生效的变量绑定（最后一次赋值说了算）。"""
    out: dict[str, tuple[str | None, int]] = {}
    for off, var, call in assigns:
        if off > offset:
            continue
        out[var] = (call, off)
    return out


def _paren_args(text: str, smask: bytearray, popen: int
                ) -> list[tuple[int, int]] | None:
    """括号里的实参，返回 [(起点, 长度)]。括号没闭合则 None。"""
    pclose = _match_close(text, popen, smask)
    if pclose < 0:
        return None
    return _split_top_spans(text, smask, popen + 1, pclose)


def _scan_assertions(clean: str, smask: bytearray
                     ) -> tuple[list[_Assertion], list[_Snapshot]]:
    """扫出文件里的比较断言与快照断言。"""
    asserts: list[_Assertion] = []
    snaps: list[_Snapshot] = []

    def add(offset: int, left: tuple[int, int], right: tuple[int, int]) -> None:
        asserts.append(_Assertion(offset, _line_of(clean, offset),
                                   left[0], left[1], right[0], right[1]))

    # JS/TS：expect(实际).toBe(期望)
    for m in _JS_EXPECT_RE.finditer(clean):
        pclose = _paren_close(clean, smask, m.end() - 1)
        args = _split_top_spans(clean, smask, m.end(), pclose)
        if not args or not args[0][1]:
            continue
        actual = args[0]
        mm = _JS_MATCHER_RE.match(clean, pclose + 1)
        if not mm:
            continue
        if mm.group(1) in ("toMatchSnapshot", "toMatchInlineSnapshot"):
            snaps.append(_Snapshot(m.start(), _line_of(clean, m.start()),
                                   actual[0], actual[1]))
            continue
        eargs = _paren_args(clean, smask, mm.end() - 1)
        if not eargs or not eargs[0][1]:
            continue
        add(m.start(), actual, eargs[0])

    # JS/TS：assert.strictEqual(实际, 期望) / assert.equal(...)
    # Python：assertEqual(实际, 期望)
    for rx in (_JS_ASSERT_CALL_RE, _PY_ASSERT_CALL_RE):
        for m in rx.finditer(clean):
            if clean[max(0, m.start() - 4):m.start()].strip().endswith("def"):
                continue
            args = _paren_args(clean, smask, m.end() - 1)
            if not args or len(args) < 2 or not args[1][1]:
                continue
            add(m.start(), args[0], args[1])

    # Rust：assert_eq!(实际, 期望)
    for m in _RUST_ASSERT_RE.finditer(clean):
        args = _paren_args(clean, smask, m.end() - 1)
        if not args or len(args) < 2 or not args[1][1]:
            continue
        add(m.start(), args[0], args[1])

    # Python：assert 实际 == 期望
    for m in _PY_ASSERT_STMT_RE.finditer(clean):
        start = m.end()
        eol = clean.find("\n", start)
        if eol < 0:
            eol = len(clean)
        s, e = _strip_outer_parens(clean, smask, start, eol)
        cmp_ = _split_compare(clean, smask, s, e)
        if not cmp_:
            continue
        _ls, mid, _op = cmp_
        # 断言消息（逗号后面）不算操作数
        tail = _split_top_spans(clean, smask, mid + 2, e)
        r_end = mid + 2 + tail[0][1] if len(tail) > 1 else e
        left = _trim_span(clean, s, mid)
        right = _trim_span(clean, mid + 2, r_end)
        if not left[1] or not right[1]:
            continue
        add(m.start(), left, right)

    # Go：if 实际 != 期望 { t.Fatalf(...) }
    for m in _GO_IF_RE.finditer(clean):
        brace = _find_top_brace(clean, m.end(), smask)
        if brace < 0:
            continue
        cmp_ = _split_compare(clean, smask, m.end(), brace)
        if not cmp_:
            continue
        ls, mid, _op = cmp_
        left = _trim_span(clean, ls, mid)
        right = _trim_span(clean, mid + 2, brace)
        if not left[1] or not right[1]:
            continue
        body = clean[brace:brace + 400]
        if not re.search(r"t\.(?:Fatal|Fatalf|Fatalln|Error|Errorf|Fail|"
                         r"FailNow)\b", body):
            continue
        add(m.start(), left, right)

    return asserts, snaps


def _paren_close(text: str, smask: bytearray, popen: int) -> int:
    """配对的右括号下标；没闭合返回 popen。"""
    pclose = _match_close(text, popen, smask)
    return popen if pclose < 0 else pclose


def _find_top_brace(text: str, start: int, smask: bytearray) -> int:
    """从 start 找顶层 '{'（if 条件的结束）。"""
    depth = 0
    for i in range(start, len(text)):
        if smask[i]:
            continue
        ch = text[i]
        if ch == "{" and depth == 0:
            return i
        if ch == ";" and depth == 0:
            return -1
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
    return -1


# ------------------------------------------------------------ 判定


def _judge(asserts: list[_Assertion], snaps: list[_Snapshot], sut: set[str],
           clean: str, smask: bytearray,
           assigns) -> list[tuple[str, str, int]]:
    """返回 [(kind, why, line)]。"""
    out: list[tuple[str, str, int]] = []
    seen: set[tuple[str, int]] = set()

    for a in asserts:
        aliases = _aliases_before(assigns, a.offset)
        left_calls = _find_sut_calls(clean, sut, smask, a.left_off,
                                     a.left_off + a.left_len)
        right_calls = _find_sut_calls(clean, sut, smask, a.right_off,
                                      a.right_off + a.right_len)

        # 形态 1：同一个被测对象调用出现在断言两侧
        if any(ctext == rtext for _n, ctext in left_calls
               for _rn, rtext in right_calls):
            key = (KIND_SELF, a.line)
            if key not in seen:
                seen.add(key)
                out.append((KIND_SELF, WHY_SELF, a.line))
            continue

        # 形态 2：期望值变量来自被测对象输出，另一侧是被测对象调用
        hit = False
        for off, length, other in ((a.left_off, a.left_len, right_calls),
                                   (a.right_off, a.right_len, left_calls)):
            v = clean[off:off + length].strip()
            if not re.fullmatch(r"[A-Za-z_$][\w$]*", v):
                continue
            if _resolve_alias(v, aliases) and other:
                hit = True
                break
        if hit:
            key = (KIND_FROM_SUT, a.line)
            if key not in seen:
                seen.add(key)
                out.append((KIND_FROM_SUT, WHY_FROM_SUT, a.line))
            continue

    # 形态 3：快照基线与被测比较指向同一个表达式
    for s in snaps:
        aliases = _aliases_before(assigns, s.offset)
        if not _expr_has_sut(clean, sut, smask, s.expr_off,
                             s.expr_off + s.expr_len, aliases):
            continue
        s_norm = _norm(clean, smask, s.expr_off, s.expr_off + s.expr_len)
        for a in asserts:
            l_norm = _norm(clean, smask, a.left_off, a.left_off + a.left_len)
            r_norm = _norm(clean, smask, a.right_off, a.right_off + a.right_len)
            if s_norm == l_norm or s_norm == r_norm:
                key = (KIND_SNAPSHOT, s.line)
                if key not in seen:
                    seen.add(key)
                    out.append((KIND_SNAPSHOT, WHY_SNAPSHOT, s.line))
                break

    return out


# ------------------------------------------------------------ 文件扫描


def scan_file(root: Path, path: Path, sut: set[str]) -> list[dict]:
    """扫一个测试文件，返回 findings 列表。"""
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    smask = _scan_strings(raw)
    clean = _mask_comments(raw, smask)
    asserts, snaps = _scan_assertions(clean, smask)
    if not asserts and not snaps:
        return []
    assigns = _collect_assignments(clean, smask, sut)
    judged = _judge(asserts, snaps, sut, clean, smask, assigns)
    if not judged:
        return []
    raw_lines = raw.splitlines()
    try:
        rel = path.resolve().relative_to(root).as_posix()
    except ValueError:
        rel = str(path)
    findings = [{
        "file": rel,
        "line": line,
        "kind": kind,
        "snippet": _snippet_of(raw_lines, line),
        "why": why,
    } for kind, why, line in judged]
    findings.sort(key=lambda d: (d["line"], d["kind"]))
    return findings


# ------------------------------------------------------------ 公开入口


def discover_test_files(root: Path) -> list[Path]:
    """root 下认作测试文件的文件。"""
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in filenames:
            if _is_test_name(fn):
                out.append(Path(dirpath) / fn)
    return sorted(out)


def check_oracle_independence(root: Path, files: list[Path]) -> dict:
    """扫测试文件，标出"期望值取自被测对象自身"的断言形态。

    返回 {'scanned': int, 'findings': [...], 'verdict': str}。
    files 为空时扫 root 下认作测试文件的文件。只读，不改任何文件。
    """
    root = Path(root).resolve()
    paths = [Path(f) for f in (files or [])]
    if not paths:
        paths = discover_test_files(root)
    sut = collect_sut_names(root)
    findings: list[dict] = []
    scanned = 0
    for p in paths:
        if not p.is_file():
            continue
        scanned += 1
        findings.extend(scan_file(root, p, sut))
    findings.sort(key=lambda d: (d["file"], d["line"], d["kind"]))
    n = len(findings)
    verdict = (f"{n} 处断言的期望值可能来自被测对象自身" if n
               else "未发现期望值取自被测对象自身的断言")
    return {"scanned": scanned, "findings": findings, "verdict": verdict}


# ------------------------------------------------------------ 输出与 CLI


def print_text_report(result: dict) -> None:
    findings = result["findings"]
    print("oracle 独立性检查（提醒级 —— 不阻塞交付判定）")
    print(f"扫描 {result['scanned']} 个测试文件")
    print()
    for f in findings:
        print(f"{f['file']}:{f['line']}  [{f['kind']}]  {f['why']}")
        if f["snippet"]:
            print(f"    {f['snippet']}")
    print()
    print(f"结论：{result['verdict']}")
    print("提示：这是启发式提醒，不是判定 —— 请人工确认期望值是否真的独立取自外部。")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="oracle_check.py",
        description="oracle 独立性启发式检查（提醒级，不阻塞）")
    ap.add_argument("paths", nargs="*",
                    help="测试文件或目录（不给则扫 --repo 下认作测试文件的文件）")
    ap.add_argument("--repo", default=".",
                    help="项目根 —— 用于识别被测对象的导出符号")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    args = ap.parse_args(argv)

    root = Path(args.repo).resolve()
    if not root.is_dir():
        print(f"项目根不存在：{args.repo}", file=sys.stderr)
        return 2

    files: list[Path] = []
    for raw in args.paths:
        p = Path(raw)
        if not p.is_absolute():
            p = root / p
        if p.is_dir():
            files.extend(discover_test_files(p))
        elif p.is_file():
            files.append(p)
        else:
            print(f"路径不存在：{raw}", file=sys.stderr)
            return 2

    result = check_oracle_independence(root, files)
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print_text_report(result)
    return 0


if __name__ == "__main__":
    sys.exit(main())
