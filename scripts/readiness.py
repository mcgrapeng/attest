#!/usr/bin/env python3
"""需求交付就绪度分析：一条命令，实测。

    req-plan readiness <模块路径> --repo <根> [--plan <计划文件>] [--json]

回答的是"这个模块能不能直接交付给客户"。

每条判据要么有**实测证据**（跑测试出的覆盖率、查 PG 出的表结构），
要么明确标「未实测（缺什么条件）」—— 不猜、不用静态推断冒充实测。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from req_plan import case_matches, parse_pins, parse_plan, test_case_names  # noqa: E402

CODE_EXT = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
            ".py", ".pyi"}
SKIP_DIRS = {"node_modules", ".git", "dist", "build", "coverage", ".next",
             "out", "target", ".venv", "venv"}

# 仓库级测试目录（独立于模块目录的常见布局）
REPO_TEST_DIRS = ("test", "tests", "spec")

# 子进程环境白名单 —— 目标仓库的脚本只拿到这些变量，其余不透传。
# 之前是 `{**os.environ}`：父进程里的一切（含各家的密钥）都进了子进程。
ENV_ALLOW_EXACT = {
    "PATH", "HOME", "LANG", "LANGUAGE", "TZ", "TMPDIR", "TEMP", "TMP",
    "SHELL", "USER", "LOGNAME", "TERM", "SYSTEMROOT", "COMSPEC", "CI",
    # 工具自己要用、目标仓库的脚本也可能要的
    "POSTGRES_URL", "DATABASE_URL", "MEMORY_ITEST",
    "MEMORY_RECONCILE_OUT_DIR", "REQ_PLAN_RECONCILE_OUT_DIR",
}
ENV_ALLOW_PREFIX = ("LC_", "NODE_", "PYTHON", "NPM_", "PNPM_", "YARN_",
                    "PIP_", "UV_")

# 脚本白名单 —— 只认这些名字（前缀式：脚本名用冒号分段，前若干段命中即算）。
# 之前是子串匹配（"coverage" in name）⇒ `reconcile:reset` 这种也会被选中执行。
SCRIPT_NAMES = {
    "gate": {
        "exact": {"coverage", "test:coverage", "cov", "check:coverage",
                  "coverage:check", "test:cov"},
        "prefix": (("test", "coverage"), ("test", "cov"), ("check", "coverage")),
    },
    "itest": {
        "exact": {"itest", "test:itest", "integration", "test:integration"},
        "prefix": (("test", "integration"), ("test", "itest")),
    },
    "reconcile": {
        "exact": {"reconcile", "data:reconcile", "test:reconcile"},
        "prefix": (("data", "reconcile"), ("test", "reconcile")),
    },
    "baseline": {
        "exact": {"baseline:check", "eval:baseline:check", "test:baseline"},
        "prefix": (("eval", "baseline", "check"), ("test", "baseline")),
    },
}

# 测试支撑文件 —— 不是被测的核心逻辑
_SUPPORT_RE = re.compile(
    r"(itest-support|test-support|spec-support|fixtures?/|/helpers/|"
    r"\.e2e-support|setup/|/test-utils?/)", re.I)

# 通用名 —— 同名不等于重复实现（errorMessage / helper / config 这类到处都有）
_GENERIC_NAMES = {
    "error", "errmsg", "errormessage", "helper", "helpers", "util", "utils",
    "config", "options", "result", "results", "data", "value", "values",
    "item", "items", "name", "names", "type", "types", "id", "ids", "key",
    "keys", "index", "main", "default", "init", "create", "update", "delete",
    "get", "set", "list", "run", "start", "stop", "test", "check", "parse",
    "format", "build", "load", "save", "handle", "process", "execute", "map",
    "filter", "reduce", "clone", "copy", "move", "sort", "find", "search",
}

# 只在编译期存在的导出 —— 不需要测试引用
_TYPE_ONLY = {"interface", "type"}


# ---------------------------------------------------------------- 数据结构


@dataclass
class Check:
    key: str
    title: str
    status: str          # pass / fail / warn / skipped / untested
    detail: str = ""
    evidence: list[str] = field(default_factory=list)
    fix: str = ""
    # 证据类别：executed（实测）/ static（静态信号）/ untested（没测到）。
    # G004 用它分层输出 —— 静态信号永远不能单独支撑"通过"。
    evidence_kind: str = "static"

    @property
    def mark(self) -> str:
        return {"pass": "✓", "fail": "✗", "warn": "·", "skipped": "—",
                "untested": "○"}[self.status]


# 本工具不判定的事 —— 固定清单，每份报告都列。
# 为什么要有：工具只判机械判据（测试绿、覆盖率、跨存储对账、表结构…）。
# 需求本身对不对、产品行为、性能、安全、可用性、运维就绪都不在射程里。
# 列出来是让读报告的人知道边界在哪，而不是以为"全绿了就能交付"。
NOT_JUDGED = (
    "需求是否被正确理解",
    "产品行为是否正确",
    "性能",
    "安全",
    "可用性",
    "运维就绪",
)


@dataclass
class Module:
    root: Path
    path: str
    base: Path
    sources: list[Path] = field(default_factory=list)
    tests: list[Path] = field(default_factory=list)
    itests: list[Path] = field(default_factory=list)
    support: list[Path] = field(default_factory=list)
    repo_tests: list[Path] = field(default_factory=list)
    tables: list[str] = field(default_factory=list)
    deps: list[str] = field(default_factory=list)
    workspace: Path | None = None
    lang: str = "other"      # ts / py / other


def child_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """给子进程的环境：只透传白名单里的，其余不透传。

    之前是 `{**os.environ}` —— 父进程里的一切（含别的服务的密钥）都进了
    目标仓库的脚本。白名单之外的一律不透传。
    """
    env: dict[str, str] = {}
    for k, v in os.environ.items():
        if k in ENV_ALLOW_EXACT or k.startswith(ENV_ALLOW_PREFIX):
            env[k] = v
    env["PYTHONUTF8"] = "1"
    if extra:
        env.update(extra)
    return env


# ---------------------------------------------------------------- 发现


def _walk(root: Path, exts: set[str]):
    import os
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d for d in dirnames
            if d not in SKIP_DIRS and not d.startswith(".")
            and d != "worktrees"
        ]
        for fn in filenames:
            if Path(fn).suffix in exts:
                yield Path(dirpath) / fn


def _looks_like_test_file(p: Path) -> bool:
    """这个文件名像测试文件吗（仓库级测试目录里的判定）。"""
    name = p.name
    if re.search(r"\.(test|spec|itest)\.(ts|tsx|js|jsx|mjs|cjs|py)$", name):
        return True
    return bool(re.search(r"^(test_[^/]+|[^/]+_test)\.py$", name))


def _module_tokens(m: Module) -> list[str]:
    """这个模块在测试文件里可能出现的名字（路径 / 目录名 / Python 包名）。"""
    toks: list[str] = []
    try:
        rel = str(m.base.relative_to(m.root))
    except ValueError:
        rel = ""
    if rel and rel != ".":
        toks.append(rel)
        toks.append(Path(rel).name)
    pkg = python_package_dir(m)
    if pkg:
        toks.append(pkg.name)
    out: list[str] = []
    for t in toks:
        if t and t not in out:
            out.append(t)
    return out


def _mentions_module_path(p: Path, tokens: list[str]) -> bool:
    """这个测试文件提到这个模块吗（路径片段或文件名）。"""
    for tag in tokens:
        if tag and tag in p.name:
            return True
    if not tokens:
        return False
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    for tag in tokens:
        pat = r"(?<![A-Za-z0-9_.-])" + re.escape(tag) + r"(?![A-Za-z0-9_-])"
        if re.search(pat, text):
            return True
    return False


def repo_level_tests(root: Path, m: Module,
                     extra_dirs: list[str] | None = None
                     ) -> tuple[list[Path], list[Path]]:
    """仓库级测试目录（`test/` `tests/` `spec/`）里的测试文件。

    返回 (全部仓库级测试文件, 归属于这个模块的测试文件)。

    模块目录之外的测试也是这个模块的测试 —— 之前只 walk 模块目录，
    于是 `test/` 布局的仓库里健康代码被误判成"导出零引用"。
    归属判定：文件提到这个模块（路径/目录名/Python 包名）才算它的测试；
    `--test-dir` 显式指定的目录无条件算。
    """
    dirs: list[Path] = []
    explicit: set[Path] = set()
    for d in (extra_dirs or []):
        p = Path(d)
        p = p if p.is_absolute() else (root / p)
        dirs.append(p)
        explicit.add(p)
    dirs += [root / d for d in REPO_TEST_DIRS]
    tokens = _module_tokens(m)
    all_files: list[Path] = []
    attributed: list[Path] = []
    seen: set[Path] = set()
    for d in dirs:
        if not d.is_dir():
            continue
        for p in sorted(d.rglob("*")):
            if not p.is_file() or p in seen:
                continue
            if not _looks_like_test_file(p):
                continue
            seen.add(p)
            all_files.append(p)
            if p in explicit or _mentions_module_path(p, tokens):
                attributed.append(p)
    return all_files, attributed


_JS_IMPORT_RES = (
    re.compile(r"from\s+['\"]([^'\"]+)['\"]"),              # import/export … from 'x'
    re.compile(r"require\(\s*['\"]([^'\"]+)['\"]\s*\)"),    # require('x')
    re.compile(r"^\s*import\s+['\"]([^'\"]+)['\"]", re.M),  # import 'x'
)
_PY_IMPORT_RE = re.compile(r"^\s*import\s+([A-Za-z_][\w.]*)", re.M)
_PY_FROM_RE = re.compile(r"^\s*from\s+([A-Za-z_][\w.]*)\s+import\b", re.M)
# Python 标准库（3.10+ 有这个清单）—— 标准库不是第三方依赖
_PY_STDLIB = set(getattr(sys, "stdlib_module_names", ()))


def _import_specs(text: str, suffix: str) -> set[str]:
    """文件里引用的第三方依赖。

    JS/TS：`from 'x'`、`require('x')`、`import 'x'` 三种写法都算。
    Python：`import x` 与 `from x import y`（取顶层包名，排除标准库）。
    """
    out: set[str] = set()
    if suffix in (".py", ".pyi"):
        specs = set()
        for mm in _PY_IMPORT_RE.finditer(text):
            specs.add(mm.group(1))
        for mm in _PY_FROM_RE.finditer(text):
            specs.add(mm.group(1))
        for spec in specs:
            if spec.startswith("."):
                continue                      # 相对导入（包内）
            root = spec.split(".")[0]
            if not root or root in _PY_STDLIB:
                continue
            out.add(root)
        return out
    for rx in _JS_IMPORT_RES:
        for mm in rx.finditer(text):
            spec = mm.group(1)
            # 内部路径别名（@/…）与相对路径不是第三方库
            if spec.startswith(".") or spec.startswith("node:"):
                continue
            # 路径别名（@config/env.ts、@lib/x.ts）不是包名
            if re.search(r"\.(ts|tsx|js|jsx|mjs|cjs|json)$", spec):
                continue
            if spec.startswith("@/") or spec.startswith("~"):
                continue
            if "/" in spec and not spec.startswith("@"):
                continue          # 子路径导入，不是包名
            out.add(spec)
    return out


def discover_module(root: Path, modpath: str,
                    test_dirs: list[str] | None = None) -> Module:
    m = Module(root=root, path=modpath, base=root / modpath)
    if not m.base.exists():
        return m
    files = [m.base] if m.base.is_file() else list(_walk(m.base, CODE_EXT))
    for p in files:
        rel = str(p.relative_to(root))
        if p.suffix == ".pyi":
            continue          # 类型存根（protobuf 生成的）不是逻辑
        if ".itest." in rel:
            m.itests.append(p)
        elif re.search(r"\.(test|spec)\.(ts|tsx|js|jsx|mjs|cjs)$", rel):
            m.tests.append(p)
        elif re.search(r"(^|/)(test_[^/]+|[^/]+_test)\.pyi?$", rel):
            m.tests.append(p)
        elif _SUPPORT_RE.search(rel):
            m.support.append(p)      # 测试支撑，不是核心逻辑
        else:
            m.sources.append(p)
    # 仓库级测试目录（test/ tests/ spec/ 或 --test-dir 指定的）
    all_repo_tests, attributed = repo_level_tests(root, m, test_dirs)
    m.repo_tests.extend(all_repo_tests)
    for p in attributed:
        rel = str(p.relative_to(root)) if p.is_relative_to(root) else str(p)
        if ".itest." in rel:
            m.itests.append(p)
        else:
            m.tests.append(p)
    for up in [m.base] + list(m.base.parents):
        if (up / "package.json").exists():
            m.workspace = up
            break
    # 模块语言（决定哪些判据适用）
    py = sum(1 for p in m.sources if p.suffix in (".py", ".pyi"))
    js = len(m.sources) - py
    m.lang = "py" if py > js else ("ts" if js else "other")
    tables: set[str] = set()
    deps: set[str] = set()
    for p in m.sources:
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for mm in re.finditer(r"pgTable\(\s*['\"](\w+)['\"]", text):
            tables.add(mm.group(1))
        for mm in re.finditer(r"__tablename__\s*=\s*['\"](\w+)['\"]", text):
            tables.add(mm.group(1))
        for mm in re.finditer(r"\bTable\(\s*['\"](\w+)['\"]", text):
            tables.add(mm.group(1))
        for spec in _import_specs(text, p.suffix):
            deps.add(spec)
    m.tables = sorted(tables)
    m.deps = sorted(deps)
    return m


# ---------------------------------------------------------------- 全仓索引（一次扫描）


@dataclass
class RepoIndex:
    texts: list[tuple[str, str]] = field(default_factory=list)
    tokens: Counter = field(default_factory=Counter)
    exports: dict[str, str] = field(default_factory=dict)   # 符号 → 出处


def index_repo(root: Path) -> RepoIndex:
    """扫全仓一次：文本、标识符词频、导出符号。

    逐个符号走全仓是 O(符号数 × 文件数) —— agent-memory 上百个导出符号，
    那是几十万次文件读。
    """
    idx = RepoIndex()
    tok_re = re.compile(r"[A-Za-z_$][\w$]*")
    exp_re = re.compile(
        r"^\s*export\s+(?:default\s+)?(?:async\s+)?"
        r"(function|const|let|var|class|interface|type|enum)\s+"
        r"([A-Za-z_$][\w$]*)", re.M)
    for p in _walk(root, CODE_EXT):
        rel = str(p.relative_to(root))
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        idx.texts.append((rel, text))
        idx.tokens.update(tok_re.findall(text))
        if "/node_modules/" in f"/{rel}" or re.search(r"\.(test|spec)\.", rel):
            continue
        for mm in exp_re.finditer(text):
            idx.exports.setdefault(mm.group(2), rel)
    return idx


# ---------------------------------------------------------------- 实测：覆盖率


def project_test_cmd(m: Module) -> tuple[list[str], str]:
    """项目声明的测试命令（runner + flags），文件部分我们自己换。

    必须用项目自己的 runner —— lumen 用 tsx（解析 tsconfig 路径别名），
    裸 node --test 会让一堆测试假挂。
    """
    import shlex
    if not m.workspace:
        return ["node", "--test"], "(默认)"
    try:
        scripts = (json.loads((m.workspace / "package.json").read_text(
            encoding="utf-8")).get("scripts") or {})
    except (OSError, json.JSONDecodeError):
        return ["node", "--test"], "(默认)"
    for key in ("test:unit", "test"):
        cmd = scripts.get(key)
        if not cmd or not isinstance(cmd, str):
            continue
        parts = shlex.split(cmd)
        # 去掉文件 glob（末尾那个带 * 或 .test.* 的）
        while parts and ("*" in parts[-1]
                         or ".test." in parts[-1]
                         or ".spec." in parts[-1]):
            parts.pop()
        if parts:
            return parts, key
    return ["node", "--test"], "(默认)"


def _failed_tests(out: str) -> list[str]:
    """从 node 测试输出里挑出失败的用例名。"""
    return [mm.group(1).strip()
            for mm in re.finditer(r"^not ok \d+ - (.+)$", out, re.M)]


_COV_ROW = re.compile(
    r"^#\s+(\S+\.(?:ts|tsx|js|mjs|cjs))\s*\|\s*([\d.]+)\s*\|\s*"
    r"([\d.]+)\s*\|\s*([\d.]+)\s*\|(.*)$")


def _parse_cov_rows(out: str) -> dict[str, dict]:
    """解析 node 的覆盖率表，顺带把「未覆盖行区间」解出来。

    未覆盖区间是并集法的关键：覆盖 = 各次未覆盖区间的补集的并集
    ⇔ 未覆盖 = 各次未覆盖区间的交集。
    """
    cov: dict[str, dict] = {}
    for line in out.splitlines():
        mm = _COV_ROW.match(line)
        if not mm:
            continue
        fname = mm.group(1)
        # 只算生产代码 —— 测试文件自己、以及测试支撑文件，都不算"核心逻辑"
        if re.search(r"\.(test|spec)\.|\.itest\.", fname):
            continue
        if _SUPPORT_RE.search(fname):
            continue
        ranges: set[int] = set()
        for part in mm.group(5).split():
            rm = re.match(r"^(\d+)(?:-(\d+))?$", part)
            if rm:
                a = int(rm.group(1))
                b = int(rm.group(2) or a)
                ranges.update(range(a, b + 1))
        cov[fname] = {
            "line": float(mm.group(2)),
            "branch": float(mm.group(3)),
            "func": float(mm.group(4)),
            "_uncovered": ranges,
        }
    # node 的汇总行（"all files"）—— 闸门自己的判定就是拿它比阈值
    am = re.search(r"^#\s+all files\s*\|\s*([\d.]+)\s*\|\s*([\d.]+)"
                    r"\s*\|\s*([\d.]+)", out, re.M)
    if am:
        cov["__all__"] = {"line": float(am.group(1)),
                          "branch": float(am.group(2)),
                          "func": float(am.group(3)),
                          "_uncovered": set()}
    return cov


def _merge_coverage(per_run: list[dict[str, dict]]) -> dict[str, dict]:
    """把多次（逐文件）跑出来的覆盖率并起来。

    - 行覆盖率：未覆盖行取交集 ⇒ 稳定且语义正确（"有没有被任何测试执行过"）
    - 分支/函数：没有区间数据可并 ⇒ 取最大值（偏保守）
    """
    names = set().union(*(set(r) for r in per_run)) if per_run else set()
    out: dict[str, dict] = {}
    for name in names:
        recs = [r[name] for r in per_run if name in r]
        # 可执行行总数：node 给的 line% = 已覆盖/总数，未覆盖区间给出未覆盖数
        #   total = 未覆盖数 / (1 - line%)；各次推出来应当一致
        totals = []
        for rec in recs:
            pct = rec["line"]
            if 0 < pct < 100 and rec["_uncovered"]:
                totals.append(round(len(rec["_uncovered"]) / (1 - pct / 100)))
        never = set.intersection(*[rec["_uncovered"] for rec in recs]) if recs else set()
        if totals:
            total = max(totals)
            line = (total - len(never)) / total * 100
        else:
            line = max(rec["line"] for rec in recs)
        out[name] = {
            "line": round(line, 2),
            "branch": max(rec["branch"] for rec in recs),
            "func": max(rec["func"] for rec in recs),
        }
    return out


def run_gates(m: Module, gates: list[tuple[Path, str, str]],
               timeout: int = 900
               ) -> tuple[dict[str, dict], list[str], list[str]]:
    """跑仓库自己的覆盖率闸门 ⇒ (逐文件覆盖率, 失败用例, 逐闸说明)。"""
    per_run: list[dict[str, dict]] = []
    failed: list[str] = []
    notes: list[str] = []
    for ws, name, cmd in gates:
        code, out, _ = run_gate(ws, name, cmd, timeout=timeout)
        rows = _parse_cov_rows(out)
        if rows:
            per_run.append(rows)
        failed += [f"{name}: {x}" for x in _failed_tests(out)]
        agg = rows.get("__all__")
        detail = ""
        if agg:
            detail = (f"行 {agg['line']:.0f}% / 分支 {agg['branch']:.0f}% / "
                      f"函数 {agg['func']:.0f}%")
        if code == 0:
            mark = "✓ 通过"
        elif code == 127:
            mark = "— 跑不起来（环境没就绪，不是阈值没过）"
        else:
            mark = "✗ 未达阈值"
        notes.append(f"{name}: {mark}"
                     + (f"（{detail}）" if detail else "")
                     + ("" if code == 0 else f"（退出码 {code}）"))
    cov = _merge_coverage(per_run) if per_run else {}
    return cov, failed, notes


# 环境没就绪的报错特征（连不上 Milvus/PG、鉴权失败、连接超时）
_ENV_ERROR_RE = re.compile(
    r"ECONNREFUSED|ECONNRESET|ETIMEDOUT|InvalidPassword|"
    r"password authentication failed|connection refused|Connection refused|"
    r"ConnectTimeout|Milvus.*(not.*running|unavailable)|"
    r"could not connect|ConnectionError|connection error|"
    r"MilvusException|ServiceUnavailable", re.I)


def _failed_tests_detail(out: str) -> list[str]:
    """失败用例：标题 **+ 紧随其后的 YAML 块里的 error 详情**。

    只看标题判不出"环境没就绪"—— 标题是用例名，报错细节在 `error:` 行
    （例：`14 UNAVAILABLE: ... connect ECONNREFUSED 127.0.0.1:19999`）。
    """
    res: list[str] = []
    lines = out.splitlines()
    for i, ln in enumerate(lines):
        m = re.match(r"^not ok \d+ - (.+)$", ln)
        if not m:
            continue
        title = m.group(1)
        detail = ""
        for j in range(i + 1, min(i + 40, len(lines))):
            if re.match(r"^not ok \d+ - ", lines[j]):
                break
            em = re.match(r"^\s*error:\s*'?(.+?)'?\s*$", lines[j])
            if em:
                detail = em.group(1)
                break
        res.append(f"{title} | {detail}" if detail else title)
    return res


def _node_summary(out: str) -> dict[str, int]:
    """node 测试运行器的汇总（# tests / pass / fail / skipped）。"""
    out_d: dict[str, int] = {}
    for key in ("tests", "pass", "fail", "skipped"):
        mm = re.search(rf"^#\s+{key}\s+(\d+)", out, re.M)
        out_d[key] = int(mm.group(1)) if mm else 0
    return out_d


def coverage_globs(m: Module, inc: str) -> list[str]:
    """覆盖率 include 的 glob —— 按模块里**实际存在**的扩展名推导。

    之前写死 `**/*.ts` ⇒ JavaScript 项目（.js/.mjs）永远拿不到覆盖率。
    """
    exts = sorted({p.suffix for p in m.sources if p.suffix in CODE_EXT})
    if not exts:
        exts = [".ts"]
    return [f"{inc}/**/*{e}" for e in exts]


def coverage_flags(m: Module, inc: str, runner: str) -> list[str]:
    """按 runner 给覆盖率参数（include glob 按实际扩展名逐个给）。"""
    globs = coverage_globs(m, inc)
    if runner in ("node", "tsx", "nodejs"):
        return ["--experimental-test-coverage"] + \
            [f"--test-coverage-include={g}" for g in globs]
    if runner == "vitest":
        return ["--coverage"] + [f"--coverage.include={g}" for g in globs]
    if runner == "jest":
        return ["--coverage", "--collectCoverageFrom"] + globs
    return []


def run_itest_lane(m: Module, scripts: list[tuple[Path, str, str]],
                   timeout: int = 900
                   ) -> tuple[dict[str, dict], list[str], list[str], list[str]]:
    """跑仓库自己的真后端车道，并顺带取它的覆盖率。

    区分三件事（与「跑不起来 ≠ 不达标」同一条规矩）：
    - 用例全绿 ⇒ 通过
    - 用例真失败（断言没过）⇒ 阻塞
    - **环境没就绪**（连不上 Milvus/PG，车道根本没跑起来）⇒ skipped，
      不算产品缺陷
    """
    per_run: list[dict[str, dict]] = []
    failed: list[str] = []
    env_errors: list[str] = []
    notes: list[str] = []
    base = str(m.base.relative_to(m.workspace)) if m.workspace else m.path
    inc = coverage_flags(m, base, "node")
    for ws, name, cmd in scripts:
        code, out, _ = run_gate(ws, name, cmd, extra=inc, timeout=timeout)
        rows = _parse_cov_rows(out)
        if rows:
            per_run.append(rows)
        summ = _node_summary(out)
        fails = _failed_tests_detail(out)
        if summ["tests"] == 0:
            # 一个用例都没跑 ⇒ 车道被跳过或没起来
            env_errors.append(f"{name}: 车道没跑起来（0 个用例）")
            notes.append(f"{name}: — 车道没跑起来（0 个用例，环境没就绪）")
            continue
        if fails:
            env_like = [f for f in fails if _ENV_ERROR_RE.search(f)]
            if len(env_like) == len(fails):
                env_errors += [f"{name}: {f}" for f in env_like]
                notes.append(f"{name}: — 环境没就绪（{len(fails)} 个用例连不上"
                             f"Milvus/PG，不算产品缺陷）")
                continue
        failed += [f"{name}: {x}" for x in fails]
        notes.append(f"{name}: {'✓ 用例全绿' if code == 0 and not fails else '✗ 有失败'}"
                     f"（{summ['pass']}/{summ['tests']} 通过，退出码 {code}）")
    cov = _merge_coverage(per_run) if per_run else {}
    return cov, failed, notes, env_errors


def run_coverage(m: Module, timeout: int = 120,
                 include_itest: bool = False
                 ) -> tuple[dict[str, dict], list[str], str]:
    """真跑模块的测试，拿真实覆盖率。

    默认只跑**单元测试车道**（*.test.ts）。*.itest.ts 是"真后端"车道，
    要连 PG + 向量库，另算 —— 混进来会挂住。
    """
    tests = m.tests + (m.itests if include_itest else [])
    if not tests or not m.workspace:
        return {}, [], "没有找到该模块的测试文件"
    # include 的 glob 要相对**工作区根**（测试从 workspace 跑）——
    # 用仓库相对路径会永远匹配不到，per-file 覆盖率全丢
    inc = str(m.base.relative_to(m.workspace)) if m.workspace else m.path
    prefix, script_name = project_test_cmd(m)
    runner = prefix[0] if prefix else "node"
    cmd = list(prefix)
    cmd += coverage_flags(m, inc, runner)
    cmd_base = list(cmd)
    env = child_env()
    if include_itest:
        # itest 车道默认跳过（要 MEMORY_ITEST=1 + 本地 Milvus/PG）
        env["MEMORY_ITEST"] = "1"
    bin_dir = m.workspace / "node_modules" / ".bin"
    if bin_dir.is_dir():
        env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    # 逐个测试文件跑：各自独立进程 ⇒ node 的覆盖率合并不竞争 ⇒ 数字确定。
    # 整包并发跑在用了 mock.module() 的项目里，合并会竞态（同样的命令能跑出
    # 7% 和 59% 两个覆盖率）—— 一个会随机变的数字不能用来判交付。
    from concurrent.futures import ThreadPoolExecutor

    def one(t: Path) -> tuple[Path, str, str]:
        c = cmd_base + [str(t.relative_to(m.workspace))]
        try:
            r = subprocess.run(c, cwd=m.workspace, capture_output=True,
                               text=True, encoding="utf-8", timeout=timeout,
                               env=env)
        except subprocess.TimeoutExpired:
            return t, "", f"{t.name}: 测试跑超时（{timeout}s）"
        except FileNotFoundError as e:
            return t, "", f"项目的测试命令跑不起来（{e.filename}）"
        return t, r.stdout + r.stderr, ""

    jobs = max(1, min(8, (os.cpu_count() or 2)))
    with ThreadPoolExecutor(max_workers=jobs) as pool:
        results = list(pool.map(one, tests))
    per_run: list[dict[str, dict]] = []
    failed: list[str] = []
    notes: list[str] = []
    for t, out, err in results:
        if err:
            notes.append(err)
            continue
        failed += [f"{t.name}: {x}" for x in _failed_tests(out)]
        rows = _parse_cov_rows(out)
        if rows:
            per_run.append(rows)
    if not per_run:
        ran_any = any(not err for _, _, err in results)
        if ran_any:
            return {}, failed, ("测试跑了，但没解析出覆盖率表"
                                "（runner 可能不支持覆盖率参数）")
        return {}, failed, ("；".join(notes) or
                            "没解析出覆盖率表（测试可能没跑起来）")
    cov = _merge_coverage(per_run)
    note = (f"逐个测试文件跑（{len(tests)} 个）后取覆盖并集 —— 整包并发跑时 "
            f"node 的覆盖率合并会竞态")
    if notes:
        note += "；" + "；".join(notes[:3])
    return cov, failed, note


def _run_coverage_single(m: Module, timeout: int = 120,
                         include_itest: bool = False
                         ) -> tuple[dict[str, dict], list[str], str]:
    """整包跑一次（快，但覆盖率在有 mock.module 时不稳定）。"""
    tests = m.tests + (m.itests if include_itest else [])
    if not tests or not m.workspace:
        return {}, [], "没有找到该模块的测试文件"
    inc = str(m.base.relative_to(m.workspace)) if m.workspace else m.path
    prefix, _ = project_test_cmd(m)
    runner = prefix[0] if prefix else "node"
    cmd = list(prefix)
    cmd += coverage_flags(m, inc, runner)
    cmd += [str(t.relative_to(m.workspace)) for t in tests]
    env = child_env()
    if include_itest:
        env["MEMORY_ITEST"] = "1"
    bin_dir = m.workspace / "node_modules" / ".bin"
    if bin_dir.is_dir():
        env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    try:
        r = subprocess.run(cmd, cwd=m.workspace, capture_output=True,
                           text=True, encoding="utf-8", timeout=timeout,
                           env=env)
    except subprocess.TimeoutExpired:
        return {}, [], f"测试跑超时（{timeout}s）"
    except FileNotFoundError as e:
        return {}, [], f"项目的测试命令跑不起来（找不到可执行文件：{e.filename}）"
    out = r.stdout + r.stderr
    failed = _failed_tests(out)
    cov = _parse_cov_rows(out)
    if not cov:
        tail = "\n".join(out.splitlines()[-25:])
        return {}, failed, "覆盖率表没解析出来（测试可能没跑起来）：\n" + tail
    cov = {k: {kk: vv for kk, vv in v.items() if kk != "_uncovered"}
           for k, v in cov.items()}
    return cov, failed, out


# ---------------------------------------------------------------- 实测：PG 表结构


_PG_ENV_KEYS = ("PGHOST", "PGPORT", "PGUSER", "PGPASSWORD", "PGDATABASE",
                "PGSSLMODE")


def pg_env(url: str) -> dict[str, str]:
    """连接串 → `PG*` 环境变量（口令不落 argv、不进日志）。

    之前是把整条连接串当 argv 传给 psql ⇒ 含口令的连接串出现在进程表，
    本机其他用户可见。解析不出来就返回空 ⇒ 查不到表结构，而不是把口令
    塞进命令行。
    """
    if url:
        from urllib.parse import parse_qsl, unquote, urlparse
        try:
            u = urlparse(url)
        except ValueError:
            return {}
        if not u.scheme or not u.hostname:
            return {}
        env = {"PGHOST": u.hostname}
        if u.port:
            env["PGPORT"] = str(u.port)
        if u.username:
            env["PGUSER"] = unquote(u.username)
        if u.password:
            env["PGPASSWORD"] = unquote(u.password)
        db = (u.path or "").lstrip("/")
        if db:
            env["PGDATABASE"] = db
        qs = dict(parse_qsl(u.query or "")) if u.query else {}
        if qs.get("sslmode"):
            env["PGSSLMODE"] = qs["sslmode"]
        return env
    # 没给连接串 ⇒ 用环境里已有的 PG* 变量（同样不进 argv）
    return {k: v for k, v in os.environ.items() if k in _PG_ENV_KEYS}


def _psql(url: str, sql: str) -> list[str]:
    env = pg_env(url)
    if not env:
        return []
    try:
        r = subprocess.run(
            ["psql", "-t", "-A", "-F", "\x1f", "-c", sql],
            capture_output=True, text=True, encoding="utf-8", timeout=30,
            env=child_env(env))
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []
    if r.returncode != 0:
        return []
    return [ln for ln in r.stdout.splitlines() if ln.strip()]


def query_schema(m: Module, url: str) -> dict[str, dict]:
    """从 PG catalog 查真实的表结构事实。"""
    if not m.tables or not shutil.which("psql") or not pg_env(url):
        return {}
    facts: dict[str, dict] = {}
    for tbl in m.tables:
        t = tbl.replace("'", "''")
        facts[tbl] = {
            "columns": _psql(url,
                "SELECT column_name, is_nullable, data_type, "
                "coalesce(column_default,'') FROM information_schema.columns "
                "WHERE table_schema='public' AND table_name='%s' "
                "ORDER BY ordinal_position;" % t),
            "indexes": _psql(url,
                "SELECT indexdef FROM pg_indexes WHERE schemaname='public' "
                "AND tablename='%s';" % t),
            "policies": _psql(url,
                "SELECT polname FROM pg_policies WHERE schemaname='public' "
                "AND tablename='%s';" % t),
            "rls": _psql(url,
                "SELECT c.relrowsecurity, c.relforcerowsecurity FROM pg_class c "
                "JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname='public' AND c.relname='%s';" % t),
        }
    return facts


# ---------------------------------------------------------------- 检查


def split_env_cmd(cmd: str) -> tuple[dict[str, str], list[str]]:
    """拆 `A=1 B=2 runner args...` ⇒ (env 覆盖, argv)。

    仓库脚本常把环境变量写在命令前面（`MEMORY_ITEST=1 tsx --test ...`），
    用 shlex + 列表执行时必须自己拆出来。
    """
    import shlex
    env: dict[str, str] = {}
    argv = shlex.split(cmd)
    while argv:
        tok = argv[0]
        if "=" in tok and not tok.startswith("-") \
                and re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", tok):
            k, v = tok.split("=", 1)
            env[k] = v
            argv.pop(0)
            continue
        break
    return env, argv


def _script_files(cmd: str) -> list[str]:
    """命令里引用的测试文件（用来判断这个脚本管不管这个模块）。"""
    import shlex
    _, argv = split_env_cmd(cmd)
    return [a for a in argv
            if a.endswith((".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
                           ".py")) or "*" in a]


def script_allowed(kind: str, name: str, cmd: str) -> bool:
    """这个脚本该被自动执行吗？

    显式白名单：脚本名命中白名单（精确名，或冒号分段的前缀模式）。
    之前是子串匹配（`"coverage" in name`）⇒ `reconcile:reset` 这种
    名字里带关键词的脚本也会被选中执行。

    覆盖率闸门额外要求命令里带 `cov`（闸门名字可能很泛，命令能确认
    它真的是覆盖率任务）。
    """
    spec = SCRIPT_NAMES.get(kind)
    if not spec:
        return False
    low = name.lower()
    if low not in spec["exact"]:
        segs = low.split(":")
        if not any(tuple(segs[:len(p)]) == p for p in spec["prefix"]):
            return False
    if kind == "gate":
        return "cov" in cmd.lower()
    return True


def _python_gate_scripts(root: Path) -> list[tuple[Path, str, str]]:
    """Python 仓库自己声明的覆盖率闸门（Makefile 目标 / tox 环境）。

    之前只读 `package.json` ⇒ Python 仓库永远读不到自己的闸门。
    """
    out: list[tuple[Path, str, str]] = []
    for mk in sorted(root.rglob("Makefile")):
        if any(part in SKIP_DIRS for part in mk.parts):
            continue
        try:
            text = mk.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for mm in re.finditer(r"^([A-Za-z0-9_.-]+)\s*:(?!=)", text, re.M):
            target = mm.group(1)
            if "cov" not in target.lower():
                continue
            out.append((mk.parent, f"make {target}", f"make {target}"))
    for tox in sorted(root.rglob("tox.ini")):
        if any(part in SKIP_DIRS for part in tox.parts):
            continue
        try:
            text = tox.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for mm in re.finditer(r"^\[testenv:([A-Za-z0-9_.-]+)\]", text, re.M):
            env_name = mm.group(1)
            if "cov" not in env_name.lower():
                continue
            out.append((tox.parent, f"tox -e {env_name}", f"tox -e {env_name}"))
    return out


def repo_coverage_threshold(root: Path) -> tuple[float | None, float | None]:
    """仓库自己在 `pyproject.toml` 里声明的覆盖率阈值（行、分支）。

    阈值归仓库所有 —— 通用阈值（80/70）在没有仓库口径时是错的。
    """
    try:
        import tomllib
    except ModuleNotFoundError:
        return (None, None)
    for pj in sorted(root.rglob("pyproject.toml")):
        if any(part in SKIP_DIRS for part in pj.parts):
            continue
        try:
            data = tomllib.loads(pj.read_text(encoding="utf-8", errors="replace"))
        except (OSError, ValueError):
            continue
        rep = ((data.get("tool") or {}).get("coverage") or {}).get("report") or {}
        line = rep.get("fail_under")
        branch = rep.get("fail_under_branch")
        try:
            line = float(line) if line is not None else None
        except (TypeError, ValueError):
            line = None
        try:
            branch = float(branch) if branch is not None else None
        except (TypeError, ValueError):
            branch = None
        if line is not None or branch is not None:
            return (line, branch)
    return (None, None)


def find_gate_scripts(m: Module, root: Path) -> list[tuple[Path, str, str]]:
    """找仓库自己的覆盖率闸门入口。

    入口 = 候选里**没被别的候选引用**的那个（链式脚本引用各子闸）。
    """
    cands: list[tuple[Path, str, str]] = []
    for ws, pkg in _workspace_manifests(root):
        scripts = pkg.get("scripts") or {}
        for name, cmd in scripts.items():
            if not isinstance(cmd, str) or not isinstance(name, str):
                continue
            if not script_allowed("gate", name, cmd):
                continue
            if not _mentions_module(m, cmd):
                continue
            cands.append((ws, name, cmd))
    if not cands:
        # Python 仓库：Makefile / tox.ini 里声明的闸门
        for ws, name, cmd in _python_gate_scripts(root):
            if not (_mentions_module(m, cmd) or _covers_module(m, ws)):
                continue
            cands.append((ws, name, cmd))
    if not cands:
        return []
    return _entries(cands)


def _covers_module(m: Module, ws: Path) -> bool:
    """这个工作区目录是不是模块的祖先（Makefile / tox.ini 在模块上层）。"""
    try:
        m.base.relative_to(ws)
    except ValueError:
        return False
    return True


def _entries(cands: list[tuple[Path, str, str]]) -> list[tuple[Path, str, str]]:
    """入口 = 没被**别的候选按整个名字引用**的那些。

    不能用子串判断：`test:coverage:agent-memory` 是
    `test:coverage:agent-memory:core` 的前缀 ⇒ 子串会把入口误删。
    """
    referenced = " ".join(c[2] for c in cands)
    entries = []
    for c in cands:
        pat = r"(?<![\w:.-])" + re.escape(c[1]) + r"(?![\w:.-])"
        if not re.search(pat, referenced):
            entries.append(c)
    return entries or cands


def _mentions_module(m: Module, cmd: str) -> bool:
    """命令/名字提到这个模块吗？"""
    tag = Path(m.path).name
    if tag and tag in cmd:
        return True
    files = _script_files(cmd)
    if not files:
        return False
    base = str(m.base.relative_to(m.workspace)) if m.workspace else m.path
    return any(base in f or f.startswith(base) for f in files)


def _has_glob(cmd: str) -> bool:
    return any("*" in a for a in _script_files(cmd))


def find_itest_scripts(m: Module, root: Path) -> list[tuple[Path, str, str]]:
    """找仓库自己的真后端（itest / integration）脚本。

    **显式清单优先于 glob**：仓库钉死文件清单是有意的（glob 会把在途文件
    一起吸进来，红灯与产品无关）⇒ 有清单就用清单，别用 glob。
    """
    cands: list[tuple[Path, str, str]] = []
    for ws, pkg in _workspace_manifests(root):
        scripts = pkg.get("scripts") or {}
        for name, cmd in scripts.items():
            if not isinstance(cmd, str) or not isinstance(name, str):
                continue
            if not script_allowed("itest", name, cmd):
                continue
            if not _mentions_module(m, cmd):
                continue
            cands.append((ws, name, cmd))
    entries = _entries(cands)
    explicit = [c for c in entries if not _has_glob(c[2])]
    return explicit or entries


def _insert_coverage_flags(argv: list[str], flags: list[str]) -> list[str]:
    """把覆盖率参数插到**文件参数之前**（node 的选项必须在文件前）。"""
    for i, a in enumerate(argv):
        if a.endswith((".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".py")) \
                or ("*" in a and "/" in a):
            return argv[:i] + flags + argv[i:]
    return argv + flags


def run_gate(ws: Path, name: str, cmd: str, extra: list[str] | None = None,
             timeout: int = 900) -> tuple[int, str, dict[str, str]]:
    """跑仓库自己的脚本 ⇒ (退出码, 输出, 注入的环境)。"""
    env_over, argv = split_env_cmd(cmd)
    if extra:
        argv = _insert_coverage_flags(argv, extra)
    env = child_env(env_over)
    bin_dir = ws / "node_modules" / ".bin"
    if bin_dir.is_dir():
        env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    try:
        r = subprocess.run(argv, cwd=ws, capture_output=True, text=True,
                           encoding="utf-8", timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return 124, f"{name}: 超时（{timeout}s）", env_over
    except FileNotFoundError as e:
        return 127, f"{name}: 跑不起来（{e.filename}）", env_over
    return r.returncode, r.stdout + r.stderr, env_over


def _workspace_manifests(root: Path) -> list[tuple[Path, dict]]:
    out: list[tuple[Path, dict]] = []
    for pj in _walk(root, {".json"}):
        if pj.name != "package.json":
            continue
        try:
            pkg = json.loads(pj.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(pkg, dict):
            out.append((pj.parent, pkg))
    return out


def find_reconcilers(m: Module, root: Path) -> list[tuple[Path, str, str]]:
    """找模块自己的对账机制：{workspace, 脚本名, 命令}。

    判据：脚本名/命令含 reconcile，且脚本文件里提到这个模块的表或路径。
    """
    out: list[tuple[Path, str, str]] = []
    for ws, pkg in _workspace_manifests(root):
        scripts = pkg.get("scripts") or {}
        for name, cmd in scripts.items():
            if not isinstance(cmd, str) or not isinstance(name, str):
                continue
            if not script_allowed("reconcile", name, cmd):
                continue
            # 脚本文件里要提到这个模块的表名或模块路径
            body = ""
            for tok in cmd.split():
                if tok.endswith((".ts", ".js", ".mjs", ".tsx")):
                    try:
                        body = (ws / tok).read_text(encoding="utf-8",
                                                   errors="replace")
                    except OSError:
                        body = ""
                    break
            mentions = (m.tables and any(t in body for t in m.tables)) \
                or m.path in body
            if mentions:
                out.append((ws, name, cmd))
    return out


def run_reconciler(m: Module, ws: Path, name: str, cmd: str,
                   timeout: int = 180,
                   drift_re: "re.Pattern | None" = None,
                   header_re: "re.Pattern | None" = None,
                   tenant_col: str | None = None
                   ) -> tuple[list[str], str, list[str], Path, int | None]:
    """实跑对账脚本，返回 (漂移租户行, 原始输出, 表格行, 输出目录, 租户列位置)。

    对账脚本通常会把报告写到磁盘（人工看的那份）—— 自动化跑不能覆盖它，
    所以把输出目录指到临时目录（通用变量 `REQ_PLAN_RECONCILE_OUT_DIR`，
    同时兼容旧的 `MEMORY_RECONCILE_OUT_DIR`）。

    漂移行与表头的识别是可配置的（`--drift-pattern` / `--table-header-pattern`）
    —— 之前写死中文「漂移」+「❌」，英文输出的对账脚本一律假绿。
    租户列的位置从表头认（认不到就退回第一列）。
    """
    import shlex
    import tempfile
    drift_re = drift_re or DRIFT_RE
    header_re = header_re or HEADER_RE
    out_dir = tempfile.mkdtemp(prefix="reconcile-")
    env = child_env({"REQ_PLAN_RECONCILE_OUT_DIR": out_dir,
                     "MEMORY_RECONCILE_OUT_DIR": out_dir})
    bin_dir = ws / "node_modules" / ".bin"
    if bin_dir.is_dir():
        env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    # 用项目自己的 runner（路径别名要 tsx 才解析）—— 不改被调脚本的语义
    parts = shlex.split(cmd)
    prefix = parts
    while prefix and ("*" in prefix[-1] or ".test." in prefix[-1]):
        prefix.pop()
    try:
        r = subprocess.run(prefix, cwd=ws, capture_output=True, text=True,
                           encoding="utf-8", timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return [], [], [], Path(out_dir), None
    except FileNotFoundError:
        return [], [], [], Path(out_dir), None
    text = r.stdout + r.stderr
    # 表头行也要排除 —— 英文表头常含 drift 字样（"| tenant | drift | status |"），
    # 不排除就会把表头当成一条漂移：计数虚高，租户归属还错乱。
    drift = [ln.strip() for ln in text.splitlines()
             if drift_re.search(ln) and not header_re.search(ln)]
    table_rows = [ln.strip() for ln in text.splitlines()
                  if ln.strip().startswith("|")]
    # 租户列的位置：从表头认（认不到就退回第一列）
    tenant_idx: int | None = None
    for rrow in table_rows:
        cells = [c.strip() for c in rrow.strip().strip("|").split("|")]
        for i, c in enumerate(cells):
            if (tenant_col and c.lower() == tenant_col.lower()) \
                    or _TENANT_COL_RE.match(c):
                tenant_idx = i
                break
        if tenant_idx is not None:
            break
    # 一致的租户也留着 —— 只列漂移的会让人以为生产租户也漂了
    all_rows = [rrow for rrow in table_rows if not header_re.search(rrow)]
    if not drift and r.returncode != 0:
        # 顺带修一个隐患：出错时这里原来只返回 2 个值，调用方解包会炸
        return [], f"对账脚本退出码 {r.returncode}：\n" + text[-1500:], [], \
            Path(out_dir), tenant_idx
    return drift, text, all_rows, Path(out_dir), tenant_idx


def _read_csv(path: Path) -> tuple[list[str], list[dict[str, str]]]:
    import csv
    try:
        with path.open(encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            return list(reader.fieldnames or []), list(reader)
    except (OSError, UnicodeDecodeError, csv.Error):
        return [], []


def _key_col(cols: list[str]) -> str | None:
    for c in cols:
        if c == "id" or c.endswith("_id"):
            return c
    return None


# 时间戳类字段 —— 两边天然不同，不算数据不一致
_TS_COL_RE = re.compile(
    r"(?:^|_)(?:at|timestamp|time|date)$|"
    r"^(?:created|updated|deleted|modified|inserted|generated|exported)", re.I)
# 正文类字段 —— 不一致要人判断（不能自动判 fail）
_BODY_COL_RE = re.compile(
    r"(?:^|_)(?:text|content|body|raw|payload|chunk|embedding|vector|"
    r"doc|document|value)$|^(?:text|content|body|message)$", re.I)


def _field_kind(col: str) -> str:
    """字段分类：ts（时间戳）/ body（正文）/ plain（普通字段）。"""
    if _TS_COL_RE.search(col):
        return "ts"
    if _BODY_COL_RE.search(col):
        return "body"
    return "plain"


def _csv_pairs(files: list[Path],
               declared: list[tuple[str, str]] | None = None
               ) -> list[tuple[Path, Path]]:
    """CSV 怎么配对。

    优先用**声明的对照关系**（`--csv-pair pg.csv=vec.csv`）；
    没声明时按"共同列最多的互为最佳"配对 —— 不是两两配对（3 个 CSV
    会产生 3 组比较，含无关文件对）。
    """
    by_name = {p.name: p for p in files}
    out: list[tuple[Path, Path]] = []
    used: set[Path] = set()
    for a, b in (declared or []):
        pa, pb = by_name.get(a), by_name.get(b)
        if pa and pb and pa != pb and pa not in used and pb not in used:
            out.append((pa, pb))
            used.add(pa)
            used.add(pb)
    rest = [p for p in files if p not in used]
    if len(rest) < 2:
        return out
    cols = {p: _read_csv(p)[0] for p in rest}
    score: dict[tuple[Path, Path], int] = {}
    for i, a in enumerate(rest):
        for b in rest[i + 1:]:
            common = [c for c in cols[a] if c in cols[b]]
            if not _key_col(common) or len(common) < 2:
                continue
            score[(a, b)] = len(common)
    best: dict[Path, tuple[Path, int]] = {}
    for (a, b), s in score.items():
        for x, y in ((a, b), (b, a)):
            cur = best.get(x)
            if cur is None or s > cur[1]:
                best[x] = (y, s)
    for a, b in score:
        if best.get(a, (None, 0))[0] == b and best.get(b, (None, 0))[0] == a:
            out.append((a, b) if str(a) < str(b) else (b, a))
    return out


def compare_csv_fields(out_dir: Path,
                       declared: list[tuple[str, str]] | None = None,
                       tenant_col: str | None = None
                       ) -> tuple[list[dict], list[dict], int, int]:
    """成对 CSV 之间做字段级比对。

    返回 (普通字段不一致, 正文类不一致, 比过几对, 排除的时间戳字段数)。
    时间戳类字段排除（两边天然不同）；正文类字段仍报出来，但**不自动判
    fail** —— 正文不一致要人判断。
    """
    try:
        files = sorted(p for p in out_dir.iterdir() if p.suffix.lower() == ".csv")
    except OSError:
        return [], [], 0, 0
    if len(files) < 2:
        return [], [], 0, 0
    diffs: list[dict] = []
    body_diffs: list[dict] = []
    pairs = 0
    ts_skipped = 0
    for fa, fb in _csv_pairs(files, declared):
        cols_a, rows_a = _read_csv(fa)
        cols_b, rows_b = _read_csv(fb)
        if not cols_a or not cols_b:
            continue
        common = [c for c in cols_a if c in cols_b]
        key = _key_col(common)
        if not key or len(common) < 2:
            continue
        pairs += 1
        map_a = {r.get(key, ""): r for r in rows_a}
        map_b = {r.get(key, ""): r for r in rows_b}
        fields = [c for c in common if c != key]
        tcol = tenant_col or next(
            (c for c in fields if _TENANT_COL_RE.match(c)), None)
        for kid in sorted(set(map_a) & set(map_b)):
            ra, rb = map_a[kid], map_b[kid]
            for f in fields:
                va = (ra.get(f) or "").strip()
                vb = (rb.get(f) or "").strip()
                if va == vb:
                    continue
                # 一侧空 vs 另一侧空：空串与缺失都算"同为空"
                if not va and not vb:
                    continue
                kind = _field_kind(f)
                if kind == "ts":
                    ts_skipped += 1
                    continue
                rec = {
                    "id": kid,
                    "field": f,
                    "kind": kind,
                    "a": va[:60],
                    "b": vb[:60],
                    "tenant": (ra.get(tcol) or "").strip() if tcol else "",
                    "text": (f"{fa.name} ↔ {fb.name}：id={kid} 的 "
                             f"{f} 不一致（前者 {va[:40]!r} / 后者 {vb[:40]!r}）"),
                }
                if kind == "body":
                    body_diffs.append(rec)
                else:
                    diffs.append(rec)
    return diffs, body_diffs, pairs, ts_skipped


# 派生存储（权威在 PG、派生数据在别处 ⇒ 必须能校对）与旁路存储（缓存/队列）
_DERIVED_STORE_RE = re.compile(
    r"milvus|MilvusClient|DataType\.|createCollection|upsert\(|"
    r"qdrant|QdrantClient|weaviate|pinecone|chromadb|"
    r"minio|S3Client|putObject|blob|objectStore", re.I)
_SIDE_STORE_RE = re.compile(
    r"\bredis\b|ioredis|memcached|Redis\(|"
    r"kafka|rabbitmq|amqp|sqs|bullmq", re.I)


def detect_stores(m: Module) -> tuple[list[str], list[str]]:
    """返回 (派生存储的线索, 旁路存储的线索)。"""
    derived: list[str] = []
    side: list[str] = []
    for p in m.sources:
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        head = "\n".join(text.splitlines()[:60])
        if _DERIVED_STORE_RE.search(text) or _DERIVED_STORE_RE.search(p.name):
            derived.append(p.name)
        elif _SIDE_STORE_RE.search(text) or _SIDE_STORE_RE.search(head):
            side.append(p.name)
    return sorted(set(derived)), sorted(set(side))


# 测试租户的名字约定（默认；可用 --test-tenant-prefix 覆盖）
_TEST_TENANT_RE = re.compile(
    r"^(itest|memtest|mock|test|eval|e2e|ci|scratch|probe|tmp|temp)[-_]",
    re.I)
# 租户列的名字（CSV 里用来定位这条记录属于哪个租户）
_TENANT_COL_RE = re.compile(r"^(tenant_id|tenantId|tenant)$")

# 对账输出的漂移行识别 —— 可配置（--drift-pattern）。
# 之前写死「漂移」+「❌」两个中文/符号特征 ⇒ 英文输出的对账脚本一律假绿。
DRIFT_RE = re.compile(r"❌|漂移|\bdrift(?:ed)?\b|mismatch", re.I)
# 表格里的表头 / 分隔行 —— 可配置（--table-header-pattern）
HEADER_RE = re.compile(r"一致性|租户|tenant|---", re.I)


def is_test_tenant(name: str, declared: list[str],
                   prefixes: list[str] | None = None) -> bool:
    """这个租户是测试专用的吗？

    判据：名字像测试租户，或仓库显式声明过（精确名或前缀）。
    `prefixes` 给了就**替换**内置的测试租户前缀表（默认表是 lumen 的
    itest-/memtest-/mock-/eval-，别的仓库要用自己的）。
    """
    if not name:
        return False
    for d in declared:
        if not d:
            continue
        if name == d or name.startswith(d):
            return True
    if prefixes:
        return any(p and name.startswith(p) for p in prefixes)
    return bool(_TEST_TENANT_RE.match(name))


def _tenant_of_row(row: str, idx: int | None = None) -> str:
    """从对账报告的表格行里取租户名：`| tenant | ... |`

    `idx` 是**单元格**序号（0 起，从表头认）；认不到就退回第一列。
    """
    parts = row.split("|")
    if len(parts) < 2:
        return ""
    # parts[0] 是首个 "|" 之前的空串 ⇒ 单元格 i 对应 parts[i+1]
    i = (idx + 1) if idx is not None else 1
    if not (0 <= i < len(parts)):
        i = 1
    return parts[i].strip()


def check_data_flow(m: Module, root: Path,
                    declared_test_tenants: list[str] | None = None,
                    drift_re: "re.Pattern | None" = None,
                    header_re: "re.Pattern | None" = None,
                    tenant_prefixes: list[str] | None = None,
                    csv_pairs: list[tuple[str, str]] | None = None,
                    tenant_col: str | None = None) -> Check:
    """D1 数据流一致 —— 实跑模块自己的对账机制。

    判交付只看**生产租户**：测试租户的状态是信息，不是阻塞项
    （测试用例常刻意注入失败态/直接插库占位）。

    漂移/表头/租户列/测试租户前缀/CSV 对照关系都可配置 —— 之前写死
    一种中文方言，别的仓库要么假绿要么假红。
    """
    declared_test_tenants = declared_test_tenants or []
    stores = []
    if m.tables:
        stores.append("PG " + ", ".join(m.tables))
    if m.support or m.sources:
        pass
    derived, side = detect_stores(m)
    if derived:
        stores.append("派生存储 " + ", ".join(derived[:5]))
    if side:
        stores.append("旁路存储 " + ", ".join(side[:5]))
    reconcilers = find_reconcilers(m, root)
    if not reconcilers:
        if derived:
            return Check("data-flow", "数据流一致（跨存储对账）", "fail",
                         "模块有派生存储却没有对账机制 —— "
                         "PG 与派生层的数据漂了没人知道",
                         [f"存储：{s}" for s in stores],
                         "写对账脚本：逐条比 id 集合、字段值、正文；"
                         "能自动修的修，需要人判断的只报告")
        if side:
            return Check("data-flow", "数据流一致（跨存储对账）", "warn",
                         "模块用了旁路存储（缓存/队列），确认它不是权威数据",
                         [f"存储：{s}" for s in stores],
                         "旁路存储丢了可以重建就没事；里面若有权威数据，"
                         "它需要一条对账路径")
        return Check("data-flow", "数据流一致（跨存储对账）", "skipped",
                     "模块只碰了 PG 一个存储 —— 没有派生层要跨存储校对", stores,
                     "若其实还有第二个存储（向量库/对象存储），"
                     "先让工具认出来再判",
                     evidence_kind="untested")
    drift_all: list[str] = []
    notes: list[str] = []
    clean: list[str] = []
    field_diffs: list[dict] = []
    body_diffs: list[dict] = []
    pairs_checked = 0
    ts_skipped = 0
    tenant_idx: int | None = None
    for ws, name, cmd in reconcilers:
        drift, text, rows, out_dir, tidx = run_reconciler(
            m, ws, name, cmd, drift_re=drift_re, header_re=header_re,
            tenant_col=tenant_col)
        if tenant_idx is None:
            tenant_idx = tidx
        drift_all += drift
        clean += [r for r in rows if r not in drift]
        notes.append(f"{name}：{len(drift)} 个租户漂移")
        # 字段级：同一个 id 在两个存储里的同一个字段对不上
        diffs, bodies, pairs, ts_n = compare_csv_fields(
            out_dir, csv_pairs, tenant_col)
        pairs_checked += pairs
        ts_skipped += ts_n
        field_diffs += diffs
        body_diffs += bodies
    # 按租户口径拆：生产租户的漂移才阻塞交付
    prod_drift = [r for r in drift_all
                  if not is_test_tenant(_tenant_of_row(r, tenant_idx),
                                        declared_test_tenants,
                                        tenant_prefixes)]
    test_drift = [r for r in drift_all
                  if is_test_tenant(_tenant_of_row(r, tenant_idx),
                                    declared_test_tenants,
                                    tenant_prefixes)]
    prod_fields = [d for d in field_diffs
                   if not is_test_tenant(d.get("tenant", ""),
                                         declared_test_tenants,
                                         tenant_prefixes)]
    test_fields = [d for d in field_diffs
                   if is_test_tenant(d.get("tenant", ""), declared_test_tenants,
                                     tenant_prefixes)]
    prod_body = [d for d in body_diffs
                 if not is_test_tenant(d.get("tenant", ""),
                                       declared_test_tenants,
                                       tenant_prefixes)]
    test_body = [d for d in body_diffs
                 if is_test_tenant(d.get("tenant", ""), declared_test_tenants,
                                   tenant_prefixes)]
    pair_note = (f"比了 {pairs_checked} 对存储导出"
                 + (f"（排除了 {ts_skipped} 个时间戳类字段）" if ts_skipped else "")
                 if pairs_checked
                 else "字段级**无法比对**（没找到成对的 CSV 导出）")

    if prod_fields:
        return Check("data-flow", "数据流一致（跨存储对账）", "fail",
                     f"生产租户有 {len(prod_fields)} 处跨存储字段不一致"
                     f"（{pair_note}）",
                     [d["text"] for d in prod_fields[:10]],
                     "同一 id 在两个存储里的同一字段值不同 ⇒ 读到的和写下的不是"
                     "同一份数据（常见原因：截断、归一化不一致、写路径漏更新）",
                     evidence_kind="executed")
    if prod_drift:
        return Check("data-flow", "数据流一致（跨存储对账）", "fail",
                     f"{len(prod_drift)} 个**生产租户**的数据流漂移"
                     + (f"（另有 {len(test_drift)} 个测试租户漂移，不阻塞交付）"
                        if test_drift else "")
                     + f"；{pair_note}"
                     + (f"，{len(prod_body)} 处正文类字段不一致（需人工判断）"
                        if prod_body else ""),
                     prod_drift[:10] + clean[:6],
                     "缺向量⇒召不回；多余向量⇒占 top-K 名额后被 join 丢掉；"
                     "正文不一致⇒索引的是旧正文",
                     evidence_kind="executed")
    if prod_body:
        # 正文类字段不一致 —— 报给人判断，**不自动判 fail**
        return Check("data-flow", "数据流一致（跨存储对账）", "warn",
                     f"生产租户有 {len(prod_body)} 处跨存储字段不一致"
                     f"（正文类字段，需人工判断；{pair_note}）",
                     [d["text"] for d in prod_body[:10]],
                     "正文类字段（正文/向量/负载）不一致不自动判 fail —— "
                     "仓库自己的对账脚本知道那些取舍，需要人判断是索引旧正文"
                     "还是写路径漏更新",
                     evidence_kind="executed")
    if test_drift or test_fields or test_body:
        return Check("data-flow", "数据流一致（跨存储对账）", "warn",
                     f"生产租户一致；{len(test_drift)} 个测试租户有漂移"
                     + (f"、{len(test_fields)} 处字段不一致" if test_fields else "")
                     + "（测试夹具状态，不阻塞交付）",
                     test_drift[:6] + [d["text"] for d in test_fields[:4]]
                     + [d["text"] for d in test_body[:4]],
                     "确认两件事：①这些租户不会流进生产；"
                     "②状态是刻意的失败注入/直接插库占位（不是漏镜像）。"
                     "认错了用 --test-tenant 声明",
                     evidence_kind="executed")
    return Check("data-flow", "数据流一致（跨存储对账）", "pass",
                 f"实跑了 {len(reconcilers)} 个对账脚本，生产租户无漂移"
                 + (f"（{pair_note}）" if pairs_checked else f"；{pair_note}"),
                 notes,
                 evidence_kind="executed")


def check_data_accuracy(m: Module, root: Path) -> Check:
    """D2 数据准确性 —— 实跑项目的基线对拍。"""
    cands = find_baseline_scripts(m, root)
    if not cands:
        return Check("data-accuracy", "数据准确（基线对拍）", "skipped",
                     "项目里没有基线对拍脚本",
                     fix="加一个：当前指标对拍已提交基线，低于阈值就红",
                     evidence_kind="untested")
    import shlex
    fails: list[str] = []
    notes: list[str] = []
    skipped: list[str] = []
    for ws, name, cmd in cands:
        parts = shlex.split(cmd)
        prefix = parts
        while prefix and ("*" in prefix[-1] or ".test." in prefix[-1]):
            prefix.pop()
        env = child_env()
        bin_dir = ws / "node_modules" / ".bin"
        if bin_dir.is_dir():
            env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
        try:
            r = subprocess.run(prefix, cwd=ws, capture_output=True,
                               text=True, encoding="utf-8", timeout=240,
                               env=env)
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            notes.append(f"{name}: 跑不起来（{e}）")
            continue
        text = r.stdout + r.stderr
        # "没产物"和"指标不达标"是两回事 —— 前者是环境没准备好
        no_artifact = re.search(
            r"(没有找到|先跑|缺少.*产物|no artifacts|not found.*artifacts)", text)
        bad = [ln.strip() for ln in text.splitlines()
               if re.search(r"(低于|不达标|超出容差|回归|drift|regress)", ln)]
        if bad:
            fails += bad[:10]
        elif no_artifact:
            skipped.append(f"{name}: {no_artifact.group(0)}（缺产物，不是数据回归）")
        elif r.returncode != 0:
            skipped.append(f"{name}: 退出码 {r.returncode}，但没有解析出指标差异")
        else:
            notes.append(f"{name}: 通过")
    if fails:
        return Check("data-accuracy", "数据准确（基线对拍）", "fail",
                     f"{len(fails)} 项指标不达标/回归", fails[:10],
                     "修数据或更新基线（先确认是缺陷还是基线过期）",
                     evidence_kind="executed")
    if skipped and not notes:
        return Check("data-accuracy", "数据准确（基线对拍）", "skipped",
                     "基线对拍没跑成（不是数据回归，是没产物）", skipped[:6],
                     "先跑对应的 eval 套件产出 artifacts，再对拍基线",
                     evidence_kind="executed")
    return Check("data-accuracy", "数据准确（基线对拍）", "pass",
                 f"实跑了 {len(cands)} 个基线对拍脚本，通过 {len(notes)} 个"
                 + (f"，{len(skipped)} 个没跑成" if skipped else ""),
                 notes + skipped[:4],
                 evidence_kind="executed")


# 测试往**库**里写数据的痕迹。
# 注意：光有 `.insert(` 会把内存适配器（adapter.insert / store.insert）也
# 算进来 —— 那是进程内结构，不是残留源 ⇒ 排除掉。
_DB_WRITE_RE = re.compile(
    r"tx\.insert\(|"
    r"\b(?!adapter|store|map|cache|array|list|dict|set|queue|vec)[A-Za-z_]\w*"
    r"\.insert\("
    r"|INSERT\s+INTO|appendMemories\(|createMemory\(|updateMemory\(|"
    r"purgeMemory\(|session\.add\(|\bdb\.add\(|\.save\(\)|"
    r"bulk_save|insert_many|executemany", re.I)
# 清理的痕迹（钩子 / 清库 / 删集合）
_CLEANUP_RE = re.compile(
    r"\bafterEach\b|\bafterAll\b|\bafter\(|\bteardown|finalizer|"
    r"TRUNCATE|DELETE\s+FROM|resetDb|dropCollection|\.delete\(|"
    r"yield\s*$|\.close\(\)", re.M)
# 真正"清场"的动作（钩子里得有它才算清理）
_DESTRUCTIVE_RE = re.compile(
    r"delete|truncate|drop|resetDb|remove|clear|purge", re.I)
# 收场钩子的位置
_AFTER_HOOK_RE = re.compile(
    r"\b(?:afterEach|afterAll|after)\s*\(|\bteardown|finalizer|"
    r"yield\s*$", re.M)
# 只在 before 清理（能防跨轮累积，但跑完仍留最后一轮）
_BEFORE_ONLY_RE = re.compile(
    r"\bbeforeEach\b|\bbeforeAll\b|\bbefore\(|setup_function|"
    r"@pytest\.fixture")
# 字面量的固定等待（flaky 的来源）
_FIXED_WAIT_RE = re.compile(
    r"sleep\(\s*\d{3,}|time\.sleep\(\s*\d|"
    r"setTimeout\(\s*['\"]?\d{3,}|asyncio\.sleep\(\s*\d|"
    r"waitForTimeout\(\s*\d")


def check_test_hygiene(m: Module) -> Check:
    """测试卫生：固定等待（flaky 源）与不清理（残留源）。"""
    findings: list[str] = []
    sleeps: list[str] = []
    dirty: list[str] = []
    for t in m.tests + m.itests:
        try:
            text = t.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        rel = str(t.relative_to(m.root))
        for mm in _FIXED_WAIT_RE.finditer(text):
            line = text[:mm.start()].count("\n") + 1
            sleeps.append(f"{rel}:{line} 固定等待 {mm.group(0)!r}")
        if _DB_WRITE_RE.search(text):
            hook = _AFTER_HOOK_RE.search(text)
            if not _CLEANUP_RE.search(text):
                dirty.append(f"{rel}：有写库动作，没看到任何清理钩子 ⇒ 会留残留")
            elif hook is None:
                dirty.append(f"{rel}：清理只在 before 钩子 ⇒ 跑完仍留最后一轮残留")
            elif not _DESTRUCTIVE_RE.search(text[hook.end():]):
                # 有收场钩子，但钩子里没有清场动作 ⇒ 一样留残留
                dirty.append(f"{rel}：收场钩子里没有清场动作 ⇒ 跑完仍留残留")
    findings += sleeps[:8]
    findings += dirty[:8]
    if not findings:
        return Check("hygiene", "测试卫生（不留残留、不靠 sleep）", "pass",
                     "没有固定等待，也没发现不清理的写库测试")
    status = "fail" if dirty else "warn"
    return Check("hygiene", "测试卫生（不留残留、不靠 sleep）", status,
                 f"{len(sleeps)} 处固定等待、{len(dirty)} 处不清理的写库测试",
                 findings[:10],
                 "固定等待 ⇒ 换成等结果（等条件成立）；"
                 "不清理 ⇒ 测试要自己收场（after 钩子清干净），"
                 "否则共享库里留的残留会让下一个测试看到别人的数据")


def check_tests_green(failed: list[str],
                     setup_errors: list[str] | None = None,
                     tests_found: bool = True,
                     tests_ran: bool = True) -> Check:
    """R0 测试全绿 —— 测试红着就没有"达到交付条件"可言。

    只认**断言失败**（FAILED）。setup 报错（fixture/环境没就绪，例：连不上
    PG）不是断言失败 —— 那是"环境没准备好"，判 skipped 并说清缺什么。

    **一个测试文件都没找到**时判 skipped（不是 pass）—— 之前这里直接
    返回"没有失败用例"⇒ pass，于是零测试的模块被判"达到交付条件"。
    找到测试文件但这次没跑起来（runner 缺失 / 只有真后端车道）同样判不了。
    """
    setup_errors = setup_errors or []
    if not tests_found:
        return Check("tests-green", "测试全绿", "skipped",
                     "没有找到任何测试文件 —— 无法判定测试是否绿",
                     ["模块目录与仓库级 test/ tests/ spec/ 下都没有测试文件"],
                     "先写测试（或用 --test-dir 指到测试目录），再判交付",
                     evidence_kind="untested")
    if not tests_ran:
        return Check("tests-green", "测试全绿", "skipped",
                     "找到测试文件但这次一个都没跑起来 —— 无法判定测试是否绿",
                     ["测试命令跑不起来 / 只有真后端车道（要 --include-itest）"],
                     "把项目的 test 命令跑通（能用的 runner + 依赖），再判交付",
                     evidence_kind="untested")
    if not failed:
        if setup_errors:
            return Check("tests-green", "测试全绿", "skipped",
                         f"{len(setup_errors)} 个用例 setup 报错（环境/依赖没就绪）"
                         f" —— 不是断言失败，判不了",
                         setup_errors[:10],
                         "先把环境准备好（数据库/中间件/依赖），再判测试绿不绿",
                         evidence_kind="executed")
        return Check("tests-green", "测试全绿", "pass", "没有失败用例",
                     evidence_kind="executed")
    return Check("tests-green", "测试全绿", "fail",
                 f"{len(failed)} 个用例失败"
                 + (f"（另有 {len(setup_errors)} 个用例 setup 报错，"
                    f"环境没就绪）" if setup_errors else ""),
                 failed[:10],
                 "先修到全绿 —— 红的测试不能交付",
                 evidence_kind="executed")


# Python 模块级 def/class（公开的）—— Python 的"导出"
_PY_EXPORT_RE = re.compile(
    r"^(?:async\s+)?(def|class)\s+([A-Za-z_][A-Za-z0-9_]*)"
    r"\s*(\([^)]*\))?", re.M)
# 生成代码（protobuf / grpc）—— 不是人写的逻辑
_PY_GENERATED_RE = re.compile(r"_pb2(_grpc)?\.pyi?$")
# 抽象基类/协议 ⇒ 接口，不是逻辑（对应 TS 侧排除 interface/type）
_PY_IFACE_BASES = ("Protocol", "ABC", "ABCMeta", "Generic", "TypedDict")


def python_package_dir(m: Module) -> Path | None:
    """src 布局里的包目录（src/<pkg>/__init__.py）。"""
    if m.base.name in ("src", "lib"):
        for child in sorted(m.base.iterdir()) if m.base.is_dir() else []:
            if child.is_dir() and (child / "__init__.py").exists():
                return child
        return None
    if (m.base / "__init__.py").exists():
        return m.base
    return None


def python_test_cmd(m: Module, root: Path) -> tuple[str | None, list[str]]:
    """找 pytest 可执行文件与测试目录。"""
    exe: str | None = None
    for rel in (".venv/bin/pytest", "venv/bin/pytest"):
        cand = root / rel
        if cand.exists():
            exe = str(cand)
            break
    if exe is None and shutil.which("pytest"):
        exe = shutil.which("pytest")
    tests: list[str] = []
    # pyproject 的 testpaths
    for name in ("pyproject.toml", "pytest.ini", "setup.cfg", "tox.ini"):
        f = root / name
        if not f.exists():
            continue
        txt = f.read_text(encoding="utf-8", errors="replace")
        mm = re.search(r"testpaths\s*=\s*\[([^\]]*)\]", txt)
        if mm:
            tests = [x.strip().strip('"\'') for x in mm.group(1).split(",")
                     if x.strip().strip('"\'')]
        break
    if not tests:
        for cand in ("test", "tests"):
            if (root / cand).is_dir():
                tests = [cand]
                break
    return exe, tests


def run_python_coverage(m: Module, root: Path, timeout: int = 600
                        ) -> tuple[dict[str, dict], list[str], str]:
    """跑 pytest-cov ⇒ (逐文件覆盖率, 失败用例, 说明)。"""
    exe, tests = python_test_cmd(m, root)
    if not exe:
        return {}, [], [], "没找到 pytest（.venv/bin/pytest 或 PATH 上的 pytest）"
    pkg = python_package_dir(m)
    if not pkg:
        return {}, [], [], "没认出 Python 包目录（src/<pkg>/__init__.py）"
    import tempfile
    out_dir = tempfile.mkdtemp(prefix="pycov-")
    cmd = [exe, f"--cov={pkg.name}", f"--cov-report=json:{out_dir}/cov.json",
           "-q"]
    cmd += tests or ["."]
    env = child_env()
    try:
        r = subprocess.run(cmd, cwd=root, capture_output=True, text=True,
                           encoding="utf-8", timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return {}, [], [], f"pytest 超时（{timeout}s）"
    except FileNotFoundError as e:
        return {}, [], [], f"pytest 跑不起来（{e.filename}）"
    out = r.stdout + r.stderr
    failed = [ln.strip() for ln in out.splitlines()
              if ln.strip().startswith("FAILED")]
    errors = [ln.strip() for ln in out.splitlines()
              if ln.strip().startswith("ERROR")]
    import json as _json
    try:
        data = _json.loads((Path(out_dir) / "cov.json").read_text(
            encoding="utf-8"))
    except (OSError, ValueError) as e:
        return {}, failed, errors, f"覆盖率报告没解析出来（{e}）"
    cov: dict[str, dict] = {}
    for fname, rec in (data.get("files") or {}).items():
        summ = rec.get("summary") or {}
        nb = summ.get("num_branches") or 0
        cb = summ.get("covered_branches") or 0
        missing = set(rec.get("missing_lines") or [])
        cov[fname] = {
            "line": float(summ.get("percent_covered") or 0),
            "branch": (cb / nb * 100) if nb else float(summ.get("percent_covered") or 0),
            "func": float(summ.get("percent_covered") or 0),
            "_uncovered": missing,
        }
    note = (f"pytest-cov 实跑（{pkg.name}，{len(tests) or 0} 个测试目录）；"
            f"coverage.py 的合并是确定的 ⇒ 一次就够")
    if errors:
        note += f"；另有 {len(errors)} 个用例 setup 报错（环境没就绪，不是断言失败）"
    return cov, failed, errors, note


def _py_class_is_data_shape(text: str, start: int) -> bool:
    """这个类体里**没有任何 def** ⇒ 它只是数据形状（DTO / 响应模型）。

    对应 TS 侧排除 interface/type：纯数据形状的"零引用"是噪声。
    """
    rest = text[start:]
    mm = re.search(r"\n(?=\S)", rest)          # 下一个顶层定义
    body = rest[:mm.start()] if mm else rest
    return not re.search(r"^\s+(async\s+)?def\s", body, re.M)


def python_exports(m: Module) -> dict[str, str]:
    """Python 的导出 = 模块级公开 def/class（排除纯数据类与异常类）。"""
    out: dict[str, str] = {}
    for p in m.sources:
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if _PY_GENERATED_RE.search(p.name):
            continue                      # protobuf/grpc 生成码
        for mm in _PY_EXPORT_RE.finditer(text):
            kind, name = mm.group(1), mm.group(2)
            bases = mm.group(3) or ""
            if name.startswith("_"):
                continue
            if kind == "class":
                if name.endswith(("Error", "Exception")):
                    continue                      # 异常类通常不直接测
                if any(b in bases for b in _PY_IFACE_BASES):
                    continue                      # Protocol/ABC ⇒ 接口
                if _py_class_is_data_shape(text, mm.end()):
                    continue                      # 纯数据形状，不是逻辑
            out.setdefault(name, str(p.relative_to(m.root)))
    return out


def check_coverage(m: Module, cov: dict, raw_note: str,
                   runner_note: str = "",
                   lane_notes: list[str] | None = None,
                   itest_cov: dict[str, dict] | None = None,
                   itest_notes: list[str] | None = None,
                   min_line: float | None = None,
                   min_branch: float | None = None,
                   repo_threshold: tuple[float | None, float | None] | None = None
                   ) -> Check:
    """R1 核心逻辑测试到位 —— 实测覆盖率。

    覆盖率判定当前只实现 JS/TS 的（node --experimental-test-coverage /
    vitest / jest）。别的语言 ⇒ skipped 并说清，不给假数字。

    门槛归仓库所有：仓库有自己的覆盖率闸门 ⇒ **用仓库的阈值**（它把文件按
    "只测自己那批"分闸，每闸自带阈值）⇒ 通用阈值在这里是错的
    （例：service.ts 行 56%，仓库 service 闸的阈值是 49 ⇒ 仓库认为合格）。
    仓库在 `pyproject.toml` 里声明的 `fail_under` 同样是仓库的口径。

    **没有仓库闸门、又没显式给阈值时判 skipped**（"缺少判据"）——
    通用阈值 80/70 与文档矛盾，且会把仓库明确允许的文件判红。
    要用通用阈值就显式 `--min-line-coverage`。

    真后端（itest）车道的覆盖**并进来**（逐文件取大 = 并集语义），两条车道
    分开报 —— 混成一个数会让人以为是一个口径。
    """
    repo_line, repo_branch = repo_threshold or (None, None)
    thr_line = repo_line if repo_line is not None else min_line
    thr_branch = repo_branch if repo_branch is not None else min_branch
    thr_note = ""
    if thr_line is not None or thr_branch is not None:
        thr_note = (f"行<{thr_line:g}%" if thr_line is not None else "") + \
                   (f" 分支<{thr_branch:g}%" if thr_branch is not None else "")
        thr_note = thr_note.strip()
    lane_notes = lane_notes or []
    itest_cov = itest_cov or {}
    itest_notes = itest_notes or []
    if m.lang == "py":
        if not cov and not lane_notes:
            return Check("coverage", "核心逻辑测试到位", "skipped",
                         raw_note or "没拿到 Python 覆盖率",
                         fix="装 pytest-cov，确认 .venv/bin/pytest 可用",
                         evidence_kind="executed")
        vals = [v for k, v in cov.items() if k != "__all__"]
        avg = sum(v["line"] for v in vals) / len(vals) if vals else 0
        ev = [f"{k}: 行{v['line']:.0f}% 分支{v['branch']:.0f}%"
              for k, v in sorted(cov.items(), key=lambda kv: kv[1]["line"])[:10]
              if k != "__all__"]
        if lane_notes:
            # 仓库自己的闸门 ⇒ 判定看闸门（它的阈值是权威），不套通用阈值
            # 闸门说明的前缀是脚本名（"make coverage: ✗ 未达阈值"）⇒ 用 in 不用
            # startswith —— startswith 永远不匹配 ⇒ 闸门红了也报「全过」
            bad = [x for x in lane_notes if "✗" in x]
            if bad:
                return Check("coverage", "核心逻辑测试到位", "fail",
                             f"仓库自己的覆盖率闸门有 {len(bad)} 个没过",
                             lane_notes + ev[:6],
                             "按闸的阈值补测试（阈值在闸门脚本或 pyproject 里）",
                             evidence_kind="executed")
            return Check("coverage", "核心逻辑测试到位", "pass",
                         "仓库自己的覆盖率闸门全过", lane_notes + ev[:6],
                         evidence_kind="executed")
        if thr_line is None and thr_branch is None:
            return Check("coverage", "核心逻辑测试到位", "skipped",
                         f"仓库没有覆盖率闸门，缺少判据（pytest-cov 实测平均行覆盖 "
                         f"{avg:.0f}%）；通用阈值默认不阻塞",
                         ev,
                         "补测试把低覆盖的文件拉过门槛，或让仓库声明自己的闸门"
                         "（要按通用阈值判就加 --min-line-coverage）",
                         evidence_kind="executed")
        low = [(k, v) for k, v in cov.items()
               if k != "__all__" and _below_threshold(v, thr_line, thr_branch)]
        if low:
            return Check("coverage", "核心逻辑测试到位", "fail",
                         f"平均行覆盖 {avg:.0f}%；{len(low)}/{len(vals)} 个文件"
                         f"低于门槛（{thr_note}）",
                         ev, "补测试把低覆盖的文件拉过门槛",
                         evidence_kind="executed")
        return Check("coverage", "核心逻辑测试到位", "pass",
                     f"平均行覆盖 {avg:.0f}%（pytest-cov 实跑，门槛 {thr_note}）", ev,
                     evidence_kind="executed")
    merged: dict[str, dict] = {}
    for k, v in cov.items():
        if k == "__all__":
            continue
        merged[k] = dict(v)
    for k, v in itest_cov.items():
        if k == "__all__":
            continue
        cur = merged.get(k)
        if cur is None:
            merged[k] = dict(v)
        else:
            merged[k] = {"line": max(cur["line"], v["line"]),
                         "branch": max(cur["branch"], v["branch"]),
                         "func": max(cur["func"], v["func"])}
    itest_lane_note = ""
    if itest_notes:
        itest_lane_note = ("（真后端车道已并入：" + "；".join(itest_notes) + "）")
    elif m.itests:
        itest_lane_note = (f"（另有 {len(m.itests)} 个 itest 真后端用例未计入 "
                           f"本覆盖率 —— 它们的覆盖要跑 --include-itest 才看得到）")
    avg_line = (sum(v["line"] for v in merged.values()) / len(merged)
                if merged else 0)
    avg_branch = (sum(v["branch"] for v in merged.values()) / len(merged)
                  if merged else 0)
    ev = [f"{k}: 行{v['line']:.0f}% 分支{v['branch']:.0f}% 函数{v['func']:.0f}%"
          for k, v in sorted(merged.items(), key=lambda kv: kv[1]["line"])[:10]]
    if lane_notes:
        # 仓库自己的闸门 ⇒ 判定看闸门（它的阈值是权威），不套通用阈值。
        # 闸门的退出码就是结论 —— 哪怕它的覆盖率表没被解析出来。
        # 闸门说明的前缀是脚本名（"make coverage: ✗ 未达阈值"）⇒ 用 in 不用
        # startswith —— startswith 永远不匹配 ⇒ 闸门红了也报「全过」
        bad = [x for x in lane_notes if "✗" in x]
        if bad:
            return Check("coverage", "核心逻辑测试到位", "fail",
                         f"仓库自己的覆盖率闸门有 {len(bad)} 个没过"
                         + (f"（平均行覆盖 {avg_line:.0f}% / 分支 {avg_branch:.0f}%）"
                            if merged else "")
                         + " " + itest_lane_note,
                         lane_notes + ev[:6],
                         "按闸的阈值补测试（阈值在脚本命令里）",
                         evidence_kind="executed")
        return Check("coverage", "核心逻辑测试到位", "pass",
                     "仓库自己的覆盖率闸门全过"
                     + (f"（平均行覆盖 {avg_line:.0f}% / 分支 {avg_branch:.0f}%）"
                        if merged else "")
                     + " " + itest_lane_note,
                     lane_notes + ev[:6],
                     evidence_kind="executed")
    if not merged:
        # 覆盖率没拿到时，**真后端车道的状态仍要说出来**（别把它连同
        # 说明一起吞掉 —— 那是本次判定里唯一的真后端证据）
        extra = ("；真后端车道：" + "；".join(itest_notes)) if itest_notes else ""
        return Check("coverage", "核心逻辑测试到位", "skipped",
                     (raw_note or "没能拿到覆盖率") + " " + runner_note + extra,
                     itest_notes,
                     fix="先让模块的测试能独立跑起来",
                     evidence_kind="executed")
    if thr_line is None and thr_branch is None:
        # 没有仓库闸门、也没显式阈值 ⇒ 缺少判据，不拿通用阈值判 fail
        return Check("coverage", "核心逻辑测试到位", "skipped",
                     f"仓库没有覆盖率闸门，缺少判据（实测平均行覆盖 "
                     f"{avg_line:.0f}% / 分支 {avg_branch:.0f}%）；"
                     f"通用阈值默认不阻塞" + " " + runner_note
                     + " " + itest_lane_note,
                     ev,
                     "补测试把低覆盖的文件拉过门槛，或让仓库声明自己的闸门"
                     "（要按通用阈值判就加 --min-line-coverage）",
                     evidence_kind="executed")
    low = [(k, v) for k, v in merged.items()
           if _below_threshold(v, thr_line, thr_branch)]
    if low:
        return Check("coverage", "核心逻辑测试到位", "fail",
                     f"平均行覆盖 {avg_line:.0f}% / 分支 {avg_branch:.0f}%；"
                     f"{len(low)}/{len(merged)} 个文件低于门槛（{thr_note}）"
                     + " " + runner_note + " " + itest_lane_note,
                     ev, "补测试把低覆盖的文件拉过门槛",
                     evidence_kind="executed")
    return Check("coverage", "核心逻辑测试到位", "pass",
                 f"平均行覆盖 {avg_line:.0f}% / 分支 {avg_branch:.0f}%"
                 f"（门槛 {thr_note}）"
                 + " " + runner_note + " " + itest_lane_note, ev,
                 evidence_kind="executed")


def _below_threshold(v: dict, thr_line: float | None,
                     thr_branch: float | None) -> bool:
    """这个文件的覆盖率低于门槛吗。"""
    if thr_line is not None and v["line"] < thr_line:
        return True
    if thr_branch is not None and v["branch"] < thr_branch:
        return True
    return False


_EXPORT_FN_RE = re.compile(
    r"^\s*export\s+(?:default\s+)?(?:async\s+)?"
    r"(function|const|let|var|class|interface|type|enum)\s+"
    r"([A-Za-z_$][\w$]*)", re.M)


def module_exports(m: Module,
                   kinds: tuple[str, ...] | None = None) -> dict[str, str]:
    """模块的导出（默认排除只存在于编译期的 type/interface）。

    kinds 可限定只取某几类（"function" ⇒ 只取函数/类）。
    """
    out: dict[str, str] = {}
    for p in m.sources:
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for mm in _EXPORT_FN_RE.finditer(text):
            kind, name = mm.group(1), mm.group(2)
            if kind in _TYPE_ONLY:
                continue
            if kinds and kind not in kinds:
                continue
            out.setdefault(name, str(p.relative_to(m.root)))
    return out


def _check_py_exports(m: Module, root: Path) -> Check:
    """Python 导出（模块级 def/class）在测试里零引用 ⇒ 点名。"""
    exports = python_exports(m)
    if not exports:
        return Check("uncovered-exports", "导出的逻辑都有测试", "skipped",
                     "这个模块没有公开的 def/class",
                     evidence_kind="untested")
    _, test_dirs = python_test_cmd(m, root)
    test_text = ""
    for d in test_dirs:
        for p in sorted((root / d).rglob("*.py")) if (root / d).is_dir() else []:
            if re.search(r"(^|/)(test_[^/]+|[^/]+_test)\.py$", str(p)):
                try:
                    test_text += p.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    pass
    # 仓库级测试目录（test/ tests/ spec/）里的用例也算
    for p in m.repo_tests:
        if p.suffix != ".py":
            continue
        try:
            test_text += p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            pass
    # 模块目录内 colocated 的测试（src/pkg/test_mod.py）也要算 ——
    # Python 的惯例就是把测试放在包旁边，之前只扫 test/ tests/ ⇒
    # 这类仓库的所有公开 def/class 都被误判成"零引用"（与 TS 分支不一致）。
    for p in m.tests + m.itests:
        if p.suffix != ".py":
            continue
        try:
            test_text += p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            pass
    if not test_text:
        return Check("uncovered-exports", "导出的逻辑都有测试", "skipped",
                     "没找到 Python 测试文件（test/ 或 tests/ 下没有 test_*.py）",
                     evidence_kind="untested")
    zero = [k for k in exports if not re.search(r"\b" + re.escape(k) + r"\b",
                                                 test_text)]
    if zero:
        return Check("uncovered-exports", "导出的逻辑都有测试", "fail",
                     f"{len(zero)}/{len(exports)} 个公开 def/class 在测试里零引用",
                     [f"{k}（{exports[k]}）" for k in sorted(zero)[:10]],
                     "要么补测试，要么这个函数没人用（删掉）")
    return Check("uncovered-exports", "导出的逻辑都有测试", "pass",
                 f"{len(exports)} 个公开 def/class 都有测试引用")


def check_uncovered_exports(m: Module, idx: RepoIndex) -> Check:
    """R2 导出的运行时逻辑有没有被测到。"""
    if m.lang == "py":
        return _check_py_exports(m, m.root)
    # 只查函数/类 —— 常量（阈值、前缀、维度）本来就没人直接测
    exports = module_exports(m, kinds=("function", "class"))
    support_names = set()
    for sp in m.support:
        try:
            support_names |= set(re.findall(
                r"^\s*export\s+(?:async\s+)?(?:function|const|class)\s+"
                r"([A-Za-z_$][\w$]*)",
                sp.read_text(encoding="utf-8", errors="replace"), re.M))
        except OSError:
            pass
    exports = {k: v for k, v in exports.items() if k not in support_names}
    if not exports:
        return Check("uncovered-exports", "导出的逻辑都有测试", "skipped",
                     "这个模块没有导出的函数/类",
                     evidence_kind="untested")
    test_text = ""
    # 仓库级测试目录（test/ tests/ spec/）里的用例也算 —— 之前只看模块
    # 目录内的测试 ⇒ 标准 test/ 布局的仓库被误判成"导出零引用"
    for t in m.tests + m.itests + m.repo_tests:
        try:
            test_text += t.read_text(encoding="utf-8", errors="replace")
        except OSError:
            pass
    dead = [f"{nm}（{where}）" for nm, where in sorted(exports.items())
            if not re.search(r"\b" + re.escape(nm) + r"\b", test_text)]
    if dead:
        return Check("uncovered-exports", "导出的逻辑都有测试", "fail",
                     f"{len(dead)}/{len(exports)} 个运行时导出在测试里零引用",
                     dead[:10],
                     "要么补测试，要么这个导出没人用（删掉）")
    return Check("uncovered-exports", "导出的逻辑都有测试", "pass",
                 f"{len(exports)} 个运行时导出都有测试引用")


# 模块里出现租户字段 ⇒ 这个模块是租户隔离的（否则不要求租户列）
_TENANT_HINT_RE = re.compile(r"\b(tenant_id|tenantId|tenant|org_id|orgId|"
                             r"organization_id|workspace_id)\b")
# 全局配置表 —— 不是业务数据，不要求租户列/唯一约束
_CONFIG_TABLE_RE = re.compile(
    r"(^|_)(config|configs|setting|settings|meta|metadata|migrations|"
    r"schema_version|kv|feature_flag|feature_flags)(_|$)", re.I)
# 日志/追加型表 —— 本来就会重复，不要求唯一约束
_APPEND_TABLE_RE = re.compile(
    r"(^|_)(log|logs|audit|audits|history|trace|event_log|append|append_only|"
    r"journal)(_|$)", re.I)


def check_schema(m: Module, facts: dict) -> Check:
    """R3/R6 表结构 —— 查 PG catalog 的真实结构。

    判的是**自家五条规则**（主键/唯一约束/租户列/RLS/时间戳时区），
    不是"主流" ⇒ 判据名是"表结构基本合规"，证据里说清查了哪几条。

    租户列与唯一约束有豁免：单租户模块（模块里没有租户字段）、
    全局配置表、日志/追加型表 —— 之前无条件要求 ⇒ 单租户仓库必假红。
    """
    if not facts:
        return Check("schema", "表结构基本合规", "skipped",
                     "没连上数据库（或模块不碰 PG）",
                     fix="设好 POSTGRES_URL 再跑",
                     evidence_kind="untested")
    tenant_scoped = False
    for p in m.sources:
        try:
            if _TENANT_HINT_RE.search(
                    p.read_text(encoding="utf-8", errors="replace")):
                tenant_scoped = True
                break
        except OSError:
            continue
    problems: list[str] = []
    ev: list[str] = []
    missing: list[str] = []
    exemptions: list[str] = []
    for tbl, f in facts.items():
        cols = {}
        for c in f["columns"]:
            parts = c.split("\x1f")
            if parts:
                cols[parts[0]] = parts
        if not cols:
            # 表在连的库里没找到 —— 多半是**连错库**了，不是表设计缺陷。
            # 报红会让人去改表设计，而真正的问题在连接串。
            missing.append(tbl)
            continue
        is_config = bool(_CONFIG_TABLE_RE.search(tbl))
        is_append = bool(_APPEND_TABLE_RE.search(tbl))
        # 豁免：单租户模块（没有租户列体系）、全局配置表、日志/追加型表
        # —— 这三类无条件要求租户列/唯一约束 ⇒ 必假红
        exempt = (not tenant_scoped) or is_config or is_append
        why = ("单租户模块，模块里没有租户字段" if not tenant_scoped else
               "全局配置表" if is_config else "追加型表")
        # 主键：pg_indexes.indexdef 里没有 "PRIMARY KEY" 字样（那是 \d 的
        # 输出），主键索引名以 _pkey 结尾
        if not any("_pkey" in ix for ix in f["indexes"]):
            problems.append(f"{tbl}: 没有主键")
        # 租户列（多租户表）
        has_tenant = any(k in cols for k in ("tenant_id", "tenantId", "org_id"))
        if not has_tenant:
            if exempt:
                exemptions.append(f"{tbl}: 租户列（{why}）")
            else:
                problems.append(f"{tbl}: 没有租户列")
        # 唯一约束
        if not any("UNIQUE" in ix for ix in f["indexes"]):
            if exempt:
                exemptions.append(f"{tbl}: 唯一约束（{why}）")
            else:
                problems.append(f"{tbl}: 没有唯一约束（并发写入会落重复行）")
        # 时间戳用 timestamptz（避免时区歧义）
        for name, parts in cols.items():
            dtype = parts[2] if len(parts) > 2 else ""
            if dtype == "timestamp without time zone":
                problems.append(f"{tbl}.{name}: timestamp 没带时区")
        # RLS
        if f["rls"]:
            parts = f["rls"][0].split("\x1f")
            if len(parts) > 1 and parts[0] != "t":
                problems.append(f"{tbl}: 没开 RLS")
            if len(parts) > 2 and parts[1] != "t":
                problems.append(f"{tbl}: RLS 没 FORCE（表 owner 绕过策略）")
        elif f["policies"]:
            problems.append(f"{tbl}: 有策略但 RLS 没开")
        ev.append(f"{tbl}: {len(cols)} 列 / {len(f['indexes'])} 索引 / "
                  f"{len(f['policies'])} 策略 / RLS={f['rls'] or '未开'}")
    checked = ("查了：主键 / 租户列 / 唯一约束 / 时间戳时区 / RLS"
               + ("（本模块是租户隔离的）" if tenant_scoped else "（单租户模块）"))
    if exemptions:
        ev.append("豁免：" + "；".join(exemptions[:6]))
    if missing:
        # 表没在连的库里找到 ⇒ 这批表的判定**没实测**，不算缺陷
        problems = [p for p in problems if not any(p.startswith(t + ":") for t in missing)]
        if not problems:
            return Check("schema", "表结构基本合规", "skipped",
                         f"{len(missing)} 张表在连的库里没找到（可能连错库）",
                         [f"{t}: 表在库里不存在" for t in missing],
                         "确认 POSTGRES_URL 指向的库里有这些表，再跑一次",
                         evidence_kind="untested")
    if problems:
        return Check("schema", "表结构基本合规", "fail",
                     f"{len(problems)} 处问题", ev + problems[:8],
                     "补主键/唯一约束/租户列/RLS，或把表移出多租户范围",
                     evidence_kind="executed")
    return Check("schema", "表结构基本合规", "pass",
                 f"{len(facts)} 张表都合规", ev + [checked],
                 evidence_kind="executed")


def _print_script_plan(gate_scripts, itest_scripts, reconcilers, baselines
                        ) -> None:
    """列出工具将要执行哪些仓库脚本（--dry-run 用）。"""
    groups = (("覆盖率闸门", gate_scripts), ("真后端车道", itest_scripts),
              ("跨存储对账", reconcilers), ("基线对拍", baselines))
    any_script = False
    for label, scripts in groups:
        for ws, name, cmd in scripts:
            any_script = True
            print(f"  [{label}] {name}（工作目录 {ws}）")
            print(f"      $ {cmd}")
    if not any_script:
        print("  （没有命中白名单的仓库脚本）")


def _echo_script_plan(m: Module, gate_scripts, itest_scripts, reconcilers,
                      baselines) -> None:
    """正常模式下一行列出将执行的仓库脚本（审计入口）。"""
    parts = []
    for label, scripts in (("覆盖率闸门", gate_scripts),
                           ("真后端车道", itest_scripts),
                           ("跨存储对账", reconcilers),
                           ("基线对拍", baselines)):
        for ws, name, cmd in scripts:
            parts.append(f"{label} {name}（$ {cmd}）")
    if parts:
        print("将执行的仓库脚本：" + "；".join(parts))


def check_oracle_independence(m: Module) -> Check:
    """R3 断言的期望值是否独立于被测对象。

    oracle 问题是测试的中心难题：判据来自被测对象自身就是同义反复，测试再多也不
    产生信息（Howden 1968）。SKILL.md 早就写了这条要求，但工具层从来没查过。

    这是**静态启发式**（正则 + 简单别名追踪）：会漏也会误报 ⇒ 只报提醒，不阻塞。
    措辞保持"可能来自被测对象自身"，不要写成"期望值不独立"——后者会把幂等/不变性
    断言（按定义就是拿被测对象输出当期望值）误报成缺陷。
    """
    try:
        from oracle_check import check_oracle_independence as _check
    except ImportError:
        return Check("oracle", "断言的期望值独立", "skipped",
                     "oracle_check 模块不可用（没跟 scripts/ 放在一起）",
                     evidence_kind="untested")
    files = [p for p in (m.tests + m.itests + m.repo_tests) if p.is_file()]
    if not files:
        return Check("oracle", "断言的期望值独立", "skipped",
                     "这个模块没有测试文件，没有断言可查",
                     evidence_kind="untested")
    res = _check(m.root, files)
    findings = res.get("findings") or []
    scanned = res.get("scanned", 0)
    if not findings:
        return Check("oracle", "断言的期望值独立", "pass",
                     f"扫了 {scanned} 个测试文件，没发现期望值取自被测对象自身的断言",
                     evidence_kind="static")
    taut = [f for f in findings if f.get("kind") in ("自比较", "快照自比对")]
    detail = f"{len(findings)} 处断言的期望值可能来自被测对象自身"
    if taut:
        detail += f"（其中 {len(taut)} 处是恒真的同义反复 —— 那一定要改）"
    return Check("oracle", "断言的期望值独立", "warn", detail,
                 [f"{f.get('file')}:{f.get('line')} {f.get('kind')} —— "
                  f"{f.get('snippet')}" for f in findings[:6]],
                 "期望值改从接口/库里独立取；恒真的同义反复一定要改",
                 evidence_kind="static")


def _pin_outside_repo(root: Path, fpath: str) -> str:
    """钉指向仓库外 ⇒ 返回原因；没问题返回空串。

    与 `req_plan.py` 的 `_pin_violation` 同一把尺子：钉只能是仓库内的相对路径。
    越界的钉既不读也不写 —— 否则一份计划就能让工具去探测仓库外的文件。
    """
    p = Path(fpath)
    if p.is_absolute():
        return "是绝对路径"
    try:
        base = root.resolve()
        target = (root / p).resolve()
    except OSError:
        return "路径无法解析"
    if target != base and base not in target.parents:
        return "含 `..`（会跳到仓库外）"
    return ""


def check_requirement(m: Module, plan_path: str | None) -> Check:
    """R7 需求覆盖 —— 计划里的每条不变式都要有真实用例。"""
    if not plan_path:
        return Check("requirement", "需求覆盖到位", "skipped",
                     "没给计划文件（--plan）",
                     evidence_kind="untested")
    pp = Path(plan_path)
    if not pp.is_absolute() and not pp.exists():
        pp = m.root / pp
    if not pp.exists():
        return Check("requirement", "需求覆盖到位", "fail",
                     f"计划文件不存在: {plan_path}")
    plan = parse_plan(pp)
    if not plan.invariants:
        return Check("requirement", "需求覆盖到位", "fail",
                     "计划里没有不变式", fix="先填不变式清单")
    missing: list[str] = []
    outside: list[str] = []
    for ident, desc, pin in plan.invariants:
        pins = parse_pins(pin)
        if not pins:
            missing.append(f"{ident}: 没有钉")
            continue
        ok = False
        for fpath, case in pins:
            why = _pin_outside_repo(m.root, fpath)
            if why:
                # 越界的钉不读那个文件，也不当成"覆盖到了"
                outside.append(f"{ident}: 钉「{fpath}」{why}")
                continue
            if not (m.root / fpath).exists():
                continue
            if case is None:
                ok = True
                break
            if case_matches(case, test_case_names(m.root, fpath)):
                ok = True
                break
        if not ok and not any(o.startswith(ident + ":") for o in outside):
            missing.append(f"{ident}: {desc[:30]}")
    if outside:
        return Check("requirement", "需求覆盖到位", "fail",
                     f"{len(outside)} 条不变式的钉指向仓库外",
                     outside[:8],
                     "钉只能是仓库内的相对路径 —— 越界的钉不读也不写",
                     evidence_kind="executed")
    if missing:
        return Check("requirement", "需求覆盖到位", "fail",
                     f"{len(missing)}/{len(plan.invariants)} 条不变式没落到真实用例",
                     missing[:8], "写用例，或把不变式改到真实落点上")
    return Check("requirement", "需求覆盖到位", "pass",
                 f"{len(plan.invariants)} 条不变式都有真实用例",
                 [f"{i}: {d[:40]}" for i, d, _ in plan.invariants[:6]])


def _module_exports_any(m: Module) -> dict[str, str]:
    """模块的导出符号（Python 用公开 def/class，TS 用 export）。"""
    if m.lang == "py":
        return python_exports(m)
    return module_exports(m)


def check_extensibility(m: Module, idx: RepoIndex) -> Check:
    """R8 不过度抽象 —— 导出符号几乎没人用（规则之三）。

    之前叫"具备扩展性"，但它测的是"有没有过度抽象" ⇒ 名字反了。
    Python 下导出集为空时不能判 pass（那是零分析的正面结论）。
    """
    exports = _module_exports_any(m)
    if not exports:
        return Check("extensibility", "不过度抽象", "skipped",
                     "这个模块没有可分析的导出符号（零分析 ⇒ 不给结论）",
                     evidence_kind="untested")
    thin = [f"{nm}（全仓 {idx.tokens[nm]} 处引用）"
            for nm in sorted(exports)
            if idx.tokens[nm] <= 2]
    if thin:
        return Check("extensibility", "不过度抽象", "warn",
                     f"{len(thin)}/{len(exports)} 个导出符号几乎没有调用点",
                     thin[:8],
                     "规则之三：抽象要等第三个用例；只有一个调用点先内联")
    return Check("extensibility", "不过度抽象", "pass",
                 f"{len(exports)} 个导出符号都有多个调用点")


def check_no_wheels(m: Module, idx: RepoIndex) -> Check:
    """R9 没有重复造轮子 —— 全仓同名导出（排除通用名与测试文件）。"""
    own = _module_exports_any(m)
    if not own:
        return Check("no-wheels", "没有重复造轮子", "skipped",
                     "这个模块没有可分析的导出符号（零分析 ⇒ 不给结论）",
                     evidence_kind="untested")
    dup: list[str] = []
    for nm, where in sorted(own.items()):
        if nm.lower() in _GENERIC_NAMES or len(nm) < 5:
            continue
        # 测试支撑文件里的同名不算重复实现
        if re.search(r"(itest|test-support|fixtures|helpers)", where):
            continue
        other = idx.exports.get(nm)
        if other and not other.startswith(m.path + "/"):
            if re.search(r"(itest|test-support|fixtures|helpers)", other):
                continue
            dup.append(f"{nm}（本模块 {where}，另有 {other}）")
    if dup:
        return Check("no-wheels", "没有重复造轮子", "warn",
                     f"{len(dup)} 个符号在别处有同名实现 —— 确认是不是同一件事",
                     dup[:8],
                     "是同一件事就复用；不同领域同名可以放过")
    return Check("no-wheels", "没有重复造轮子", "pass",
                 f"模块的导出符号在全仓都是独一份")


def check_open_source(m: Module) -> Check:
    """R10 用的是主流开源库。"""
    if not m.deps:
        return Check("open-source", "使用主流开源库", "warn",
                     "模块没有第三方依赖 —— 确认这是有意的（简单优先）",
                     fix="如果需求领域有成熟库，别自己写",
                     evidence_kind="static")
    ev = [d for d in m.deps[:10]]
    return Check("open-source", "使用主流开源库", "pass",
                 f"{len(m.deps)} 个第三方依赖", ev)


# ---------------------------------------------------------------- 主流程


def find_baseline_scripts(m: Module, root: Path) -> list[tuple[Path, str, str]]:
    """找仓库自己的基线对拍脚本（白名单精确名）。"""
    cands: list[tuple[Path, str, str]] = []
    for ws, pkg in _workspace_manifests(root):
        for name, cmd in (pkg.get("scripts") or {}).items():
            if not isinstance(cmd, str) or not isinstance(name, str):
                continue
            if not script_allowed("baseline", name, cmd):
                continue
            cands.append((ws, name, cmd))
    return cands


def _tests_ran(m: Module, gate_scripts, raw: str, cov: dict,
               failed: list[str], itest_notes: list[str]) -> bool:
    """这次判定里，测试到底跑没跑？

    跑不动（找不到 runner / 超时 / 没 pytest）与"跑了全绿"是两回事 ——
    之前两者都报"没有失败用例"⇒ pass。
    """
    if gate_scripts or failed or cov or itest_notes:
        return True
    if not (m.tests or m.itests):
        return False
    blockers = ("没有找到该模块的测试文件", "没找到 pytest",
                "没认出 Python 包目录", "跑不起来", "测试跑超时")
    return not any(b in raw for b in blockers)


def run_readiness(args) -> int:
    root = Path(args.repo).resolve()
    m = discover_module(root, args.module, getattr(args, "test_dir", None))
    if not m.base.exists():
        print(f"模块不存在: {m.base}", file=sys.stderr)
        return 2

    if not m.sources and not m.tests:
        # 没识别出源码 ⇒ 不能给判定（否则会输出一堆关于"空"的 ✓）
        print(f"没在 {m.base} 下识别出源码文件。"
              f"当前按 TS/JS/Python 的文件约定识别"
              f"（.ts/.tsx/.js/.jsx/.py 等）——"
              f"换路径，或扩展 CODE_EXT。", file=sys.stderr)
        return 2

    idx = index_repo(root)
    # 优先用**仓库自己的闸门**（它的阈值与批次划分是仓库的权威口径）
    setup_errors: list[str] = []
    gate_scripts = find_gate_scripts(m, root)
    itest_scripts = find_itest_scripts(m, root)
    reconcilers = find_reconcilers(m, root)
    baselines = find_baseline_scripts(m, root)
    if getattr(args, "dry_run", False):
        # 只列出会执行什么，不执行
        print(f"将要执行的仓库脚本 — {m.path}（--dry-run，不执行）")
        print(f"仓库：{root}")
        _print_script_plan(gate_scripts, itest_scripts, reconcilers, baselines)
        return 0
    # 正常模式也把"将执行哪些仓库脚本"说清楚 —— 工具会跑目标仓库自己的脚本，
    # 这行字是审计入口：白名单命中的脚本 + 确切命令，一眼可核对。
    _echo_script_plan(m, gate_scripts, itest_scripts if args.include_itest else [],
                      reconcilers, baselines)
    lane_notes: list[str] = []
    if gate_scripts:
        cov, failed, lane_notes = run_gates(m, gate_scripts)
        raw = ""
    elif m.lang == "py":
        cov, failed, setup_errors, raw = run_python_coverage(m, root)
    else:
        cov, failed, raw = run_coverage(m, include_itest=args.include_itest)
    # 真后端车道：驱动仓库自己的 itest 脚本（自带环境与防挂参数）
    # 默认**不跑** —— 要连 PG + 向量库，且慢；--include-itest 才跑。
    itest_cov: dict[str, dict] = {}
    itest_failed: list[str] = []
    itest_notes: list[str] = []
    itest_env: list[str] = []
    if itest_scripts and args.include_itest:
        itest_cov, itest_failed, itest_notes, itest_env = run_itest_lane(
            m, itest_scripts)
        failed += itest_failed
        # 环境没就绪的报错不算产品缺陷 ⇒ 不进"测试全绿"的阻塞项
        setup_errors += itest_env
    _prefix, script_name = project_test_cmd(m)
    runner_note = f"（用项目自己的 {script_name} 命令）"
    if args.verbose and raw:
        print(raw[-3000:], file=sys.stderr)

    tests_found = bool(m.tests or m.itests)
    tests_ran = _tests_ran(m, gate_scripts, raw, cov, failed, itest_notes)
    checks = [
        check_tests_green(failed, setup_errors, tests_found=tests_found,
                          tests_ran=tests_ran),
        check_test_hygiene(m),
        check_coverage(m, cov, "" if cov else raw, runner_note,
                       lane_notes=lane_notes, itest_cov=itest_cov,
                       itest_notes=itest_notes,
                       min_line=getattr(args, "min_line_coverage", None),
                       min_branch=getattr(args, "min_branch_coverage", None),
                       repo_threshold=repo_coverage_threshold(root)),
        check_uncovered_exports(m, idx),
        check_schema(m, query_schema(m, args.database_url or
                                     os.environ.get("POSTGRES_URL", ""))),
        check_data_flow(m, root, getattr(args, "test_tenant", []),
                        drift_re=getattr(args, "drift_re", None),
                        header_re=getattr(args, "header_re", None),
                        tenant_prefixes=getattr(args, "test_tenant_prefix", None),
                        csv_pairs=getattr(args, "csv_pairs", None),
                        tenant_col=getattr(args, "tenant_col", None)),
        check_data_accuracy(m, root),
        check_requirement(m, args.plan),
        check_extensibility(m, idx),
        check_no_wheels(m, idx),
        check_open_source(m),
        check_oracle_independence(m),
    ]

    # ---- 结论：证据分层 + 三档，不用"达到交付条件"这种裁决措辞 ----
    # 为什么不是裁决：工具掌握的是"11 条机械判据的实测状态"，不是"这个模块能不能
    # 交付客户"。那两者之间隔着一整层未言明的推理（需求是否被正确理解、性能、安全、
    # 运维就绪都不在判据内）。所以输出的是**证据清单**，决策留给有职责的人。
    fails = [c for c in checks if c.status == "fail"]
    warns = [c for c in checks if c.status == "warn"]
    no_signal = [c for c in checks if c.status in ("skipped", "untested")]
    # 「实测通过」只数 executed —— 静态信号永远不能算进实测通过里。
    passed = [c for c in checks if c.status == "pass"
              and c.evidence_kind == "executed"]
    static_signals = [c for c in checks if c.evidence_kind == "static"]
    # 关键判据（测试是否绿）没测到 ⇒ 不能说"测过了"。退出码据此非 0，
    # 否则一个零测试的仓库会在 CI 里拿到绿灯。
    key_untested = [c for c in checks if c.key == "tests-green"
                    and c.status in ("skipped", "untested")]
    # 静态信号永远不能单独支撑"通过"：没有一条实测判据通过时，不给肯定结论。
    only_static = not passed and not fails

    if args.json:
        print(json.dumps({
            "module": m.path,
            "tables": m.tables,
            "checks": [c.__dict__ for c in checks],
            "conclusion": {
                "blocking": len(fails),
                "needs_human": len(warns),
                "no_signal": len(no_signal),
                "measured_pass": len(passed),
                "static_signals": len(static_signals),
                "not_judged": list(NOT_JUDGED),
                "exit_rule": "有阻塞项，或关键判据（测试是否绿）没测到 ⇒ 退出码 1",
            },
        }, ensure_ascii=False, indent=2))
        return 1 if (fails or key_untested) else 0

    print(f"\n交付就绪度分析 — {m.path}")
    print(f"仓库：{root}")
    print(f"源码 {len(m.sources)} 文件（+{len(m.support)} 个测试支撑）"
          f" / 单测 {len(m.tests)} 个"
          f" / itest {len(m.itests)} 个"
          + (f" / 表 {', '.join(m.tables)}" if m.tables else ""))
    print()

    def _section(title: str, items: list[Check]) -> None:
        if not items:
            return
        print(f"── {title} " + "─" * max(4, 46 - len(title)))
        for c in items:
            print(f"  {c.mark} {c.title} — {c.detail}")
            for e in c.evidence[:6]:
                print(f"      · {e}")
            if c.status in ("fail", "warn") and c.fix:
                print(f"      → {c.fix}")
        print()

    _section("已实测", [c for c in checks if c.evidence_kind == "executed"])
    _section("静态信号（不能单独支撑「通过」）",
             [c for c in checks if c.evidence_kind == "static"])
    _section("未实测", [c for c in checks if c.evidence_kind == "untested"])

    selfcheck_failed = False
    if getattr(args, "self_check", False):
        selfcheck_failed = _selfcheck_section(m.path, root)

    print("── 本工具不判定 " + "─" * 32)
    print("  " + " · ".join(NOT_JUDGED))
    print("  上面只是测试就绪度的实测结果；「能不能交付」仍要人判断。")
    print()

    print(f"结论：{len(fails)} 项阻塞 · {len(warns)} 项待人工确认 · "
          f"{len(no_signal)} 项无信号"
          + (f" · {len(passed)} 项实测通过" if passed else "")
          + (f"（另有 {len(static_signals)} 条静态信号）"
             if static_signals else ""))
    for c in fails:
        print(f"  ✗ {c.title}：{c.detail}")
    for c in key_untested:
        print(f"  ○ {c.title}：{c.detail}")
    if only_static:
        print("  （没有一条实测判据通过 —— 静态信号不足以支撑任何肯定结论）")
    if selfcheck_failed:
        print("反向验证未通过 —— 这是**工具自检**失败（判据抓不住注入的缺陷），"
              "与模块判定无关。")
    print("退出码 1 = 有阻塞项，或关键判据（测试是否绿）没测到")
    if selfcheck_failed:
        return 1
    return 1 if (fails or key_untested) else 0


def _selfcheck_section(module: str, root: Path) -> bool:
    """反向验证：注入一个已知缺陷，证明判据会翻红。

    一个只输出绿色的判定工具，用户只能相信它的绿 —— 这里让工具证明自己会失败。
    失败返回 True（当作退出码 1 的理由）。
    """
    print("── 反向验证 " + "─" * 34)
    try:
        import selfcheck
    except ImportError:
        print("  selfcheck 模块不可用（没跟 scripts/ 放在一起）")
        return True
    try:
        res = selfcheck.run_selfcheck(module, str(root))
    except Exception as e:                       # 自检失败不该把整份报告带崩
        print(f"  自检跑不起来：{e}")
        return True
    for f in res.get("faults") or []:
        mark = "✔" if f.get("flipped") else "✗"
        print(f"  {mark} [{f.get('kind')}] {f.get('target')}")
        b, a = f.get("before") or {}, f.get("after") or {}
        if b.get("id") or b.get("key"):
            bid = b.get("id") or b.get("key")
            print(f"      {bid}：{b.get('status')} → {a.get('status')}")
        for label, side in (("前", b), ("后", a)):
            if side.get("detail"):
                print(f"      {label}：{side['detail']}")
        if f.get("note"):
            print(f"      说明：{f['note']}")
    print(f"  结论：{res.get('verdict')}")
    if res.get("reason"):
        print(f"  依据：{res['reason']}")
    return res.get("verdict") != "反向验证通过"


def _parse_csv_pairs(items: list[str]) -> list[tuple[str, str]]:
    """`a.csv=b.csv` / `a.csv:b.csv` ⇒ [(a, b)]。"""
    out: list[tuple[str, str]] = []
    for it in items or []:
        for sep in ("=", ":"):
            if sep in it:
                a, b = it.split(sep, 1)
                a, b = a.strip(), b.strip()
                if a and b:
                    out.append((a, b))
                break
    return out


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("module", help="模块路径（相对仓库根）")
    ap.add_argument("--repo", default=".", help="仓库根")
    ap.add_argument("--plan", default=None, help="需求测试计划文件")
    ap.add_argument("--database-url", default=None,
                    help="PG 连接串（默认取 POSTGRES_URL）")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    ap.add_argument("--verbose", action="store_true", help="打印测试原始输出")
    ap.add_argument("--test-tenant", action="append", default=[],
                    help="声明测试租户（可重复；精确名或前缀）—— "
                         "测试租户的漂移是夹具状态，不阻塞交付")
    ap.add_argument("--include-itest", action="store_true",
                    help="把真后端 itest 车道也算进覆盖率（要连 PG + 向量库，慢）")
    ap.add_argument("--test-dir", action="append", default=[],
                    help="额外的测试目录（相对仓库根；可重复）—— "
                         "仓库级 test/ tests/ spec/ 之外还有别处放测试时用它")
    ap.add_argument("--min-line-coverage", type=float, default=None,
                    help="行覆盖率门槛（默认**不**用通用阈值：没有仓库闸门时"
                         "判 skipped，不判 fail）")
    ap.add_argument("--min-branch-coverage", type=float, default=None,
                    help="分支覆盖率门槛（同上）")
    ap.add_argument("--drift-pattern", default=None,
                    help="对账输出里『漂移行』的正则（默认认 漂移/❌/drift）")
    ap.add_argument("--table-header-pattern", default=None,
                    help="对账输出里表头/分隔行的正则（默认认 一致性/租户/---）")
    ap.add_argument("--test-tenant-prefix", action="append", default=[],
                    help="测试租户前缀（可重复；给了就**替换**内置默认表）")
    ap.add_argument("--csv-pair", action="append", default=[],
                    help="CSV 对照关系 a.csv=b.csv（可重复）—— 按声明配对，"
                         "不做两两配对")
    ap.add_argument("--tenant-col", default=None,
                    help="租户列名（CSV 里定位租户的那一列）")
    ap.add_argument("--dry-run", action="store_true",
                    help="只列出候选脚本与将执行的命令，然后退出（不执行）")
    ap.add_argument("--self-check", action="store_true",
                    help="反向验证：往副本里注入一个已知缺陷，证明判据会翻红"
                         "（只作用于临时副本，不改你的仓库）")
    args = ap.parse_args(argv)
    for name, pat in (("--drift-pattern", args.drift_pattern),
                      ("--table-header-pattern", args.table_header_pattern)):
        if pat:
            try:
                re.compile(pat)
            except re.error as e:
                print(f"{name} 不是合法正则：{pat!r}（{e}）", file=sys.stderr)
                return 2
    args.drift_re = re.compile(args.drift_pattern) if args.drift_pattern else None
    args.header_re = (re.compile(args.table_header_pattern)
                      if args.table_header_pattern else None)
    args.csv_pairs = _parse_csv_pairs(args.csv_pair)
    args.test_tenant_prefix = args.test_tenant_prefix or None
    return run_readiness(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
