#!/usr/bin/env python3
"""需求级测试计划：生成与校验。

用法:
    req-plan new <feature> --repo <目录> [--module <子路径>...]   # 生成计划骨架
    req-plan check <计划文件> --repo <目录>                        # 校验计划 vs 代码实况

设计约束:
  - 只读代码，不改任何东西。
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


def derive_dimensions(root: Path, modules: list[str]) -> dict[str, list[str]]:
    """从目标模块的代码特征推导适用的维度：{维度: [证据]}。

    只扫生产代码（测试文件里的特征不算 —— 那是测试自己造的）。
    """
    code_exts = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".py", ".rs", ".go"}
    found: dict[str, list[str]] = {}
    for mod in modules:
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
    newcap_rows: list[list[str]] = field(default_factory=list)


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
            plan.modules = re.findall(r"`([^`]+)`", line)
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
        if kind is None and len(cells) >= 3 and any(
                any(h in c for h in _CONSISTENCY_HINT) for c in cells):
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
    return plan


# ---------------------------------------------------------------- 校验


# 各框架的用例声明写法。
# 修饰符链（it.skip / test.only / it.each）要认；引号要配对（用例名里
# 可能有单引号：it("doesn't fail", …)）。
_CASE_PATTERNS = (
    re.compile(r"\b(?:it|test)(?:\.\w+)*\s*\(\s*(['\"])(.*?)\1"),
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
    """全仓有没有已存在的同名导出。返回出处（没有就空串）。"""
    if not re.fullmatch(r"[A-Za-z_$][\w$]*", symbol):
        return ""
    exp = re.compile(
        r"^\s*export\s+(?:default\s+)?(?:async\s+)?"
        r"(?:function|const|let|var|class|interface|type|enum)\s+"
        + re.escape(symbol) + r"\b", re.M)
    code_exts = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}
    for p in _walk(root, code_exts):
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if exp.search(text):
            return repo_rel(root, p)
    return ""


# 测试约定探测：生成的东西必须像这个仓库自己写的
_FRAMEWORK_MARKERS = (
    ("node:test", r"from 'node:test'"),
    ("vitest", r"from 'vitest'"),
    ("jest", r"from '@jest|from 'jest"),
)
_ASSERT_MARKERS = (
    ("node:assert", r"node:assert"),
    ("expect", r"\bexpect\("),
)


@dataclass
class Conventions:
    """项目的测试约定。"""
    framework: str = "node:test"
    assertion: str = "node:assert"
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

    for p in _walk(root, {".ts", ".tsx", ".js", ".mjs"}):
        rel = str(p.relative_to(root))
        if "/node_modules/" in f"/{rel}":
            continue
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
        if "e2e" in parts:
            idx = parts.index("e2e")
            key = "/".join(parts[:idx + 1])
            e2e_dirs[key] = e2e_dirs.get(key, 0) + 1
        else:
            # 单测通常与源码同目录（colocated）—— 记下它，好生成同位置的测试
            key = "/".join(parts[:-1])
            unit_dirs[key] = unit_dirs.get(key, 0) + 1

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
        conv.framework = max(fw, key=lambda k: fw[k])
    if asrt:
        conv.assertion = max(asrt, key=lambda k: asrt[k])
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


def check_plan(plan: Plan, repo: Repo) -> list[Finding]:
    """计划 vs 代码实况。"""
    fs: list[Finding] = []

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
                if case is not None:
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
    if plan.detail_rows:
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
                if not (repo.root / fpath).exists():
                    fs.append(Finding(
                        "error", "核心逻辑细节",
                        f"维度 {dim!r} 的落点不存在: {fpath}",
                        "测试文件还没写，或路径写错了",
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
                f"{data_name!r} 出现了 {seen_data[data_name]} 行",
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
            if not (repo.root / fpath).exists():
                fs.append(Finding(
                    "error", "复用表",
                    f"声称复用的文件不存在: {fpath}",
                    "别引用不存在的东西",
                ))
    return fs


# ---------------------------------------------------------------- 生成


TEMPLATE = """# 测试计划：{feature}

- 范围：{scope}
- 生成方式：机器级 `req-test-plan` skill
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

本项目可用的车道（从 package.json 读出来的，别自己编）：

{lanes_block}

## 覆盖矩阵

每行一个关键流程，列出这个流程的每一环落在哪个测试文件。
**关键流程细节都要有行** —— 只测主干的矩阵不算覆盖。

| 流程/场景 | 测试落点 | 车道 |
| --- | --- | --- |
| <待填：流程名> | <待填：src/x.test.ts> | <待填：单测> |

## 数据一致性

产出数据的权威源、镜像路径、对账方式。**没有跨存储就删掉整节**（留空表会被判红）。

| 数据 | 权威源 | 镜像/派生 | 一致性怎么验（钉） |
| --- | --- | --- | --- |
| <待填：数据名> | <待填：唯一权威源> | <待填：派生/镜像，没有就写"无"> | <待填：src/x.test.ts> |

## 约束：不引入中间件

跨存储一致性的解法在本项目里长什么样（**不引入额外的协调组件**）：

<待填：单权威 + 镜像 + 对账 的具体做法>

对账/修复路径的测试落点：<待填：src/x.test.ts>

## 核心逻辑细节

下面的维度是**按目标模块的代码特征推导**出来的（括号里是触发它的证据）。
每个维度都要有测试落点，或写「不适用」+理由 —— **默默跳过 = 没测到**。

本需求额外涉及：<待填：没有就删掉这行；填了就多几个维度要答>

| 维度 | 落点 / 不适用理由 |
| --- | --- |
{detail_block}

## 新建能力（先论证不能复用）

新写的抽象/工具列这里。**先证明现成的不能复用**，再写。调用点 ≤1 ⇒ 过度设计。

| 能力 | 为什么不复用现成的 | 调用点 |
| --- | --- | --- |
| <待填：符号名> | <待填> | <待填> |

## 数据一致性

<待填：这个需求明确不做的事>
"""


def _case_label(case: str) -> str:
    """用例名 → 可读的 describe/it 名。"""
    return case.strip().strip("`").strip()


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
    """
    if conv.framework == "vitest":
        imp = "import { describe, it } from 'vitest';"
        if conv.assertion == "expect":
            imp += "\nimport { expect } from 'vitest';"
    elif conv.framework == "jest":
        imp = "import { describe, it } from '@jest/globals';"
    else:
        imp = "import assert from 'node:assert';\nimport { describe, it } from 'node:test';"

    lines = ["/**", f" * {title}", " *"]
    if invariants:
        lines.append(" * 这里钉的不变量：")
        for ident, desc in invariants:
            lines.append(f" *   - {ident}: {desc}")
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
        lines.append(f"describe('{group}', () => {{")
        for case in items:
            lines.append(f"  it('{case}', () => {{")
            lines.append("    // TODO: 断言要钉住的东西（期望值 ≠ 输入值）")
            lines.append("  });")
            lines.append("")
        lines.append("});")
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")


def check_test_toolkit(plan: Plan, repo: Repo) -> list[Finding]:
    """测试包的四性检查：严谨 / 逻辑 / 复用 / 扩展。"""
    fs: list[Finding] = []

    # 计划里所有被钉到的测试文件
    tool_files: set[str] = set()
    for row in plan.matrix_rows:
        if len(row) < 2:
            continue
        for fpath, case in parse_pins(row[1]):
            if case:
                tool_files.add(fpath)
    for _ident, _desc, pin in plan.invariants:
        for fpath, case in parse_pins(pin):
            if case:
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

    # 细节清单里的落点也是"这次要落到哪个文件"
    for row in plan.detail_rows:
        if len(row) < 2:
            continue
        for fpath, case in parse_pins(row[1]):
            if case:
                entries.append((row[0], fpath, case))

    by_file: dict[str, list[tuple[str, str]]] = {}
    for flow, fpath, case in entries:
        by_file.setdefault(fpath, []).append((flow, case))

    written: list[str] = []
    into_existing: list[tuple[str, list[str]]] = []

    for fpath, cases in sorted(by_file.items()):
        target = out_dir / fpath
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

    print(f"约定：{conv.framework} / {conv.assertion}")
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
                print(f"      + it('{t}', ...)")
    if not written and not into_existing:
        if by_file:
            print("\n计划里的用例在既有测试文件里都已经有了 —— 没有要新增的。")
            for f in sorted(by_file):
                print(f"  = {f}")
        else:
            print("\n没有可落地的用例 —— 计划里的矩阵/细节清单还没填落点")
    print("\n下一步：填断言（TODO 处），再跑 req-plan check 核对钉。")
    return 0


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
        # 目标模块用路径末段匹配（service/src/agent-memory → agent-memory）
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
    detail_lines = []
    for dim in _DETAIL_DIMENSIONS:
        if dim not in dims:
            continue
        ev = dims[dim][0] if dims[dim] else ""
        suffix = f"（触发证据：{ev}）" if ev else ""
        detail_lines.append(f"| {dim} | <待填：测试文件，或\"不适用：…\">{suffix} |")
    detail_block = "\n".join(detail_lines)
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
        reuse_block="\n".join(reuse_lines) or "（没指定模块）",
        lanes_block=lanes_block,
    ), encoding="utf-8")
    print(f"已生成: {out}")
    print(f"下一步：填不变式与矩阵，然后跑\n"
          f"  python3 {_SELF} check {out} --repo {root}")
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
    p_sc.add_argument("--force", action="store_true", help="覆盖已存在的测试文件")
    p_sc.set_defaults(func=cmd_scaffold)

    p_chk = sub.add_parser("check", help="校验计划 vs 代码实况")
    p_chk.add_argument("plan", help="计划文件")
    p_chk.add_argument("--repo", default=".", help="仓库根")
    p_chk.set_defaults(func=cmd_check)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
