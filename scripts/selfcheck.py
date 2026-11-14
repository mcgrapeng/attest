#!/usr/bin/env python3
"""反向验证（self-check）：证明这套判据抓得住东西。

    python3 scripts/selfcheck.py <模块路径> --repo <项目根> \
        [--fault drop-test|inject-sleep|all] [--json]

做法：把目标仓库**复制**到临时目录（绝不改用户仓库），往副本里注入一个
**已知缺陷**，再跑一遍判据，看它从绿翻红。

    drop-test    清空一个引用了模块导出符号的测试文件
                 ⇒「导出的逻辑都有测试」应当从 pass 翻成 fail
    inject-sleep 在一个测试文件里插入固定等待（flaky 的来源）
                 ⇒「测试卫生」应当从 pass 翻成 warn

黑盒：判据结果全部来自 `readiness.py <模块> --repo <副本> --json` 的
`checks` 数组 —— 本文件**不 import** readiness 的内部函数（它正在被改，
import 会造成耦合与竞态）。

结论语义：
    至少一种故障翻红        ⇒「反向验证通过」（退出码 0）
    故障都没翻红            ⇒「反向验证未通过」（退出码 1）
    找不到可注入的故障目标  ⇒「没有可注入的故障目标」（退出码 1）——
                             这是"没法证明"，不是"证明没问题"。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

# ---------------------------------------------------------------- 常量

#: 判据 id —— readiness.py --json 的 checks[].key（也兼容未来的 checks[].id）
ID_UNCOVERED_EXPORTS = "uncovered-exports"
ID_HYGIENE = "hygiene"

FAULT_KINDS = ("drop-test", "inject-sleep")

#: 判据里"翻红"算失败的状态（skipped 也算——它不再是绿的）
RED_STATUS = ("fail", "warn", "skipped")

#: 复制副本时跳过的目录 —— node_modules 太大，判据用不到
SKIP_DIRS = (
    "node_modules", ".git", "dist", "build", "coverage", ".next", "out",
    "target", ".venv", "venv", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", ".turbo", ".cache", ".idea", ".vscode",
)

#: 仓库根上要带过去的配置文件（判定要读 test 命令、覆盖率闸门、pytest 配置）
#: 注意**不含 .npmrc**：那文件常带 registry token，凭据不该被复制进临时副本。
ROOT_CONFIG_FILES = (
    "package.json", "package-lock.json", "pnpm-lock.yaml",
    "pnpm-workspace.yaml", "yarn.lock", "bun.lockb", "pyproject.toml",
    "setup.cfg", "setup.py", "tox.ini", "pytest.ini", "Makefile",
)
ROOT_CONFIG_GLOBS = ("tsconfig*.json", "vitest.config.*", "jest.config.*")

#: 仓库级测试目录（readiness 也会去这几个地方找测试）
REPO_TEST_DIRS = ("test", "tests", "spec")

# ---- 与 readiness.py 对齐的文件分类（只抄判定逻辑，不 import 它） ----

#: 模块目录里的单测（readiness 的 discover_module 用的同一套正则）
_MOD_TEST_RE = re.compile(r"\.(test|spec)\.(ts|tsx|js|jsx|mjs|cjs)$")
_MOD_PY_TEST_RE = re.compile(r"(^|/)(test_[^/]+|[^/]+_test)\.pyi?$")
#: 仓库级测试目录里的测试文件（readiness 的 _looks_like_test_file）
_REPO_TEST_RE = re.compile(r"\.(test|spec)\.(ts|tsx|js|jsx|mjs|cjs|py)$")
_REPO_PY_TEST_RE = re.compile(r"^(test_[^/]+|[^/]+_test)\.py$")

#: 测试支撑文件 —— 不是被测的核心逻辑（与 readiness 的 _SUPPORT_RE 对齐）
_SUPPORT_RE = re.compile(
    r"(itest-support|test-support|spec-support|fixtures?/|/helpers/|"
    r"\.e2e-support|setup/|/test-utils?/)", re.I)

#: TS/JS 的运行时导出（与 readiness 的 kinds=("function","class") 对齐：
#: 只认 function/class —— 常量本来就没必要被测试直接引用）
_JS_EXPORT_RE = re.compile(
    r"^\s*export\s+(?:default\s+)?(?:async\s+)?(function|class)\s+"
    r"([A-Za-z_$][\w$]*)", re.M)
#: Python 的模块级 def/class（与 readiness 的 _PY_EXPORT_RE 对齐）
_PY_EXPORT_RE = re.compile(
    r"^(?:async\s+)?(def|class)\s+([A-Za-z_][A-Za-z0-9_]*)"
    r"\s*(\([^)]*\))?", re.M)
_PY_GENERATED_RE = re.compile(r"_pb2(_grpc)?\.pyi?$")

#: JS 里插入固定等待的位置：第一个 test/it 用例的回调开头
_JS_CASE_RE = re.compile(
    r"(\b(?:test|it)\s*\(\s*(?:'[^']*'|\"[^\"]*\"|`[^`]*`)\s*,\s*)"
    r"(async\s+)?(\(\s*\)\s*=>\s*\{)")
#: JS 文件里已经有 sleep 绑定吗（有就别再声明一次）
_JS_HAS_SLEEP_RE = re.compile(
    r"(?:const|let|var|function)\s+sleep\b|import\s*\{[^}]*\bsleep\b")
#: Python 里插入固定等待的位置：第一个 test_ 函数（行尾有冒号才安全）
_PY_CASE_RE = re.compile(
    r"(?m)^([ \t]*)((?:async\s+)?def\s+test_\w+\s*\(\s*\)\s*"
    r"(?:->[^:\n]*)?:[ \t]*$)")
_PY_HAS_TIME_RE = re.compile(r"(?m)^(?:import\s+time\b|from\s+time\s+import)")

#: 固定等待的时长（毫秒 / 秒）。要够大才能被 readiness 的正则认出来
#: （`sleep\(\s*\d{3,}` 要三位数字以上）。
JS_WAIT_MS = 1000
PY_WAIT_S = 1.0


# ---------------------------------------------------------------- 小工具


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _iter_files(root: Path):
    if not root.is_dir():
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames
                       if d not in SKIP_DIRS and not d.startswith(".")]
        for fn in sorted(filenames):
            yield Path(dirpath) / fn


def _is_repo_test_file(p: Path) -> bool:
    """readiness 的 _looks_like_test_file（仓库级测试目录里的判定）。"""
    name = p.name
    return bool(_REPO_TEST_RE.search(name) or _REPO_PY_TEST_RE.search(name))


def _module_test_files(module_dir: Path) -> list[Path]:
    """模块目录里的测试文件（含 itest —— readiness 也把 itest 算进引用集）。"""
    out: list[Path] = []
    for p in _iter_files(module_dir):
        rel = str(p)
        if ".itest." in rel:
            out.append(p)
        elif _MOD_TEST_RE.search(rel) or _MOD_PY_TEST_RE.search(rel):
            out.append(p)
    return sorted(out)


def _repo_test_files(root: Path) -> list[Path]:
    """仓库级 test/ tests/ spec/ 里的测试文件（readiness 的 repo_tests）。"""
    out: list[Path] = []
    seen: set[Path] = set()
    for d in REPO_TEST_DIRS:
        dp = root / d
        if not dp.is_dir():
            continue
        for p in sorted(dp.rglob("*")):
            if not p.is_file() or p in seen:
                continue
            if not _is_repo_test_file(p):
                continue
            seen.add(p)
            out.append(p)
    return out


def _export_symbols(module_dir: Path) -> list[str]:
    """模块的运行时导出符号名（TS/JS 的 function/class，Python 的 def/class）。"""
    names: list[str] = []
    for p in _iter_files(module_dir):
        if p.suffix == ".pyi":
            continue
        text = _read(p)
        if p.suffix in (".py", ".pyi"):
            if _PY_GENERATED_RE.search(p.name):
                continue
            for mm in _PY_EXPORT_RE.finditer(text):
                kind, name = mm.group(1), mm.group(2)
                if name.startswith("_"):
                    continue
                if kind == "class" and name.endswith(("Error", "Exception")):
                    continue
                if name not in names:
                    names.append(name)
        else:
            for mm in _JS_EXPORT_RE.finditer(text):
                name = mm.group(2)
                if name not in names:
                    names.append(name)
    return names


def _mentions(text: str, name: str) -> bool:
    """readiness 的引用判定：整词匹配。"""
    return bool(re.search(r"\b" + re.escape(name) + r"\b", text))


def _module_lang(mod_dir: Path) -> str:
    """模块语言（与 readiness 的 discover_module 对齐）：py / ts / other。"""
    py = js = 0
    for p in _iter_files(mod_dir):
        if p.suffix == ".pyi":
            continue
        rel = str(p)
        if ".itest." in rel:
            continue
        if _MOD_TEST_RE.search(rel) or _MOD_PY_TEST_RE.search(rel):
            continue
        if _SUPPORT_RE.search(rel):
            continue
        if p.suffix == ".py":
            py += 1
        else:
            js += 1
    return "py" if py > js else ("ts" if js else "other")


def _python_test_dirs(root: Path) -> list[str]:
    """pytest 的测试目录（与 readiness 的 python_test_cmd 对齐）。"""
    for name in ("pyproject.toml", "pytest.ini", "setup.cfg", "tox.ini"):
        f = root / name
        if not f.exists():
            continue
        mm = re.search(r"testpaths\s*=\s*\[([^\]]*)\]", _read(f))
        if mm:
            return [x.strip().strip('"\'')
                    for x in mm.group(1).split(",")
                    if x.strip().strip('"\'')]
        break
    for cand in ("test", "tests"):
        if (root / cand).is_dir():
            return [cand]
    return []


def _reference_files(copy_root: Path, module_rel: str) -> list[Path]:
    """判据 `uncovered-exports` 实际扫描的测试文件集合。

    TS/JS 分支：模块目录里的测试 + 仓库级 test/ tests/ spec/ 下的全部测试。
    Python 分支：只扫 pyproject/pytest 声明的测试目录里的 test_*.py，
                 加上仓库级测试目录里的 .py。
    选错集合 ⇒ 挑出来的"唯一引用者"不是真的唯一 ⇒ 白注入一次。
    """
    mod_dir = copy_root / module_rel
    if _module_lang(mod_dir) == "py":
        out: list[Path] = []
        for d in _python_test_dirs(copy_root):
            dp = copy_root / d
            if not dp.is_dir():
                continue
            for p in sorted(dp.rglob("*.py")):
                if re.search(r"(^|/)(test_[^/]+|[^/]+_test)\.py$", str(p)):
                    out.append(p)
        out += [p for p in _repo_test_files(copy_root) if p.suffix == ".py"]
        return out
    return _module_test_files(mod_dir) + _repo_test_files(copy_root)


def _module_tokens(copy_root: Path, module_rel: str) -> list[str]:
    """模块在测试文件里可能出现的名字（与 readiness 的 _module_tokens 对齐）。"""
    toks: list[str] = []
    if module_rel and module_rel != ".":
        toks.append(module_rel)
        toks.append(Path(module_rel).name)
    mod_dir = copy_root / module_rel
    pkg: Path | None = None
    if mod_dir.name in ("src", "lib"):
        for child in sorted(mod_dir.iterdir()) if mod_dir.is_dir() else []:
            if child.is_dir() and (child / "__init__.py").exists():
                pkg = child
                break
    elif (mod_dir / "__init__.py").exists():
        pkg = mod_dir
    if pkg:
        toks.append(pkg.name)
    out: list[str] = []
    for t in toks:
        if t and t not in out:
            out.append(t)
    return out


def _mentions_module(p: Path, tokens: list[str]) -> bool:
    """readiness 的 _mentions_module_path：测试文件算不算这个模块的测试。"""
    for tag in tokens:
        if tag and tag in p.name:
            return True
    if not tokens:
        return False
    text = _read(p)
    for tag in tokens:
        pat = r"(?<![A-Za-z0-9_.-])" + re.escape(tag) + r"(?![A-Za-z0-9_-])"
        if re.search(pat, text):
            return True
    return False


# ---------------------------------------------------------------- 副本


def _copy_file(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def _link_deps(repo: Path, dst: Path) -> list[str]:
    """把依赖目录**软链**进副本（不复制体积）。

    不带依赖 ⇒ 副本里跑不动测试 ⇒ 反向验证只能证明静态判据抓得住东西。
    软链（不是复制）既能跑真测试，又不花时间与磁盘。
    """
    linked: list[str] = []
    for name in ("node_modules", ".venv", "venv", "target"):
        src = repo / name
        dst_link = dst / name
        if not src.is_dir() or dst_link.exists() or dst_link.is_symlink():
            continue
        try:
            dst_link.symlink_to(src, target_is_directory=True)
            linked.append(name)
        except OSError:
            pass
    return linked


def _copy_subset(repo: Path, dst: Path, module_rel: str) -> None:
    """只把判定需要的部分复制到 dst。

    复制：模块目录子树 + 仓库级测试目录 + 根上的配置文件 +
    模块祖先链上的 package.json（readiness 靠它找工作区与 test 命令）。
    不复制：node_modules（体积大）、.git、构建产物。
    """
    ignore = shutil.ignore_patterns(*SKIP_DIRS, "*.pyc", "*.pyo")
    dst.mkdir(parents=True, exist_ok=True)

    for name in ROOT_CONFIG_FILES:
        p = repo / name
        if p.is_file():
            _copy_file(p, dst / name)
    for pat in ROOT_CONFIG_GLOBS:
        for p in sorted(repo.glob(pat)):
            if p.is_file():
                _copy_file(p, dst / p.name)

    mod_abs = (repo / module_rel).resolve()
    chain: list[Path] = []
    cur = mod_abs
    while True:
        chain.append(cur)
        if cur == repo or repo not in cur.parents:
            break
        cur = cur.parent
    for d in chain:
        pj = d / "package.json"
        if pj.is_file():
            _copy_file(pj, dst / pj.relative_to(repo))

    if mod_abs.is_dir():
        shutil.copytree(mod_abs, dst / module_rel, ignore=ignore,
                        dirs_exist_ok=True)
    elif mod_abs.is_file():
        _copy_file(mod_abs, dst / module_rel)

    for d in REPO_TEST_DIRS:
        p = repo / d
        if p.is_dir():
            shutil.copytree(p, dst / d, ignore=ignore, dirs_exist_ok=True)


# ---------------------------------------------------------------- 跑判据


def _child_env() -> dict[str, str]:
    """子进程环境：**白名单**透传，不是整个 os.environ。

    与 readiness.py 的 child_env 同一套口径：只有 PATH/HOME/LANG 之类与运行有关的
    变量和工具自己要用的（POSTGRES_URL 等）才传下去，云厂商密钥、npm token 之类
    不进副本里的进程。POSTGRES_URL / DATABASE_URL 额外清空 —— 自检不该去连用户的库。
    """
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
    env["POSTGRES_URL"] = ""
    env["DATABASE_URL"] = ""
    return env


def _parse_checks(out: str) -> list[dict]:
    """从 readiness 的 stdout 里抠出 JSON。

    readiness 在 --json 之前可能先打印一行"将执行的仓库脚本：…"，
    所以不能假设 stdout 整体就是 JSON。
    """
    dec = json.JSONDecoder()
    for m in re.finditer(r"(?m)^\{", out):
        try:
            obj, _ = dec.raw_decode(out[m.start():])
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and isinstance(obj.get("checks"), list):
            return obj["checks"]
    return []


def _run_readiness(readiness_py: Path, copy_root: Path, module_rel: str,
                   timeout: int) -> tuple[list[dict], str]:
    """跑一遍判据（子进程，黑盒）。返回 (checks, 原始输出)。"""
    cmd = [sys.executable, str(readiness_py), module_rel,
           "--repo", str(copy_root), "--json"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding="utf-8", env=_child_env(),
                           timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return [], f"readiness 超时（{timeout}s）"
    out = (r.stdout or "") + (r.stderr or "")
    return _parse_checks(r.stdout or ""), out


def _check(checks: list[dict], cid: str) -> dict | None:
    """按 id 取判据。

    readiness 现在把 id 放在 checks[].key 上（未来可能改名成 .id），
    两个都认，免得集成方改了字段名这里就瞎。
    """
    for c in checks:
        if c.get("id") == cid or c.get("key") == cid:
            return {"id": cid,
                    "title": c.get("title", ""),
                    "status": c.get("status", ""),
                    "detail": c.get("detail", "")}
    return None


def _statuses(checks: list[dict]) -> dict[str, str]:
    return {(c.get("id") or c.get("key")): c.get("status", "")
            for c in checks}


# ---------------------------------------------------------------- 故障注入


def _pick_drop_target(copy_root: Path, module_rel: str,
                      symbols: list[str]) -> tuple[Path | None, str]:
    """挑一个"清空它就有导出变成零引用"的测试文件。

    判据（readiness 的 uncovered-exports）看的是**所有**被扫测试文件的文本拼接，
    所以只有当某个导出符号**只**出现在这一个文件里，清空它才会翻红。
    """
    files = _reference_files(copy_root, module_rel)
    if not files:
        return None, "副本里没有测试文件"
    if not symbols:
        return None, "模块没有导出符号"
    texts = {p: _read(p) for p in files}
    sole: dict[Path, int] = {}
    for sym in symbols:
        refs = [p for p, t in texts.items() if _mentions(t, sym)]
        if len(refs) == 1:
            sole[refs[0]] = sole.get(refs[0], 0) + 1
    if not sole:
        return None, ("没有任何导出符号只被某一个测试文件引用 —— "
                      "清空单个测试文件不会让「导出的逻辑都有测试」翻红")
    # 命中数最多的优先（同数按路径排序，保证确定性）
    best = min(sole.items(), key=lambda kv: (-kv[1], str(kv[0])))[0]
    return best, (f"{sole[best]} 个导出符号只被这个文件引用，"
                  f"清空它 ⇒ 「{ID_UNCOVERED_EXPORTS}」应当翻红")


def _stub_text(module_rel: str, target: Path) -> str:
    """把测试文件清空成占位注释。

    保留模块路径 —— readiness 靠"测试文件提到模块"来归属测试，
    占位里不提模块的话，测试文件会不再算这个模块的测试。
    """
    lines = [
        ("selfcheck 注入的已知缺陷：这个测试文件被清空 —— "
         "导出的逻辑从此没有任何测试引用"),
        f"module: {module_rel}",
    ]
    if target.suffix in (".py", ".pyi"):
        return "".join(f"# {ln}\n" for ln in lines)
    return "".join(f"// {ln}\n" for ln in lines)


def _has_insert_point(text: str, path: Path) -> bool:
    """这个文件里有没有能干净插固定等待的位置。"""
    if path.suffix in (".py", ".pyi"):
        return bool(_PY_CASE_RE.search(text))
    return bool(_JS_CASE_RE.search(text))


def _inject_sleep(text: str, path: Path) -> tuple[str | None, str]:
    """往一个测试文件里插入固定等待。返回 (新内容, 说明)。"""
    suffix = path.suffix
    if suffix in (".py", ".pyi"):
        m = _PY_CASE_RE.search(text)
        if m:
            indent = m.group(1)
            new = (text[:m.end()] + f"\n{indent}    time.sleep({PY_WAIT_S})"
                   + text[m.end():])
            if not _PY_HAS_TIME_RE.search(new):
                new = "import time\n" + new
            return new, (f"在 {m.group(2).split('(')[0].strip()} 里插入 "
                         f"time.sleep({PY_WAIT_S})")
        # 没有可插入的位置 ⇒ 追加一个专门的用例
        new = text.rstrip("\n") + (
            "\n\n\ndef test_selfcheck_injected_fixed_wait():\n"
            f"    time.sleep({PY_WAIT_S})\n")
        if not _PY_HAS_TIME_RE.search(new):
            new = "import time\n" + new
        return new, "追加一个带固定等待的用例"

    # JS / TS
    helper = ""
    if not _JS_HAS_SLEEP_RE.search(text):
        helper = ("const sleep = (ms) => new Promise((resolve) => "
                  "setTimeout(resolve, ms));\n")
    m = _JS_CASE_RE.search(text)
    if m:
        head, is_async, arrow = m.group(1), m.group(2), m.group(3)
        new_arrow = arrow if is_async else "async " + arrow
        ins = f"\n  await sleep({JS_WAIT_MS});"
        new = (text[:m.start()] + head + new_arrow + ins + text[m.end():])
        return helper + new, "在第一个用例的回调里插入 await sleep(...)"
    name = "it" if re.search(r"\bit\s*\(", text) else "test"
    new = text.rstrip("\n") + (
        f"\n\n// selfcheck 注入的已知缺陷：固定等待\n{helper}"
        f'{name}("selfcheck: fixed wait", async () => {{\n'
        f"  await sleep({JS_WAIT_MS});\n}});\n")
    return new, "追加一个带固定等待的用例"


# ---------------------------------------------------------------- 主流程


def _fault_result(kind: str, target: str | None, flipped: bool,
                  before: dict | None, after: dict | None,
                  changed: list[dict], note: str,
                  copy_runnable: bool | None = None) -> dict:
    return {
        "kind": kind,
        "target": target,
        "flipped": flipped,
        "expected_check": (ID_UNCOVERED_EXPORTS if kind == "drop-test"
                           else ID_HYGIENE),
        "before": before,
        "after": after,
        "changed": changed,
        "note": note,
        # 副本里能不能真跑起来测试（False ⇒ 判据退化成静态分析）
        "copy_runnable": copy_runnable,
    }


def _run_one_fault(kind: str, readiness_py: Path, repo: Path,
                   module_rel: str, timeout: int,
                   with_deps: bool = False) -> dict:
    """在**独立副本**上注入一种故障，比对前后判据。"""
    copy_runnable: bool | None = None
    run_note = ""

    def res(*args, **kw):
        kw.setdefault("copy_runnable", copy_runnable)
        if run_note:
            if len(args) >= 7 and isinstance(args[6], str):
                args = list(args)
                args[6] = f"{args[6]}；{run_note}" if args[6] else run_note
                args = tuple(args)
            else:
                kw["note"] = run_note
        return _fault_result(*args, **kw)

    with tempfile.TemporaryDirectory(prefix="req-plan-selfcheck.") as tmp:
        copy_root = Path(tmp) / "repo"
        _copy_subset(repo, copy_root, module_rel)
        if with_deps:
            # 软链依赖目录 ⇒ 副本里能真跑测试 ⇒ 反向验证能覆盖**实测类**判据
            # （默认不带：软链让副本里的测试连到真实依赖，代价与风险都更高）
            _link_deps(repo, copy_root)

        base_checks, base_out = _run_readiness(readiness_py, copy_root,
                                               module_rel, timeout)
        if not base_checks:
            return res(kind, None, False, None, None, [],
                       "副本里跑不动判据：readiness 没输出可解析的 JSON"
                       + (f"（{base_out.strip()[:200]}）"
                          if base_out.strip() else ""))

        mod_dir = copy_root / module_rel
        symbols = _export_symbols(mod_dir)
        target: Path | None = None
        note = ""
        # 副本里能不能真跑起来测试（node_modules 没复制 ⇒ 多数仓库跑不动）
        tg = _check(base_checks, "tests-green")
        copy_runnable = bool(tg and tg.get("status") == "pass")
        if not copy_runnable:
            run_note = (f"副本里跑不动测试（tests-green = "
                        f"{(tg or {}).get('status', '?')}："
                        f"{(tg or {}).get('detail', '')}）—— "
                        f"本次翻红的是静态判据，不依赖真跑测试")

        if kind == "drop-test":
            target, note = _pick_drop_target(copy_root, module_rel, symbols)
            if target is None:
                return res(kind, None, False, None, None, [], note)
            rel = str(target.relative_to(copy_root))
            # 清空（而不是删掉）这个测试文件：文件还在，导出的逻辑却再也没有
            # 任何测试引用 ⇒ 「导出的逻辑都有测试」应当**判 fail**。
            # 删掉文件会让判据变成 skipped（"没找到测试文件，判不了"）——
            # 那是"判不了"，不是"判出红"，演示力弱得多。
            stub = _stub_text(module_rel, target)
            try:
                target.write_text(stub, encoding="utf-8")
            except OSError as e:
                return res(kind, rel, False, None, None, [],
                           f"写不进去目标文件：{e}")
        elif kind == "inject-sleep":
            # hygiene 只扫 m.tests + m.itests —— 模块目录里的测试一定在内，
            # 仓库级测试只有"提到这个模块"的才算（与 readiness 归属同规则）。
            cands = _module_test_files(mod_dir)
            if not cands:
                tokens = _module_tokens(copy_root, module_rel)
                cands = [p for p in _repo_test_files(copy_root)
                         if _mentions_module(p, tokens)]
            if not cands:
                return res(kind, None, False, None, None, [],
                           "副本里没有这个模块的测试文件"
                           "（模块目录里没有，仓库级测试目录里也没有"
                           "提到这个模块的）")
            # 优先选"能干净插进去"的：有用例开头 / 有 def test_ 的文件
            target = next((p for p in cands
                           if _has_insert_point(_read(p), p)), cands[0])
            rel = str(target.relative_to(copy_root))
            text = _read(target)
            new_text, note = _inject_sleep(text, target)
            if new_text is None:
                return res(kind, rel, False, None, None, [], note)
            target.write_text(new_text, encoding="utf-8")
        else:
            return res(kind, None, False, None, None, [],
                       f"未知的故障类型：{kind}")

        after_checks, _after_out = _run_readiness(readiness_py, copy_root,
                                                   module_rel, timeout)
        if not after_checks:
            return res(kind, rel, False, None, None, [],
                       "注入后副本里跑不动判据：readiness 没输出"
                       "可解析的 JSON")

        cid = (ID_UNCOVERED_EXPORTS if kind == "drop-test" else ID_HYGIENE)
        before = _check(base_checks, cid)
        after = _check(after_checks, cid)
        b_st = (before or {}).get("status", "")
        a_st = (after or {}).get("status", "")
        flipped = bool(before) and bool(after) and b_st == "pass" \
            and a_st in RED_STATUS

        b_map = _statuses(base_checks)
        a_map = _statuses(after_checks)
        changed = [{"id": k, "before": b_map.get(k, ""),
                    "after": a_map.get(k, "")}
                   for k in sorted(set(b_map) | set(a_map))
                   if b_map.get(k, "") != a_map.get(k, "")]

        if before is None:
            note += f"；副本里没有判据 {cid}（readiness 的判据集合变了？）"
        elif b_st != "pass":
            note += (f"；注入前 {cid} 就是 {b_st}（不是绿的），"
                     f"无法演示从绿翻红")
        elif not flipped:
            note += f"；注入后 {cid} 仍是 {a_st}（没翻红）"

        return res(kind, rel, flipped, before, after, changed, note)


def _no_target(module: str, repo: str, reason: str) -> dict:
    """连跑都不用跑就知道没法注入（仓库/模块/readiness 都不对）。"""
    return {"module": module, "repo": repo, "faults": [],
            "verdict": "没有可注入的故障目标", "exit_code": 1,
            "reason": reason, "notes": [reason]}


def run_selfcheck(module: str, repo: str | os.PathLike,
                  faults: tuple[str, ...] | list[str] = FAULT_KINDS,
                  skill_root: str | os.PathLike | None = None,
                  timeout: int = 600,
                  with_deps: bool = False) -> dict:
    """反向验证：注入已知缺陷，看判据会不会翻红。

    返回一个 dict（同时也是 --json 的输出结构）：

        {
          "module": "src",
          "repo": "/abs/repo",
          "faults": [
            {"kind": "drop-test", "target": "src/calc.test.ts",
             "flipped": true,
             "expected_check": "uncovered-exports",
             "before": {"id": ..., "title": ..., "status": "pass",
                        "detail": ...},
             "after":  {"id": ..., "title": ..., "status": "fail",
                        "detail": ...},
             "changed": [{"id": ..., "before": ..., "after": ...}],
             "note": "..."}
          ],
          "verdict": "反向验证通过",
          "exit_code": 0,
          "reason": "",
          "notes": [...]
        }

    绝不修改 repo 里的任何文件 —— 所有注入都发生在临时副本里。

    `verdict` 只有三个取值（稳定枚举，集成方可以直接判）：
        "反向验证通过" / "反向验证未通过" / "没有可注入的故障目标"
    """
    repo_path = Path(repo).resolve()
    if not repo_path.is_dir():
        return _no_target(module, str(repo_path), f"仓库不存在：{repo_path}")

    root = Path(skill_root).resolve() if skill_root \
        else Path(__file__).resolve().parent.parent
    readiness_py = root / "scripts" / "readiness.py"
    if not readiness_py.is_file():
        return _no_target(module, str(repo_path),
                          f"找不到 readiness.py：{readiness_py}")

    # 模块路径：相对仓库根（readiness 的用法）
    mod_abs = (repo_path / module).resolve()
    if not mod_abs.exists():
        return _no_target(module, str(repo_path),
                          f"模块路径不存在：{mod_abs}")
    try:
        module_rel = str(mod_abs.relative_to(repo_path))
    except ValueError:
        return _no_target(module, str(repo_path),
                          f"模块不在仓库内：{mod_abs}")
    if module_rel == ".":
        module_rel = "."

    results: list[dict] = []
    for kind in faults:
        results.append(_run_one_fault(kind, readiness_py, repo_path,
                                       module_rel, timeout, with_deps))

    flipped = [r for r in results if r["flipped"]]
    attempted = [r for r in results if r["target"]]
    runnable = [r["copy_runnable"] for r in results
                if r["copy_runnable"] is not None]
    copy_runnable = all(runnable) if runnable else None
    notes: list[str] = []
    reason = ""
    if not copy_runnable:
        notes.append("副本里跑不动测试（node_modules 没复制 / runner 没装）—— "
                     "本次翻红的是静态判据（导出引用 / 测试卫生），"
                     "不依赖真跑测试")
    if flipped:
        verdict = "反向验证通过"
        code = 0
        reason = "、".join(f"{r['kind']} 让 {r['expected_check']} 翻红"
                           for r in flipped)
    elif not attempted:
        verdict = "没有可注入的故障目标"
        code = 1
        reason = "、".join(r["note"] for r in results if r["note"])
        notes.append("两种故障都找不到可注入的目标 —— "
                     "这是「没法证明」，不是「证明没问题」")
    else:
        verdict = "反向验证未通过"
        code = 1
        reason = "、".join(r["note"] for r in results if r["note"])
        notes.append("故障注入了，但判据没有翻红 —— "
                     "这套判据抓不住注入的缺陷")

    return {"module": module_rel,
            "repo": str(repo_path),
            "faults": results,
            "verdict": verdict,
            "exit_code": code,
            "reason": reason,
            "copy_runnable": copy_runnable,
            "notes": notes}


# ---------------------------------------------------------------- CLI


def _print_human(res: dict) -> None:
    print(f"反向验证 —— {res['module']}（仓库 {res['repo']}）")
    print("判据结果来自 readiness.py --json（黑盒，不 import 内部函数）")
    for f in res["faults"]:
        print(f"\n[{f['kind']}] 目标：{f['target'] or '（没有）'}")
        if f["before"] and f["after"]:
            print(f"  {f['expected_check']}："
                  f"{f['before']['status']} → {f['after']['status']}"
                  f"  {'✔ 翻红' if f['flipped'] else '✘ 没翻红'}")
            print(f"    前：{f['before']['detail']}")
            print(f"    后：{f['after']['detail']}")
        if f["changed"]:
            print("  其他翻动的判据：")
            for c in f["changed"]:
                print(f"    {c['id']}：{c['before']} → {c['after']}")
        if f["note"]:
            print(f"  说明：{f['note']}")
    if res.get("copy_runnable") is False:
        print("\n注意：副本里跑不动测试（node_modules 没复制 / runner 没装）—— "
              "翻红的是静态判据，不依赖真跑测试")
    for n in res.get("notes", []):
        print(f"\n{n}")
    if res.get("reason"):
        print(f"依据：{res['reason']}")
    print(f"\n结论：{res['verdict']}")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(
        description="反向验证：注入已知缺陷，看判据会不会翻红")
    ap.add_argument("module", help="模块路径（相对仓库根）")
    ap.add_argument("--repo", default=".", help="仓库根")
    ap.add_argument("--fault", default="all",
                    choices=("drop-test", "inject-sleep", "all"),
                    help="注入哪种故障（默认 all）")
    ap.add_argument("--skill-root", default=None,
                    help="skill 根目录（默认取本文件的上级）")
    ap.add_argument("--timeout", type=int, default=600,
                    help="每次跑判据的超时秒数（默认 600）")
    ap.add_argument("--with-deps", action="store_true",
                    help="把 node_modules / .venv 软链进副本 —— 副本里就能真跑测试，"
                         "反向验证因此能覆盖**实测类**判据（默认不带）")
    ap.add_argument("--json", action="store_true", help="机器可读输出")
    args = ap.parse_args(argv)

    kinds = FAULT_KINDS if args.fault == "all" else (args.fault,)
    res = run_selfcheck(args.module, args.repo, faults=kinds,
                        skill_root=args.skill_root, timeout=args.timeout,
                        with_deps=args.with_deps)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    else:
        _print_human(res)
    return res["exit_code"]


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
