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
    status: str          # pass / fail / warn / skipped
    detail: str = ""
    evidence: list[str] = field(default_factory=list)
    fix: str = ""

    @property
    def mark(self) -> str:
        return {"pass": "✓", "fail": "✗", "warn": "·", "skipped": "—"}[self.status]


@dataclass
class Module:
    root: Path
    path: str
    base: Path
    sources: list[Path] = field(default_factory=list)
    tests: list[Path] = field(default_factory=list)
    itests: list[Path] = field(default_factory=list)
    support: list[Path] = field(default_factory=list)
    tables: list[str] = field(default_factory=list)
    deps: list[str] = field(default_factory=list)
    workspace: Path | None = None
    lang: str = "other"      # ts / py / other


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


def discover_module(root: Path, modpath: str) -> Module:
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
        for mm in re.finditer(r"from\s+['\"]([^'\"]+)['\"]", text):
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
        notes.append(f"{name}: {'✓ 通过' if code == 0 else '✗ 未达阈值'}"
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
    inc = f"--test-coverage-include={base}/**/*.ts"
    for ws, name, cmd in scripts:
        code, out, _ = run_gate(ws, name, cmd, extra=[inc], timeout=timeout)
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
    if runner in ("node", "tsx", "nodejs"):
        cmd += ["--experimental-test-coverage",
                f"--test-coverage-include={inc}/**/*.ts"]
    elif runner == "vitest":
        cmd += ["--coverage", f"--coverage.include={inc}/**/*.ts"]
    elif runner == "jest":
        cmd += ["--coverage", "--collectCoverageFrom",
                f"{inc}/**/*.ts"]
    cmd_base = list(cmd)
    env = {**os.environ, "PYTHONUTF8": "1"}
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
    if runner in ("node", "tsx", "nodejs"):
        cmd += ["--experimental-test-coverage",
                f"--test-coverage-include={inc}/**/*.ts"]
    elif runner == "vitest":
        cmd += ["--coverage", f"--coverage.include={inc}/**/*.ts"]
    elif runner == "jest":
        cmd += ["--coverage", "--collectCoverageFrom", f"{inc}/**/*.ts"]
    cmd += [str(t.relative_to(m.workspace)) for t in tests]
    env = {**os.environ, "PYTHONUTF8": "1"}
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
    # 项目本地的可执行文件（tsx / vitest / jest）不在 PATH 上 ——
    # pnpm 跑脚本时会把 node_modules/.bin 加进去，我们照做
    env = {**os.environ, "PYTHONUTF8": "1"}
    if include_itest:
        # itest 车道默认跳过（要 MEMORY_ITEST=1 + 本地 Milvus/PG）
        env["MEMORY_ITEST"] = "1"
    bin_dir = m.workspace / "node_modules" / ".bin"
    if bin_dir.is_dir():
        env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"


# ---------------------------------------------------------------- 实测：PG 表结构


def _psql(url: str, sql: str) -> list[str]:
    try:
        r = subprocess.run(
            ["psql", url, "-t", "-A", "-F", "\x1f", "-c", sql],
            capture_output=True, text=True, encoding="utf-8", timeout=30)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return []
    if r.returncode != 0:
        return []
    return [ln for ln in r.stdout.splitlines() if ln.strip()]


def query_schema(m: Module, url: str) -> dict[str, dict]:
    """从 PG catalog 查真实的表结构事实。"""
    if not m.tables or not shutil.which("psql") or not url:
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
            if "coverage" not in name.lower() and "coverage" not in cmd.lower():
                continue
            if not _mentions_module(m, cmd):
                continue
            cands.append((ws, name, cmd))
    if not cands:
        return []
    return _entries(cands)


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
            low = name.lower()
            if "integration" not in low and "itest" not in low:
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
    env = {**os.environ, "PYTHONUTF8": "1", **env_over}
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
            if "reconcile" not in name.lower() and "reconcile" not in cmd.lower():
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
                   timeout: int = 180
                   ) -> tuple[list[str], str, list[str], Path]:
    """实跑对账脚本，返回 (漂移租户行, 原始输出)。

    对账脚本通常会把报告写到磁盘（人工看的那份）—— 自动化跑不能覆盖它，
    所以把输出目录指到临时目录。
    """
    import shlex
    import tempfile
    out_dir = tempfile.mkdtemp(prefix="reconcile-")
    env = {**os.environ, "PYTHONUTF8": "1",
           "MEMORY_RECONCILE_OUT_DIR": out_dir}
    bin_dir = ws / "node_modules" / ".bin"
    if bin_dir.is_dir():
        env["PATH"] = f"{bin_dir}{os.pathsep}{env.get('PATH', '')}"
    # 用项目自己的 runner（路径别名要 tsx 才解析）
    parts = shlex.split(cmd)
    runner = parts[0] if parts else "node"
    prefix = parts
    while prefix and ("*" in prefix[-1] or ".test." in prefix[-1]):
        prefix.pop()
    if runner in ("node", "tsx", "nodejs"):
        prefix += ["--experimental-test-coverage"]
    elif runner == "vitest":
        prefix += ["--run"]
    try:
        r = subprocess.run(prefix, cwd=ws, capture_output=True, text=True,
                           encoding="utf-8", timeout=timeout, env=env)
    except subprocess.TimeoutExpired:
        return [], [], "", Path(out_dir)
    except FileNotFoundError as e:
        return [], [], "", Path(out_dir)
    text = r.stdout + r.stderr
    drift = [ln.strip() for ln in text.splitlines()
             if "漂移" in ln and "❌" in ln]
    # 一致的租户也留着 —— 只列漂移的会让人以为生产租户也漂了
    all_rows = [ln.strip() for ln in text.splitlines()
                if ln.strip().startswith("|") and "一致性" not in ln
                and "---" not in ln and "租户" not in ln]
    if not drift and r.returncode != 0:
        # 顺带修一个隐患：出错时这里原来只返回 2 个值，调用方解包会炸
        return [], f"对账脚本退出码 {r.returncode}：\n" + text[-1500:], [], \
            Path(out_dir)
    return drift, text, all_rows, Path(out_dir)


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


def compare_csv_fields(out_dir: Path) -> tuple[list[dict], int]:
    """成对 CSV 之间做字段级比对，返回 (不一致清单, 比过几对)。"""
    import csv  # noqa: F401  (被 _read_csv 用)
    try:
        files = sorted(p for p in out_dir.iterdir() if p.suffix.lower() == ".csv")
    except OSError:
        return [], 0
    if len(files) < 2:
        return [], 0
    diffs: list[str] = []
    pairs = 0
    for i in range(len(files)):
        for j in range(i + 1, len(files)):
            cols_a, rows_a = _read_csv(files[i])
            cols_b, rows_b = _read_csv(files[j])
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
            tcol = next((c for c in fields if _TENANT_COL_RE.match(c)), None)
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
                    diffs.append({
                        "id": kid,
                        "field": f,
                        "a": va[:60],
                        "b": vb[:60],
                        "tenant": (ra.get(tcol) or "").strip() if tcol else "",
                        "text": (f"{files[i].name} ↔ {files[j].name}：id={kid} 的 "
                                 f"{f} 不一致（前者 {va[:40]!r} / 后者 {vb[:40]!r}）"),
                    })
    return diffs, pairs


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


# 测试租户的名字约定（lumen: itest-* / memtest-* / mock-* / eval-*）
_TEST_TENANT_RE = re.compile(
    r"^(itest|memtest|mock|test|eval|e2e|ci|scratch|probe|tmp|temp)[-_]",
    re.I)
# 租户列的名字（CSV 里用来定位这条记录属于哪个租户）
_TENANT_COL_RE = re.compile(r"^(tenant_id|tenantId|tenant)$")


def is_test_tenant(name: str, declared: list[str]) -> bool:
    """这个租户是测试专用的吗？

    判据：名字像测试租户，或仓库显式声明过（精确名或前缀）。
    """
    if not name:
        return False
    for d in declared:
        if not d:
            continue
        if name == d or name.startswith(d):
            return True
    return bool(_TEST_TENANT_RE.match(name))


def _tenant_of_row(row: str) -> str:
    """从对账报告的表格行里取租户名：`| tenant | ... |`"""
    parts = row.split("|")
    if len(parts) < 2:
        return ""
    return parts[1].strip()


def check_data_flow(m: Module, root: Path,
                    declared_test_tenants: list[str] | None = None) -> Check:
    """D1 数据流一致 —— 实跑模块自己的对账机制。

    判交付只看**生产租户**：测试租户的状态是信息，不是阻塞项
    （测试用例常刻意注入失败态/直接插库占位）。
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
                     "先让工具认出来再判")
    drift_all: list[str] = []
    notes: list[str] = []
    clean: list[str] = []
    field_diffs: list[str] = []
    pairs_checked = 0
    for ws, name, cmd in reconcilers:
        drift, text, rows, out_dir = run_reconciler(m, ws, name, cmd)
        drift_all += drift
        clean += [r for r in rows if r not in drift]
        notes.append(f"{name}：{len(drift)} 个租户漂移")
        # 字段级：同一个 id 在两个存储里的同一个字段对不上
        diffs, pairs = compare_csv_fields(out_dir)
        pairs_checked += pairs
        field_diffs += diffs
    # 按租户口径拆：生产租户的漂移才阻塞交付
    prod_drift = [r for r in drift_all
                  if not is_test_tenant(_tenant_of_row(r), declared_test_tenants)]
    test_drift = [r for r in drift_all
                  if is_test_tenant(_tenant_of_row(r), declared_test_tenants)]
    prod_fields = [d for d in field_diffs
                   if not is_test_tenant(d.get("tenant", ""),
                                         declared_test_tenants)]
    test_fields = [d for d in field_diffs
                   if is_test_tenant(d.get("tenant", ""), declared_test_tenants)]

    if prod_fields:
        return Check("data-flow", "数据流一致（跨存储对账）", "fail",
                     f"生产租户有 {len(prod_fields)} 处跨存储字段不一致"
                     f"（比了 {pairs_checked} 对存储导出）",
                     [d["text"] for d in prod_fields[:10]],
                     "同一 id 在两个存储里的同一字段值不同 ⇒ 读到的和写下的不是"
                     "同一份数据（常见原因：截断、归一化不一致、写路径漏更新）")
    if prod_drift:
        return Check("data-flow", "数据流一致（跨存储对账）", "fail",
                     f"{len(prod_drift)} 个**生产租户**的数据流漂移"
                     + (f"（另有 {len(test_drift)} 个测试租户漂移，不阻塞交付）"
                        if test_drift else "")
                     + f"；字段级比对 {pairs_checked} 对无差异",
                     prod_drift[:10] + clean[:6],
                     "缺向量⇒召不回；多余向量⇒占 top-K 名额后被 join 丢掉；"
                     "正文不一致⇒索引的是旧正文")
    if test_drift or test_fields:
        return Check("data-flow", "数据流一致（跨存储对账）", "warn",
                     f"生产租户一致；{len(test_drift)} 个测试租户有漂移"
                     + (f"、{len(test_fields)} 处字段不一致" if test_fields else "")
                     + "（测试夹具状态，不阻塞交付）",
                     test_drift[:6] + [d["text"] for d in test_fields[:4]],
                     "确认两件事：①这些租户不会流进生产；"
                     "②状态是刻意的失败注入/直接插库占位（不是漏镜像）。"
                     "认错了用 --test-tenant 声明")
    return Check("data-flow", "数据流一致（跨存储对账）", "pass",
                 f"实跑了 {len(reconcilers)} 个对账脚本，生产租户无漂移"
                 + (f"（字段级比对 {pairs_checked} 对无差异）" if pairs_checked
                    else ""),
                 notes)


def check_data_accuracy(m: Module, root: Path) -> Check:
    """D2 数据准确性 —— 实跑项目的基线对拍。"""
    cands: list[tuple[Path, str, str]] = []
    for ws, pkg in _workspace_manifests(root):
        for name, cmd in (pkg.get("scripts") or {}).items():
            if not isinstance(cmd, str) or not isinstance(name, str):
                continue
            low = name.lower()
            if "baseline" in low and ("check" in low or cmd.endswith("--check")):
                cands.append((ws, name, cmd))
            elif "eval" in low and "check" in low:
                cands.append((ws, name, cmd))
    if not cands:
        return Check("data-accuracy", "数据准确（基线对拍）", "skipped",
                     "项目里没有基线对拍脚本",
                     fix="加一个：当前指标对拍已提交基线，低于阈值就红")
    import shlex
    fails: list[str] = []
    notes: list[str] = []
    skipped: list[str] = []
    for ws, name, cmd in cands:
        parts = shlex.split(cmd)
        prefix = parts
        while prefix and ("*" in prefix[-1] or ".test." in prefix[-1]):
            prefix.pop()
        env = {**os.environ, "PYTHONUTF8": "1"}
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
                     "修数据或更新基线（先确认是缺陷还是基线过期）")
    if skipped and not notes:
        return Check("data-accuracy", "数据准确（基线对拍）", "skipped",
                     "基线对拍没跑成（不是数据回归，是没产物）", skipped[:6],
                     "先跑对应的 eval 套件产出 artifacts，再对拍基线")
    return Check("data-accuracy", "数据准确（基线对拍）", "pass",
                 f"实跑了 {len(cands)} 个基线对拍脚本，通过 {len(notes)} 个"
                 + (f"，{len(skipped)} 个没跑成" if skipped else ""),
                 notes + skipped[:4])


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
                     setup_errors: list[str] | None = None) -> Check:
    """R0 测试全绿 —— 测试红着就没有"达到交付条件"可言。

    只认**断言失败**（FAILED）。setup 报错（fixture/环境没就绪，例：连不上
    PG）不是断言失败 —— 那是"环境没准备好"，判 skipped 并说清缺什么。
    """
    setup_errors = setup_errors or []
    if not failed:
        if setup_errors:
            return Check("tests-green", "测试全绿", "skipped",
                         f"{len(setup_errors)} 个用例 setup 报错（环境/依赖没就绪）"
                         f" —— 不是断言失败，判不了",
                         setup_errors[:10],
                         "先把环境准备好（数据库/中间件/依赖），再判测试绿不绿")
        return Check("tests-green", "测试全绿", "pass", "没有失败用例")
    return Check("tests-green", "测试全绿", "fail",
                 f"{len(failed)} 个用例失败"
                 + (f"（另有 {len(setup_errors)} 个用例 setup 报错，"
                    f"环境没就绪）" if setup_errors else ""),
                 failed[:10],
                 "先修到全绿 —— 红的测试不能交付")


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
    env = {**os.environ, "PYTHONUTF8": "1"}
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
                   itest_notes: list[str] | None = None) -> Check:
    """R1 核心逻辑测试到位 —— 实测覆盖率。

    覆盖率判定当前只实现 JS/TS 的（node --experimental-test-coverage /
    vitest / jest）。别的语言 ⇒ skipped 并说清，不给假数字。

    门槛归仓库所有：仓库有自己的覆盖率闸门 ⇒ **用仓库的阈值**（它把文件按
    "只测自己那批"分闸，每闸自带阈值）⇒ 通用阈值在这里是错的
    （例：service.ts 行 56%，仓库 service 闸的阈值是 49 ⇒ 仓库认为合格）。

    真后端（itest）车道的覆盖**并进来**（逐文件取大 = 并集语义），两条车道
    分开报 —— 混成一个数会让人以为是一个口径。
    """
    if m.lang == "py":
        if not cov:
            return Check("coverage", "核心逻辑测试到位", "skipped",
                         raw_note or "没拿到 Python 覆盖率",
                         fix="装 pytest-cov，确认 .venv/bin/pytest 可用")
        vals = [v for k, v in cov.items() if k != "__all__"]
        avg = sum(v["line"] for v in vals) / len(vals) if vals else 0
        low = [(k, v) for k, v in cov.items()
               if k != "__all__" and (v["line"] < 80 or v["branch"] < 70)]
        ev = [f"{k}: 行{v['line']:.0f}% 分支{v['branch']:.0f}%"
              for k, v in sorted(cov.items(), key=lambda kv: kv[1]["line"])[:10]
              if k != "__all__"]
        if low:
            return Check("coverage", "核心逻辑测试到位", "fail",
                         f"平均行覆盖 {avg:.0f}%；{len(low)}/{len(vals)} 个文件"
                         f"低于门槛（行<80% 或 分支<70%）",
                         ev, "补测试把低覆盖的文件拉过门槛")
        return Check("coverage", "核心逻辑测试到位", "pass",
                     f"平均行覆盖 {avg:.0f}%（pytest-cov 实跑）", ev)
    lane_notes = lane_notes or []
    itest_cov = itest_cov or {}
    itest_notes = itest_notes or []
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
    if not merged:
        # 覆盖率没拿到时，**真后端车道的状态仍要说出来**（别把它连同
        # 说明一起吞掉 —— 那是本次判定里唯一的真后端证据）
        extra = ("；真后端车道：" + "；".join(itest_notes)) if itest_notes else ""
        return Check("coverage", "核心逻辑测试到位", "skipped",
                     (raw_note or "没能拿到覆盖率") + " " + runner_note + extra,
                     itest_notes,
                     fix="先让模块的测试能独立跑起来")
    avg_line = sum(v["line"] for v in merged.values()) / len(merged)
    avg_branch = sum(v["branch"] for v in merged.values()) / len(merged)
    ev = [f"{k}: 行{v['line']:.0f}% 分支{v['branch']:.0f}% 函数{v['func']:.0f}%"
          for k, v in sorted(merged.items(), key=lambda kv: kv[1]["line"])[:10]]
    if lane_notes:
        # 仓库自己的闸门 ⇒ 判定看闸门（它的阈值是权威），不套通用阈值
        bad = [x for x in lane_notes if x.startswith("✗")]
        if bad:
            return Check("coverage", "核心逻辑测试到位", "fail",
                         f"仓库自己的覆盖率闸门有 {len(bad)} 个没过"
                         f"（平均行覆盖 {avg_line:.0f}% / 分支 {avg_branch:.0f}%）"
                         + " " + itest_lane_note,
                         lane_notes + ev[:6],
                         "按闸的阈值补测试（阈值在脚本命令里）")
        return Check("coverage", "核心逻辑测试到位", "pass",
                     f"仓库自己的覆盖率闸门全过（平均行覆盖 {avg_line:.0f}% / "
                     f"分支 {avg_branch:.0f}%）" + " " + itest_lane_note,
                     lane_notes + ev[:6])
    low = [(k, v) for k, v in merged.items()
           if v["line"] < 80 or v["branch"] < 70]
    if low:
        return Check("coverage", "核心逻辑测试到位", "fail",
                     f"平均行覆盖 {avg_line:.0f}% / 分支 {avg_branch:.0f}%；"
                     f"{len(low)}/{len(merged)} 个文件低于门槛（行<80% 或 分支<70%）"
                     + " " + runner_note + " " + itest_lane_note,
                     ev, "补测试把低覆盖的文件拉过门槛")
    return Check("coverage", "核心逻辑测试到位", "pass",
                 f"平均行覆盖 {avg_line:.0f}% / 分支 {avg_branch:.0f}%"
                 + " " + runner_note + " " + itest_lane_note, ev)


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
                     "这个模块没有公开的 def/class")
    _, test_dirs = python_test_cmd(m, root)
    test_text = ""
    for d in test_dirs:
        for p in sorted((root / d).rglob("*.py")) if (root / d).is_dir() else []:
            if re.search(r"(^|/)(test_[^/]+|[^/]+_test)\.py$", str(p)):
                try:
                    test_text += p.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    pass
    if not test_text:
        return Check("uncovered-exports", "导出的逻辑都有测试", "skipped",
                     "没找到 Python 测试文件（test/ 或 tests/ 下没有 test_*.py）")
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
                     "这个模块没有导出的函数/类")
    test_text = ""
    for t in m.tests + m.itests:
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


def check_schema(m: Module, facts: dict) -> Check:
    """R3/R6 表设计 —— 查 PG catalog 的真实结构。"""
    if not facts:
        return Check("schema", "表设计符合主流", "skipped",
                     "没连上数据库（或模块不碰 PG）",
                     fix="设好 POSTGRES_URL 再跑")
    problems: list[str] = []
    ev: list[str] = []
    missing: list[str] = []
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
        # 主键：pg_indexes.indexdef 里没有 "PRIMARY KEY" 字样（那是 \d 的
        # 输出），主键索引名以 _pkey 结尾
        if not any("_pkey" in ix for ix in f["indexes"]):
            problems.append(f"{tbl}: 没有主键")
        # 租户列（多租户表）
        if not any(k in cols for k in ("tenant_id", "tenantId", "org_id")):
            problems.append(f"{tbl}: 没有租户列")
        # 唯一约束
        if not any("UNIQUE" in ix for ix in f["indexes"]):
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
    if missing:
        # 表没在连的库里找到 ⇒ 这批表的判定**没实测**，不算缺陷
        problems = [p for p in problems if not any(p.startswith(t + ":") for t in missing)]
        if not problems:
            return Check("schema", "表设计符合主流", "skipped",
                         f"{len(missing)} 张表在连的库里没找到（可能连错库）",
                         [f"{t}: 表在库里不存在" for t in missing],
                         "确认 POSTGRES_URL 指向的库里有这些表，再跑一次")
    if problems:
        return Check("schema", "表设计符合主流", "fail",
                     f"{len(problems)} 处问题", ev + problems[:8],
                     "补主键/唯一约束/租户列/RLS，或把表移出多租户范围")
    return Check("schema", "表设计符合主流", "pass",
                 f"{len(facts)} 张表都合规", ev)


def check_reconcile(m: Module) -> Check:
    """R4 数据流一致 —— 找仓库里的对账脚本。"""
    cands = [p for p in m.root.rglob("*.ts")
             if "reconcile" in p.name.lower()
             and not re.search(r"\.(test|spec)\.", p.name)
             and "node_modules" not in str(p)]
    if not cands:
        return Check("reconcile", "数据流一致（跨存储对账）", "fail",
                     "没有跨存储对账脚本 —— PG 与向量库的数据可能已经漂了",
                     fix="写一个：权威库 ↔ 派生库逐条对账 + 修复路径")
    return Check("reconcile", "数据流一致（跨存储对账）", "warn",
                 f"找到 {len(cands)} 个对账脚本，但没实跑（要真 PG + 派生库）",
                 [str(p.relative_to(m.root)) for p in cands[:5]],
                 "手动跑一次并确认输出为 0 差异")


def check_requirement(m: Module, plan_path: str | None) -> Check:
    """R7 需求覆盖 —— 计划里的每条不变式都要有真实用例。"""
    if not plan_path:
        return Check("requirement", "需求覆盖到位", "skipped",
                     "没给计划文件（--plan）")
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
    for ident, desc, pin in plan.invariants:
        pins = parse_pins(pin)
        if not pins:
            missing.append(f"{ident}: 没有钉")
            continue
        ok = False
        for fpath, case in pins:
            if not (m.root / fpath).exists():
                continue
            if case is None:
                ok = True
                break
            if case_matches(case, test_case_names(m.root, fpath)):
                ok = True
                break
        if not ok:
            missing.append(f"{ident}: {desc[:30]}")
    if missing:
        return Check("requirement", "需求覆盖到位", "fail",
                     f"{len(missing)}/{len(plan.invariants)} 条不变式没落到真实用例",
                     missing[:8], "写用例，或把不变式改到真实落点上")
    return Check("requirement", "需求覆盖到位", "pass",
                 f"{len(plan.invariants)} 条不变式都有真实用例",
                 [f"{i}: {d[:40]}" for i, d, _ in plan.invariants[:6]])


def check_extensibility(m: Module, idx: RepoIndex) -> Check:
    """R8 扩展性 —— 导出符号几乎没人用。"""
    exports = module_exports(m)
    thin = [f"{nm}（全仓 {idx.tokens[nm]} 处引用）"
            for nm in sorted(exports)
            if idx.tokens[nm] <= 2]
    if thin:
        return Check("extensibility", "具备扩展性（不过度抽象）", "warn",
                     f"{len(thin)}/{len(exports)} 个导出符号几乎没有调用点",
                     thin[:8],
                     "规则之三：抽象要等第三个用例；只有一个调用点先内联")
    return Check("extensibility", "具备扩展性（不过度抽象）", "pass",
                 f"{len(exports)} 个导出符号都有多个调用点")


def check_no_wheels(m: Module, idx: RepoIndex) -> Check:
    """R9 没有重复造轮子 —— 全仓同名导出（排除通用名与测试文件）。"""
    own = module_exports(m)
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
                 "模块的导出符号在全仓都是独一份")


def check_open_source(m: Module) -> Check:
    """R10 用的是主流开源库。"""
    if not m.deps:
        return Check("open-source", "使用主流开源库", "warn",
                     "模块没有第三方依赖 —— 确认这是有意的（简单优先）",
                     fix="如果需求领域有成熟库，别自己写")
    ev = [d for d in m.deps[:10]]
    return Check("open-source", "使用主流开源库", "pass",
                 f"{len(m.deps)} 个第三方依赖", ev)


# ---------------------------------------------------------------- 主流程


def run_readiness(args) -> int:
    root = Path(args.repo).resolve()
    m = discover_module(root, args.module)
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
    lane_notes: list[str] = []
    if gate_scripts:
        cov, failed, lane_notes = run_gates(m, gate_scripts)
        raw = ""
    elif m.lang == "py":
        cov, failed, setup_errors, raw = run_python_coverage(m, root)
    else:
        cov, failed, raw = run_coverage(m, include_itest=args.include_itest)
    # 真后端车道：驱动仓库自己的 itest 脚本（自带环境与防挂参数）
    itest_cov: dict[str, dict] = {}
    itest_failed: list[str] = []
    itest_notes: list[str] = []
    itest_env: list[str] = []
    itest_scripts = find_itest_scripts(m, root)
    if itest_scripts and (args.include_itest or m.itests):
        itest_cov, itest_failed, itest_notes, itest_env = run_itest_lane(
            m, itest_scripts)
        failed += itest_failed
        # 环境没就绪的报错不算产品缺陷 ⇒ 不进"测试全绿"的阻塞项
        setup_errors += itest_env
    _prefix, script_name = project_test_cmd(m)
    runner_note = f"（用项目自己的 {script_name} 命令）"
    if args.verbose and raw:
        print(raw[-3000:], file=sys.stderr)

    checks = [
        check_tests_green(failed, setup_errors),
        check_test_hygiene(m),
        check_coverage(m, cov, "" if cov else raw, runner_note,
                       lane_notes=lane_notes, itest_cov=itest_cov,
                       itest_notes=itest_notes),
        check_uncovered_exports(m, idx),
        check_schema(m, query_schema(m, args.database_url or
                                     os.environ.get("POSTGRES_URL", ""))),
        check_data_flow(m, root, getattr(args, "test_tenant", [])),
        check_data_accuracy(m, root),
        check_requirement(m, args.plan),
        check_extensibility(m, idx),
        check_no_wheels(m, idx),
        check_open_source(m),
    ]

    if args.json:
        print(json.dumps({
            "module": m.path,
            "tables": m.tables,
            "checks": [c.__dict__ for c in checks],
        }, ensure_ascii=False, indent=2))
        return 0 if all(c.status != "fail" for c in checks) else 1

    print(f"\n交付就绪度分析 — {m.path}")
    print(f"仓库：{root}")
    print(f"源码 {len(m.sources)} 文件（+{len(m.support)} 个测试支撑）"
          f" / 单测 {len(m.tests)} 个"
          f" / itest {len(m.itests)} 个"
          + (f" / 表 {', '.join(m.tables)}" if m.tables else ""))
    print()
    for c in checks:
        print(f"  {c.mark} {c.title} — {c.detail}")
        for e in c.evidence[:6]:
            print(f"      · {e}")
        if c.status in ("fail", "warn") and c.fix:
            print(f"      → {c.fix}")
    fails = [c for c in checks if c.status == "fail"]
    warns = [c for c in checks if c.status == "warn"]
    print()
    if fails:
        print(f"结论：不可交付 —— {len(fails)} 项不通过"
              + (f"，{len(warns)} 项待确认" if warns else ""))
        for c in fails:
            print(f"  ✗ {c.title}：{c.detail}")
        return 1
    print(f"结论：达到交付条件"
          + (f"（{len(warns)} 项待人工确认）" if warns else ""))
    return 0


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
    args = ap.parse_args(argv)
    return run_readiness(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
