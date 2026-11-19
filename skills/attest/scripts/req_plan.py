#!/usr/bin/env python3
"""需求级测试计划：生成与校验。

用法:
    req-plan new <feature> --repo <目录> [--module <子路径>...]   # 生成计划骨架
    req-plan check <计划文件> --repo <目录>                        # 校验计划 vs 代码实况

设计约束:
  - 默认只读：只读代码，不动仓库里的任何东西。
    **例外是 `scaffold`** —— 它会按计划往磁盘写文件（测试骨架），
    写入范围被限制在 `--out-dir` 与仓库根之内（越界的钉一律拒绝，不写盘）。
  - 判据是"仓库里真实存在什么"，不是"计划里写了什么"——
    计划自称覆盖了但文件不存在 ⇒ 报红。
  - 车道从项目自己的 package.json scripts 读，不硬编码。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from decision_points import (  # noqa: E402
    DpAnalysis, HIGH_RISK_KINDS, analyze_files, dp_key,
)

# ---------------------------------------------------------------- 常量

# 判定"这是条测试车道"的脚本名关键词。
# 只看脚本名本身（工作区前缀已剥掉）—— "contracts" 这种包名里含
# contract 不算车道，frontend/packages/contracts#build 不是测试车道。
_LANE_KEYWORDS = (
    "e2e", "itest", "integration", "coverage", "contract", "property",
    "mutation", "fuzz", "benchmark", "smoke", "visual", "a11y",
    "accessibility", "unit", "spec", "jest", "mocha", "pytest", "vitest",
)


def _is_lane(script_name: str) -> bool:
    """脚本名像不像一条测试车道。"""
    name = script_name.split("#")[-1].lower()
    if name == "test" or name.startswith("test:") or name.startswith("test-"):
        return True
    if name == "eval" or name.startswith("eval:") or name.startswith("eval-"):
        return True
    return any(k in name for k in _LANE_KEYWORDS)
# 不变式表格里"钉在哪"那一列的形状：file.ts:123 或 file.ts（多个逗号分隔）
_PIN_RE = re.compile(
    r"[\w./@-]+\.(?:tsx|jsx|mjs|cjs|ts|js|py|rs|go|java|rb|sh)"
    r"(?::[\d,\-]+)?\b"
)

# 表格行
_ROW_RE = re.compile(r"^\s*\|(.+)\|\s*$")

# 表头单元格里常见的标签词。只有"下一行是分隔行 + 本行全是这些词"才认表头 ——
# 否则单列的 `| 记忆行 |` 会被当表头吞掉（假绿）。
# 一致性表的形状特征：含"权威源"或"镜像/派生"这类列
_CONSISTENCY_HINT = ("权威源", "权威", "镜像", "派生", "一致性怎么验", "校验方式")

# 表头 → 这张表是哪一节。表头自己说了算，标题只在表头不认识时兜底。
_TABLE_HINTS = {
    "consistency": _CONSISTENCY_HINT,
    "matrix": ("测试落点", "落点", "测试文件", "流程", "场景", "车道", "覆盖"),
    "inv": ("不变式", "钉在哪", "钉", "行为契约", "契约", "描述"),
    # newcap 要排在 reuse 前面 —— 它的表头也含"能力"和"复用"，
    # 排后面会被 reuse 抢走，数据行就落错节了。
    "newcap": ("能力", "调用点", "为什么不复用", "新建"),
    "reuse": ("能力", "位置", "用法", "已有", "复用"),
    "lane": ("车道", "命令"),
}


def row_kinds_preview(sections: list[str]) -> list[str]:
    """占位行属于哪一节（用来记"这节还没填"）。"""
    return list(sections)


def _kind_from_header(cells: list[str]) -> str | None:
    """按表头猜这张表是哪一节。≥2 个特征词才认 —— 少一个就别乱认。"""
    for kind, hints in _TABLE_HINTS.items():
        hits = sum(1 for c in cells if any(h in c for h in hints))
        if hits >= 2:
            return kind
    return None

# 核心逻辑必须逐个想到的维度。"测出核心逻辑的细节"落到这张表上：
# 每个维度要么有测试落点，要么写「不适用」+理由 —— 不能默默跳过。
#
# 但**哪些维度适用**由代码特征决定（见 derive_dimensions）—— 固定清单会
# 逼着人给无关的维度填「不适用」，噪声盖住真缺口。
_DETAIL_DIMENSIONS = (
    "边界", "错误路径", "并发", "重试", "幂等", "降级/超时",
    "跨租户隔离", "权限", "资源上限", "数据对账",
)

# 维度 → 触发它的代码特征（触发即适用，附证据）
_DIMENSION_TRIGGERS = (
    ("边界", (
        r"\.length\b", r"\bclamp\b", r"\bMAX_", r"\bmax[A-Z]", r"\bmin[A-Z]",
        r"[<>]=?\s*\d", r"Math\.(?:min|max)", r"\bslice\(",
    ), "取值上下界"),
    ("错误路径", (
        r"\bthrow\b", r"\breject\b", r"\.catch\s*\(", r"\braise\b",
        r"ApiError|HttpError|badRequest|notFound|Conflict",
    ), "错误分支"),
    ("并发", (
        r"Promise\.all", r"\bglobalThis\b", r"\bsetTimeout\b", r"\bsetInterval\b",
        r"\bawait\b[^;]*\bfor\b", r"\bmutex\b|\block\b",
    ), "异步聚合/共享状态"),
    ("重试", (
        r"\bretry\b", r"\bbackoff\b", r"\battempts?\b", r"maxRetries",
        r"\bwithRetry\b",
    ), "重试逻辑"),
    ("幂等", (
        r"onConflictDoNothing", r"\bidempoten", r"\bdedup", r"\bunique\b",
        r"\bfingerprint\b", r"onConflict",
    ), "幂等/去重/唯一约束"),
    ("降级/超时", (
        r"\bcatch\b", r"\btimeout\b", r"AbortSignal", r"\bdegrade\b",
        r"\bfallback\b", r"\bexpiresIn\b",
    ), "降级/超时"),
    ("跨租户隔离", (
        r"\btenant", r"\borgId\b", r"\bworkspaceId\b", r"withTenant",
        r"\bRLS\b|rowLevelSecurity|tenant_id",
    ), "租户维度"),
    ("权限", (
        r"\bauthoriz", r"\bpermission", r"\bgrant", r"\brole\b",
        r"accessLevel", r"\bresource_grant\b",
    ), "权限检查"),
    ("资源上限", (
        r"\bquota\b", r"\brateLimit\b", r"\bmaxActive\b", r"\bbudget\b",
        r"tokenBudget", r"\bmaxRows\b",
    ), "配额/限额"),
)

# 推导不出任何维度时的兜底（模块扫不到 / 纯空目录）
_CORE_DIMENSIONS = ("边界", "错误路径")


def _module_inside(root: Path, mod: str) -> bool:
    """模块路径是不是落在仓库根内。

    绝对路径、`..`、软链逃逸都算越界 —— 目标模块是计划里写的字符串，
    不能让它把仓库外的文件读进来。
    """
    if not mod:
        return False
    try:
        root_real = root.resolve()
        (root_real / mod).resolve().relative_to(root_real)
    except (ValueError, OSError):
        return False
    return True


def derive_dimensions(root: Path, modules: list[str]) -> dict[str, list[str]]:
    """从目标模块的代码特征推导适用的维度：{维度: [证据]}。

    只扫生产代码（测试文件里的特征不算 —— 那是测试自己造的）。
    """
    code_exts = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".py", ".rs", ".go"}
    found: dict[str, list[str]] = {}
    for mod in modules:
        if not _module_inside(root, mod):
            continue
        base = root / mod
        if not base.exists():
            continue
        files = [base] if base.is_file() else _walk(base, code_exts)
        for p in files:
            if re.search(r"\.(test|spec)\.|_test\.go$|\.itest\.", p.name):
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for dim, pats, why in _DIMENSION_TRIGGERS:
                for pat in pats:
                    if re.search(pat, text):
                        line = text.count("\n", 0, re.search(pat, text).start()) + 1
                        ev = f"{repo_rel(root, p)}:{line} — {why}"
                        found.setdefault(dim, [])
                        if ev not in found[dim]:
                            found[dim].append(ev)
                        break
    return found

_HEADER_WORDS = {
    "#", "编号", "id", "序号", "不变式", "契约", "流程", "场景", "流程/场景",
    "关键流程", "数据", "能力", "名称", "项目", "类型", "描述", "说明", "备注",
    "测试落点", "落点", "钉在哪", "钉", "车道", "命令", "权威源", "权威",
    "镜像/派生", "派生", "一致性怎么验", "怎么验", "用法", "复用", "位置",
    "lane", "test", "flow", "scenario", "data", "invariant",
    # 常见表头词（缺一个就可能把表头当数据行）
    "调用点", "测试文件", "落点", "怎么", "为什么", "是否", "结果", "预期",
    "实际", "输入", "输出", "数量", "维度", "优先级", "顺序", "状态",
    "分类", "标识", "键", "值", "来源", "派生", "唯一", "校验方式", "一致性",
    "镜像", "权威", "落点或不适用理由", "不适用理由",
    # 判定点粒度表（v2）的表头词 —— 少一个就可能把表头当数据行
    "dp id", "判定点", "豁免理由", "归属", "归属的不变式",
}

# 解决一致性时"引入中间件"的做法 —— 用户明确不打算引入这些。
_MIDDLEWARE_TERMS = (
    "kafka", "消息队列", "amqp", "rabbitmq", "pulsar", "sqs", "sns",
    "nats", "temporal", "outbox", "发件箱", "saga", "tcc", "两阶段提交",
    "2pc", "分布式事务", "补偿事务",
)

# 不用中间件的替代形状 = 「谁是权威」+「怎么对账修复」两件事都说到。
# 分两组、要求**同时**命中 —— 单张平铺 OR 表太窄，合规计划会被误判。
# 否定词：出现在组件名前面 ⇒ 是在说"我们避开它"，不是"我们要用它"
_NEGATION = ("不引入", "不用", "不采用", "避免", "避开", "拒绝", "rather than",
             "without", "no ")

_AUTHORITY_TERMS = (
    "权威", "来源", "唯一", "单一", "主库", "源库", "真相",
)
_RECONCILE_TERMS = (
    "对账", "reconcile", "修复", "repair", "比对", "校准", "补齐", "回填",
    "镜像", "mirror", "派生", "副本",
)

# 存储探测：认出目标模块碰了哪些存储（PG / 向量 / 文件 / KV…）。
# 证据带文件:行，不是猜的 —— 报告里能核对。
_STORE_PROBES = (
    ("pg", re.compile(r"pgTable\(\s*['\"](\w+)['\"]"), "PG 表 {}"),
    ("pg", re.compile(r"\bCREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([\w.]+)",
                      re.I), "PG 表 {}"),
    ("vector", re.compile(r"createCollection|MilvusClient|WeaviateClient|"
                          r"PineconeClient|QdrantClient|ChromaClient"),
     "向量库客户端"),
    ("vector", re.compile(r"COLLECTION_PREFIX|COLLECTION_NAME|"
                          r"collectionName|getOrCreateCollection"),
     "向量集合构造"),
    ("file", re.compile(r"\b(putObject|deleteObject|objectExists|getObjectStream|"
                        r"copyObject|headObject|listObjects)\(|"
                        r"storageKey|storage_key|objectKey|object_key"),
     "对象存储"),
    ("kv", re.compile(r"\b(redis|ioredis)\b|\bcreateClient\("), "KV 存储"),
)


def scan_stores(root: Path, modules: list[str]) -> dict[str, list[str]]:
    """目标模块碰到的存储：{存储种类: [证据]}。

    只扫生产代码（跳过测试文件）—— 测试里出现的存储名不代表模块在用它。
    """
    code_exts = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".py", ".rs", ".go"}
    found: dict[str, list[str]] = {}
    for mod in modules:
        if not _module_inside(root, mod):
            continue
        base = root / mod
        if not base.is_dir():
            continue
        for p in _walk(base, code_exts):
            if re.search(r"\.(test|spec|itest)\.|_test\.go$", p.name):
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for kind, pattern, label in _STORE_PROBES:
                for m in pattern.finditer(text):
                    line = text.count("\n", 0, m.start()) + 1
                    name = m.group(1) if m.groups() else ""
                    desc = label.format(name) if name else label
                    entry = f"{repo_rel(root, p)}:{line} — {desc}"
                    found.setdefault(kind, [])
                    if entry not in found[kind]:
                        found[kind].append(entry)
    return found


# 不变式编号（I1 / INV-2 / inv-2 / #3 / 3 之类）
_INV_ID_RE = re.compile(r"^\s*(?:#|\*\*)?\s*([A-Za-z]{1,4}[-_]?\d+|\d+)\b")

_SELF = str(Path(__file__).resolve())

_SKIP_DIRS = {
    "node_modules", ".venv", "venv", "target", "dist", "build", ".git",
    "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache", ".next",
    ".nuxt", "out", "coverage", "vendor", "site-packages", ".gradle", "Pods",
}


# ---------------------------------------------------------------- 数据结构


@dataclass
class Finding:
    level: str          # "error" / "warn" / "info"
    item: str
    detail: str
    fix: str = ""

    def render(self) -> str:
        mark = {"error": "✗", "warn": "·", "info": "i"}[self.level]
        out = f"  {mark} {self.item}: {self.detail}"
        if self.fix:
            out += f"\n      {self.fix}"
        return out


@dataclass
class Repo:
    root: Path
    packages: list[Path] = field(default_factory=list)   # 全部 package.json
    scripts: dict[str, str] = field(default_factory=dict)  # 脚本名 → 完整命令

    def rel(self, p: Path) -> str:
        try:
            return str(p.relative_to(self.root))
        except ValueError:
            return str(p)


# ---------------------------------------------------------------- 仓库扫描


def _walk(root: Path, exts: set[str], max_files: int = 40000) -> list[Path]:
    import os
    out: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d for d in dirnames
            if d not in _SKIP_DIRS
            and not d.startswith(".")
            and not d.startswith("target")
            and not d.startswith("node_modules")
        ]
        for fn in filenames:
            if Path(fn).suffix in exts:
                out.append(Path(dirpath) / fn)
                if len(out) >= max_files:
                    return out
    return out


def _python_lanes(root: Path) -> dict[str, str]:
    """Python 仓自己声明的车道：Makefile 目标 / tox.ini 环境。

    判定侧（readiness.py）已经认这两条路，计划侧也得认 —— 否则同一个仓库，
    判定说"有闸门"，计划却说"项目里可能还没有测试"。
    """
    out: dict[str, str] = {}
    for mk in sorted(root.rglob("Makefile")):
        if any(p in ("node_modules", ".git", ".venv", "venv") for p in mk.parts):
            continue
        try:
            text = mk.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for mm in re.finditer(r"^([A-Za-z0-9_.-]+)\s*:(?!=)", text, re.M):
            target = mm.group(1)
            if not _is_lane(target):
                continue
            key = f"{mk.parent.relative_to(root)}#make:{target}" \
                if mk.parent != root else f"make:{target}"
            out[key] = f"make {target}"
    for tox in sorted(root.rglob("tox.ini")):
        if any(p in ("node_modules", ".git", ".venv", "venv") for p in tox.parts):
            continue
        try:
            text = tox.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for mm in re.finditer(r"^\[testenv:([A-Za-z0-9_.-]+)\]", text, re.M):
            env = mm.group(1)
            if not _is_lane(env):
                continue
            key = f"{tox.parent.relative_to(root)}#tox:{env}" \
                if tox.parent != root else f"tox:{env}"
            out[key] = f"tox -e {env}"
    return out


def scan_repo(root: Path) -> Repo:
    """收集项目自己的 manifest 与测试车道。

    车道真相源 = 各 package.json 的 scripts 里名字像测试的那些。
    这样脚本不需要知道项目用什么测试框架。
    """
    repo = Repo(root=root)
    for pj in _walk(root, {".json"}):
        if pj.name != "package.json":
            continue
        repo.packages.append(pj)
        try:
            pkg = json.loads(pj.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(pkg, dict):
            continue
        scripts = pkg.get("scripts")
        if not isinstance(scripts, dict):
            continue
        for name, cmd in scripts.items():
            if not isinstance(cmd, str):
                continue
            key = f"{repo.rel(pj.parent)}#{name}" if repo.rel(pj.parent) != "." else name
            repo.scripts[key] = cmd
    # Python 仓没有 package.json ⇒ 车道在 Makefile / tox.ini 里（判定侧也认这两条路）
    repo.scripts.update(_python_lanes(root))
    return repo


def lane_scripts(repo: Repo) -> dict[str, str]:
    """只留看起来是测试车道的脚本。"""
    return {k: v for k, v in repo.scripts.items() if _is_lane(k)}


def existing_test_files(root: Path) -> list[str]:
    """仓库里已有的测试文件（相对路径）。"""
    out = []
    for p in _walk(root, {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
                          ".py", ".rs", ".go"}):
        name = p.name
        if (name.endswith((".test.ts", ".test.tsx", ".test.js", ".test.py",
                            ".test.rs", "_test.go", ".spec.ts", ".spec.tsx"))
                or ".e2e.test." in name or ".itest." in name
                or ".property.test." in name):
            out.append(str(p.relative_to(root)))
    return sorted(out)


def module_helpers(root: Path, modules: list[str]) -> dict[str, list[str]]:
    """每个目标模块里已有的导出符号（防重复造轮子的清单）。

    只取 export function/const/class/interface/type 和 Python def/class，
    以及 TS 的 export { a, b } 形式。
    """
    out: dict[str, list[str]] = {}
    code_exts = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".py", ".rs", ".go"}
    for mod in modules:
        if not _module_inside(root, mod):
            continue
        base = root / mod
        if not base.exists():
            continue
        syms: list[str] = []
        for p in _walk(base, code_exts):
            if re.search(r"\.(test|spec)\.", p.name) or ".itest." in p.name:
                continue
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            for m in re.finditer(
                r"^\s*export\s+(?:default\s+)?(?:async\s+)?"
                r"(?:function|const|let|var|class|interface|type|enum)\s+"
                r"([A-Za-z_$][\w$]*)",
                text, re.M,
            ):
                syms.append(f"{m.group(1)}  ({repo_rel(root, p)})")
            for m in re.finditer(
                r"^\s*export\s*\{([^}]*)\}", text, re.M
            ):
                for part in m.group(1).split(","):
                    name = part.split(" as ")[-1].strip()
                    if name:
                        syms.append(f"{name}  ({repo_rel(root, p)})")
            for m in re.finditer(
                r"^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)", text, re.M,
            ):
                syms.append(f"{m.group(1)}  ({repo_rel(root, p)})")
            for m in re.finditer(
                r"^class\s+([A-Za-z_]\w*)", text, re.M,
            ):
                syms.append(f"{m.group(1)}  ({repo_rel(root, p)})")
            for m in re.finditer(
                r"^\s*(?:pub(?:\([^)]*\))?\s+)?"
                r"(?:fn|struct|enum|trait|type|const|static)\s+([A-Za-z_]\w*)",
                text, re.M,
            ):
                syms.append(f"{m.group(1)}  ({repo_rel(root, p)})")
            # TS 解构导出：export const { a, b } = ...
            for m in re.finditer(
                r"^\s*export\s+(?:const|let|var)\s*\{([^}]*)\}", text, re.M,
            ):
                for part in m.group(1).split(","):
                    if "{" in part:
                        continue        # 嵌套解构（a: { b }）拿不准，跳过
                    # 先按 = 切取左边的键名，再按 : 取别名 ——
                    # "alpha = 1" 的名字是 alpha，不是 1
                    name = part.split("=")[0].split(":")[-1].strip()
                    if name:
                        syms.append(f"{name}  ({repo_rel(root, p)})")
        out[mod] = sorted(set(syms))
    return out


def repo_rel(root: Path, p: Path) -> str:
    try:
        return str(p.relative_to(root))
    except ValueError:
        return str(p)


# ---------------------------------------------------------------- 计划解析


@dataclass
class Plan:
    path: Path
    text: str = ""
    invariants: list[tuple[str, str, str]] = field(default_factory=list)
    # (编号, 不变式文本, 钉在哪)
    lanes: list[str] = field(default_factory=list)
    matrix_rows: list[list[str]] = field(default_factory=list)
    reuse_rows: list[list[str]] = field(default_factory=list)
    consistency_rows: list[list[str]] = field(default_factory=list)
    constraint_text: str = ""
    sections: set[str] = field(default_factory=set)
    mixed_sections: list[str] = field(default_factory=list)
    modules: list[str] = field(default_factory=list)
    title: str = ""
    extra_dimensions: list[str] = field(default_factory=list)
    unfilled: set[str] = field(default_factory=set)
    detail_rows: list[list[str]] = field(default_factory=list)
    # 「核心逻辑细节」表的表头格 —— 新旧格式的判据靠它（规格 §4.3）
    detail_header: list[str] = field(default_factory=list)
    # 正文注释里的豁免：{DP ID: 理由}
    waivers: dict[str, str] = field(default_factory=dict)
    # 正文注释里的**不变式**豁免：{不变式 ID: 理由}
    # （复合不变式在判定点粒度下没有认领者 ⇒ 需要一个写明理由的出口）
    invariant_exemptions: dict[str, str] = field(default_factory=dict)
    newcap_rows: list[list[str]] = field(default_factory=list)
    # 「人工确认」节：谁读过这份计划、什么时候、对不对。需求理解对不对本工具判不了，
    # 但"没人签过字"是一条必须出现在报告里的事实。
    reviewed: bool = False
    review_note: str = ""
    # 「确认」节里的表格行（审什么 | 谁 | 什么时候 | 结论）—— 谁确认的要能进报告
    review_rows: list[list[str]] = field(default_factory=list)
    # 谁确认了（报告里要写出来：人还是哪个 agent）
    review_by: str = ""
    # 「本工具不判定项的归属」节：{项 | 由谁判 | 依据}
    # 这八项本工具判不了，但**必须有人（或某个工具）认领并给出依据** ——
    # 否则"通过"只是"没人反对"，不是"有人签过字"。这是交付前置条件之一。
    not_judged_owners: list[list[str]] = field(default_factory=list)
    # 八项里还没人认领的
    not_judged_missing: list[str] = field(default_factory=list)


# 本工具**不判定**的事 —— 固定清单，每份报告都列。
# 计划里的「本工具不判定项的归属」节要逐项写明「由谁判、依据是什么」，
# 否则交付前置条件不齐（readiness 会在报告里说清缺哪几项）。
NOT_JUDGED_ITEMS = (
    "需求是否被正确理解",
    "产品行为是否正确",
    "性能",
    "安全",
    "可用性",
    "运维就绪",
    # 判定点引擎判的是「有没有判定点、判点有没有被钉住」，
    # 不是「并发下对不对」「扛不扛得住量」—— 后两样本工具没有条件判。
    "并发正确性",
    "容量与规模行为",
)


# ---------------------------------------------------------------- 计划格式
#
# v1 = 旧的「模块级维度表」（| 维度 | 落点 / 不适用理由 |）
# v2 = 判定点粒度（| 维度 | DP ID | 判定点 | 归属的不变式 | 落点 | 豁免理由 |）
# 规格 §4.3：判据是**列语义**不是列数 —— 人加列就变。

PLAN_FORMAT_V1 = "v1"
PLAN_FORMAT_V2 = "v2"

# 正文里的豁免注释：<!-- attest:waive <DP ID> reason="..." -->
_WAIVE_RE = re.compile(
    r"<!--\s*attest:waive\s+(?P<id>[^\s]+)\s+reason=\"(?P<reason>[^\"]*)\"\s*-->")

# 不变式豁免：写法与判定点豁免一致，只是豁免的是「不变式没有判定点认领」这件事
# （复合不变式在判定点粒度下天然没有认领者 ⇒ 需要一个写明理由的出口）
_INV_EXEMPT_RE = re.compile(
    r"<!--\s*attest:invariant-exempt\s+(?P<id>[^\s]+)\s+reason=\"(?P<reason>[^\"]*)\"\s*-->")

# 豁免理由只有这几个字 ⇒ 没说明豁免了什么（规格 §4.2：提醒）
_VAGUE_WAIVERS = ("不适用", "n/a", "na", "无", "none")


@dataclass
class DetailRow:
    """「核心逻辑细节」表的一行（v1 只有 dim / pin 有值）。"""

    dim: str = ""
    dp_id: str = ""
    dp_desc: str = ""
    invariant: str = ""
    pin: str = ""
    waiver: str = ""


def detail_dp_index(plan: Plan) -> int | None:
    """DP ID 在第几列。表头说了算；表头没写就看数据行里哪一列像 DP ID。"""
    for i, c in enumerate(plan.detail_header):
        if re.sub(r"\s+", "", c).lower() == "dpid":
            return i
    for row in plan.detail_rows:
        for i, c in enumerate(row):
            if dp_key(c):
                return i
    return None


def detect_plan_format(plan: Plan) -> str:
    """这份计划是旧的模块级维度表（v1）还是判定点粒度表（v2）。

    表头里有「DP ID」⇒ v2；表头没写但数据行有一列能解析成 DP ID ⇒ v2；
    都没有 ⇒ v1。**不许靠列数猜**（规格 §4.3）。
    """
    if not plan.detail_rows:
        return PLAN_FORMAT_V1
    return PLAN_FORMAT_V2 if detail_dp_index(plan) is not None else PLAN_FORMAT_V1


def detail_pin_index(plan: Plan) -> int:
    """落点在第几列。v1 是第 2 列；v2 的落点列跟着 DP ID 列走。

    相对索引而不是写死 4 —— 退化写法（表头没写 DP ID、DP ID 在第 1 列）
    里落点列也会跟着挪；标准 6 列布局算出来正好是 4（规格 §4.5）。
    """
    if detect_plan_format(plan) == PLAN_FORMAT_V1:
        return 1
    idx = detail_dp_index(plan)
    return 4 if idx is None else idx + 3


def parse_detail_rows(plan: Plan) -> list[DetailRow]:
    """把「核心逻辑细节」表按当前格式解析成结构化行。"""
    if detect_plan_format(plan) == PLAN_FORMAT_V1:
        return [DetailRow(dim=r[0] if r else "",
                          pin=r[1] if len(r) > 1 else "")
                for r in plan.detail_rows]
    idx = detail_dp_index(plan) or 0

    def cell(row: list[str], i: int) -> str:
        return row[i] if 0 <= i < len(row) else ""

    return [DetailRow(
        dim=cell(row, idx - 1),
        dp_id=cell(row, idx),
        dp_desc=cell(row, idx + 1),
        invariant=cell(row, idx + 2),
        pin=cell(row, idx + 3),
        waiver=cell(row, idx + 4),
    ) for row in plan.detail_rows]


def _waiver_for(plan: Plan, dp_id: str) -> str:
    """这个判定点在正文注释里的豁免理由（理由为空的不算）。"""
    key = dp_key(dp_id)
    if key is None:
        return ""
    for raw, reason in plan.waivers.items():
        if reason.strip() and dp_key(raw) == key:
            return reason.strip()
    return ""


def _vague_waiver(reason: str) -> bool:
    """豁免理由是不是只有「不适用」这种没内容的说法。"""
    r = reason.strip().lower()
    return r in _VAGUE_WAIVERS


def _parse_review_section(plan: Plan) -> None:
    """读「确认」节：填了（谁 + 什么时候 + 结论）才算有人读过。

    确认者**可以是人，也可以是 agent** —— 零人工流程里由 agent 确认。
    但确认者必须写清「谁」与「结论」：模型的确认也是一条可追溯的记录，
    而不是"没人反对"。
    """
    lines = plan.text.splitlines()
    start = None
    for i, ln in enumerate(lines):
        if ln.strip().startswith("## ") and ("人工确认" in ln
                                            or "人工复核" in ln
                                            or "人工审" in ln
                                            or ("确认" in ln and "不判定" not in ln)):
            start = i
            break
    if start is None:
        plan.reviewed = False
        plan.review_note = "计划里没有「确认」节 ⇒ 没人（或 agent）对需求理解签过字"
        return
    body = []
    for ln in lines[start + 1:]:
        if ln.strip().startswith("## "):
            break
        body.append(ln)
    text = "\n".join(body)
    if not text.strip():
        plan.reviewed = False
        plan.review_note = "「确认」节是空的 ⇒ 没人（或 agent）对需求理解签过字"
        return
    # 表格形式：审什么 | 谁 | 什么时候 | 结论 —— 把「谁」记下来进报告
    rows = []
    for ln in body:
        if not ln.strip().startswith("|"):
            continue
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if len(cells) < 2 or set("".join(cells)) <= set("- :"):
            continue
        if cells[0] in ("审什么", "项"):
            continue
        rows.append(cells)
    plan.review_rows = rows
    if rows:
        who = rows[0][1] if len(rows[0]) > 1 else ""
        plan.review_by = "" if "<待填" in who else who
    if "<待填" in text or "待填" in text:
        plan.reviewed = False
        plan.review_note = "「确认」节还是模板占位 ⇒ 没人（或 agent）对需求理解签过字"
        return
    plan.reviewed = True
    plan.review_note = "计划已有人（或 agent）确认（需求理解不由本工具判）"


def parse_plan(path: Path) -> Plan:
    """从计划 markdown 里抽出不变式清单、车道、覆盖矩阵。

    结构按标题认：
      ## 不变式 / ## 不变式清单 / ## Invariants → 不变式表
      ## 车道 / ## 测试车道 / ## Lanes        → 车道表
      ## 矩阵 / ## 覆盖矩阵 / ## Matrix      → 覆盖矩阵
      ## 复用 / ## 已有能力 / ## Reuse        → 复用表
    """
    plan = Plan(path=path)
    try:
        plan.text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        raise SystemExit(f"读不了计划文件: {e}")
    for m in _WAIVE_RE.finditer(plan.text):
        plan.waivers[m.group("id")] = m.group("reason")
    for m in _INV_EXEMPT_RE.finditer(plan.text):
        plan.invariant_exemptions[m.group("id")] = m.group("reason")

    sections: list[str] = []
    table_kind: str | None = None
    in_fence = False
    prev_was_row = False
    lines = plan.text.splitlines()
    for i, line in enumerate(lines):
        # 代码围栏里的示例表不是真数据
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        if "目标模块" in line and not line.startswith("#"):
            # 只有**真的带路径**的那行才算目标模块声明（`- 目标模块：`src``）。
            # 正文里提到「目标模块」的一句话不该把已声明的模块清空 ——
            # new 生成的模板里就有一句「按目标模块的代码特征推导出来的」，
            # 它会让 upgrade 找不到模块、判定点推成 0 个。
            for m in re.findall(r"`([^`]+)`", line):
                if m not in plan.modules:
                    plan.modules.append(m)
        if "额外涉及" in line and not line.startswith("#"):
            raw = line.split("：", 1)[-1]
            plan.extra_dimensions = [
                d.strip() for d in re.split(r"[、,，]", raw)
                if d.strip() and "待填" not in d
            ]
        if line.startswith("# ") and not plan.title:
            plan.title = line[2:].strip()
        if line.startswith("#"):
            low = line.lower()
            level = len(line) - len(line.lstrip("#"))
            # 一个标题里混了两类内容（比如"## 覆盖矩阵与一致性"）⇒ 后面
            # 的表会被当成同一节解析，一致性表就被吞了。记下来报红。
            kinds = []
            if any(k in low for k in ("不变式", "invariant", "契约", "contract")):
                kinds.append("inv")
            if any(k in low for k in ("矩阵", "matrix", "覆盖", "coverage")):
                kinds.append("matrix")
            if "一致性" in low:
                kinds.append("consistency")
            if any(k in low for k in ("约束", "中间件", "协调组件")):
                kinds.append("constraint")
            if any(k in low for k in ("复用", "reuse", "已有")):
                kinds.append("reuse")
            if any(k in low for k in ("细节", "detail")):
                kinds.append("detail")
            if any(k in low for k in ("新建", "新造")):
                kinds.append("newcap")
            if any(k in low for k in ("车道", "lane")):
                kinds.append("lane")
            if "不判定" in low:
                kinds.append("notjudged")
            # 「## 新建能力（先论证不能复用）」这类标题里天然带"复用"，
            # 不算混节 —— 把 reuse 从 newcap 标题里摘掉。
            if "newcap" in kinds and "reuse" in kinds:
                kinds.remove("reuse")
            if len(kinds) > 1:
                plan.mixed_sections.append(line.strip())
            if kinds:
                # 一行数据归到**所有**匹配的节 ⇒ 没有任何表会被吞
                sections = kinds
                plan.sections.update(kinds)
            elif level <= 2:
                sections = []
            else:
                sections = sections  # ### 继承父节
            # 换节 ⇒ 表块状态重置（上一节的最后一行不该让下一节的首行
            # 被当成"不是表块首行"）；表归属也要重置，否则新节的第一张表
            # 会继承上一节的种类。
            prev_was_row = False
            table_kind = None
            continue
        m = _ROW_RE.match(line)
        if not m:
            # 非表格行 ⇒ 打断表块（下一行的"块首行"判定要重新开始）
            prev_was_row = False
            if "constraint" in sections:
                if "<待填" in line or "待填" in line:
                    plan.unfilled.add("constraint")
                else:
                    # 占位里的路径不是真钉
                    plan.constraint_text += re.sub(
                        r"<待填[^>]*>", "", line) + "\n"
            # 车道也可能是 bullet 形态（模板生成的就是这种）：
            # - `service#test:e2e` — `playwright test`
            if "lane" in sections:
                bm = re.match(r"^\s*[-*]\s+`([^`]+)`", line)
                if bm:
                    plan.lanes.append(bm.group(1))
            continue
        cells = [c.strip() for c in m.group(1).split("|")]
        # 分隔行（|---|---|）跳过。分隔行也是"表格行"，会延续表块。
        if not cells or all(set(c) <= set("-: =") for c in cells):
            prev_was_row = True
            continue
        # 表头判定：必须是**表块的第一行**且下一行是分隔行。
        # 只看"下一行是分隔行"会把表尾的框线（| --- | --- |，手写表格
        # 上下加框的常见写法）当成表头 ⇒ 紧邻其上的最后一条数据行被丢掉。
        nxt = lines[i + 1] if i + 1 < len(lines) else ""
        nm = _ROW_RE.match(nxt)
        nxt_cells = [c.strip() for c in nm.group(1).split("|")] if nm else []
        next_is_sep = bool(nxt_cells) and all(
            set(c) <= set("-: =") for c in nxt_cells)
        # 是数据行还是表头？
        #   有钉 / 有空格子 / 首格像不变式编号 ⇒ 数据行
        #   全是标签词 ⇒ 表头
        # 单列的 `| 记忆行 |` 不是表头（"记忆行" 不在词表里）⇒ 不会被吞。
        looks_like_data = any(_PIN_RE.findall(c) for c in cells)
        if any(not c.strip() for c in cells):
            looks_like_data = True
        if "inv" in sections and _INV_ID_RE.match(cells[0]):
            looks_like_data = True
        # 表头可能带括号注释（"一致性怎么验（钉）"）⇒ 先剥注释再比
        def _is_label(c: str) -> bool:
            base = re.sub(r"[（(][^）)]*[）)]", "", c).strip().lower()
            return base in _HEADER_WORDS
        # ≥2 个标签格 ⇒ 表头。词表不必穷举（"测试文件"/"校验方式"这些
        # 合法写法很难列全）；有钉/空格子/不变式编号优先判数据行。
        label_hits = sum(1 for c in cells if c.strip() and _is_label(c))
        is_header = (not prev_was_row and next_is_sep and not looks_like_data
                     and label_hits >= 2)
        if is_header:
            prev_was_row = True
            # 表头定这张表的归属；认不出来就用标题兜底
            table_kind = _kind_from_header(cells) or (
                sections[0] if sections else None)
            if table_kind:
                plan.sections.add(table_kind)
            if table_kind == "detail":
                plan.detail_header = list(cells)
            continue            # 表块首行 + 下一行是分隔行 + ≥2 个标签格 ⇒ 表头
        prev_was_row = True

        # 模板占位行（<待填…>）不是数据 —— 跳过，并记下这一节还没填
        if any("<待填" in c or "待填" in c for c in cells):
            if row_kinds_preview(sections):
                plan.unfilled.update(row_kinds_preview(sections))
            continue

        # 这张表归哪一节：表头定的优先；没有表头（手写漏了表头）时用标题兜底；
        # 标题也没认出来时按行形状猜（一致性表：多列且含权威源/镜像/派生）。
        kind = table_kind or (sections[0] if sections else None)
        if kind is None and len(cells) >= 3:
            # 认不出表头时的兜底：**≥2 个特征词**才认（与 _kind_from_header 同纪律）。
            # 只命中一个词就认 ⇒ 判定点表里一句「镜像失败」就能把整行塞进一致性节
            # ⇒ 报一堆假红，还可能让 check 崩。
            hits = sum(1 for c in cells
                       if any(h in c for h in _CONSISTENCY_HINT))
            if hits >= 2:
                kind = "consistency"
        if kind:
            plan.sections.add(kind)
        row_kinds = [kind] if kind else []

        if "inv" in row_kinds:
            inv_id = _INV_ID_RE.match(cells[0])
            ident = inv_id.group(1) if inv_id else f"第{i + 1}行"
            # 钉在第三列 —— 不是最后一列（多一列"备注"就不该把备注当钉）
            # 少于三列的行不是"不是不变式"，是**漏了钉列** —— 报成
            # 没钉的错（原来直接跳过 ⇒ 假全绿）。
            if len(cells) > 2:
                pin = cells[2]
                desc = cells[1]
            elif len(cells) > 1:
                # 两列行：钉可能在第 2 列（| 不变式 | 钉 |），也可能是
                # 真的漏了钉列（| I1 | 没钉 |）。有钉就认，没钉就报红。
                pin = cells[1] if _PIN_RE.findall(cells[1]) else ""
                desc = cells[0]
            else:
                pin, desc = "", ""
            plan.invariants.append((ident, desc, pin))
        if "lane" in row_kinds and len(cells) >= 2 and cells[0]:
            plan.lanes.append(cells[0])
        if "matrix" in row_kinds:
            plan.matrix_rows.append(cells)
        if "reuse" in row_kinds:
            plan.reuse_rows.append(cells)
        if "consistency" in row_kinds:
            plan.consistency_rows.append(cells)
        if "detail" in row_kinds:
            plan.detail_rows.append(cells)
        if "newcap" in row_kinds:
            plan.newcap_rows.append(cells)
        if "constraint" in row_kinds:
            plan.constraint_text += " ".join(cells) + "\n"
    _parse_review_section(plan)
    _parse_not_judged_section(plan)
    return plan


def _parse_not_judged_section(plan: Plan) -> None:
    """读「本工具不判定项的归属」节：{项 | 由谁判 | 依据}。

    独立解析，不走通用表格机制 —— 这张表的表头（「由谁判」「依据」）不在
    表头词表里，走通用机制会把表头当成数据行。

    结果两个字段：
      owners   —— 计划里写了的行
      missing  —— NOT_JUDGED_ITEMS 里**没人认领**的那些
    """
    lines = plan.text.splitlines()
    start = None
    for i, ln in enumerate(lines):
        if ln.strip().startswith("## ") and "不判定" in ln:
            start = i
            break
    if start is None:
        plan.not_judged_missing = list(NOT_JUDGED_ITEMS)
        return
    rows = []
    for ln in lines[start + 1:]:
        if ln.strip().startswith("## "):
            break
        if not ln.strip().startswith("|"):
            continue
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if len(cells) < 2 or set("".join(cells)) <= set("- :"):
            continue
        if cells[0] in ("项", "项目", "不判定项", "不判定的事项"):
            continue                       # 表头
        rows.append(cells)
    plan.not_judged_owners = rows
    owned = set()
    for cells in rows:
        item = cells[0]
        who = cells[1] if len(cells) > 1 else ""
        basis = cells[2] if len(cells) > 2 else ""
        if "<待填" in who or "<待填" in basis or not who.strip():
            continue
        owned.add(item)
    plan.not_judged_missing = [i for i in NOT_JUDGED_ITEMS if i not in owned]


# ---------------------------------------------------------------- 校验


# 各框架的用例声明写法。
# 修饰符链（it.skip / test.only / it.each）要认；引号要配对（用例名里
# 可能有单引号：it("doesn't fail", …)）。
_CASE_PATTERNS = (
    re.compile(r"\b(?:it|test)(?:\.\w+)*\s*\(\s*(['\"])(.*?)\1"),
    # 模板字符串写的用例名（`it(\`rejects ${request.method} …\`)`）—— TS 里常见
    # （把 method/url 插进用例名）⇒ 保留 `${…}` 原文，计划里按同样文本就能钉住。
    re.compile(r"\b(?:it|test)(?:\.\w+)*\s*\(\s*(`)(.*?)\1"),
    # it.each([…])('name') —— 参数在括号里，名字在后面
    re.compile(r"\b(?:it|test)\.\w+\s*\([^)]*\)\s*\(\s*(['\"])(.*?)\1"),
    re.compile(r"^\s*(?:async\s+)?def\s+(test_\w+)", re.M),
    re.compile(r"^\s*func\s+(?:\([^)]*\)\s*)?(Test\w+)", re.M),
)

# describe 的分隔符：不同框架写法不同，统一成空格再比
_CASE_SEP_RE = re.compile(r"\s*(?:>|›|»|->|→)\s*")


_DESCRIBE_RE = re.compile(r"\bdescribe\s*\(\s*(['\"])(.*?)\1")


def test_case_names(root: Path, relpath: str) -> list[str]:
    """一个测试文件里有哪些用例名。

    TS/JS：it('…') / test('…')（含 .each/.skip/.only）；Python：def test_…；
    Go：func Test…。describe 嵌套时同时给出全名（"外层 > 内层"），
    这样计划里写全名或只写内层名都能对上。
    """
    p = root / relpath
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    names: list[str] = []
    stack: list[tuple[int, str]] = []
    for line in text.splitlines():
        indent = len(line) - len(line.lstrip())
        while stack and stack[-1][0] >= indent:
            stack.pop()
        dm = _DESCRIBE_RE.search(line)
        if dm:
            stack.append((indent, dm.group(2).strip()))
            continue
        for pat in _CASE_PATTERNS:
            m = pat.search(line)
            if not m:
                continue
            name = (m.group(2) if m.re.groups >= 2 else m.group(1)).strip()
            if not name:
                continue
            if name not in names:
                names.append(name)
            if stack:
                full = " > ".join([n for _, n in stack] + [name])
                if full not in names:
                    names.append(full)
    return names


def _norm_case(s: str) -> str:
    """用例名归一：分隔符统一成空格、空白折叠、小写。"""
    s = _CASE_SEP_RE.sub(" ", s.strip().strip("`"))
    return re.sub(r"\s+", " ", s).strip().lower()


def case_matches(case: str, names: list[str]) -> bool:
    """计划里写的用例名能不能对上文件里的某个用例。

    完全相同算对上；长前缀/后缀（≥8 字符、落在词边界上）也算 ——
    逼人写全名容易写错，但短名（"a"、"origin"）不能蒙混过关。
    """
    c = _norm_case(case)
    if not c:
        return False
    for name in names:
        n = _norm_case(name)
        if n == c:
            return True
        if len(c) >= 8 and (n.startswith(c + " ") or n.endswith(" " + c)):
            return True
    return False


# 在"逗号后紧跟路径状 token"处切段 —— 一个单元格里可能有多个钉。
# 不能按所有逗号切：用例名里本身可能有逗号（"…, never shifted down"）。
_PIN_SPLIT_RE = re.compile(
    r",\s*(?=[\w./@-]+\.(?:tsx|jsx|mjs|cjs|ts|js|py|rs|go|java|rb|sh))")


def parse_pins(cell: str) -> list[tuple[str, str | None]]:
    """从一个单元格里解析出 (文件路径, 用例名|None)。

    `::` 之后的文本整体是用例名 —— 里面再有路径状 token（pdf.js / plan.ts）
    也不能当成新钉。
    """
    out: list[tuple[str, str | None]] = []
    for seg in _PIN_SPLIT_RE.split(cell):
        seg = seg.strip()
        if not seg:
            continue
        if "::" in seg:
            head, _, tail = seg.partition("::")
            m = _PIN_RE.search(head)
            if not m:
                continue
            path = re.sub(r":[\d,\-]+$", "", m.group(0))
            case = re.sub(r":[\d,\-]+$", "", tail.strip().strip("`")).strip()
            out.append((path, case or None))
            continue
        for m in _PIN_RE.finditer(seg):
            path = re.sub(r":[\d,\-]+$", "", m.group(0))
            out.append((path, None))
    return out


def _symbol_uses(root: Path, symbol: str) -> int:
    """一个符号在全仓被用了几次（不含定义处的那一次也算）。

    新符号还没写进仓库时，这就是它的调用点数。
    """
    if not re.fullmatch(r"[A-Za-z_$][\w$]*", symbol):
        return 0
    pat = re.compile(r"\b" + re.escape(symbol) + r"\b")
    code_exts = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".py", ".rs", ".go"}
    uses = 0
    for p in _walk(root, code_exts):
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        uses += len(pat.findall(text))
    return uses


def _symbol_exists(root: Path, symbol: str) -> str:
    """全仓有没有已存在的同名导出。返回出处（没有就空串）。

    之前只认 TS 的 `export function|const|…` ⇒ Python/Rust/Go 仓库里
    「新建能力」这条"全仓已存在 ⇒ 报红"的检查**整条是死的**。
    """
    if not re.fullmatch(r"[A-Za-z_$][\w$]*", symbol):
        return ""
    s = re.escape(symbol)
    pats = (
        # TS/JS：export function|const|let|var|class|interface|type|enum
        re.compile(r"^\s*export\s+(?:default\s+)?(?:async\s+)?"
                   r"(?:function|const|let|var|class|interface|type|enum)\s+"
                   + s + r"\b", re.M),
        # Python：模块级（不在缩进里）的 def / class
        re.compile(r"^(?:async\s+)?(?:def|class)\s+" + s + r"\b", re.M),
        # Rust：pub fn/struct/enum/trait/type/const
        re.compile(r"^\s*pub\s+(?:fn|struct|enum|trait|type|const)\s+"
                   + s + r"\b", re.M),
        # Go：顶层 func / type
        re.compile(r"^(?:func|type)\s+" + s + r"\b", re.M),
    )
    code_exts = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
                  ".py", ".rs", ".go"}
    for p in _walk(root, code_exts):
        if re.search(r"\.(test|spec)\.|_test\.go$|\.itest\.", p.name):
            continue            # 测试文件里的同名不算"已有实现"
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for exp in pats:
            if exp.search(text):
                return repo_rel(root, p)
    return ""


# 测试约定探测：生成的东西必须像这个仓库自己写的
_FRAMEWORK_MARKERS = (
    ("node:test", r"from 'node:test'"),
    ("vitest", r"from 'vitest'"),
    ("jest", r"from '@jest|from 'jest"),
    ("mocha", r"from 'mocha'|require\('mocha'\)"),
)
_ASSERT_MARKERS = (
    ("node:assert", r"node:assert"),
    ("expect", r"\bexpect\("),
)

# package.json 里认出测试框架（仓库还没有测试文件时的兜底）。
# 优先级：① 依赖里的框架包 ② scripts 里测试车道的命令里提到的框架。
_JS_FRAMEWORK_DEPS = (
    ("vitest", ("vitest",)),
    ("jest", ("jest", "@types/jest")),
)
_JS_FRAMEWORK_SCRIPTS = ("vitest", "jest", "mocha")
# 各框架的断言风格
_JS_FRAMEWORK_ASSERTION = {
    "node:test": "node:assert",
    "vitest": "expect",
    "jest": "expect",
    "mocha": "node:assert",
}


def _infer_js_framework(root: Path) -> tuple[str, str]:
    """仓库里没有测试文件 ⇒ 从 package.json 推断用哪套 JS 测试框架。

    优先级：① devDependencies/dependencies 里的框架包（vitest → jest）
             ② scripts.test（再退到其它测试车道脚本）命令里提到的框架
    推断不出来返回 ("", "") —— 调用方必须**拒绝生成**，
    不得静默默认 node:test：node:test 的用例 vitest/jest 的 runner 根本不收，
    会变成"看着正常、永远不跑"的文件（比报错更糟）。
    """
    dep_hits: list[str] = []
    test_hits: list[str] = []
    other_hits: list[str] = []
    for pj in _walk(root, {".json"}):
        if pj.name != "package.json":
            continue
        try:
            pkg = json.loads(pj.read_text(encoding="utf-8", errors="replace"))
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(pkg, dict):
            continue
        deps: set[str] = set()
        for key in ("dependencies", "devDependencies", "peerDependencies"):
            block = pkg.get(key)
            if isinstance(block, dict):
                deps.update(str(k) for k in block)
        for fw, names in _JS_FRAMEWORK_DEPS:
            if fw not in dep_hits and any(d in names for d in deps):
                dep_hits.append(fw)
        scripts = pkg.get("scripts")
        if not isinstance(scripts, dict):
            continue
        for name, cmd in scripts.items():
            if not isinstance(cmd, str) or not _is_lane(str(name)):
                continue
            for fw in _JS_FRAMEWORK_SCRIPTS:
                # 词边界收紧：别把 "jest-lint"、"vitest.config" 当成框架
                if not re.search(rf"(?<![\w-]){fw}(?![\w-])", cmd):
                    continue
                bucket = test_hits if name == "test" else other_hits
                if fw not in bucket:
                    bucket.append(fw)
            # node:test 是内置的，不在依赖里，只在命令里露头。
            # 少了这一条，node:test 仓库（还没有测试文件）会走进死胡同：
            # 推断不出框架 ⇒ 拒绝生成，而工具又没有"指定框架"的口子。
            if re.search(r"node\s+--test", cmd):
                bucket = test_hits if name == "test" else other_hits
                if "node:test" not in bucket:
                    bucket.append("node:test")
    for fw in dep_hits + test_hits + other_hits:
        return fw, _JS_FRAMEWORK_ASSERTION[fw]
    return "", ""


@dataclass
class Conventions:
    """项目的测试约定。"""
    framework: str = "node:test"       # 主导语言的框架
    assertion: str = "node:assert"      # 主导语言的断言风格
    language: str = "js"                # 主导语言：js / python / rust / go
    js_framework: str = "node:test"     # JS 生态的框架（仓库混语言时也用它）
    js_assertion: str = "node:assert"
    js_files: int = 0                   # 仓库里的 JS/TS 文件数（代码 + 测试）
    js_tests: int = 0                   # 仓库里的 JS/TS 测试文件数
    js_framework_known: bool = False    # 框架是认出来的（不是默认值）
    unit_dir: str = ""          # 单元测试放哪（相对仓库根，通常与源码同目录）
    e2e_dir: str = ""           # e2e 放哪
    helper_dir: str = ""        # 夹具/助手层放哪
    e2e_suffix: str = ".e2e.test.ts"
    fixtures: list[str] = field(default_factory=list)  # 已有夹具文件名（复用参照）


def detect_conventions(root: Path, near: str = "") -> Conventions:
    """从仓库里认出测试约定。

    near = 目标模块路径时，夹具层/e2e 目录取**离它最近**的那个
    （同一个 workspace），而不是全仓出现最多的那个。
    """
    conv = Conventions()
    fw: dict[str, int] = {}
    asrt: dict[str, int] = {}
    unit_dirs: dict[str, int] = {}
    e2e_dirs: dict[str, int] = {}
    helper_dirs: dict[str, int] = {}
    fixtures: list[str] = []
    js_tests = 0
    py_tests = 0
    rs_tests = 0
    go_tests = 0

    for p in _walk(root, {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs",
                          ".py", ".rs", ".go"}):
        rel = str(p.relative_to(root))
        if "/node_modules/" in f"/{rel}":
            continue
        suffix = p.suffix.lower()
        if suffix in (".py", ".rs", ".go"):
            # Python / Rust / Go 的测试文件 —— 仓库主语言不是 JS 时，约定从这儿认
            name = p.name
            if suffix == ".py":
                if name.startswith("test_") or name.endswith("_test.py"):
                    py_tests += 1
            elif suffix == ".go":
                if name.endswith("_test.go"):
                    go_tests += 1
            else:
                try:
                    text = p.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                if "#[test]" in text or "#[cfg(test)]" in text:
                    rs_tests += 1
            continue
        conv.js_files += 1
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        head = text[:800]
        for name, pat in _FRAMEWORK_MARKERS:
            if re.search(pat, head):
                fw[name] = fw.get(name, 0) + 1
        for name, pat in _ASSERT_MARKERS:
            if re.search(pat, head):
                asrt[name] = asrt.get(name, 0) + 1
        parts = rel.split("/")
        # 夹具层：先判，且不看是不是测试文件 —— 真正的夹具（app.ts /
        # tokens.ts / agent-fixtures.ts）本身不是 .test.ts
        if "helpers" in parts:
            idx = parts.index("helpers")
            key = "/".join(parts[:idx + 1])
            helper_dirs[key] = helper_dirs.get(key, 0) + 1
            if not re.search(r"\.(test|spec)\.", p.name):
                fixtures.append(p.name)
            continue
        if not re.search(r"\.(test|spec)\.(ts|tsx|js|jsx|mjs)$", rel):
            continue
        js_tests += 1
        if "e2e" in parts:
            idx = parts.index("e2e")
            key = "/".join(parts[:idx + 1])
            e2e_dirs[key] = e2e_dirs.get(key, 0) + 1
        else:
            # 单测通常与源码同目录（colocated）—— 记下它，好生成同位置的测试
            key = "/".join(parts[:-1])
            unit_dirs[key] = unit_dirs.get(key, 0) + 1

    # Python / Rust / Go 的测试文件在上面的同一次遍历里数过了

    def _nearest(candidates: dict[str, int]) -> str:
        if not candidates:
            return ""
        if not near:
            return max(candidates, key=lambda k: candidates[k])
        # 与 near 共享前缀最长的那个
        def _shared(k: str) -> int:
            i = 0
            while i < min(len(k), len(near)) and k[i] == near[i]:
                i += 1
            return i
        return max(candidates, key=lambda k: (_shared(k), candidates[k]))

    if fw:
        # 测试文件里写了框架 import ⇒ 直接认
        conv.js_framework = max(fw, key=lambda k: fw[k])
        conv.js_framework_known = True
        if asrt:
            conv.js_assertion = max(asrt, key=lambda k: asrt[k])
        else:
            conv.js_assertion = _JS_FRAMEWORK_ASSERTION.get(
                conv.js_framework, conv.js_assertion)
    else:
        # 没有测试文件（或测试文件没写 import）⇒ 从 package.json 推断；
        # 推断不出来就**不知道该用哪套约定** —— 标记成"未知"，
        # 生成时必须拒绝（静默默认 node:test 会生成 runner 不收的文件）。
        fw2, asrt2 = _infer_js_framework(root)
        if fw2:
            conv.js_framework = fw2
            conv.js_assertion = asrt2
            conv.js_framework_known = True
    conv.js_tests = js_tests
    # 主导语言 = 测试文件最多的那种语言；生成骨架按**落点的扩展名**走，
    # 这里只是"这个仓库长什么样"的画像（打印给人看）。
    lang_counts = {"js": js_tests, "python": py_tests, "rust": rs_tests, "go": go_tests}
    dominant = max(lang_counts, key=lambda k: lang_counts[k])
    if lang_counts[dominant] == 0:
        dominant = "js"
    conv.language = dominant
    if dominant == "python":
        conv.framework, conv.assertion = "pytest", "assert"
    elif dominant == "rust":
        conv.framework, conv.assertion = "cargo test", "assert_eq!"
    elif dominant == "go":
        conv.framework, conv.assertion = "go test", "t.Fatalf"
    else:
        conv.framework, conv.assertion = conv.js_framework, conv.js_assertion
    if unit_dirs:
        conv.unit_dir = _nearest(unit_dirs)
    if e2e_dirs:
        conv.e2e_dir = _nearest(e2e_dirs)
    if helper_dirs:
        conv.helper_dir = _nearest(helper_dirs)
    if near:
        scope = near.split("/")[0] if "/" in near else ""
        conv.fixtures = sorted(f for f in set(fixtures)
                              if not scope or f in fixtures)
    conv.fixtures = sorted(set(fixtures))
    return conv


# 各种断言写法
_ASSERT_CALLS = (
    re.compile(r"\bassert\.(?:strictEqual|equal|deepEqual|deepStrictEqual|"
               r"notStrictEqual|notEqual|notDeepEqual|ok|notOk|throws|"
               r"rejects|match|doesNotMatch|doesNotThrow|fail)\s*\("),
    re.compile(r"\bexpect\s*\("),
    re.compile(r"\bshould\s*\("),
    # Python：assert 语句（行首）+ unittest 风格
    re.compile(r"(?m)^[ \t]*assert[ \t]"),
    re.compile(r"\bself\.assert\w*\s*\("),
    # Rust：assert! / assert_eq! / assert_ne!
    re.compile(r"\bassert(?:_eq|_ne)?!\s*\("),
    # Go：t.Fatalf / t.Errorf / t.Fatal / t.Error
    re.compile(r"\bt\.(?:Fatalf|Fatal|Errorf|Error)\s*\("),
)


def test_assertions(text: str) -> int:
    """一个文件里有几处断言。"""
    return sum(len(pat.findall(text)) for pat in _ASSERT_CALLS)


def test_body_shapes(text: str) -> list[str]:
    """每个 it() 的"断言骨架"（去掉字面量后的形状）。

    形状一样 ⇒ 这几个用例是复制粘贴，该参数化（扩展性）。
    """
    shapes: list[str] = []
    for m in re.finditer(
            r"\b(?:it|test)\s*\(\s*(['\"`])(.*?)\1\s*,\s*"
            r"(?:async\s*)?\(?\s*\)?\s*=>\s*\{",
            text, re.S):
        start = m.end()
        depth = 1
        i = start
        while i < len(text) and depth:
            if text[i] == "{":
                depth += 1
            elif text[i] == "}":
                depth -= 1
            i += 1
        body = text[start:i - 1]
        # 去掉字符串字面量与数字，剩下的结构就是"形状"
        shape = re.sub(r"['\"`][^'\"`]*['\"`]", "S", body)
        shape = re.sub(r"\b\d+(?:\.\d+)?\b", "N", shape)
        shape = re.sub(r"\s+", " ", shape).strip()
        shapes.append(shape)
    return shapes


def _middleware_used(text: str) -> list[str]:
    """计划正文里真被当作解法的中间件。

    否定语境（"不引入消息队列"）不算 —— 那是在说避开它。
    """
    out: list[str] = []
    for line in text.splitlines():
        low = line.lower()
        for term in _MIDDLEWARE_TERMS:
            t = term.lower()
            start = 0
            while True:
                i = low.find(t, start)
                if i < 0:
                    break
                # 否定词必须紧邻着组件名（同一行、前面几个字）——
                # 跨行看会把上一节的"不引入中间件"错配到下一行。
                before = low[max(0, i - 10):i]
                if not any(neg in before for neg in _NEGATION):
                    out.append(term)
                    break
                start = i + len(t)
    return out


def _plan_pins(plan: Plan) -> list[tuple[str, str]]:
    """计划里所有钉到的文件路径（哪一节 + 哪个路径）。

    不变式 / 覆盖矩阵 / 核心逻辑细节 / 数据一致性 / 约束节 / 复用表 /
    新建能力 —— 各节都要过同一道越界闸。
    """
    out: list[tuple[str, str]] = []

    def add(section: str, fpath: str) -> None:
        out.append((section, fpath))

    for _ident, _desc, pin in plan.invariants:
        for fpath, _case in parse_pins(pin):
            add("不变式清单", fpath)
    for row in plan.matrix_rows:
        for cell in row[1:]:
            for fpath, _case in parse_pins(cell):
                add("覆盖矩阵", fpath)
    # 核心逻辑细节：只从**落点列**抽钉 —— DP ID 列不参与（规格 §4.2），
    # 否则机器推导出来的路径会被当成人写的钉去过越界闸。
    pin_idx = detail_pin_index(plan)
    for row in plan.detail_rows:
        if len(row) <= pin_idx:
            continue
        for p in _PIN_RE.findall(row[pin_idx]):
            add("核心逻辑细节", p.split(":")[0])
    for row in plan.consistency_rows:
        if len(row) < 4:
            continue
        for p in _PIN_RE.findall(row[3]):
            add("数据一致性", p.split(":")[0])
    for p in _PIN_RE.findall(plan.constraint_text):
        add("约束：不引入中间件", p.split(":")[0])
    for row in plan.reuse_rows:
        for p in _PIN_RE.findall(" ".join(row)):
            add("复用表", p.split(":")[0])
    for row in plan.newcap_rows:
        for p in _PIN_RE.findall(" ".join(row)):
            add("新建能力", p.split(":")[0])
    return out


def _check_detail_v1(plan: Plan, repo: Repo, fs: list[Finding],
                     bad_pins: dict[str, str]) -> set[str]:
    """旧格式（模块级维度表）：每个维度都要有落点，或写「不适用」+理由。

    行为与升级前逐字一致 —— 老计划不该因为格式升级被判得更严。
    """
    covered: set[str] = set()
    for row in plan.detail_rows:
        if len(row) < 2:
            continue
        dim = row[0]
        cell = row[1]
        # 一行可以覆盖多个维度（"重试/幂等"）⇒ 按子串记
        for d in _DETAIL_DIMENSIONS:
            if d in dim:
                covered.add(d)
        if "不适用" in cell:
            if len(cell.strip()) <= len("不适用"):
                fs.append(Finding(
                    "error", "核心逻辑细节",
                    f"维度 {dim!r} 写了「不适用」但没给理由",
                    "写清楚为什么不适用（例如「不适用：纯计算，无外部输入」）",
                ))
            continue
        pins = _PIN_RE.findall(cell)
        if not pins:
            fs.append(Finding(
                "error", "核心逻辑细节",
                f"维度 {dim!r} 没有测试落点",
                "写具体测试文件，或写「不适用」+理由",
            ))
            continue
        for p in pins:
            fpath = p.split(":")[0]
            if fpath in bad_pins:
                continue      # 越界的钉已经在上面报红了，别去读它
            if not (repo.root / fpath).exists():
                fs.append(Finding(
                    "error", "核心逻辑细节",
                    f"维度 {dim!r} 的落点不存在: {fpath}",
                    "测试文件还没写，或路径写错了",
                ))
    return covered


def _check_detail_v2(plan: Plan, repo: Repo, fs: list[Finding],
                     bad_pins: dict[str, str]) -> set[str]:
    """新格式（判定点粒度）：DP ID / 归属 / 落点 / 豁免 四列各有各的判据。

    规格 §4.2：高危档判定点没归属也没豁免 ⇒ 报红；常规档只计数。
    """
    covered: set[str] = set()
    inv_ids = {ident for ident, _desc, _pin in plan.invariants}
    normal_gaps = 0

    for row in parse_detail_rows(plan):
        # 维度格里带触发证据（"边界（触发证据：…）"）—— 认维度只看名字那一段
        dim = row.dim.split("（")[0].strip()
        for d in _DETAIL_DIMENSIONS:
            if d in dim:
                covered.add(d)

        dp_id = row.dp_id.strip()
        key = dp_key(dp_id) if dp_id else None
        # 消息里点名到判定点 —— 一张表几十行，光说"维度"看不出是哪行
        who = dp_id or f"维度 {dim!r}"
        if dp_id:
            if key is None:
                fs.append(Finding(
                    "error", "核心逻辑细节",
                    f"判定点 {dp_id} 不是合法的 DP ID",
                    "格式：文件#锚点#类型[#序号]（类型取 branch/boundary/error-path/…）",
                ))
            elif not (repo.root / key[0]).exists():
                fs.append(Finding(
                    "error", "核心逻辑细节",
                    f"判定点 {dp_id} 指向的文件不存在: {key[0]}",
                    "机器推导出来的 ID 指向不存在的文件 ⇒ 推导器有 bug",
                ))

        # 豁免：豁免理由列 或 正文注释（注释豁免由 _waiver_findings 单独查合法性）
        waiver = row.waiver.strip()
        if waiver and _vague_waiver(waiver):
            fs.append(Finding(
                "warn", "核心逻辑细节",
                f"{who} 的豁免理由太笼统：{waiver!r}",
                "写清楚豁免了什么、为什么这个判定点不用测",
            ))
        waived = bool(waiver) or bool(_waiver_for(plan, dp_id))

        inv = row.invariant.strip()
        if inv and inv not in inv_ids:
            fs.append(Finding(
                "error", "核心逻辑细节",
                f"判定点 {dp_id or dim!r} 归属的不变式 {inv} 不在不变式清单里",
                "填不变式清单里真实存在的编号",
            ))

        # 缺口：DP 有、没归属、没豁免
        if dp_id and key and not inv and not waived:
            if key[2] in HIGH_RISK_KINDS:
                fs.append(Finding(
                    "error", "核心逻辑细节",
                    f"高危判定点 {dp_id} 既没归属不变式，也没写豁免理由",
                    "高危判定点（错误路径/资源/状态/丢弃）必须钉到用例，"
                    "或写明为什么它不用测",
                ))
            else:
                normal_gaps += 1

        # 落点
        cell = row.pin.strip()
        if waived:
            continue
        if not cell:
            fs.append(Finding(
                "error", "核心逻辑细节",
                f"{who} 没有测试落点",
                "写具体测试文件::用例名，或在豁免理由里写明为什么不用测",
            ))
        elif "不适用" in cell:
            if len(cell) <= len("不适用"):
                fs.append(Finding(
                    "error", "核心逻辑细节",
                    f"{who} 写了「不适用」但没给理由",
                    "写清楚为什么不适用（例如「不适用：纯计算，无外部输入」）",
                ))
        else:
            for fpath, case in parse_pins(cell):
                if fpath in bad_pins:
                    continue      # 越界的钉已经在上面报红了，别去读它
                if not (repo.root / fpath).exists():
                    fs.append(Finding(
                        "error", "核心逻辑细节",
                        f"{who} 的落点不存在: {fpath}",
                        "测试文件还没写，或路径写错了",
                    ))
                    continue
                if case is None:
                    fs.append(Finding(
                        "warn", "核心逻辑细节",
                        f"{who} 的落点只到文件、没有用例名: {fpath}",
                        "钉到具体用例（文件::用例名）才看得出测的是哪条路径",
                    ))
                    continue
                names = test_case_names(repo.root, fpath)
                if not names:
                    fs.append(Finding(
                        "error", "核心逻辑细节",
                        f"{who} 的落点 {fpath} 里没解析出任何用例",
                        "确认文件名对不对，或用例写法是不是标准 test/it",
                    ))
                elif not case_matches(case, names):
                    fs.append(Finding(
                        "error", "核心逻辑细节",
                        f"{who} 的落点用例在 {fpath} 里不存在: {case!r}",
                        f"该文件的用例有：{'; '.join(names[:5])}",
                    ))
    if normal_gaps:
        fs.append(Finding(
            "warn", "核心逻辑细节",
            f"有 {normal_gaps} 个常规判定点既没归属不变式，也没写豁免理由",
            "给每个判定点挂到一条不变式，或写明为什么它不用测",
        ))
    return covered


def _waiver_findings(plan: Plan, repo: Repo, fs: list[Finding]) -> None:
    """正文注释里的豁免（<!-- attest:waive <DP ID> reason="…" -->）。

    豁免的是哪个判定点必须说清楚 —— 空的豁免、指向空气的豁免都是假豁免。
    """
    if not plan.waivers:
        return
    table_keys = {k for k in (dp_key(r.dp_id) for r in parse_detail_rows(plan))
                  if k}
    v2 = detect_plan_format(plan) == PLAN_FORMAT_V2
    for raw, reason in plan.waivers.items():
        key = dp_key(raw)
        if key is None:
            fs.append(Finding(
                "error", "核心逻辑细节",
                f"豁免 {raw} 不是合法的 DP ID",
                "格式：文件#锚点#类型[#序号]",
            ))
            continue
        if not reason.strip():
            fs.append(Finding(
                "error", "核心逻辑细节",
                f"豁免 {raw} 没给理由",
                "写清楚豁免了什么、为什么这个判定点不用测",
            ))
            continue
        if not (repo.root / key[0]).exists():
            fs.append(Finding(
                "error", "核心逻辑细节",
                f"豁免 {raw} 指向的文件不存在: {key[0]}",
                "文件不存在 ⇒ 这条豁免指的是不存在的判定点",
            ))
            continue
        if v2 and key not in table_keys:
            fs.append(Finding(
                "error", "核心逻辑细节",
                f"豁免 {raw} 不在判定点表里",
                "表里没有这个判定点 ⇒ 豁免的是空气",
            ))


def _invariant_exemption_findings(plan: Plan, fs: list[Finding]) -> None:
    """正文注释里的不变式豁免（<!-- attest:invariant-exempt <不变式 ID> reason="…" -->）。

    豁免的是「这条不变式没有判定点认领」。指向空气、没给理由、理由太笼统
    都是假豁免 —— 假豁免比不豁免更糟（它把「没测到」藏起来了）。
    """
    if not plan.invariant_exemptions:
        return
    inv_ids = {ident for ident, _d, _p in plan.invariants}
    for raw, reason in plan.invariant_exemptions.items():
        ident = raw.strip()
        if not ident:
            fs.append(Finding(
                "error", "不变式清单",
                "不变式豁免没写是哪条不变式",
                "格式：<!-- attest:invariant-exempt I3 reason=\"…\" -->",
            ))
            continue
        if ident not in inv_ids:
            fs.append(Finding(
                "error", "不变式清单",
                f"不变式豁免 {ident} 不在不变式清单里",
                "清单里没有这条不变式 ⇒ 豁免的是空气",
            ))
            continue
        if not reason.strip():
            fs.append(Finding(
                "error", "不变式清单",
                f"不变式豁免 {ident} 没给理由",
                "写清楚这条不变式为什么不需要判定点认领",
            ))
            continue
        if _vague_waiver(reason):
            fs.append(Finding(
                "warning", "不变式清单",
                f"不变式豁免 {ident} 的理由「{reason.strip()}」太笼统",
                "写清楚豁免了什么、为什么（规格 §4.2：提醒）",
            ))


def check_plan(plan: Plan, repo: Repo) -> list[Finding]:
    """计划 vs 代码实况。"""
    fs: list[Finding] = []

    # 0. 钉越界 ⇒ 报红，而且**不去读仓库外的文件**。
    #    读取面与写入面同一把尺子（_pin_violation）：钉只能是仓库内的相对路径。
    bad_pins: dict[str, str] = {}
    for _section, fpath in _plan_pins(plan):
        if fpath in bad_pins:
            continue
        reason = _pin_violation(repo.root, fpath)
        if reason:
            bad_pins[fpath] = reason
    for fpath, reason in sorted(bad_pins.items()):
        fs.append(Finding(
            "error", "钉越界",
            f"钉指向仓库外的文件: {fpath}（{reason}）",
            "钉只能是仓库内的相对路径 —— 越界的钉不读也不写。",
        ))

    weak_inv: set[str] = set()
    pinned_cases: dict[str, set[str]] = {}

    # 1. 每条不变式必须有钉
    if not plan.invariants:
        if "inv" in plan.unfilled:
            fs.append(Finding(
                "error", "不变式清单",
                "这一节还是模板占位，没填",
                "每行一条行为契约：编号 | 不变式 | 钉在哪",
            ))
        else:
            fs.append(Finding(
                "error", "不变式清单",
                "没找到不变式表（标题含「不变式」或「契约」）",
                "加一节 `## 不变式清单`，每行：| 编号 | 不变式 | 钉在哪 |",
            ))
    for inv_id, desc, pin in plan.invariants:
        pins = parse_pins(pin)
        if not pins:
            fs.append(Finding(
                "error", f"不变式 {inv_id}",
                f"没有钉（原文：{desc[:40]!r}）",
                "钉写成 `path/to/file.test.ts::用例名`（或 `path/to/file.ts:123`）"
                "—— 没有钉的不变式等于没测",
            ))
            continue
        for fpath, case in pins:
            if fpath in bad_pins:
                continue          # 越界的钉已经在上面报红了，别去读它
            if not (repo.root / fpath).exists():
                fs.append(Finding(
                    "error", f"不变式 {inv_id}",
                    f"钉指向的文件不存在: {fpath}",
                    "要么文件改名了，要么这条不变式是编的",
                ))
                continue
            if case is None:
                weak_inv.add(inv_id)
                continue
            names = test_case_names(repo.root, fpath)
            if not names:
                fs.append(Finding(
                    "error", f"不变式 {inv_id}",
                    f"钉到了用例名，但 {fpath} 里没解析出任何用例",
                    "确认文件名对不对，或用例写法是不是标准 test/it",
                ))
                continue
            if not case_matches(case, names):
                fs.append(Finding(
                    "error", f"不变式 {inv_id}",
                    f"钉的用例在 {fpath} 里不存在: {case!r}",
                    f"该文件的用例有：{'; '.join(names[:5])}",
                ))
            else:
                pinned_cases.setdefault(fpath, set()).add(case)

    # 2. 矩阵每行都要有测试落点
    if not plan.matrix_rows:
        if "matrix" in plan.unfilled:
            fs.append(Finding(
                "error", "覆盖矩阵",
                "这一节还是模板占位，没填",
                "每行一个关键流程，列出对应测试文件",
            ))
        else:
            fs.append(Finding(
                "error", "覆盖矩阵",
                "没找到覆盖矩阵（标题含「矩阵」或「覆盖」）",
                "加一节 `## 覆盖矩阵`，每行一个流程/场景，列出对应测试文件",
            ))

    # 3. 车道要真实存在
    lanes = lane_scripts(repo)
    for lane in plan.lanes:
        if not lane or lane.startswith("|") or lane.startswith("-"):
            continue
        # 车道名可能是 package.json 的脚本名（含 "#" 前缀），也可能是裸名
        hit = any(k == lane or k.split("#")[-1] == lane for k in lanes)
        if not hit:
            fs.append(Finding(
                "warn", f"车道 {lane}",
                "项目里找不到这个测试车道脚本",
                f"现有车道：{', '.join(sorted(lanes)[:8])}",
            ))

    # 1b. 只钉到文件（没指到用例）⇒ 聚合提醒
    if weak_inv:
        fs.append(Finding(
            "warn", "不变式清单",
            f"{len(weak_inv)} 条不变式的钉只到文件、没指到具体用例",
            "写成 `文件::用例名` 才算钉到点上 —— "
            "同一文件里测了别的用例也算出过",
        ))

    # 1c. 被钉文件里没被任何不变式钉到的用例 ⇒ 聚合提醒。
    #     作用域 = 不变式钉过的文件 ∪ 矩阵落点文件 —— 矩阵说"这个流程测在
    #     这里"，那这个文件里的用例就该有不变式认领。
    scope = set(pinned_cases)
    for row in plan.matrix_rows:
        for cell in row[1:]:
            for fpath, case in parse_pins(cell):
                if case is not None and fpath not in bad_pins:
                    scope.add(fpath)
    unpinned: list[str] = []
    for fpath in sorted(scope):
        cases = pinned_cases.get(fpath, set())
        for name in test_case_names(repo.root, fpath):
            if not any(case_matches(c, [name]) for c in cases):
                unpinned.append(f"{fpath}::{name}")
    if unpinned:
        fs.append(Finding(
            "warn", "不变式清单",
            f"{len(unpinned)} 个用例没有对应的不变式",
            "要么这些测试是多余的，要么漏了一条不变式 —— "
            + "; ".join(unpinned[:4]),
        ))

    # 4. 矩阵每行都要有测试落点，且落点真实存在
    known_tests = set(existing_test_files(repo.root))
    lane_keys = lane_scripts(repo)
    # 全名（`service#test:e2e`）与裸名（`test:e2e`）都认 ——
    # 工具自己生成的模板就是带 # 的全名，只留裸名会把它误判成"编的车道"
    lane_names = set(lane_keys) | {k.split("#")[-1] for k in lane_keys}
    for row in plan.matrix_rows:
        # 落点只看第 2 列起（描述列里提到别的文件名不算落点）
        pins = [p for cell in row[1:] for p in parse_pins(cell)]
        if not pins:
            fs.append(Finding(
                "error", "覆盖矩阵",
                f"这一行没有测试落点: {row[0][:40]!r}",
                "每个关键流程都要指到具体测试文件 —— 没有落点 = 没测",
            ))
            continue
        for fpath, case in pins:
            if fpath in bad_pins:
                continue          # 越界的钉已经在上面报红了，别去读它
            if case is not None and (repo.root / fpath).exists():
                # 用例校验不依赖 known_tests —— 与不变式那条路径保持一致
                names = test_case_names(repo.root, fpath)
                if names and not case_matches(case, names):
                    fs.append(Finding(
                        "error", "覆盖矩阵",
                        f"落点的用例在 {fpath} 里不存在: {case!r}",
                        f"该文件的用例有：{'; '.join(names[:5])}",
                    ))
            if fpath in known_tests:
                continue
            if not (repo.root / fpath).exists():
                fs.append(Finding(
                    "error", "覆盖矩阵",
                    f"矩阵引用了不存在的文件: {fpath}",
                    "测试文件还没写，或路径写错了",
                ))
            else:
                fs.append(Finding(
                    "warn", "覆盖矩阵",
                    f"落点不是测试文件: {fpath}",
                    "落点应该指测试文件（*.test.* / *.spec.* / *.itest.*）",
                ))
        # 车道格：只认 `pkg#script` 形态（工具自己生成的那种）。
        # 场景描述里写 "issue #123" 不是车道。
        for cell in row[1:]:
            c = cell.strip()
            if not c or " " in c:
                continue
            if "#" in c and c not in lane_names:
                fs.append(Finding(
                    "warn", "覆盖矩阵",
                    f"车道 {c!r} 不是项目里的车道",
                    f"现有车道：{', '.join(sorted(lane_names)[:8])}",
                ))

    # 4b. 一个标题里混了两节 —— 两张表都会被收（不会吞），但还是提醒拆开
    for mixed in plan.mixed_sections:
        fs.append(Finding(
            "warn", "计划结构",
            f"一个标题里混了两节: {mixed!r}",
            "拆成两个二级标题（如 `## 覆盖矩阵` 和 `## 数据一致性`）更清楚",
        ))

    # 4c. 目标模块碰到 ≥2 个存储 ⇒ 必须有数据一致性表。
    consistency_unfilled = "consistency" in plan.unfilled
    #     这是用户目标里"pg数据/向量数据/文件数据严格校对"的兜底：
    #     同时写了两个存储却不校对，数据漂了没人知道。
    if plan.modules:
        store_kinds = scan_stores(repo.root, plan.modules)
        if len(store_kinds) >= 2 and not plan.consistency_rows \
                and not consistency_unfilled:
            kinds = ", ".join(sorted(store_kinds))
            fs.append(Finding(
                "error", "数据一致性",
                f"目标模块同时碰了 {len(store_kinds)} 个存储（{kinds}），"
                "但没有数据一致性校对",
                "两个存储的数据会漂 —— 补一节「数据一致性」，"
                "每行写清权威源、派生关系，以及**怎么验**（具体测试文件）",
            ))

    # 4d. 核心逻辑细节：每个维度都要有落点，或写「不适用」+理由。
    #     这是"测出核心逻辑的细节"的兜底 —— 维度默默跳过 = 没测到。
    #     旧格式（模块级维度表）与新格式（判定点粒度）走两套判据。
    if plan.detail_rows:
        if detect_plan_format(plan) == PLAN_FORMAT_V2:
            covered = _check_detail_v2(plan, repo, fs, bad_pins)
        else:
            covered = _check_detail_v1(plan, repo, fs, bad_pins)
            fs.append(Finding(
                "warn", "核心逻辑细节",
                "本计划还是旧的模块级维度表；升级到判定点粒度后，"
                "这三条判据才会硬判",
                "跑 `req_plan.py upgrade <计划文件>` 把旧表升级成判定点粒度",
            ))
        # 只要求**推导出的**维度都被回答 —— 固定清单会逼人填无关的行
        required = set()
        if plan.modules:
            derived = derive_dimensions(repo.root, plan.modules)
            if len(scan_stores(repo.root, plan.modules)) >= 2:
                derived.setdefault("数据对账", [])
            required = set(derived)
        else:
            required = set(_CORE_DIMENSIONS)
        required |= set(plan.extra_dimensions)
        missing = [d for d in _DETAIL_DIMENSIONS if d in required and d not in covered]
        if missing:
            fs.append(Finding(
                "warn", "核心逻辑细节",
                f"没考虑到的维度: {', '.join(missing)}",
                "这些维度要么有落点，要么写「不适用」+理由 —— "
                "默默跳过就是没测到",
            ))
    # 正文注释里的豁免：两种格式都要查
    _waiver_findings(plan, repo, fs)
    _invariant_exemption_findings(plan, fs)

    # 4e. 新建能力：已存在同名 ⇒ 重复造轮子；调用点 ≤1 ⇒ 过度设计
    for row in plan.newcap_rows:
        if len(row) < 2:
            continue
        name = row[0].strip()
        sym = re.split(r"[（(]", name)[0].strip()
        if not sym or "<待填" in sym:
            continue
        why = row[1] if len(row) > 1 else ""
        if len(why.strip()) <= len("不适用") or "复用" not in why and "现成" not in why:
            fs.append(Finding(
                "warn", "新建能力",
                f"{sym!r} 没写清楚为什么不能复用现成的",
                "先找过已有实现、说清差别，再新写 —— "
                "（复用扫描清单里没有它，也要说明为什么没有）",
            ))
        where = _symbol_exists(repo.root, sym)
        if where:
            fs.append(Finding(
                "error", "新建能力",
                f"{sym!r} 全仓已经存在（{where}）",
                "直接复用，别再造一个",
            ))
            continue
        uses = _symbol_uses(repo.root, sym)
        if uses <= 1:
            fs.append(Finding(
                "warn", "新建能力",
                f"{sym!r} 全仓只找到 {uses} 处引用",
                "规则之三：抽象要等第三个用例才抽 —— "
                "一个调用点先内联，别急着造抽象",
            ))

    # 5. 数据一致性：每行都要有权威源、镜像说明和"怎么验"的钉
    #    门控是"有没有写一致性/约束这一节"，不是"表里有没有数据行" ——
    #    写成散文或不写，都能绕过整个跨存储检查族。
    cross_store = bool(plan.sections & {"consistency", "constraint"})
    if "consistency" in plan.sections and not plan.consistency_rows \
            and not consistency_unfilled:
        fs.append(Finding(
            "error", "数据一致性",
            "有一节数据一致性，但没有数据行",
            "没有跨存储就删掉整节；跨存储就按 `| 数据 | 权威源 | 镜像/派生 | 一致性怎么验（钉） |` 填",
        ))
    if consistency_unfilled:
        fs.append(Finding(
            "error", "数据一致性",
            "这一节还是模板占位，没填",
            "没有跨存储就删掉整节；跨存储就按 "
            "`| 数据 | 权威源 | 镜像/派生 | 一致性怎么验（钉） |` 填",
        ))
    seen_data: dict[str, int] = {}
    for row in plan.consistency_rows:
        if not row:
            continue
        if not row[0].strip():
            fs.append(Finding(
                "error", "数据一致性",
                "这一行的数据名是空的",
                "第一列写数据名 —— 没有名字就看不出是哪份数据",
            ))
            continue
        data_name = row[0]
        authority = row[1] if len(row) > 1 else ""
        derived = row[2] if len(row) > 2 else ""
        pin_cell = row[3] if len(row) > 3 else ""
        if not authority:
            fs.append(Finding(
                "error", "数据一致性",
                f"{data_name!r} 没写权威源",
                "一份数据只能有一个权威 —— 没有权威源就没法判一致",
            ))
        if not derived:
            fs.append(Finding(
                "error", "数据一致性",
                f"{data_name!r} 没写镜像/派生（没有就写「无」）",
                "写清楚哪些是派生的，派生层坏了才知道要修哪",
            ))
        pins = _PIN_RE.findall(pin_cell)
        if not pins:
            fs.append(Finding(
                "error", "数据一致性",
                f"{data_name!r} 的一致性没有测试落点",
                "第 4 列写成具体测试文件 —— 「一致性怎么验」不是断言",
            ))
        else:
            for p in pins:
                fpath = p.split(":")[0]
                if fpath in bad_pins:
                    continue      # 越界的钉已经在上面报红了，别去读它
                if not (repo.root / fpath).exists():
                    fs.append(Finding(
                        "error", "数据一致性",
                        f"一致性落点指向的文件不存在: {fpath}",
                        "对账/一致性测试还没写，或路径写错了",
                    ))
                elif fpath not in known_tests:
                    fs.append(Finding(
                        "warn", "数据一致性",
                        f"一致性落点不是测试文件: {fpath}",
                        "第 4 列应该指测试文件 —— 「一致性怎么验」要能被测到",
                    ))
        data_key = re.sub(r"\s+", "", data_name).casefold()
        seen_data[data_key] = seen_data.get(data_key, 0) + 1
        if seen_data[data_key] > 1:
            fs.append(Finding(
                "error", "数据一致性",
                f"{data_name!r} 出现了 {seen_data[data_key]} 行",
                "同一份数据列两行 = 两个权威源；合并成一行",
            ))

    # 5b. 约束节还是占位 ⇒ 没填
    if "constraint" in plan.unfilled:
        fs.append(Finding(
            "error", "约束：不引入中间件",
            "这一节还是模板占位，没填",
            "写清替代方案（谁是权威、派生出哪、怎么对账修复）+ 对账测试落点",
        ))

    # 6. 跨存储时：约束节必须给出不用中间件的替代方案 + 对账测试落点
    constraint_unfilled = "constraint" in plan.unfilled
    if cross_store and not constraint_unfilled:
        constraint = plan.constraint_text.lower()
        # 判定"有没有替代方案"时把落点行排除 —— 「对账/修复路径的测试落点：
        # …」这行本身含"对账/修复"，留着会让任何计划都算"有替代方案"。
        prose = "\n".join(
            ln for ln in plan.constraint_text.splitlines()
            if "落点" not in ln
        ).lower()
        has_auth = any(t in prose for t in _AUTHORITY_TERMS)
        has_recon = any(t in prose for t in _RECONCILE_TERMS)
        if not (has_auth and has_recon):
            fs.append(Finding(
                "error", "约束：不引入中间件",
                "跨存储一致性没有给出「单权威 + 镜像 + 对账」的替代方案",
                "写清楚：谁是权威、派生出哪、怎么对账修复 —— "
                "不能引入 saga/TCC/outbox/消息队列",
            ))
        reconcile_pins = _PIN_RE.findall(plan.constraint_text)
        if not reconcile_pins:
            fs.append(Finding(
                "error", "约束：不引入中间件",
                "对账/修复路径没有测试落点",
                "写一行「对账/修复路径的测试落点：src/x.test.ts」—— "
                "对账代码必须被测到（掉的数据能补、重复的不产生）",
            ))
        else:
            for p in reconcile_pins:
                fpath = p.split(":")[0]
                if fpath in bad_pins:
                    continue      # 越界的钉已经在上面报红了，别去读它
                if not (repo.root / fpath).exists():
                    fs.append(Finding(
                        "error", "约束：不引入中间件",
                        f"对账测试落点指向的文件不存在: {fpath}",
                        "对账测试还没写，或路径写错了",
                    ))
                elif fpath not in known_tests:
                    fs.append(Finding(
                        "warn", "约束：不引入中间件",
                        f"对账落点不是测试文件: {fpath}",
                        "对账与修复路径必须被测到 —— 落点要指测试文件",
                    ))

    # 7. 正文若拿中间件/分布式事务当解法 ⇒ 报红。
    #    无条件跑（不靠 cross_store 门控）：否则"不写一致性节"就能绕过。
    #    "也写了替代方案"不是免罪金牌 —— 说了要用队列就是用了。
    used = [] if constraint_unfilled else _middleware_used(plan.text)
    if used:
        fs.append(Finding(
            "error", "约束：不引入中间件",
            f"计划里用了中间件/分布式事务当解法: {', '.join(used)}",
            "本项目不引入中间件 —— 改成「单权威 + 镜像 + 对账」，"
            "并写明对账测试落点",
        ))

    # 8. 复用表里说"复用"的，必须真有那个东西。
    #    关键词在整行里找 —— 原来只看最后一列，"复用 XXX" 写在第一列时
    #    这条检查永远不触发（等于没有）。
    for row in plan.reuse_rows:
        if len(row) < 2:
            continue
        joined = " ".join(row)
        if not re.search(r"(复用|reuse|已有|existing)", joined, re.I):
            continue
        pins = _PIN_RE.findall(joined)
        if not pins:
            continue
        for p in pins:
            fpath = p.split(":")[0]
            if fpath in bad_pins:
                continue          # 越界的钉已经在上面报红了，别去读它
            if not (repo.root / fpath).exists():
                fs.append(Finding(
                    "error", "复用表",
                    f"声称复用的文件不存在: {fpath}",
                    "别引用不存在的东西",
                ))

    # 「确认」没填 ⇒ 提醒（不报红）：需求理解对不对本工具判不了，
    # 但"没人读过这份计划"是必须让人知道的事。
    if not plan.reviewed:
        fs.append(Finding(
            "warn", "确认",
            plan.review_note or "计划未经确认",
            "确认一遍不变式清单与覆盖矩阵，并填上「确认」节 —— 人或 agent 都行，"
            "但要写清谁确认、依据是什么；钉得实不代表理解得对",
        ))
    # 「本工具不判定项的归属」没填齐 ⇒ 提醒（不报红）：这八项本工具判不了，
    # 但"没人认领"与"有人认领并给了依据"是两件事 —— 交付前置条件靠它区分。
    if plan.not_judged_missing:
        fs.append(Finding(
            "warn", "本工具不判定项的归属",
            f"{len(plan.not_judged_missing)} 项没人认领："
            + "、".join(plan.not_judged_missing),
            "这八项本工具判不了，但必须有人（或某个工具）认领并写明依据 —— "
            "填进计划的「本工具不判定项的归属」节；没人认领的「通过」只是没人反对",
        ))
    return fs


# ---------------------------------------------------------------- 生成


TEMPLATE = """# 测试计划：{feature}

- 范围：{scope}
- 生成方式：机器级 `attest` skill
- 目标模块：{modules_line}
- 判据：每条断言都要有钉；钉不到就说明这条没测。

## 已有能力（复用侦察 —— 先看这里，别重复造）

{reuse_block}

## 本模块碰到的存储

工具扫出来的（带文件:行证据）。**碰了 2 个以上存储 ⇒ 必须有「数据一致性」一节**
—— 两边漂了没人发现，是最贵的一种 bug。

{stores_block}

## 不变式清单

每行一条行为契约，第三列是**钉**。钉写 `测试文件::用例名` 最强 ——
工具会去核对那个文件里真有这个用例。

| # | 不变式 | 钉在哪 |
| --- | --- | --- |
| I1 | <待填：这需求必须永远成立的性质> | <待填：src/x.test.ts::用例名> |

## 测试车道

本项目可用的车道（从项目自己声明的地方读出来的：JS 仓读 package.json，
Python 仓读 Makefile 目标与 tox.ini 环境；别自己编）：

{lanes_block}

## 覆盖矩阵

每行一个关键流程，列出这个流程的每一环落在哪个测试文件。
**关键流程细节都要有行** —— 只测主干的矩阵不算覆盖。

| 流程/场景 | 测试落点 | 车道 |
| --- | --- | --- |
| <待填：流程名> | <待填：src/x.test.ts> | <待填：单测> |

{cross_store_block}
## 核心逻辑细节

每个核心逻辑**判定点**一行 —— 判定点 = 代码里一个真正要选的分支/边界/错误路径。
**DP ID 与判定点两列是机器推导出来的**（{dp_level}），后三列要人来填：
这条判定点归属哪条不变式、钉到哪个用例、或者写明为什么豁免。

下面的维度是**按目标模块的代码特征推导**出来的（括号里是触发它的证据）。
每个维度都要有测试落点，或写「不适用」+理由 —— **默默跳过 = 没测到**。

本需求额外涉及：<待填：没有就删掉这行；填了就多几个维度要答>

| 维度 | DP ID | 判定点 | 归属的不变式 | 落点（文件::用例名） | 豁免理由 |
| --- | --- | --- | --- | --- | --- |
{detail_block}

## 新建能力（先论证不能复用）

新写的抽象/工具列这里。**先证明现成的不能复用**，再写。调用点 ≤1 ⇒ 过度设计。

| 能力 | 为什么不复用现成的 | 调用点 |
| --- | --- | --- |
| <待填：符号名> | <待填> | <待填> |

## 本需求明确不做的事

<待填：这个需求明确不做的事>

## 确认（人 或 agent 都可以）

**确认者可以是人，也可以是 agent** —— 全自动流程里由 agent 确认。但确认者必须写清
「谁」与「结论」：模型的确认是一条**可追溯的记录**，不是"没人反对"。

| 审什么 | 谁确认 | 什么时候 | 结论 |
| --- | --- | --- | --- |
| 不变式清单与需求一致 | <待填：人或 agent 的名字> | <待填：日期> | <待填：一致 / 有出入（改哪条）> |
| 覆盖矩阵覆盖了全部关键流程 | <待填：人或 agent 的名字> | <待填：日期> | <待填：覆盖到 / 缺哪条> |
| 判定点的归属与豁免理由站得住 | <待填：人或 agent 的名字> | <待填：日期> | <待填：站得住 / 要改> |

没填这一节 ⇒ `check` 会提醒，`readiness` 的报告会明说「计划未经确认」。

## 本工具不判定项的归属

这八项本工具判不了，但**必须有人（或某个工具）认领并给出依据** —— 否则"通过"只是
"没人反对"，不是"有人签过字"。这一节填齐，交付前置条件才齐（readiness 会在报告里
逐项说清缺哪几项）。

| 项 | 由谁判 | 依据 |
| --- | --- | --- |
| 需求是否被正确理解 | <待填：人或 agent> | <待填：怎么判的> |
| 产品行为是否正确 | <待填：人或 agent> | <待填：怎么判的> |
| 性能 | <待填：人或 agent> | <待填：怎么判的> |
| 安全 | <待填：人或 agent> | <待填：怎么判的> |
| 可用性 | <待填：人或 agent> | <待填：怎么判的> |
| 运维就绪 | <待填：人或 agent> | <待填：怎么判的> |
| 并发正确性 | <待填：人或 agent> | <待填：怎么判的> |
| 容量与规模行为 | <待填：人或 agent> | <待填：怎么判的> |
"""


def _case_label(case: str) -> str:
    """用例名 → 可读的 describe/it 名。"""
    return case.strip().strip("`").strip()


# 目标文件的扩展名 → 语言。**语言由落点决定，不由仓库默认值决定** ——
# 否则 Python 仓库里钉 .py 会被写成 TypeScript
# （见 docs/oss-release-review.md 第五节）。
_LANG_BY_EXT = {
    ".ts": "js", ".tsx": "js", ".js": "js", ".jsx": "js",
    ".mjs": "js", ".cjs": "js",
    ".py": "python",
    ".rs": "rust",
    ".go": "go",
}

# 各语言生成骨架时的默认约定（仓库自己的约定认不出来时用这些）
_LANG_DEFAULTS = {
    "python": ("pytest", "assert"),
    "rust": ("cargo test", "assert_eq!"),
    "go": ("go test", "t.Fatalf"),
}


def _conv_for(lang: str, conv: Conventions) -> tuple[str, str]:
    """某个语言生成时用哪套约定（仓库主语言用自己的，其余用默认）。"""
    if lang == conv.language:
        return conv.framework, conv.assertion
    return _LANG_DEFAULTS.get(lang, (conv.js_framework, conv.js_assertion))


def _js_str(value: str) -> str:
    """把文本安全地嵌进 JS 单引号字符串（单引号/反斜杠/换行都要转义）。"""
    v = value.replace("\\", "\\\\").replace("'", "\\'")
    return v.replace("\r", "\\r").replace("\n", "\\n")


def _comment_safe(value: str) -> str:
    """块注释里不能出现 `*/` —— 否则后面的内容会被当成代码。"""
    return value.replace("*/", "* /")


def _one_line(value: str) -> str:
    """压成一行 —— 注释里的换行会破坏结构。"""
    return re.sub(r"\s+", " ", value).strip()


def _slug(value: str) -> str:
    """用例名 → 标识符片段（Python/Rust/Go 的函数名用）。"""
    s = re.sub(r"[^A-Za-z0-9]+", "_", value).strip("_").lower()
    return re.sub(r"_+", "_", s)


def _test_name(case: str) -> str:
    """用例名 → 测试函数名（Python/Rust）。用例名自带 test_ 前缀就不重复加。"""
    s = _slug(case)
    if not s:
        return "test_case"
    return s if s.startswith("test_") else f"test_{s}"


def _unique_name(base: str, used: set[str]) -> str:
    """同一个文件里函数名不能重复。"""
    name = base
    i = 2
    while name in used:
        name = f"{base}_{i}"
        i += 1
    used.add(name)
    return name


def _go_name(case: str) -> str:
    """用例名 → Go 测试函数名（Test + 驼峰）。用例名自带 Test 就不重复加。"""
    raw = re.sub(r"[^A-Za-z0-9]", "", case)
    if raw.startswith("Test"):
        return raw
    parts = [p for p in _slug(case).split("_") if p]
    if not parts:
        return "TestCase"
    pascal = "".join(p[:1].upper() + p[1:] for p in parts)
    return pascal if pascal.startswith("Test") else f"Test{pascal}"


def _go_pkg(dirname: str) -> str:
    """目录名 → Go 包名（拿不准就让人在 TODO 里改）。"""
    s = re.sub(r"[^A-Za-z0-9_]", "", dirname).lower()
    if not s or s[0].isdigit():
        s = "pkg" + s
    return s


def _go_pkg_for(target: Path) -> str:
    """Go 包名：跟同目录已有的 .go 文件一致，否则取目录名。"""
    try:
        candidates = sorted(target.parent.glob("*.go"))
    except OSError:
        candidates = []
    for g in candidates:
        try:
            text = g.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        m = re.search(r"(?m)^package\s+([A-Za-z_]\w*)", text)
        if m:
            return m.group(1)
    return _go_pkg(target.parent.name)


def _pin_violation(repo_root: Path, fpath: str) -> str:
    """钉里的路径是不是越界 —— 读取面（check）与写入面（scaffold）同一把尺子。

    返回空串表示没问题；否则返回越界理由。
    """
    root = repo_root.resolve()
    raw = Path(fpath)
    if raw.is_absolute():
        return "是绝对路径（只接受仓库内的相对路径）"
    if ".." in raw.parts:
        return "含 `..`（会跳到边界之外）"
    resolved = (root / raw).resolve()
    if not resolved.is_relative_to(root):
        return f"规范化后落在仓库根之外（{resolved}）"
    return ""


def _safe_target(out_dir: Path, repo_root: Path, fpath: str) -> tuple[Path | None, str]:
    """把钉里的文件路径规范化，校验它不会写到边界之外。

    返回 (目标路径, 拒绝理由)；目标路径是 None 时理由非空。
    边界有两条：--out-dir 与仓库根 —— 任一越界都不写。
    """
    reason = _pin_violation(repo_root, fpath)
    if reason:
        return None, reason
    target = (out_dir / Path(fpath)).resolve()
    if not target.is_relative_to(out_dir):
        return None, f"规范化后落在 --out-dir 之外（{target}）"
    return target, ""


def _js_lines(conv: Conventions, title: str,
              invariants: list[tuple[str, str]],
              cases: list[tuple[str, str]]) -> list[str]:
    """JS/TS 风格的骨架。"""
    if conv.js_framework == "vitest":
        imp = "import { describe, it } from 'vitest';"
        if conv.js_assertion == "expect":
            imp += "\nimport { expect } from 'vitest';"
    elif conv.js_framework == "jest":
        imp = "import { describe, it } from '@jest/globals';"
    elif conv.js_framework == "mocha":
        imp = "import assert from 'node:assert';\nimport { describe, it } from 'mocha';"
    else:
        imp = "import assert from 'node:assert';\nimport { describe, it } from 'node:test';"

    lines = ["/**", f" * {_comment_safe(_one_line(title))}", " *"]
    if invariants:
        lines.append(" * 这里钉的不变量：")
        for ident, desc in invariants:
            lines.append(f" *   - {_comment_safe(_one_line(ident))}: "
                         f"{_comment_safe(_one_line(desc))}")
    else:
        lines.append(" * （待填：这里钉哪几条不变量）")
    lines += [
        " *",
        " * 为什么单独一个文件：（待填：这个维度为什么独立于其他维度）",
        " */",
        "",
        imp,
        "",
    ]
    groups: dict[str, list[str]] = {}
    for group, case in cases:
        groups.setdefault(group, []).append(case)
    for group, items in groups.items():
        lines.append(f"describe('{_js_str(group)}', () => {{")
        for case in items:
            lines.append(f"  it('{_js_str(case)}', () => {{")
            lines.append("    // TODO: 断言要钉住的东西（期望值 ≠ 输入值）")
            lines.append("  });")
            lines.append("")
        lines.append("});")
        lines.append("")
    return lines


def _py_lines(title: str, invariants: list[tuple[str, str]],
              cases: list[tuple[str, str]]) -> list[str]:
    """Python（pytest）风格的骨架。"""
    lines = [f"# {_one_line(title)}", "#"]
    if invariants:
        lines.append("# 这里钉的不变量：")
        for ident, desc in invariants:
            lines.append(f"#   - {_one_line(ident)}: {_one_line(desc)}")
    else:
        lines.append("# （待填：这里钉哪几条不变量）")
    lines += [
        "#",
        "# 为什么单独一个文件：（待填：这个维度为什么独立于其他维度）",
        "",
    ]
    used: set[str] = set()
    for _group, case in cases:
        name = _unique_name(_test_name(case), used)
        lines.append(f"def {name}():")
        lines.append(f"    {case!r}")
        lines.append("    # TODO: 断言要钉住的东西（期望值 ≠ 输入值）")
        lines.append("    actual = ...        # TODO: 调被测代码，拿到实际值")
        lines.append("    expected = ...      # TODO: 写期望值")
        lines.append('    assert actual == expected, "TODO: 期望值 ≠ 输入值"')
        lines.append("")
    return lines


def _rs_lines(title: str, invariants: list[tuple[str, str]],
              cases: list[tuple[str, str]]) -> list[str]:
    """Rust（cargo test）风格的骨架。"""
    lines = [f"// {_one_line(title)}", "//"]
    if invariants:
        lines.append("// 这里钉的不变量：")
        for ident, desc in invariants:
            lines.append(f"//   - {_one_line(ident)}: {_one_line(desc)}")
    else:
        lines.append("// （待填：这里钉哪几条不变量）")
    lines += [
        "//",
        "// 为什么单独一个文件：（待填：这个维度为什么独立于其他维度）",
        "",
    ]
    used: set[str] = set()
    for _group, case in cases:
        name = _unique_name(_test_name(case), used)
        lines.append("#[test]")
        lines.append(f"fn {name}() {{")
        lines.append(f"    // 用例：{_one_line(case)}")
        lines.append("    // TODO: 断言要钉住的东西（期望值 ≠ 输入值）")
        lines.append("    let actual = 0;    // TODO: 调被测代码，拿到实际值")
        lines.append("    let expected = 0;  // TODO: 写期望值")
        lines.append('    assert_eq!(actual, expected, "TODO: 期望值 ≠ 输入值");')
        lines.append("}")
        lines.append("")
    return lines


def _go_lines(title: str, invariants: list[tuple[str, str]],
              cases: list[tuple[str, str]], pkg: str) -> list[str]:
    """Go（go test）风格的骨架。"""
    lines = [f"// {_one_line(title)}", "//"]
    if invariants:
        lines.append("// 这里钉的不变量：")
        for ident, desc in invariants:
            lines.append(f"//   - {_one_line(ident)}: {_one_line(desc)}")
    else:
        lines.append("// （待填：这里钉哪几条不变量）")
    lines += [
        "//",
        "// 为什么单独一个文件：（待填：这个维度为什么独立于其他维度）",
        "// TODO: 确认包名（默认取目录名）",
        "",
        f"package {pkg}",
        "",
        'import "testing"',
        "",
    ]
    used: set[str] = set()
    for _group, case in cases:
        name = _unique_name(_go_name(case), used)
        lines.append(f"func {name}(t *testing.T) {{")
        lines.append(f"\t// 用例：{_one_line(case)}")
        lines.append("\t// TODO: 断言要钉住的东西（期望值 ≠ 输入值）")
        lines.append("\tactual := 0   // TODO: 调被测代码，拿到实际值")
        lines.append("\texpected := 0 // TODO: 写期望值")
        lines.append("\tif actual != expected {")
        lines.append('\t\tt.Fatalf("TODO: 期望值 ≠ 输入值 '
                     '(actual=%v expected=%v)", actual, expected)')
        lines.append("\t}")
        lines.append("}")
        lines.append("")
    return lines


def scaffold_test_file(
    path: Path,
    conv: Conventions,
    title: str,
    invariants: list[tuple[str, str]],
    cases: list[tuple[str, str]],
    is_e2e: bool,
) -> None:
    """写一个测试文件骨架。

    文件头写清"钉了哪几条不变量" —— 这是逻辑性的载体，也是后来人
    读这个文件时知道它为什么存在的唯一线索。

    风格按**落点的扩展名**选（.py → pytest、.rs → cargo test、.go → go test），
    不按仓库默认值 —— 否则会把 TypeScript 写进 .py 路径。
    """
    lang = _LANG_BY_EXT.get(path.suffix.lower(), "js")
    if lang == "python":
        lines = _py_lines(title, invariants, cases)
    elif lang == "rust":
        lines = _rs_lines(title, invariants, cases)
    elif lang == "go":
        lines = _go_lines(title, invariants, cases, _go_pkg_for(path))
    else:
        lines = _js_lines(conv, title, invariants, cases)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def check_test_toolkit(plan: Plan, repo: Repo) -> list[Finding]:
    """测试包的四性检查：严谨 / 逻辑 / 复用 / 扩展。"""
    fs: list[Finding] = []

    # 计划里所有被钉到的测试文件（越界的钉不列 —— 不去读仓库外的文件）
    tool_files: set[str] = set()
    for row in plan.matrix_rows:
        if len(row) < 2:
            continue
        for fpath, case in parse_pins(row[1]):
            if case and not _pin_violation(repo.root, fpath):
                tool_files.add(fpath)
    for _ident, _desc, pin in plan.invariants:
        for fpath, case in parse_pins(pin):
            if case and not _pin_violation(repo.root, fpath):
                tool_files.add(fpath)

    # 严谨性：没有断言的测试文件 = 覆盖率剧场
    no_assert: list[str] = []
    # 逻辑性：文件头没声明钉了哪些不变量
    no_header: list[str] = []
    # 扩展性：结构雷同的用例组
    dup_shape: list[tuple[str, int]] = []
    # 复用性：测试里的 helper 名与仓库已有导出重名
    dup_helper: list[tuple[str, str]] = []

    for fpath in sorted(tool_files):
        full = repo.root / fpath
        try:
            text = full.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if test_assertions(text) == 0:
            no_assert.append(fpath)
        head = text[:1500]
        if "这里钉的不变量" not in head and "不变量" not in head:
            no_header.append(fpath)
        shapes = test_body_shapes(text)
        if shapes:
            counted: dict[str, int] = {}
            for sh in shapes:
                counted[sh] = counted.get(sh, 0) + 1
            top = max(counted.values())
            if top >= 3:
                dup_shape.append((fpath, top))

    if no_assert:
        fs.append(Finding(
            "error", "测试包严谨性",
            f"{len(no_assert)} 个测试文件里没有断言",
            "覆盖率剧场 —— 用例跑了但没验任何东西："
            + "; ".join(no_assert[:4]),
        ))
    if no_header:
        fs.append(Finding(
            "warn", "测试包逻辑性",
            f"{len(no_header)} 个测试文件头没声明钉了哪些不变量",
            "文件头写清「这里钉的不变量」—— 后来人才知道它为什么存在："
            + "; ".join(no_header[:4]),
        ))
    if dup_shape:
        fs.append(Finding(
            "warn", "测试包扩展性",
            f"{len(dup_shape)} 个测试文件里有多个结构完全相同的用例",
            "复制粘贴的用例该参数化（加一个维度就是加一行数据，不是加一段代码）："
            + "; ".join(f"{f}×{c}" for f, c in dup_shape[:3]),
        ))

    # 复用性：测试文件里新造的 helper 与仓库已有导出重名
    existing: dict[str, str] = {}
    for p in _walk(repo.root, {".ts", ".tsx", ".js", ".mjs", ".py"}):
        rel = str(p.relative_to(repo.root))
        if rel in tool_files or "/node_modules/" in f"/{rel}":
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in re.finditer(
                r"^\s*export\s+(?:async\s+)?(?:function|const|class)\s+([A-Za-z_$][\w$]*)",
                text, re.M):
            existing.setdefault(m.group(1), rel)
    for fpath in sorted(tool_files):
        full = repo.root / fpath
        try:
            text = full.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for m in re.finditer(
                r"^\s*(?:function|const|class)\s+([A-Za-z_$][\w$]*)", text, re.M):
            name = m.group(1)
            if name in existing:
                dup_helper.append((fpath, name))
    if dup_helper:
        fs.append(Finding(
            "warn", "测试包复用性",
            f"{len(dup_helper)} 个测试里的助手与仓库已有导出重名",
            "优先复用现成的（别另造一个）："
            + "; ".join(f"{f}:{n}" for f, n in dup_helper[:4]),
        ))
    return fs


def _reconcile_rows(plan: Plan) -> list[dict]:
    """从「数据一致性」节抽出每一行的口径：数据 / 权威源 / 镜像 / 落点。

    权威源与镜像列里常见写法：PG `agent_memory`（`path/to/models.ts`）、
    Milvus 集合 `lumen_mem_<租户>`。这里只把**反引号里的标识符**抽出来 ——
    抽不出来的留给 `<待填>`，不猜。
    """
    out = []
    for row in plan.consistency_rows:
        if len(row) < 3:
            continue
        data, auth, mirror = row[0], row[1], row[2]
        pin = row[3] if len(row) > 3 else ""
        def ids(cell: str) -> list[str]:
            return re.findall(r"`([^`]+)`", cell)
        auth_ids = ids(auth)
        mirror_ids = ids(mirror)
        first_auth = auth_ids[0] if auth_ids else ""
        out.append({
            "data": data,
            "auth": auth,
            "mirror": mirror,
            "pin": pin,
            "pg_table": (first_auth.split(".")[0] if first_auth else ""),
            "pg_column": (first_auth.split(".", 1)[1] if "." in first_auth else ""),
            "collection": next((i for i in mirror_ids if "<" in i or "_" in i or i.islower()), ""),
        })
    return out


RECONCILE_HEADER = """/**
 * 跨存储对账脚本 —— 由 attest 的 `req_plan.py reconcile` 生成。
 *
 * 生成的依据是计划里的「数据一致性」节：每一行 = 一份数据、它的权威源、
 * 它的镜像、以及一致性怎么验。**这份脚本只做比对，不修数据** ——
 * 修复是另一个动作（repair），必须人工确认后再跑。
 *
 * ⚠️ 填完之前它**拒绝运行**：脚本启动时先扫自己有没有 `<待填` ⇒ 有就打印
 * 缺哪几项并退出非零。宁可红，不可静默绿（attest 的一贯口径）。
 *
 * 填完之后：把脚本名里的 reconcile 保持住，attest 的 readiness 会自动
 * 认出它（脚本名含 reconcile + 正文提到本模块的表）⇒ 下一轮验收会真跑它。
 */
import { createHash } from 'node:crypto';

const TODO_RE = () => /<待填[^>]*>/;

/** 本模块要对账的数据（来自计划的「数据一致性」节）。 */
type ReconcileItem = {
  /** 这份数据是什么 */
  data: string;
  /** 权威源：PG 表 */
  pgTable: string;
  /** 权威源里的主键列 */
  pkColumn: string;
  /** 要比对的正文列（空 = 只比存在性） */
  bodyColumn: string;
  /** 镜像：Milvus 集合 */
  collection: string;
  /** 镜像里的主键字段 */
  mirrorPkField: string;
  /** 镜像里的正文字段 */
  mirrorBodyField: string;
  /** 租户列（PG 与向量库两边都有） */
  tenantColumn: string;
};

const ITEMS: ReconcileItem[] = [
/*ITEMS*/
];

/** 扫描自己有没有没填的占位 —— 有就别跑。 */
function unfilled(): string[] {
  const out: string[] = [];
  for (const [i, it] of ITEMS.entries()) {
    for (const [k, v] of Object.entries(it)) {
      if (typeof v === 'string' && TODO_RE().test(v)) {
        out.push(`第 ${i + 1} 行（${it.data}）的 ${k}`);
      }
    }
  }
  return out;
}

function sha256(s: string): string {
  return createHash('sha256').update(s).digest('hex');
}

async function main(): Promise<void> {
  const missing = unfilled();
  if (missing.length > 0) {
    console.error('这份对账脚本还没填完，拒绝运行（避免静默当成「没有漂移」）：');
    for (const m of missing) console.error(`  · ${m}`);
    process.exit(1);
  }
  // TODO: 接上仓库自己的 PG 客户端与 Milvus 客户端，按租户枚举后逐项比对：
  //   1. id 集合：PG 有而向量没有 = 丢数据；向量有而 PG 没有 = 多出数据
  //   2. 正文：两边同一 id 的正文哈希不同 = 对不上（索引的是旧正文）
  //   3. 状态：PG 非 active 的行在向量里存在 = 失效向量占位
  // 每项都要按租户分开报 —— 判交付只看生产租户，测试租户是信息不是阻塞。
  console.error('还没接上比对逻辑：把上面的 TODO 换成仓库自己的 PG / Milvus 客户端。');
  process.exit(1);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
"""


def _baseline_rows(plan: Plan) -> list[dict]:
    """从「数据一致性」节抽出每一行的口径：数据 / 权威源 / 基线快照。

    基线快照路径藏在「一致性怎么验（钉）」列里（形如
    ``scripts/memory-baseline-check.ts`` 基线快照 ``fixtures/baseline.json``）
    ⇒ 这里取**反引号里以 .json 结尾的那个**；抽不出来就留占位，不猜。
    """
    out = []
    for row in plan.consistency_rows:
        if len(row) < 3:
            continue
        data, auth, mirror = row[0], row[1], row[2]
        how = row[3] if len(row) > 3 else ""
        auth_ids = re.findall(r"`([^`]+)`", auth)
        ids = re.findall(r"`([^`]+)`", how)
        json_ids = [i for i in ids if i.endswith(".json")]
        first_auth = auth_ids[0] if auth_ids else ""
        out.append({
            "data": data,
            "auth": auth,
            "mirror": mirror,
            "how": how,
            "pg_table": (first_auth.split(".")[0] if first_auth else ""),
            "pg_column": (first_auth.split(".", 1)[1] if "." in first_auth else ""),
            "baseline_path": (json_ids[0] if json_ids else ""),
        })
    return out


BASELINE_HEADER = """/**
 * 基线对拍脚本 —— 由 attest 的 `req_plan.py baseline` 生成。
 *
 * 生成的依据是计划里的「数据一致性」节：每一行 = 一份数据、它的权威源、
 * 以及一致性怎么验。**这份脚本只比对，不改数据** —— 改数据是另一个动作。
 *
 * 基线对拍与跨存储对账的区别：对账比的是「两个存储之间是否一致」，
 * 对拍比的是「现在这份数据与**当初的基线快照**是否一致」—— 用来发现
 * 悄悄变了但没人说的数据漂移。
 *
 * ⚠️ 填完之前它**拒绝运行**：脚本启动时先扫自己有没有 `<待填` ⇒ 有就打印
 * 缺哪几项并退出非零。宁可红，不可静默绿（attest 的一贯口径）。
 *
 * 填完之后：在 package.json 里加一条（readiness 靠脚本名认出这条车道）：
 *   "eval:baseline:check:memory": "tsx scripts/baseline-memory.ts"
 * ⇒ 下一轮验收会真跑它。
 */

const TODO_RE = () => /<待填[^>]*>/;

/** 本模块要对拍的数据（来自计划的「数据一致性」节）。 */
type BaselineItem = {
  /** 这份数据是什么 */
  data: string;
  /** 权威源：PG 表 */
  pgTable: string;
  /** 权威源里的主键列 */
  pkColumn: string;
  /** 要比对的正文列（空 = 只比存在性） */
  bodyColumn: string;
  /** 基线快照（JSON）：键 = 主键，值 = 正文 */
  baselinePath: string;
  /** 容差：正文差多少算漂移（数字；0 = 必须完全一致） */
  tolerance: number;
};

const ITEMS: BaselineItem[] = [
/*ITEMS*/
];

/** 扫描自己有没有没填的占位 —— 有就别跑。 */
function unfilled(): string[] {
  const out: string[] = [];
  for (const [i, it] of ITEMS.entries()) {
    for (const [k, v] of Object.entries(it)) {
      if (typeof v === 'string' && TODO_RE().test(v)) {
        out.push(`第 ${i + 1} 行（${it.data}）的 ${k}`);
      }
    }
  }
  return out;
}

function main(): number {
  const missing = unfilled();
  if (missing.length > 0) {
    console.error('这份基线对拍脚本还没填完，拒绝运行（避免静默当成「没有漂移」）：');
    for (const m of missing) console.error(`  · ${m}`);
    process.exit(1);
  }
  // TODO: 接上仓库自己的 PG 客户端与基线快照读取：
  //   1. 读基线快照（baselinePath）：{ 主键: 正文 }
  //   2. 按租户枚举权威源的当前行（pgTable），按主键对齐
  //   3. 正文差异超过容差 ⇒ 报漂移（写清楚是哪条、主键是什么、差多少）
  //   4. 基线里有、现在没有的键 ⇒ 报「基线有现在没了」（删除漂移）
  //   5. 现在有、基线里没有的键 ⇒ 报「新增」（不一定是漂移，但要知道）
  // 每项都按租户分开报 —— 判交付只看生产租户，测试租户是信息不是阻塞。
  console.error('还没接上比对逻辑：把上面的 TODO 换成仓库自己的 PG 客户端与基线快照读取。');
  process.exit(1);
}

main();
"""


def cmd_baseline(args: argparse.Namespace) -> int:
    """按计划的「数据一致性」节生成基线对拍脚本（代建侧）。"""
    root = Path(args.repo).resolve()
    plan_path = Path(args.plan)
    if not plan_path.is_absolute() and not plan_path.exists():
        alt = root / plan_path
        if alt.exists():
            plan_path = alt
    if not plan_path.exists():
        print(f"计划文件不存在: {plan_path}", file=sys.stderr)
        return 1
    plan = parse_plan(plan_path.resolve())
    rows = _baseline_rows(plan)
    if not rows:
        print("计划里没有「数据一致性」节的数据行 ⇒ 没有可生成的对拍口径\n"
              "（只有一种存储的模块不需要对拍）", file=sys.stderr)
        return 1
    items = []
    for r in rows:
        pg_table = r["pg_table"] or "<待填：PG 表名>"
        baseline_path = r["baseline_path"] or "<待填：基线快照路径（JSON）>"
        items.append(
            f"  // 计划原文：权威源 {r['auth']} ｜ 镜像 {r['mirror']} ｜ 怎么验 {r['how']}\n"
            "  {\n"
            f"    data: '{_js_str(r['data'] or '<待填：这份数据是什么>')}',\n"
            f"    pgTable: '{_js_str(pg_table)}',\n"
            "    pkColumn: '<待填：主键列>',\n"
            f"    bodyColumn: '{_js_str(r['pg_column'] or '<待填：要比对的正文列（空=只比存在性）>')}',\n"
            f"    baselinePath: '{_js_str(baseline_path)}',\n"
            "    tolerance: 0,\n"
            "  },"
        )
    out = Path(args.out) if args.out else root / "scripts" / "baseline-memory.ts"
    if out.exists() and not args.force:
        print(f"文件已存在，不覆盖: {out}\n"
              "（要覆盖加 --force；想保留就换 --out 路径）", file=sys.stderr)
        return 1
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(BASELINE_HEADER.replace("/*ITEMS*/", "\n".join(items)),
                   encoding="utf-8")
    print(f"已生成 {out}（{len(rows)} 条对拍口径）")
    print("填完 `<待填>` 之前它拒绝运行（退出码 1）—— 宁可红，不可静默绿。")
    print("填完之后：在 package.json 里加一条 "
          "\"eval:baseline:check:memory\" → \"tsx scripts/baseline-memory.ts\"，"
          "readiness 就会认出这条车道并真跑。")
    return 0


def cmd_reconcile(args: argparse.Namespace) -> int:
    """按计划的「数据一致性」节生成对账脚本（代建侧）。"""
    root = Path(args.repo).resolve()
    plan_path = Path(args.plan)
    if not plan_path.is_absolute() and not plan_path.exists():
        alt = root / plan_path
        if alt.exists():
            plan_path = alt
    if not plan_path.exists():
        print(f"计划文件不存在: {plan_path}", file=sys.stderr)
        return 1
    plan = parse_plan(plan_path.resolve())
    rows = _reconcile_rows(plan)
    if not rows:
        print("计划里没有「数据一致性」节的数据行 ⇒ 没有可生成的对账口径\n"
              "（只有一种存储的模块不需要对账）", file=sys.stderr)
        return 1
    items = []
    for r in rows:
        pg_table = r["pg_table"] or "<待填：PG 表名>"
        collection = r["collection"] or "<待填：Milvus 集合名>"
        items.append(
            f"  // 计划原文：权威源 {r['auth']} ｜ 镜像 {r['mirror']}\n"
            "  {\n"
            f"    data: '{_js_str(r['data'] or '<待填：这份数据是什么>')}',\n"
            f"    pgTable: '{_js_str(pg_table)}',\n"
            "    pkColumn: '<待填：主键列>',\n"
            f"    bodyColumn: '{_js_str(r['pg_column'] or '<待填：要比对的正文列（空=只比存在性）>')}',\n"
            f"    collection: '{_js_str(collection)}',\n"
            "    mirrorPkField: '<待填：镜像里的主键字段>',\n"
            "    mirrorBodyField: '<待填：镜像里的正文字段>',\n"
            "    tenantColumn: '<待填：租户列>',\n"
            "  },"
        )
    out = Path(args.out) if args.out else root / "scripts" / "reconcile-memory.ts"
    if out.exists() and not args.force:
        print(f"文件已存在，不覆盖: {out}\n"
              "（要覆盖加 --force；想保留就换 --out 路径）", file=sys.stderr)
        return 1
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(RECONCILE_HEADER.replace("/*ITEMS*/", "\n".join(items)),
                   encoding="utf-8")
    print(f"已生成 {out}（{len(rows)} 条对账口径）")
    print("填完 `<待填>` 之前它拒绝运行（退出码 1）—— 宁可红，不可静默绿。")
    print("填完之后：把脚本名保持含 'reconcile'，readiness 会自动认出它并真跑。")
    return 0


def cmd_scaffold(args: argparse.Namespace) -> int:
    """从计划生成测试工具包骨架。"""
    root = Path(args.repo).resolve()
    repo = scan_repo(root)
    plan_path = Path(args.plan)
    if not plan_path.is_absolute() and not plan_path.exists():
        alt = root / plan_path          # 找不到就按 --repo 根再试一次
        if alt.exists():
            plan_path = alt
    plan = parse_plan(plan_path.resolve())
    conv = detect_conventions(root, near=plan.modules[0] if plan.modules else "")
    if getattr(args, "framework", None):
        # 显式指定优先于认出来的 —— 认不出时这也是唯一的出口
        conv.js_framework = args.framework
        conv.js_assertion = _JS_FRAMEWORK_ASSERTION.get(
            args.framework, conv.js_assertion)
        conv.js_framework_known = True
    out_dir = Path(args.out_dir).resolve() if args.out_dir else root

    # 从覆盖矩阵取每个流程的落点（文件::用例）
    entries: list[tuple[str, str, str]] = []   # (流程, 文件, 用例)
    for row in plan.matrix_rows:
        if len(row) < 2:
            continue
        flow = row[0]
        for fpath, case in parse_pins(row[1]):
            if case:
                entries.append((flow, fpath, case))

    # 不变式 → 归到它钉的用例所在文件
    inv_by_file: dict[str, list[tuple[str, str]]] = {}
    for ident, desc, pin in plan.invariants:
        for fpath, case in parse_pins(pin):
            if case:
                inv_by_file.setdefault(fpath, []).append((ident, desc))

    # 细节清单里的落点也是"这次要落到哪个文件"（落点列按格式走：v1 第 2 列，
    # v2 第 5 列 —— DP ID 那列不是落点）
    pin_idx = detail_pin_index(plan)
    for row in plan.detail_rows:
        if len(row) <= pin_idx:
            continue
        for fpath, case in parse_pins(row[pin_idx]):
            if case:
                entries.append((row[0], fpath, case))

    by_file: dict[str, list[tuple[str, str]]] = {}
    for flow, fpath, case in entries:
        by_file.setdefault(fpath, []).append((flow, case))

    written: list[str] = []
    into_existing: list[tuple[str, list[str]]] = []

    # 写入前的两道闸：路径越界、语言不支持 —— 有一个不合格就一个都不写。
    blocked: list[Finding] = []
    planned: list[tuple[str, Path, str]] = []   # (钉里的路径, 目标路径, 语言)
    for fpath in sorted(by_file):
        target, reason = _safe_target(out_dir, root, fpath)
        if target is None:
            blocked.append(Finding(
                "error", "写入安全",
                f"钉「{fpath}」{reason}",
                "钉只能是仓库内的相对路径 —— 越界的钉不写盘。",
            ))
            continue
        lang = _LANG_BY_EXT.get(Path(fpath).suffix.lower())
        if lang is None:
            blocked.append(Finding(
                "error", "语言不支持",
                f"钉「{fpath}」的文件类型没有对应的生成风格",
                "支持：" + "、".join(sorted(_LANG_BY_EXT)) + "。",
            ))
            continue
        if lang == "js":
            if not conv.js_files:
                blocked.append(Finding(
                    "error", "语言不确定",
                    f"钉「{fpath}」是 JS/TS，但仓库里没有 JS/TS 代码或测试文件",
                    "认不出该用哪套约定（node:test / vitest / jest）—— "
                    "先把落点改成仓库主语言的文件。",
                ))
                continue
            if not conv.js_framework_known:
                if conv.js_tests:
                    why = ("仓库里的测试文件没写框架 import，"
                           "且从 package.json 推断不出用哪套测试框架"
                           "（node:test / vitest / jest / mocha）")
                else:
                    why = ("仓库里有 JS/TS 代码但没有任何测试文件，"
                           "且从 package.json 推断不出用哪套测试框架"
                           "（node:test / vitest / jest）")
                blocked.append(Finding(
                    "error", "框架推断不出",
                    f"钉「{fpath}」是 JS/TS，但{why}",
                    "先把落点改成仓库主语言的文件，或先确认框架 —— "
                    "猜错框架会生成 runner 根本不收的测试文件"
                    "（看着正常、永远不跑）。",
                ))
                continue
        planned.append((fpath, target, lang))

    if blocked:
        print(f"\n需求测试计划校验 — {plan.path}")
        print(f"仓库：{root}")
        print(f"落点：{len(by_file)} 个\n")
        for f in blocked:
            print(f.render())
        print(f"\n{len(blocked)} 项：{len(blocked)} 个错误 / 0 个提醒")
        print("一个文件都没写 —— 有问题的钉先改掉。")
        return 1

    for fpath, target, _lang in planned:
        cases = by_file[fpath]
        live = repo.root / fpath
        if live.exists():
            # 已有文件：不碰，只列出这次要加的用例
            have = set(test_case_names(repo.root, fpath))
            todo = [c for _, c in cases if not any(
                case_matches(c, [h]) for h in have)]
            if todo:
                into_existing.append((fpath, todo))
            continue
        is_e2e = "e2e" in fpath.split("/")
        group = cases[0][0] if is_e2e else (
            plan.title or "TODO: 按被被测函数分组")
        scaffold_test_file(
            target, conv,
            title=f"{Path(fpath).name} —— （待填：这个维度为什么独立）",
            invariants=inv_by_file.get(fpath, []),
            cases=[(group, c) for _, c in cases],
            is_e2e=is_e2e,
        )
        written.append(fpath)

    langs = sorted({lang for _f, _t, lang in planned})
    conv_text = "；".join(
        f"{lang}：{fw} / {asrt}"
        for lang, (fw, asrt) in ((lg, _conv_for(lg, conv)) for lg in langs)
    ) or "（没有落点）"
    print(f"约定：{conv_text}")
    print(f"夹具层：{conv.helper_dir or '（没扫到）'}")
    if written:
        print(f"\n已生成 {len(written)} 个新测试文件：")
        for f in written:
            print(f"  + {f}")
    if into_existing:
        print(f"\n已有测试文件 {len(into_existing)} 个 —— 往里加用例（不覆盖）：")
        for f, todos in into_existing:
            print(f"  ~ {f}")
            for t in todos:
                print(f"      + it('{_js_str(t)}', ...)")
    if not written and not into_existing:
        if by_file:
            print("\n计划里的用例在既有测试文件里都已经有了 —— 没有要新增的。")
            for f in sorted(by_file):
                print(f"  = {f}")
        else:
            print("\n没有可落地的用例 —— 计划里的矩阵/细节清单还没填落点")
    print("\n下一步：填断言（TODO 处），再跑 req-plan check 核对钉。")
    return 0


# 「数据一致性」与「约束：不引入中间件」这两节**只在跨存储时才生成**。
# 之前不管有没有跨存储都生成 ⇒ 单存储模块的新计划一出生就带着两条红
# （`check` 判"这一节还是模板占位，没填"），而文档写的是"跨存储时才校验两节"。
CROSS_STORE_BLOCK = """
## 数据一致性

产出数据的权威源、镜像路径、对账方式。

| 数据 | 权威源 | 镜像/派生 | 一致性怎么验（钉） |
| --- | --- | --- | --- |
| <待填：数据名> | <待填：唯一权威源> | <待填：派生/镜像，没有就写"无"> | <待填：src/x.test.ts> |

## 约束：不引入中间件

跨存储一致性的解法在本项目里长什么样（**不引入额外的协调组件**）：

<待填：单权威 + 镜像 + 对账 的具体做法>

对账/修复路径的测试落点：<待填：src/x.test.ts>
"""


# 判定点类型 → 「核心逻辑细节」表的维度列。维度列保留既有口径
# （_DETAIL_DIMENSIONS 覆盖判据靠它），映射不到的就用自己的类别名。
_DP_KIND_DIMENSION = {
    "branch": "分支",
    "boundary": "边界",
    "boundary-io": "边界",
    "error-path": "错误路径",
    "drop": "错误路径",
    "resource": "资源上限",
    "state": "状态",
    "concurrency": "并发",
}

# 维度列的排序：既有维度清单在前，机器推导出的新类别在后
_DIMENSION_ORDER = tuple(_DETAIL_DIMENSIONS) + ("分支", "状态")


def _read_text(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def module_source_files(root: Path, modules: list[str]) -> list[str]:
    """目标模块里的生产源码（相对仓库根）—— 测试文件不算。

    判定点推导与「钉是否碰到目标模块」两处共用这一份清单 ——
    两边口径不一样 ⇒ 机器矩阵与计划对不齐 ⇒ 满屏假缺口。
    """
    out: list[str] = []
    exts = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".py"}
    for mod in modules:
        if not _module_inside(root, mod):
            continue          # 模块路径越出仓库根（含软链逃逸）⇒ 不看它
        base = root / mod
        if not base.exists():
            continue
        paths = [base] if base.is_file() else _walk(base, exts)
        for p in paths:
            if re.search(r"\.(test|spec)\.|_test\.go$|\.itest\.", p.name):
                continue
            rel = repo_rel(root, p)
            if rel not in out:
                out.append(rel)
    return sorted(out)


def derive_plan_analysis(root: Path, modules: list[str],
                         prefer: str = "auto") -> DpAnalysis:
    """目标模块的判定点推导 —— 等级/后端/来源如实报（规格 §7.4）。

    规格 §3.2：有 TS AST 后端 ⇒ L3；没有 ⇒ L2 词法；Python ⇒ 标准库 ast。
    """
    return analyze_files(root, module_source_files(root, modules),
                         prefer=prefer)


def derive_plan_points(root: Path, modules: list[str]) -> tuple[list, str]:
    """推导目标模块的判定点 ⇒ (判定点列表, 等级说明)。"""
    an = derive_plan_analysis(root, modules)
    return an.points, an.note or "没有可分析的源文件"


def _dp_desc(root: Path, point) -> str:
    """判定点的一句话描述 —— 取源码那一行，比光一个 kind 好懂。"""
    text = point.detail
    try:
        lines = _read_text(root / point.file).splitlines()
        if 0 < point.line <= len(lines):
            text = lines[point.line - 1].strip()
    except OSError:
        pass
    # 表格按 "|" 分列 —— 竖线不能进单元格（源码里的 || / | 换成全角）
    text = re.sub(r"\s+", " ", text).replace("|", "｜")
    return text[:77] + "…" if len(text) > 78 else text


def _detail_table(root: Path, points: list, required: dict[str, list[str]],
                  keep: dict[tuple, tuple[str, str, str]] | None = None) -> str:
    """生成 6 列「核心逻辑细节」表的行。

    机器填 DP ID 与判定点两列；后三列留空 —— 归属是人的判断，工具不代劳。
    `keep` 是已经填好的归属（DP 键 → 归属/落点/豁免）：重跑 upgrade 时
    别把人填过的擦掉。
    """
    keep = keep or {}
    by_dim: dict[str, list] = {}
    for p in points:
        by_dim.setdefault(_DP_KIND_DIMENSION.get(p.kind, p.kind), []).append(p)
    lines: list[str] = []
    covered: set[str] = set()
    for dim in _DIMENSION_ORDER:
        dps = sorted(by_dim.get(dim, []),
                     key=lambda p: (p.file, p.anchor, p.line))
        if not dps:
            continue
        ev = required.get(dim) or []
        suffix = f"（触发证据：{ev[0]}）" if ev else ""
        for p in dps:
            key = dp_key(p.dp_id)
            inv, pin, waiver = keep.get(key, ("", "", "")) if key else ("", "", "")
            lines.append(
                f"| {dim}{suffix} | {p.dp_id} | {_dp_desc(root, p)} | "
                f"{inv} | {pin} | {waiver} |")
        covered.add(dim)
    # 推导出的维度里没有判定点 ⇒ 给一行空 DP（规格 §4.1 的第四行那种）
    for dim in _DETAIL_DIMENSIONS:
        if dim not in required or dim in covered:
            continue
        ev = required.get(dim) or []
        suffix = f"（触发证据：{ev[0]}）" if ev else ""
        lines.append(
            f"| {dim}{suffix} |  | （该维度不对应任何判定点） |  |  |  |")
    return "\n".join(lines)


def cmd_new(args: argparse.Namespace) -> int:
    root = Path(args.repo).resolve()
    repo = scan_repo(root)
    feature = args.feature
    scope = args.module[0] if args.module else "(整个仓库)"

    modules = args.module or []
    helpers = module_helpers(root, modules)

    reuse_lines = []
    for mod, syms in helpers.items():
        reuse_lines.append(f"### `{mod}`")
        if syms:
            reuse_lines += [f"- `{s}`" for s in syms[:40]]
            if len(syms) > 40:
                reuse_lines.append(f"- …（另有 {len(syms) - 40} 个）")
        else:
            reuse_lines.append("- （没扫到导出符号）")
        reuse_lines.append("")

    lanes = lane_scripts(repo)
    if lanes:
        # 车道太多时按"名字含目标模块"优先，其余截断 —— 刷 100 行没人看
        # 目标模块用路径末段匹配（service/src/orders → orders）
        mod_keys = [m.split("/")[-1] for m in modules if m]
        def rank(k: str) -> tuple[int, int, str]:
            hit = 0 if any(mk and mk in k for mk in mod_keys) else 1
            return (hit, len(k), k)
        mod_hits = sorted((k for k in lanes if rank(k)[0] == 0), key=rank)
        other = sorted((k for k in lanes if rank(k)[0] == 1), key=rank)
        shown = mod_hits[:30] + other[:8]
        lanes_block = "\n".join(
            f"- `{k}` — `{lanes[k][:80]}`" for k in shown
        )
        hidden = len(lanes) - len(shown)
        if hidden > 0:
            lanes_block += f"\n- …（另有 {hidden} 条车道）"
    else:
        lanes_block = "（没扫到测试车道脚本 —— 项目里可能还没有测试）"

    modules = args.module or []
    stores = scan_stores(root, modules)

    # 细节维度按代码特征推导（不是固定清单）
    dims = derive_dimensions(root, modules)
    if len(stores) >= 2:
        dims.setdefault("数据对账", [])
        dims["数据对账"].insert(
            0, f"目标模块同时碰了 {len(stores)} 个存储（"
                + ", ".join(sorted(stores)) + "）")
    if not dims:
        dims = {d: [] for d in _CORE_DIMENSIONS}
    # 判定点由机器推导（规格 §3.2：TS 有 AST 后端走 L3，没有走 L2）
    points, dp_level = derive_plan_points(root, modules)
    detail_block = _detail_table(root, points, dims)
    if stores:
        blocks = []
        for kind, ev in sorted(stores.items()):
            blocks.append(f"### {kind}")
            blocks += [f"- {e}" for e in ev[:6]]
            if len(ev) > 6:
                blocks.append(f"- …（另有 {len(ev) - 6} 条）")
            blocks.append("")
        stores_block = "\n".join(blocks)
    else:
        stores_block = ("（没扫到存储特征 —— 这模块可能只碰内存，"
                        "或者用了别的存储形态）")

    out = Path(args.out) if args.out else root / "docs" / f"{feature}-test-plan.md"
    if out.exists() and not args.force:
        print(f"计划已存在，不覆盖: {out}\n"
              f"（要覆盖加 --force；想保留就换 --out 路径）", file=sys.stderr)
        return 1
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(TEMPLATE.format(
        feature=feature,
        scope=scope,
        modules_line=" ".join(f"`{m}`" for m in modules) or "（未指定）",
        stores_block=stores_block,
        detail_block=detail_block,
        dp_level=dp_level,
        reuse_block="\n".join(reuse_lines) or "（没指定模块）",
        lanes_block=lanes_block,
        # 跨存储才生成「数据一致性」+「约束」两节（单存储模块不需要）
        cross_store_block=(CROSS_STORE_BLOCK if len(stores) >= 2 else ""),
    ), encoding="utf-8")
    print(f"已生成: {out}")
    print(f"下一步：填不变式与矩阵，然后跑\n"
          f"  python3 {_SELF} check {out} --repo {root}")
    return 0


def cmd_upgrade(args: argparse.Namespace) -> int:
    """把旧计划升级成判定点粒度：旧表留成附录，新表机器填好 DP ID 与判定点。

    不自动归属 —— 哪个判定点归哪条不变式是人的判断，工具不代劳。
    """
    root = Path(args.repo).resolve()
    plan_path = Path(args.plan)
    if not plan_path.is_absolute() and not plan_path.exists():
        alt = root / plan_path          # 找不到就按 --repo 根再试一次
        if alt.exists():
            plan_path = alt
    plan_path = plan_path.resolve()
    if not plan_path.exists():
        print(f"计划文件不存在: {plan_path}", file=sys.stderr)
        return 1
    plan = parse_plan(plan_path)
    modules = args.module or plan.modules
    points, dp_level = derive_plan_points(root, modules)
    # 已经填好的归属要留着 —— 重跑 upgrade 别把人填的擦掉
    keep = _existing_assignments(plan)

    # 旧表里写过的维度也要出现在新表里（人自己声明过的，机器推导不出来）
    required = derive_dimensions(root, modules) if modules else {}
    for row in plan.detail_rows:
        dim = row[0].split("（")[0].strip()
        if dim:
            required.setdefault(dim, [])

    lines = plan_path.read_text(encoding="utf-8").splitlines()
    start = None
    for i, ln in enumerate(lines):
        if re.match(r"^#{1,6}\s", ln) and "细节" in ln:
            start = i
            break
    if start is None:
        print("这份计划里没有「核心逻辑细节」节 —— 没什么可升级的",
              file=sys.stderr)
        return 1
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if re.match(r"^#{1,6}\s", lines[j]):
            end = j
            break
    old_body = lines[start + 1:end]
    # 「额外涉及」那一行搬进新表 —— 它是给新表用的声明，不是旧表的遗产
    extra = [ln for ln in old_body if "额外涉及" in ln]
    old_body = [ln for ln in old_body if "额外涉及" not in ln]

    new_section = [
        "## 核心逻辑细节",
        "",
        "每个核心逻辑**判定点**一行 —— 判定点 = 代码里一个真正要选的分支/边界/错误路径。",
        f"**DP ID 与判定点两列是机器推导出来的**（{dp_level}），后三列要人来填：",
        "这条判定点归属哪条不变式、钉到哪个用例、或者写明为什么豁免。",
        "",
        "下面的维度是**按目标模块的代码特征推导**出来的（括号里是触发它的证据）。",
        "每个维度都要有测试落点，或写「不适用」+理由 —— **默默跳过 = 没测到**。",
        "",
        *extra,
        "",
        "| 维度 | DP ID | 判定点 | 归属的不变式 | 落点（文件::用例名） | 豁免理由 |",
        "| --- | --- | --- | --- | --- | --- |",
        *_detail_table(root, points, required, keep).splitlines(),
        "",
        "## 附录：旧的模块级维度表（保留作参考）",
        "",
        *old_body,
    ]
    plan_path.write_text(
        "\n".join(lines[:start] + new_section + lines[end:]) + "\n",
        encoding="utf-8")
    print(f"已升级: {plan_path}")
    print(f"判定点 {len(points)} 个（{dp_level}）—— "
          "DP ID 与判定点两列已填好，后三列请人工补"
          "（可用 req_plan.py assign 分批填）")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    root = Path(args.repo).resolve()
    repo = scan_repo(root)
    plan_path = Path(args.plan)
    if not plan_path.is_absolute() and not plan_path.exists():
        # 计划路径相对当前目录；找不到就按 --repo 根再试一次
        alt = root / plan_path
        if alt.exists():
            plan_path = alt
    plan = parse_plan(plan_path.resolve())
    findings = check_plan(plan, repo)
    findings += check_test_toolkit(plan, repo)

    print(f"\n需求测试计划校验 — {plan.path}")
    print(f"仓库：{root}")
    print(f"不变式 {len(plan.invariants)} 条 / 车道 {len(plan.lanes)} 条 / "
          f"矩阵 {len(plan.matrix_rows)} 行\n")
    if not findings:
        print("无问题。")
        return 0
    for f in findings:
        print(f.render())
    errs = sum(1 for f in findings if f.level == "error")
    print(f"\n{len(findings)} 项：{errs} 个错误 / "
          f"{len(findings) - errs} 个提醒")
    return 1 if errs else 0


# ---------------------------------------------------------------- 判定点归属（assign）

# assign 要写回的三列，按表头前缀匹配（表头允许带括号说明）
_ASSIGN_COL_NAMES = {
    "invariant": ("归属",),
    "pin": ("落点",),
    "waiver": ("豁免",),
}


def _norm_cell(c: str) -> str:
    return re.sub(r"\s+", "", c)


def _assign_col_index(header: list[str], key: str) -> int | None:
    for j, c in enumerate(header):
        n = _norm_cell(c)
        for nm in _ASSIGN_COL_NAMES[key]:
            if n.startswith(nm):
                return j
    return None


def _assign_table(lines: list[str]) -> tuple[int, int, list[int]] | None:
    """判定点表的位置：返回 (表头行号, DP ID 列号, 数据行行号)。

    找法：第一个表头里有「DP ID」列、且有「归属」列的表 —— v1 的模块级
    维度表没有这两列，所以不会认错。
    """
    for i, ln in enumerate(lines):
        if not ln.lstrip().startswith("|"):
            continue
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        dp_idx = next((j for j, c in enumerate(cells)
                       if _norm_cell(c).lower() == "dpid"), None)
        if dp_idx is None:
            continue
        if _assign_col_index(cells, "invariant") is None:
            continue
        rows: list[int] = []
        for j in range(i + 2, len(lines)):     # i+1 是分隔行
            if not lines[j].lstrip().startswith("|"):
                break
            rows.append(j)
        return i, dp_idx, rows
    return None


def _existing_assignments(plan: Plan) -> dict[tuple, tuple[str, str, str]]:
    """计划里已经填好的归属（DP 键 → 归属/落点/豁免）。

    重跑 upgrade 时别把人填的擦掉。
    """
    if detect_plan_format(plan) == PLAN_FORMAT_V1:
        return {}
    out: dict[tuple, tuple[str, str, str]] = {}
    for row in parse_detail_rows(plan):
        key = dp_key(row.dp_id) if row.dp_id.strip() else None
        if key is None:
            continue
        inv = row.invariant.strip()
        pin = row.pin.strip()
        wv = row.waiver.strip()
        if inv or pin or wv:
            out[key] = (inv, pin, wv)
    return out


def _assign_candidates(root: Path, rows: list[dict]) -> dict[str, list[str]]:
    """候选测试文件：同一目录下、源码里提到这个符号的测试文件。"""
    by_dir: dict[str, list[str]] = {}
    for tf in existing_test_files(root):
        by_dir.setdefault(str(Path(tf).parent), []).append(tf)
    cache: dict[str, str] = {}
    out: dict[str, list[str]] = {}
    for r in rows:
        d = str(Path(r["key"][0]).parent)
        sym = r["key"][1]
        hits: list[str] = []
        for tf in by_dir.get(d, []):
            if tf not in cache:
                try:
                    cache[tf] = (root / tf).read_text(
                        encoding="utf-8", errors="replace")
                except OSError:
                    cache[tf] = ""
            if sym and re.search(rf"\b{re.escape(sym)}\b", cache[tf]):
                hits.append(tf)
        out[r["dp_id"]] = hits
    return out


def cmd_assign(args: argparse.Namespace) -> int:
    """判定点归属：拉待填清单（默认）/ 校验并写回答案（--apply）。

    为什么有这条命令：判定点表的三列（归属/落点/豁免）是**人的判断**，
    工具不代劳 —— 但让人手改上千行 markdown 表格既慢又容易错。这条命令把
    「填表」变成一个可分批、可校验、可增量提交的循环：拉清单 → 回答 →
    写回 → check 看缺口还剩几条。
    """
    root = Path(args.repo).resolve()
    plan_path = Path(args.plan)
    if not plan_path.is_absolute() and not plan_path.exists():
        alt = root / plan_path
        if alt.exists():
            plan_path = alt
    if not plan_path.exists():
        print(f"读不了计划文件: {plan_path}", file=sys.stderr)
        return 1
    plan_path = plan_path.resolve()
    plan = parse_plan(plan_path)
    raw = plan_path.read_text(encoding="utf-8")
    trailing_nl = raw.endswith("\n")
    lines = raw.splitlines()

    tbl = _assign_table(lines)
    if tbl is None:
        print("这份计划里没有判定点表（表头没有 DP ID 列）"
              "—— 先跑 req_plan.py upgrade", file=sys.stderr)
        return 1
    head_i, dp_idx, row_ids = tbl
    header = [c.strip() for c in lines[head_i].strip().strip("|").split("|")]
    inv_i = _assign_col_index(header, "invariant")
    pin_i = _assign_col_index(header, "pin")
    wv_i = _assign_col_index(header, "waiver")

    cells_of: dict[int, list[str]] = {}
    rows: list[dict] = []
    for ln_i in row_ids:
        cells = [c.strip() for c in lines[ln_i].strip().strip("|").split("|")]
        if dp_idx >= len(cells) or not cells[dp_idx]:
            continue
        key = dp_key(cells[dp_idx])
        if key is None:
            continue
        cells_of[ln_i] = cells

        def cell(idx: int | None) -> str:
            if idx is None or idx >= len(cells):
                return ""
            return cells[idx]

        rows.append({
            "line_no": ln_i,
            "dp_id": cells[dp_idx],
            "key": key,
            "kind": key[2],
            "high_risk": key[2] in HIGH_RISK_KINDS,
            "dimension": cell(0),
            "code": cell(dp_idx + 1),
            "invariant": cell(inv_i),
            "pin": cell(pin_i),
            "waiver": cell(wv_i),
        })

    if not args.apply:
        pending = [r for r in rows
                   if not (r["invariant"] or r["pin"] or r["waiver"])]
        kinds = {k.strip() for k in (args.kinds or "").split(",") if k.strip()}
        if kinds:
            pending = [r for r in pending if r["kind"] in kinds]
        pending.sort(key=lambda r: (0 if r["high_risk"] else 1,
                                    r["key"][0], r["key"][1]))
        total = len(pending)
        if args.offset:
            pending = pending[args.offset:]
        if args.limit:
            pending = pending[:args.limit]
        cand = _assign_candidates(root, pending)
        meta = {
            "_invariants": [{"id": i, "desc": d}
                            for i, d, _p in plan.invariants],
            "_pending_total": total,
            "_high_risk_kinds": sorted(HIGH_RISK_KINDS),
            "_answer_format": {"dp_id": "原样抄回来",
                               "invariant": "不变式清单里的编号",
                               "pin": "测试文件::用例名",
                               "waiver": "为什么这个判定点不用测"},
        }
        print(json.dumps(meta, ensure_ascii=False))
        for r in pending:
            print(json.dumps({
                "dp_id": r["dp_id"],
                "kind": r["kind"],
                "high_risk": r["high_risk"],
                "dimension": r["dimension"],
                "file": r["key"][0],
                "symbol": r["key"][1],
                "code": r["code"],
                "candidates": cand.get(r["dp_id"], []),
            }, ensure_ascii=False))
        return 0

    # ---- 写回模式
    inv_ids = {ident for ident, _d, _p in plan.invariants}
    answers: list[tuple[dict, str, str, str]] = []
    bad: list[tuple[int, str]] = []
    ans_path = Path(args.apply)
    if not ans_path.is_absolute() and not ans_path.exists():
        alt = root / ans_path
        if alt.exists():
            ans_path = alt
    if not ans_path.exists():
        print(f"读不了答案文件: {ans_path}", file=sys.stderr)
        return 1
    by_key = {r["key"]: r for r in rows}
    for n, line in enumerate(ans_path.read_text(encoding="utf-8").splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            a = json.loads(line)
        except json.JSONDecodeError as e:
            bad.append((n, f"不是合法 JSON: {e}"))
            continue
        if not isinstance(a, dict):
            bad.append((n, "每行得是一个 JSON 对象"))
            continue
        dp_id = str(a.get("dp_id", "")).strip()
        key = dp_key(dp_id)
        if key is None:
            bad.append((n, f"DP ID 不合法: {dp_id!r}"))
            continue
        row = by_key.get(key)
        if row is None:
            bad.append((n, f"计划里没有这个判定点: {dp_id}"))
            continue
        inv = str(a.get("invariant", "")).strip()
        pin = str(a.get("pin", "")).strip()
        waiver = str(a.get("waiver", "")).strip()
        errs: list[str] = []
        if inv and inv not in inv_ids:
            errs.append(f"归属的不变式 {inv!r} 不在不变式清单里")
        if waiver and _vague_waiver(waiver):
            errs.append(f"豁免理由太笼统: {waiver!r}")
        if inv and not pin and not waiver:
            errs.append("认领了不变式就得给落点（文件::用例名）")
        if pin:
            pins = parse_pins(pin)
            if not pins:
                errs.append(f"落点不是文件路径: {pin!r}")
            for fpath, case in pins:
                if not (root / fpath).exists():
                    errs.append(f"落点文件不存在: {fpath}")
                    break
                if case is None:
                    errs.append(f"落点只到文件、没有用例名: {fpath}")
                    break
                names = test_case_names(root, fpath)
                if not names:
                    errs.append(f"落点文件里没解析出用例: {fpath}")
                    break
                if not case_matches(case, names):
                    errs.append(f"落点用例在 {fpath} 里不存在: {case!r}"
                                f"（该文件的用例有：{'; '.join(names[:5])}）")
                    break
        if not errs and not inv and not waiver:
            errs.append("既没给归属也没给豁免理由")
        if errs:
            bad.append((n, "；".join(errs)))
            continue
        answers.append((row, inv, pin, waiver))

    # 写回：只改那三列，别的原样
    by_line: dict[int, tuple[str, str, str]] = {}
    for row, inv, pin, waiver in answers:
        by_line[row["line_no"]] = (inv, pin, waiver)
    for ln_i, (inv, pin, waiver) in by_line.items():
        parts = lines[ln_i].split("|")
        for idx, val in ((inv_i, inv), (pin_i, pin), (wv_i, waiver)):
            if idx is None or idx + 1 >= len(parts) - 1:
                continue
            parts[idx + 1] = f" {val} " if val else "  "
        lines[ln_i] = "|".join(parts)
    plan_path.write_text(
        "\n".join(lines) + ("\n" if trailing_nl else ""), encoding="utf-8")

    # 写完重新算一遍还剩多少高危缺口
    plan2 = parse_plan(plan_path)
    left = 0
    for r in parse_detail_rows(plan2):
        if not r.dp_id.strip():
            continue
        k = dp_key(r.dp_id)
        if k is None or k[2] not in HIGH_RISK_KINDS:
            continue
        if r.invariant.strip() or r.waiver.strip() or _waiver_for(plan2, r.dp_id):
            continue
        left += 1
    print(f"写回 {len(by_line)} 条；拒绝 {len(bad)} 条；高危缺口还剩 {left} 条")
    for n, msg in bad:
        print(f"  ✗ 答案第 {n} 行：{msg}", file=sys.stderr)
    return 1 if bad else 0


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p_new = sub.add_parser("new", help="生成计划骨架")
    p_new.add_argument("feature", help="需求/特性名（文件名用它）")
    p_new.add_argument("--repo", default=".", help="仓库根")
    p_new.add_argument("--module", action="append", default=[],
                       help="目标模块（相对仓库根），可重复")
    p_new.add_argument("--out", default=None, help="输出路径（默认 docs/<feature>-test-plan.md）")
    p_new.add_argument("--force", action="store_true", help="覆盖已存在的计划")
    p_new.set_defaults(func=cmd_new)

    p_sc = sub.add_parser("scaffold", help="从计划生成测试工具包骨架")
    p_sc.add_argument("plan", help="计划文件")
    p_sc.add_argument("--repo", default=".", help="仓库根")
    p_sc.add_argument("--out-dir", default=None, help="生成根目录（默认仓库根）")
    p_sc.add_argument("--framework", default=None,
                      choices=["node:test", "vitest", "jest", "mocha"],
                      help="显式指定 JS 测试框架（默认从仓库认；认不出时用它）")
    p_sc.add_argument("--force", action="store_true", help="覆盖已存在的测试文件")
    p_sc.set_defaults(func=cmd_scaffold)

    p_up = sub.add_parser("upgrade", help="把旧计划升级成判定点粒度")
    p_up.add_argument("plan", help="计划文件")
    p_up.add_argument("--repo", default=".", help="仓库根")
    p_up.add_argument("--module", action="append", default=[],
                      help="目标模块（相对仓库根），可重复；默认取计划里写的")
    p_up.set_defaults(func=cmd_upgrade)

    p_rc = sub.add_parser(
        "reconcile", help="按计划的「数据一致性」节生成跨存储对账脚本")
    p_rc.add_argument("plan", help="计划文件")
    p_rc.add_argument("--repo", default=".", help="仓库根")
    p_rc.add_argument("--out", default=None,
                      help="生成路径（默认 <仓库根>/scripts/reconcile-memory.ts）")
    p_rc.add_argument("--force", action="store_true", help="覆盖已存在的文件")
    p_rc.set_defaults(func=cmd_reconcile)

    p_bl = sub.add_parser(
        "baseline", help="按计划的「数据一致性」节生成基线对拍脚本")
    p_bl.add_argument("plan", help="计划文件")
    p_bl.add_argument("--repo", default=".", help="仓库根")
    p_bl.add_argument("--out", default=None,
                      help="生成路径（默认 <仓库根>/scripts/baseline-memory.ts）")
    p_bl.add_argument("--force", action="store_true", help="覆盖已存在的文件")
    p_bl.set_defaults(func=cmd_baseline)

    p_chk = sub.add_parser("check", help="校验计划 vs 代码实况")
    p_chk.add_argument("plan", help="计划文件")
    p_chk.add_argument("--repo", default=".", help="仓库根")
    p_chk.set_defaults(func=cmd_check)

    p_asg = sub.add_parser(
        "assign",
        help="判定点归属：拉待填清单 / 校验并写回答案（v2 计划）")
    p_asg.add_argument("plan", help="计划文件")
    p_asg.add_argument("--repo", default=".", help="仓库根")
    p_asg.add_argument("--apply", metavar="answers.jsonl",
                       help="把填好的答案写回计划（校验不过的不写）")
    p_asg.add_argument("--kinds", default="",
                       help="只列这些类型的判定点（drop,error-path,resource,state,…）")
    p_asg.add_argument("--offset", type=int, default=0,
                       help="跳过前 N 条（分批填用）")
    p_asg.add_argument("--limit", type=int, default=0,
                       help="只列 N 条（分批填用）")
    p_asg.set_defaults(func=cmd_assign)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
