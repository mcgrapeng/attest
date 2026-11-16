#!/usr/bin/env python3
"""DPSP —— 区分力自证（规格 §6）。

要回答的问题只有一句：**这条钉能不能发现这类缺陷**。
办法：把生产代码改坏（变异），跑测试，看钉住的那**一个**用例会不会红。

四档结论（§6.1）：
  pass             有变异被钉住的用例杀死 ⇒ 这条钉有区分力
  fail             变异全部存活 ⇒ 这条钉发现不了这类缺陷（**只有这一档报红**）
  untested         环境没就绪 / runner 跑不起来 / 输出认不出 ⇒ 判不了
  not-applicable   零变异点 / 变异没生效 / 文件不是相对路径可达

自证不藏缺口：返回的 dict 一定带 `checked` / `total`，抽样比例写在
`budget_note` 里（抽样要说出来，否则就是自欺）。
"""
from __future__ import annotations

import ast
import json
import math
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parent))
from decision_points import ts_parses  # noqa: E402
from mutation_check import (  # noqa: E402
    L2_OPERATORS, MUTANT_MARKER, OPERATORS, _backend_for, _project_root,
    _strip_js_code, match_case, run_mutation_check)
from req_plan import parse_pins  # noqa: E402

__all__ = ["ImportRef", "Verdict", "file_exports", "parse_imports",
           "python_imports", "python_analysis", "runtime_symbols",
           "match_case", "run_dpsp"]

#: 不变式超过这个数就抽样（规格 §6.5）
BUDGET = 20


@dataclass
class ImportRef:
    """一条 import 引用。"""

    spec: str                       # 原始 specifier
    target: str | None = None       # 解析出的（工作区相对）路径
    symbols: tuple[str, ...] = ()   # 引用的符号名（局部名）
    namespace_level: bool = False   # 命名空间级 ⇒ 整个模块的公开面都算引用
    type_only: bool = False         # 纯类型 import ⇒ 运行时被擦除
    relative: bool = False          # 相对路径 ⇒ 副本密闭，可以变异


@dataclass
class Verdict:
    """一条不变式的四档结论。"""

    invariant_id: str = ""           # 归属到哪条不变式（报告要能对回去）
    status: str = ""
    detail: str = ""
    evidence: list[str] = field(default_factory=list)
    killed: int = 0
    survivors: list[dict] = field(default_factory=list)
    pin: str = ""                    # 这条不变式的钉（规格 §7.4 的 JSON 要回指）
    test: str = ""                   # 钉住的那个用例


# ---------------------------------------------------------------- 导入解析

#: 静态 import：`import ... from '...'`
_STATIC_IMPORT_RE = re.compile(
    r"^[ \t]*(?P<kw>import)[ \t]+(?P<clause>[^'\";]*?)[ \t\r\n]*from[ \t\r\n]*"
    r"(?P<q>['\"])(?P<spec>[^'\"]+)(?P=q)[ \t]*;?", re.M)

#: 副作用 import：`import './x.js'`
_SIDE_EFFECT_RE = re.compile(
    r"^[ \t]*(?P<kw>import)[ \t]*(?P<q>['\"])(?P<spec>[^'\"]+)(?P=q)[ \t]*;?",
    re.M)

#: 动态 import：`await import('./x')`
_DYNAMIC_RE = re.compile(
    r"(?P<kw>import)[ \t]*\([ \t]*(?P<q>['\"])(?P<spec>[^'\"]+)(?P=q)[ \t]*\)")

#: `mock.module('./x.ts')` —— 点名了那个模块 ⇒ 算引用（规格 §5.1.1）
_MOCK_MODULE_RE = re.compile(
    r"mock\s*\.\s*module[ \t]*\([ \t]*(?P<q>['\"])(?P<spec>[^'\"]+)(?P=q)")

#: 解构：`const { a, b } = await import(`
_DESTRUCTURE_RE = re.compile(
    r"(?:const|let|var)[ \t]*\{(?P<names>[^}]*)\}[ \t]*=[ \t]*(?:await[ \t]*)?$")

#: 导出声明（JS/TS）
_JS_EXPORT_DECL_RE = re.compile(
    r"^[ \t]*export[ \t]+(?:declare[ \t]+)?(?:default[ \t]+)?"
    r"(?:async[ \t]+)?(?:function\*?|class|const|let|var|interface|type|enum)"
    r"[ \t]+([A-Za-z_$][\w$]*)", re.M)
_JS_EXPORT_LIST_RE = re.compile(r"^[ \t]*export[ \t]*\{([^}]*)\}", re.M)
_JS_EXPORT_DEFAULT_RE = re.compile(r"^[ \t]*export[ \t]+default", re.M)

#: 导出（Python，只认模块级）
_PY_DEF_RE = re.compile(r"^(?:async[ \t]+)?def[ \t]+([A-Za-z_]\w*)", re.M)
_PY_CLASS_RE = re.compile(r"^class[ \t]+([A-Za-z_]\w*)", re.M)

#: 控制流关键字：它们后面跟的 `{}` 不是函数体
_CONTROL_KEYWORDS = frozenset({
    "if", "for", "while", "switch", "catch", "do", "else", "try", "finally",
    "with",
})


def _parse_clause(clause: str) -> tuple[tuple[str, ...], bool, bool]:
    """解析 import 子句 ⇒ (运行时符号名, 命名空间级, 纯类型)。

    类型位置的 specifier（`import { type Row }`）在运行时不存在 ⇒ 不进
    运行时符号；整条都是类型 specifier 时 ⇒ 整条是纯类型 import
    （运行时被擦除，规格 §5.1.1）。
    """
    symbols: list[str] = []
    namespace = False
    type_only = False
    type_specs = 0
    value_specs = 0
    clause = clause.strip()
    if clause.startswith("type ") or clause.startswith("type{"):
        type_only = True
        clause = clause[4:].strip()
    m = re.match(r"\*\s+as\s+([A-Za-z_$][\w$]*)", clause)
    if m:
        namespace = True
        symbols.append(m.group(1))
        value_specs += 1
        clause = clause[m.end():].strip().lstrip(",").strip()
    m = re.search(r"\{([^}]*)\}", clause)
    if m:
        for item in m.group(1).split(","):
            item = item.strip()
            if not item:
                continue
            is_type = item.startswith("type ")
            if is_type:
                item = item[5:].strip()
            if not item:
                continue
            parts = [p.strip() for p in item.split(" as ")]
            if is_type:
                type_specs += 1
                continue
            value_specs += 1
            symbols.append(parts[-1])
            if len(parts) == 2:
                symbols.append(parts[0])   # `a as b` ⇒ 原名也算（改名导入的锚点）
        clause = (clause[:m.start()] + clause[m.end():]).strip()
        clause = clause.lstrip(",").strip()
    if clause and re.fullmatch(r"[A-Za-z_$][\w$]*", clause):
        symbols.append(clause)
        value_specs += 1
    if not type_only and value_specs == 0 and type_specs:
        type_only = True
    return tuple(symbols), namespace, type_only


#: 类型别名：`type X = ...`（类型参数允许一层）
_TYPE_ALIAS_HEAD_RE = re.compile(
    r"^[ \t]*(?:export[ \t]+)?type[ \t]+[A-Za-z_$][\w$]*"
    r"[ \t]*(?:<[^<>=]*>)?[ \t]*=")
#: 接口继承：`interface X extends ...`
_INTERFACE_EXTENDS_RE = re.compile(
    r"^[ \t]*(?:export[ \t]+)?interface[ \t]+[A-Za-z_$][\w$]*"
    r"[ \t]*(?:<[^<>=]*>)?[ \t]*extends[ \t]")


def _in_type_position(source: str, pos: int) -> bool:
    """`import('...')` 在不在类型位置（运行时被擦除，规格 §5.1.1）。

    两种形态：`typeof import('...')`（类型查询）与 `type X = import('...').Y`
    （类型别名，可跨行）。类型位置里不会出现 `await` / `return` / `=>` ——
    借这一点把「上一行的 type 语句」和「本行的运行时 import」分开。
    """
    head = source[:pos]
    if re.search(r"typeof[ \t]*$", head):
        return True
    cut = max(head.rfind(";"), head.rfind("{"), head.rfind("}"))
    stmt = head[cut + 1:]
    if not (_TYPE_ALIAS_HEAD_RE.match(stmt) or _INTERFACE_EXTENDS_RE.match(stmt)):
        return False
    between = stmt[stmt.index("=") + 1:] if "=" in stmt else stmt
    return not re.search(r"\bawait\b|\breturn\b|=>", between)


def parse_imports(source: str, file: str, root: Path) -> list[ImportRef]:
    """解析一个文件里的运行时 import（静态具名 / 默认 / 命名空间 / 动态 / 类型）。

    `file` 是**工作区相对**的路径（相对 specifier 按它所在目录解析）；
    `root` 是工作区根（别名解析与读目标模块的导出用）。
    """
    code = _strip_js_code(source)      # 字符串与注释清空（偏移不变）
    refs: list[ImportRef] = []
    seen: set[tuple] = set()

    def emit(spec: str, symbols: tuple[str, ...], namespace: bool,
             type_only: bool) -> None:
        target, relative = _resolve_specifier(spec, file, root)
        if namespace and target:
            exports = file_exports(root / target)
            if exports:
                symbols = tuple(sorted(exports))
        ref = ImportRef(spec=spec, target=target, symbols=symbols,
                        namespace_level=namespace, type_only=type_only,
                        relative=relative)
        key = (ref.spec, ref.target, ref.symbols, ref.namespace_level,
               ref.type_only)
        if key in seen:
            return
        seen.add(key)
        refs.append(ref)

    def usable(m: re.Match) -> bool:
        """这个匹配是不是真的代码（不是字符串 / 注释里的一段）。"""
        pos = m.start("kw") if "kw" in m.groupdict() else m.start()
        return pos < len(code) and code[pos] != " "

    for m in _STATIC_IMPORT_RE.finditer(source):
        if not usable(m):
            continue
        symbols, namespace, type_only = _parse_clause(m.group("clause"))
        emit(m.group("spec"), symbols, namespace, type_only)
    for m in _SIDE_EFFECT_RE.finditer(source):
        if not usable(m):
            continue
        emit(m.group("spec"), (), False, False)
    for m in _DYNAMIC_RE.finditer(source):
        if not usable(m):
            continue
        if _in_type_position(source, m.start("kw")):
            emit(m.group("spec"), (), False, True)
            continue
        prefix = source[:m.start()]
        dm = _DESTRUCTURE_RE.search(prefix)
        if dm:
            names = [n.strip().split(" as ")[-1].strip()
                     for n in dm.group("names").split(",") if n.strip()]
            emit(m.group("spec"), tuple(names), False, False)
        else:
            # 无解构 ⇒ 命名空间级（整个模块的公开面都算引用）
            emit(m.group("spec"), (), True, False)
    for m in _MOCK_MODULE_RE.finditer(source):
        if not usable(m):
            continue
        emit(m.group("spec"), (), True, False)
    return refs


def runtime_symbols(refs: list[ImportRef]) -> set[str]:
    """运行时（非纯类型）引用的符号名。"""
    out: set[str] = set()
    for r in refs:
        if r.type_only:
            continue
        out.update(r.symbols)
    return out


# ---------------------------------------------------------------- 解析目标


def _resolve_specifier(spec: str, file: str, root: Path) -> tuple[str | None, bool]:
    """specifier ⇒ (工作区相对路径 | None, 是否相对路径)。"""
    if spec.startswith("."):
        base = PurePosixPath(file).parent
        return os.path.normpath(str(base / spec)), True
    aliased = _resolve_alias(spec, file, root)
    if aliased:
        return aliased, False
    return None, False


def _match_path_pattern(pattern: str, spec: str) -> str | None:
    """tsconfig 的 paths 模式匹配 ⇒ 通配部分（不匹配 ⇒ None）。"""
    if "*" not in pattern:
        return "" if pattern == spec else None
    prefix, _, suffix = pattern.partition("*")
    if not spec.startswith(prefix) or not spec.endswith(suffix):
        return None
    if len(spec) < len(prefix) + len(suffix):
        return None
    return spec[len(prefix): len(spec) - len(suffix)]


def _tsconfig_paths(file: str, root: Path) -> list[tuple[str, list[str], Path]]:
    """从文件所在目录往上找 tsconfig ⇒ [(paths 模式, 目标列表, tsconfig 目录)]。"""
    out: list[tuple[str, list[str], Path]] = []
    root_n = Path(os.path.normpath(str(root)))
    cur = Path(os.path.normpath(str(root_n / str(PurePosixPath(file).parent))))
    for _ in range(10):
        for name in ("tsconfig.json", "tsconfig.base.json"):
            p = cur / name
            if not p.is_file():
                continue
            data = _load_jsonc(p)
            paths = (data.get("compilerOptions") or {}).get("paths") or {}
            if not isinstance(paths, dict):
                continue
            for pat, targets in paths.items():
                if isinstance(targets, list):
                    out.append((str(pat), [str(t) for t in targets], p.parent))
        if cur == root_n or cur.parent == cur:
            break
        cur = cur.parent
    return out


def _load_jsonc(path: Path) -> dict:
    """读 tsconfig（允许注释）。"""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        stripped = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
        stripped = re.sub(r"(?m)//.*$", "", stripped)
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError:
            return {}
    return data if isinstance(data, dict) else {}


def _resolve_alias(spec: str, file: str, root: Path) -> str | None:
    """路径别名 ⇒ 工作区相对路径（按目标仓库 tsconfig 的 paths，不硬编码）。"""
    root_n = Path(os.path.normpath(str(root)))
    for pattern, targets, base in _tsconfig_paths(file, root_n):
        mid = _match_path_pattern(pattern, spec)
        if mid is None:
            continue
        for t in targets:
            cand = Path(os.path.normpath(str(base / t.replace("*", mid))))
            try:
                rel = os.path.relpath(str(cand), str(root_n))
            except ValueError:
                continue
            if rel.startswith(".."):
                continue
            rel = os.path.normpath(rel)
            if (root_n / rel).is_file():
                return rel
    return None


def python_imports(source: str) -> tuple[set[str], set[str]]:
    """Python 文件的 import ⇒ (被 import 的点号模块名, 绑定进来的名字)。

    `from pkg.core import f` ⇒ ({"pkg.core"}, {"f"})；
    `import pkg.core` ⇒ ({"pkg.core"}, {"pkg"})。
    相对 import（`from . import x`）不给模块名 —— 它的根由包结构决定，
    这里不做路径解析，交给调用方按符号交集判断。
    """
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return set(), set()
    modules: set[str] = set()
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name)
                names.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and not node.level:
            if node.module:
                modules.add(node.module)
            for alias in node.names:
                names.add(alias.asname or alias.name)
    return modules, names


def _py_module_file(module: str, source_rels: list[str]) -> str | None:
    """点号模块名 ⇒ 被测源文件（按**路径后缀**匹配）。

    `pkg.core` ⇒ `pkg/core.py`（或 `pkg/core/__init__.py`）；
    被测文件在 `src/` 下（`src/pkg/core.py`）也匹配得上。
    """
    parts = [p for p in module.split(".") if p]
    if not parts:
        return None
    stem = "/".join(parts)
    for suffix in (stem + ".py", stem + "/__init__.py"):
        cands = [r for r in source_rels
                 if r == suffix or r.endswith("/" + suffix)]
        if cands:
            return sorted(cands, key=len)[0]
    return None


def _py_class_methods(path: Path) -> dict[str, set[str]]:
    """{类名: 方法名} —— Python 的作用域是**方法名**，不是类名。"""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, SyntaxError, ValueError):
        return {}
    out: dict[str, set[str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef):
            out[node.name] = {
                n.name for n in node.body
                if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
    return out


def python_analysis(source: str, source_rels: list[str],
                    base: Path) -> tuple[set[str], set[str]]:
    """Python 用例 ⇒ (运行时引用的符号, 可达的被测源文件)。

    `parse_imports` 只认 JS/TS ⇒ Python 用例的运行时作用域恒为空 ⇒
    「钉有区分力」在 Python 仓上恒判「不适用」（橡皮章）。这里按 Python 的
    import 语义补齐：

      - `from pkg.core import classify`  ⇒ 引用 classify
      - `import pkg.core` + `pkg.core.classify(...)` ⇒ 引用 classify
      - `from pkg.core import Rules` ⇒ 引用 Rules 的**方法名**
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return set(), set()
    scope: set[str] = set()
    reachable: set[str] = set()
    bindings: set[str] = set()
    class_cache: dict[str, dict[str, set[str]]] = {}

    def class_map(rel: str) -> dict[str, set[str]]:
        if rel not in class_cache:
            class_cache[rel] = _py_class_methods(base / rel)
        return class_cache[rel]

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                head = alias.name.split(".")[0]
                bindings.add(alias.asname or head)
                scope.add(alias.name.split(".")[-1])
                rel = _py_module_file(alias.name, source_rels)
                if rel:
                    reachable.add(rel)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            for alias in node.names:
                bindings.add(alias.asname or alias.name)
                scope.add(alias.name)
                scope.add(alias.asname or alias.name)
                rel = (_py_module_file(f"{node.module}.{alias.name}",
                                       source_rels)
                       or _py_module_file(node.module, source_rels))
                if rel:
                    reachable.add(rel)
                    scope |= class_map(rel).get(alias.name, set())
    # 属性链：`pkg.core.classify(...)` ⇒ classify
    for node in ast.walk(tree):
        if not isinstance(node, ast.Attribute):
            continue
        chain: list[str] = []
        cur: ast.AST = node
        while isinstance(cur, ast.Attribute):
            chain.append(cur.attr)
            cur = cur.value
        if isinstance(cur, ast.Name) and cur.id in bindings:
            scope.update(chain)
    return scope, reachable


def file_exports(path: Path) -> set[str]:
    """一个文件导出的符号名。"""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set()
    if path.suffix == ".py":
        out = {m.group(1) for m in _PY_DEF_RE.finditer(text)}
        out |= {m.group(1) for m in _PY_CLASS_RE.finditer(text)}
        return out
    code = _strip_js_code(text)
    out = {m.group(1) for m in _JS_EXPORT_DECL_RE.finditer(code)}
    for m in _JS_EXPORT_LIST_RE.finditer(code):
        for item in m.group(1).split(","):
            item = item.strip()
            if not item:
                continue
            if item.startswith("type "):
                item = item[5:].strip()
            if item:
                out.add(item.split(" as ")[-1].strip())
    if _JS_EXPORT_DEFAULT_RE.search(code):
        out.add("default")
    return out


# ---------------------------------------------------------------- 闭包


def _module_closure(root: Path, entry_rel: str) -> tuple[set[str], set[str]]:
    """从入口文件出发沿**相对路径** import 求传递闭包。

    ⇒ (闭包内的文件, 闭包内文件的全部导出符号)
    只认相对路径：经别名/包名可达的文件副本不密闭，不能改。
    """
    files: set[str] = set()
    symbols: set[str] = set()
    queue = [entry_rel]
    while queue:
        rel = queue.pop()
        if rel in files:
            continue
        p = root / rel
        if not p.is_file():
            continue
        files.add(rel)
        symbols |= file_exports(p)
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for ref in parse_imports(text, rel, root):
            if ref.relative and ref.target and ref.target not in files:
                queue.append(ref.target)
    return files, symbols


# ---------------------------------------------------------------- 变异标记


def _match_open_paren(code: str, close_idx: int) -> int:
    """与 `close_idx` 处 ")" 配对的 "(" 的位置（找不到 ⇒ -1）。"""
    depth = 0
    for j in range(close_idx, -1, -1):
        if code[j] == ")":
            depth += 1
        elif code[j] == "(":
            depth -= 1
            if depth == 0:
                return j
    return -1


def _is_function_body(code: str, open_idx: int) -> bool:
    """这个 `{` 是不是**函数体**（控制流 / 对象字面量 / 类体都不是）。"""
    before = code[:open_idx].rstrip()
    if before.endswith("=>"):
        return True
    close = before.rfind(")")
    if close < 0:
        return False
    open_paren = _match_open_paren(code, close)
    if open_paren < 0:
        return False
    head = re.sub(r"<[^<>]*>$", "", code[:open_paren].rstrip()).rstrip()
    m = re.search(r"([A-Za-z_$][\w$]*)$", head)
    if not m:
        return False
    return m.group(1) not in _CONTROL_KEYWORDS


def _enclosing_function_body(code: str, offset: int) -> int | None:
    """变异点所在的最内层**函数体**的 `{` 位置（不是函数就往外找）。"""
    stack: list[int] = []
    blocks: list[int] = []          # 收尾顺序 = 由外到内
    for i, ch in enumerate(code):
        if ch == "{":
            stack.append(i)
        elif ch == "}" and stack:
            o = stack.pop()
            if o < offset < i:
                blocks.append(o)
    for o in reversed(blocks):      # 由内到外
        if _is_function_body(code, o):
            return o
    return None


def _inject_marker(rel: str, text: str, mut) -> str:
    """给改坏的文件打标记：变异点所在函数一被调用就打出标记。

    标记打在**函数体入口**（不是文件末尾）—— 文件末尾的标记只说明「文件被
    加载了」，而真正要问的是「这段逻辑被跑到了没有」。
    """
    token = f"{MUTANT_MARKER}:{rel}"
    if mut.path.suffix == ".py":
        return _inject_marker_py(text, mut, token)
    code = _strip_js_code(text)
    open_idx = _enclosing_function_body(code, mut.offset)
    if open_idx is None:
        # 顶层变异 ⇒ 模块被加载就会打
        return f'{text}\nconsole.error("{token}");\n'
    return text[:open_idx + 1] + f' console.error("{token}");' + text[open_idx + 1:]


def _inject_marker_py(text: str, mut, token: str) -> str:
    """Python：在函数体第一行插一行标记（行号会移位，报告里报原始行号）。"""
    lines = text.split("\n")
    def_re = re.compile(r"^([ \t]*)(?:async[ \t]+)?def[ \t]+[A-Za-z_]\w*")
    best: tuple[int, int] | None = None
    for i, ln in enumerate(lines, start=1):
        if i >= mut.line:
            break
        m = def_re.match(ln)
        if not m:
            continue
        j = i
        while j < len(lines) and not lines[j].strip():
            j += 1
        if j >= len(lines):
            continue
        body_indent = len(lines[j]) - len(lines[j].lstrip())
        if body_indent <= len(m.group(1)):
            continue                # 单行 def ⇒ 没法插
        best = (i, body_indent)
    if best is None:
        return text
    i, body_indent = best
    marker = " " * body_indent + \
        f'__import__("sys").stderr.write("{token}\\n")'
    lines.insert(i, marker)
    return "\n".join(lines)


# ---------------------------------------------------------------- 判卷


#: 变异把代码改崩了 —— 这不是产品缺陷，是判不了
_LOAD_ERROR_RE = re.compile(
    r"SyntaxError|Unexpected token|Unexpected identifier|IndentationError|"
    r"Cannot find module|ERR_MODULE_NOT_FOUND|ERR_INVALID_TYPESCRIPT_SYNTAX|"
    r"ERR_UNSUPPORTED_TYPESCRIPT_SYNTAX")


def _default_classify(code: int, out: str, name: str) -> tuple[list[str], list[str]]:
    """默认判卷器：延导入 readiness（readiness 会导入 discriminate ⇒ 不能顶层导）。"""
    from readiness import _classify_run
    return _classify_run(code, out, name)


def _guarded_classify(base):
    """判卷前两道闸：加载失败 ⇒ 判不了；失败名去掉错误详情（只留用例路径）。"""
    def classify(code: int, out: str, name: str) -> tuple[list[str], list[str]]:
        if _LOAD_ERROR_RE.search(out):
            return [], [f"{name}: 变异后代码加载失败 ⇒ 判不了"]
        fails, setup = base(code, out, name)
        return [f.split(" | ")[0] for f in fails], setup
    return classify


def _py_compiles(text: str) -> bool:
    """这段 Python 能不能编译（真解析器，不是括号配平）。"""
    try:
        compile(text, "<attest-mutant>", "exec")
    except (SyntaxError, ValueError):
        return False
    return True


def _js_shape(text: str) -> tuple[int, int, int]:
    """JS/TS 的括号结构（开-闭之差）；字符串与注释不算。"""
    code = _strip_js_code(text)
    return (code.count("{") - code.count("}"),
            code.count("(") - code.count(")"),
            code.count("[") - code.count("]"))


#: 这些后缀走 TS AST 后端真解析（其余按括号结构兜底）。
_JS_TS_SUFFIXES = (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts")


def _js_parses(path: Path, text: str, original: str) -> bool:
    """JS/TS 变异体能不能解析（真解析器，不是括号配平）。

    括号配平看不见两类东西：语法坏了但括号照样配平的变异（`x === ;`）⇒
    变异体被当成「杀死」，假的绿；正则字面量里的括号（`/)/`）⇒ 合法代码
    被当成「解析不过」，假的 untested。

    判据是「变异相对原文」：变异解析不过**而原文能解析** ⇒ 这次变异把语法
    改坏了 ⇒ 判不了。原文本身也解析不过 ⇒ 账不在变异头上，放行。
    """
    root = _project_root(path)
    backend = _backend_for(root) if root is not None else None
    if backend is None:
        return _js_shape(text) == _js_shape(original)
    mut_ok, _why = ts_parses(backend, path, text, root)
    if mut_ok is None:
        return _js_shape(text) == _js_shape(original)
    if mut_ok:
        return True
    orig_ok, _why = ts_parses(backend, path, original, root)
    if orig_ok is None:
        return True
    return not orig_ok


def _default_parse_check(path: Path, text: str) -> bool:
    """默认解析校验：**判变异，不判文件**。

    闸门要回答的是「这次变异有没有把代码改坏」，不是「这段代码本身干不干净」。
    拿绝对标准去判，会把**原文就不标准**的真代码（正则字面量里的括号、跑在
    旧 Python 上的新语法）判成「解析不过」⇒ 能判的变异点全被推成「判不了」
    —— 假的 untested。

      - Python：变异后能编译就算过；编译不过时再看**原文能不能编译**——
        原文也编译不过 ⇒ 问题不在这次变异上。
      - JS/TS：用仓库那份 TypeScript 真解析一遍；没有可用后端 ⇒ 退回
        比对变异前后的括号结构。

    原文从磁盘上读：调用方保证检查那一刻磁盘上还是原文（变异还没写下去）。
    """
    try:
        original = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        original = text
    if path.suffix == ".py":
        return _py_compiles(text) or not _py_compiles(original)
    if path.suffix in _JS_TS_SUFFIXES:
        return _js_parses(path, text, original)
    return _js_shape(text) == _js_shape(original)


# ---------------------------------------------------------------- 主流程


def _sample_invariants(invariants: list[tuple[str, str, str]],
                       budget: int = BUDGET) -> list[tuple[str, str, str]]:
    """超预算 ⇒ 等距抽样（确定性，不随机）。"""
    if len(invariants) <= budget:
        return list(invariants)
    stride = math.ceil(len(invariants) / budget)
    return [inv for i, inv in enumerate(invariants) if i % stride == 0][:budget]


def _alias_notes(root: Path, test_rel: str, source_set: set[str]) -> list[str]:
    """经别名/包名可达 ⇒ 副本不密闭 ⇒ 那些文件不变异。"""
    notes: list[str] = []
    p = root / test_rel
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return notes
    for ref in parse_imports(text, test_rel, root):
        if ref.relative:
            continue
        if ref.target and ref.target in source_set:
            notes.append(f"{ref.target} 经包路径可达（{ref.spec}）"
                         " ⇒ 副本不密闭 ⇒ 不变异")
        elif ref.target is None and not ref.spec.startswith("node:"):
            notes.append(f"测试文件经包名引用 {ref.spec}"
                         " ⇒ 经包路径可达 ⇒ 副本不密闭 ⇒ 被引模块不变异")
    return notes


def _verdict(inv_id: str, status: str, detail: str, evidence: list[str],
             killed: int, survivors: list[dict], *, pin: str = "",
             test: str = "") -> dict:
    return asdict(Verdict(invariant_id=inv_id, status=status, detail=detail,
                           evidence=evidence, killed=killed, survivors=survivors,
                           pin=pin, test=test))


def _pin_to_ws_rel(base: Path, ws_rel: str, test_rel: str) -> str | None:
    """把钉的路径归一成**工作区相对**（变异检查的契约）。

    钉的路径基准是**仓库根**（规格 §3：判定点的 file 与钉同基准），
    变异检查却在**工作区**里跑 ⇒ monorepo 里两者错位 ⇒ 每条钉都变成
    「文件不存在」⇒ 全判不了。这里两种基准各试一次，都不在 ⇒ None。
    """
    if (base / test_rel).is_file():
        return test_rel
    if ws_rel not in (".", ""):
        prefix = ws_rel.rstrip("/") + "/"
        if test_rel.startswith(prefix):
            rel = test_rel[len(prefix):]
            if (base / rel).is_file():
                return rel
    return None


def run_dpsp(repo: Path, ws_rel: str,
             invariants: list[tuple[str, str, str]],
             runner: list[str], test_rels: list[str],
             *, source_rels: list[str], timeout: int = 120,
             max_mutants: int = 6, level: str = "L3",
             budget: int = BUDGET, parse_check=None, classify=None) -> dict:
    """每条不变式跑一轮「把代码改坏 ⇒ 看钉住的用例会不会红」。

    `invariants` 是 (不变式 id, 钉的单元格, 不变式文本) 三元组；
    钉的单元格按 `path::用例名` 解析（规格 §5）。
    `budget` 是本轮最多抽样多少条不变式（超预算等距抽样，规格 §7.4）。
    返回：
        checked        判了多少条
        total          一共多少条
        killed         有区分力的不变式数（规格 §7.4 的「杀死率」）
        not_applicable 不适用（零变异点 / 变异没生效 / 不是相对路径可达）
        untested      判不了（环境没就绪 / 输出认不出）
        survivors      杀不死的变异点
        verdicts       每条一份四档结论
        notes          说明与阻碍
    """
    total = len(invariants)
    picked = _sample_invariants(invariants, budget)
    base = repo if ws_rel in (".", "") else repo / ws_rel
    ops = OPERATORS if level == "L3" else L2_OPERATORS
    base_classify = classify if classify is not None else _default_classify
    judge = _guarded_classify(base_classify)
    check = parse_check if parse_check is not None else _default_parse_check

    verdicts: list[dict] = []
    survivors: list[dict] = []
    notes: list[str] = []
    killed_inv = 0
    not_applicable = 0
    untested = 0

    for inv_id, pin_cell, _text in picked:
        pins = parse_pins(pin_cell)
        if not pins:
            untested += 1
            verdicts.append(_verdict(inv_id, "untested",
                                     f"钉解析不出来：{pin_cell}", [], 0, []))
            continue
        test_rel, case_name = pins[0]
        ws_rel_path = _pin_to_ws_rel(base, ws_rel, test_rel)
        if ws_rel_path is None:
            untested += 1
            verdicts.append(_verdict(inv_id, "untested",
                                     f"钉的文件不存在：{test_rel}", [], 0, []))
            continue
        test_rel = ws_rel_path
        alias = _alias_notes(base, test_rel, set(source_rels))
        try:
            test_text = (base / test_rel).read_text(encoding="utf-8",
                                                    errors="replace")
        except OSError:
            test_text = ""
        # 变异范围 = 钉住的那个用例**运行时引用的符号**（规格 §6.1 的 S）。
        # 用整个闭包的导出符号会把「别的函数」的变异也算进来 ⇒ 存活 ⇒ 假红。
        if test_rel.endswith(".py"):
            scope, reachable = python_analysis(test_text, source_rels, base)
            rel_sources = [s for s in source_rels if s in reachable]
        else:
            closure_files, _ = _module_closure(base, test_rel)
            rel_sources = [s for s in source_rels if s in closure_files]
            scope = runtime_symbols(parse_imports(test_text, test_rel, base))
        if not rel_sources:
            not_applicable += 1
            detail = "没有相对路径可达的源文件"
            if alias:
                detail += "；" + "；".join(alias)
            notes.extend(alias)
            verdicts.append(_verdict(inv_id, "not-applicable", detail,
                                     alias, 0, [], pin=pin_cell, test=test_rel))
            continue
        if not scope:
            not_applicable += 1
            detail = "钉没有运行时引用目标模块的任何符号 ⇒ 零覆盖"
            notes.append(f"{test_rel}：{detail}")
            verdicts.append(_verdict(inv_id, "not-applicable", detail, [], 0, [],
                                     pin=pin_cell, test=test_rel))
            continue
        if test_rels and test_rel not in test_rels:
            notes.append(f"{test_rel} 不在项目测试文件清单里（钉可能指错）")
        res = run_mutation_check(repo, ws_rel, rel_sources, [test_rel], runner,
                                 max_mutants=max_mutants, timeout=timeout,
                                 classify=judge, case_name=case_name,
                                 scope_symbols=scope, ops=ops,
                                 parse_check=check, inject=_inject_marker)
        surv = list(res["survivors"])
        survivors.extend(surv)
        evidence = list(res["notes"])
        if res["killed"] >= 1:
            status = "pass"
            killed_inv += 1
            detail = f"杀死 {res['killed']} 个变异"
        elif surv:
            status = "fail"
            detail = f"{len(surv)} 个变异全部存活 ⇒ 这条钉发现不了这类缺陷"
        elif res["unknown"]:
            status = "untested"
            detail = "判不了（环境没就绪 / 输出认不出）"
        else:
            status = "not-applicable"
            detail = "没有可判的变异点（零变异点或变异没生效）"
            not_applicable += 1
        if status in ("fail", "untested"):
            notes.extend(evidence)
        verdicts.append(_verdict(inv_id, status, detail, evidence,
                                 res["killed"], surv, pin=pin_cell,
                                 test=test_rel))

    if total > len(picked):
        budget_note = (f"共 {total} 条不变式，按预算只判了 {len(picked)} 条"
                       f"（{len(picked) * 100 // total}%）")
    else:
        budget_note = f"共 {total} 条不变式，全部判定"
    return {
        "checked": len(picked),
        "total": total,
        "killed": killed_inv,
        "not_applicable": not_applicable,
        "untested": untested,
        "survivors": survivors,
        "verdicts": verdicts,
        "notes": notes,
        "budget_note": budget_note,
    }


if __name__ == "__main__":
    print(__doc__)
