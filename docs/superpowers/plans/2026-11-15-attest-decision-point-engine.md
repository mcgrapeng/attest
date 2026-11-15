# attest 判定点引擎（DPE）实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 让 attest 能逐条判定「需求核心逻辑的每一处判定点有没有被测到、测得住吗」，并用 5 个已知缺陷做回归验收。

**Architecture:** 新增 `decision_points.py`（判定点推导引擎：L3 AST / L2 词法三级降级）、`discriminate.py`（区分力自证编排）。既有 `req_plan.py` 升级为 6 列判定点粒度的计划格式，`mutation_check.py` 扩到 7 类算子并支持「只判一个用例红没红」，`readiness.py` 接三条新判据与三段新 JSON。SKILL.md **不由本车道编辑**，只产出改动清单。

**Tech Stack:** Python 3.11+ **仅标准库**（`ast` / `re` / `json` / `subprocess` / `shutil`）；TS 侧驱动**目标仓库自己的** TypeScript（5.x 经典 API 或 7.x `unstable/` API），attest 本身不联网、不加依赖。

**Spec:** `docs/superpowers/specs/2026-11-15-attest-decision-point-engine-design.md` —— 本计划与它逐条对齐；实现中若发现规格与代码事实冲突，**以代码事实为准并在计划里标注**，不得静默偏离。

## Global Constraints

- **只加 Python 标准库**，不新增任何第三方 Python 依赖；不联网。
- **不改用户仓库**（除 `req_plan.py` 的 `new` / `upgrade` 既有受控写入范围）。
- **不编辑 `skills/attest/SKILL.md`** —— 该文件由集成方统一改；本车道只产出「要改什么」的清单。
- **既有行为不回归**：`skills/attest/scripts/test_*.py` 全绿；`bash fixtures/run_fixtures.sh` 既有 5 个 fixture 期望退出码 0/1/2 不变；14 条既有判据行为不变。
- **唯一声明的行为变更**（P0）：嵌套 `describe` 里的环境错误从「误判为产品缺陷（阻塞）」改成「未实测」。方向是消除假红，**门槛不放宽**。
- 所有 shell 命令前缀 `rtk`；编辑用 `apply_patch`；搜索用 `rg`。
- 单条命令的退出码必须就是判据本身的结果 —— 不用 `| tail`、`|| true`、结尾 `; echo`。
- 提交只带自己的文件（显式 `git add <path>`），不用 `git add -A`。

## Review Focus

下列五类输入/情形规格暗示但没有任务覆盖，是最容易在真实使用中咬人的。每一行都对应一个任务里的一条测试。

1. **旧格式计划（2 列「核心逻辑细节」表）被喂进新判据** —— 必须降为 warn 且**不改变退出码**；现有 5 个黄金 fixture 都不传 `--plan`，必须仍是 0/1/2。→ Task 4
2. **同一函数内有多个同类判定点** —— DP ID 会撞，`ordinal` 必须把它们分开，且 ID 在无关改动后保持稳定。→ Task 1
3. **测试文件用动态 `await import()` 或纯 `import type` 引用目标模块** —— 前者必须算运行时覆盖，后者必须不算；漏判会让「钉不碰目标模块」假红。→ Task 4
4. **变异体编译不过，或被测代码压根没被加载** —— 前者不能算「杀死」，后者不能算「存活」，两者都只能记 `unknown` / `not-applicable`。→ Task 5、Task 6
5. **副本不密闭（pnpm 的 `@lumen/*` 软链回原仓）** —— 变异到原仓等于在测原仓库；只能变异相对路径可达的文件。→ Task 6

---

### Task 0: 修 `_failed_tests_detail` 的嵌套 TAP 解析（P0）

**Files:**
- Modify: `skills/attest/scripts/readiness.py` — `_failed_tests_detail`（现 `:722`）
- Modify: `skills/attest/scripts/test_readiness_runners.py`

**Interfaces:**
- Consumes: 无（既有缺陷修复）
- Produces: `readiness._failed_tests_detail(out: str) -> list[str]` —— 行为变为：返回**失败用例的全路径**（`describe` 嵌套时形如 `outer group > failing case`），套件聚合行被排除。

- [ ] **Step 1: 写失败的测试**

在 `skills/attest/scripts/test_readiness_runners.py` 加两个用例。**测试输入必须包含顶层套件聚合行**
（真实 node:test 输出里就有；缺了它就复现不出假红 —— 现状只匹配得到行首的 `not ok`）：

```python
_NESTED_TAP = (
    "# Subtest: outer group\n"
    "    # Subtest: failing case\n"
    "    not ok 2 - failing case\n"
    "      ---\n"
    "      duration_ms: 1.2\n"
    "      error: 'connect ECONNREFUSED 127.0.0.1:19999'\n"
    "      type: 'test'\n"
    "      ---\n"
    "not ok 1 - outer group\n"
    "  ---\n"
    "  duration_ms: 3.4\n"
    "  error: '1 subtest failed'\n"
    "  type: 'suite'\n"
    "  ---\n"
)


def test_nested_failure_reports_full_path():
    assert readiness._failed_tests_detail(_NESTED_TAP) == [
        "outer group > failing case | connect ECONNREFUSED 127.0.0.1:19999"
    ]


def test_nested_env_error_is_not_blocking():
    # 嵌套在 describe 里的环境错误 ⇒ 未实测，不阻塞
    fails, setup = readiness._classify_run(1, _NESTED_TAP, "unit")
    assert fails == []
    assert setup and "环境没就绪" in setup[0]
```

- [ ] **Step 2: 跑测试确认红**

Run: `python3 skills/attest/scripts/test_readiness_runners.py`
Expected: FAIL —— 现状 `_failed_tests_detail` 只匹配得到行首的顶层聚合行，返回
`['outer group | 1 subtest failed']`（**套件名 + 套件的错误文本**，不含真实用例名）⇒
`_ENV_ERROR_RE` 匹配不到 ECONNREFUSED ⇒ 判成「产品缺陷（阻塞）」⇒ 假红。

- [ ] **Step 3: 实现修复**

改 `_failed_tests_detail`：

1. 用 `# Subtest: <名字>` 行维护一个**缩进栈**（缩进 = 该行前导空格数），重建每个 `not ok` 行的**全路径**（`>` 连接）；
2. 匹配 `not ok` 时**先 `strip()`**（去掉缩进），再匹配 `^not ok \d+ - (.+)$`；
3. 紧随其后的 YAML 块里若出现 `type: 'suite'` ⇒ 这一行是**套件聚合行**，跳过；
4. 仍要抓紧随其后的 `error:` 行作为 detail（现有行为保留）。

缩进栈的实现口径：`# Subtest:` 行的缩进 `i` ⇒ 栈里所有缩进 `>= i` 的项被弹出，然后压入该名字；`not ok` 行的缩进 `j` ⇒ 路径 = 栈里缩进 `< j` 的那些名字（按缩进升序）+ 该行标题。

- [ ] **Step 4: 跑测试确认绿**

Run: `python3 skills/attest/scripts/test_readiness_runners.py`
Expected: PASS（含既有用例全部绿 —— 既有 `test_env_error_not_blocking` 用的是行首平铺格式，去缩进后仍匹配）。

- [ ] **Step 5: 跑全部既有回归**

Run: `for t in skills/attest/scripts/test_*.py; do python3 "$t" || { echo "FAIL $t"; exit 1; }; done`
Expected: 全绿。**已知例外**：`test_req_plan_r4.py` 的「不可读目录不崩」在本机会红 —— 那是机器级 `~/.agents/skills/ai-testing-system/scripts/testkit_audit.py:106` 的 `PermissionError`，CI 上该工具缺席会跳过 ⇒ **不是本次回归**，记录不改。

- [ ] **Step 6: 提交**

```bash
git add skills/attest/scripts/readiness.py skills/attest/scripts/test_readiness_runners.py
git commit -m "fix(readiness): 嵌套 TAP 用例名能报出全路径，环境错误不再误判成产品缺陷"
```

---

### Task 1: DP 核心数据模型 + ID + L2 词法推导（P1a）

**Files:**
- Create: `skills/attest/scripts/decision_points.py`
- Create: `skills/attest/scripts/test_decision_points.py`

**Interfaces:**
- Consumes: 无
- Produces（后续任务全部依赖这些名字）：
  - `decision_points.DP_KINDS: tuple[str, ...]` —— 8 类，顺序固定：`("branch", "boundary", "error-path", "resource", "state", "drop", "boundary-io", "concurrency")`
  - `decision_points.HIGH_RISK_KINDS: frozenset[str]` —— `{"error-path", "resource", "drop", "state"}`
  - `decision_points.RESOURCE_VERBS / IO_WORDS / STATE_WORDS / CONCURRENCY_WORDS: frozenset[str]` —— 附录 A 的初始词表
  - `decision_points.DecisionPoint` — dataclass，字段：`file: str`（仓库相对 posix 路径）、`anchor: str`（`"<toplevel>"` 表示不在任何具名函数内）、`kind: str`、`line: int`（1-based，仅报告用）、`detail: str`（一句人话）、`ordinal: int`（组内序号，从 1 起）、`dp_id: str`（由 `DpAnalysis.assign_ids()` 填）
  - `decision_points.DpAnalysis` — dataclass，字段：`level: str`（`"L3"` / `"L2"` / `"L1"`）、`backend: str`（`"B1-ts5-classic"` / `"B2-ts7-project"` / `"B3-lexical"`）、`backend_source: str`（`"repo-local"` / `"global-npm"` / `"none"`）、`note: str`、`points: list[DecisionPoint]`；方法 `assign_ids() -> None`、`by_id() -> dict[str, DecisionPoint]`、`by_kind() -> dict[str, list[DecisionPoint]]`
  - `decision_points.normalize_dp_id(raw: str) -> str` —— `#` 分段、去空白、kind 小写；ordinal 可选
  - `decision_points.dp_key(raw: str) -> tuple[str, str, str, int] | None` —— 解析成可比键 `(file, anchor, kind, ordinal)`；ordinal 缺省为 1；不合法返回 `None`。**合法性判据 = 规格 §4.3 的正则 `^[^#]+#[^#]+#[a-z-]+(#\d+)?$`**（归一化后匹配；`file` 段可含 `/`、`.`、`-`）
  - `decision_points.lexical_analyze(file: str, source: str) -> list[DecisionPoint]` —— L2 词法推导（不依赖任何后端）
  - `decision_points._anchor_at(lines: list[str], lineno: int) -> str` —— L2 下取「最近的具名外层函数」

- [ ] **Step 1: 写失败的测试**

`skills/attest/scripts/test_decision_points.py`（stdlib `unittest`，与仓库现有测试脚本同风格）：

```python
def test_dp_id_omits_ordinal_when_unique():
    dp = decision_points.DecisionPoint(
        file="src/a.ts", anchor="insertRow", kind="drop",
        line=10, detail="filters rows", ordinal=1)
    dp.dp_id = "src/a.ts#insertRow#drop"
    self.assertEqual(dp.dp_id, "src/a.ts#insertRow#drop")
```

```python
def test_assign_ids_numbers_same_group():
    pts = [DecisionPoint("src/a.ts", "f", "branch", 3, "", 1),
           DecisionPoint("src/a.ts", "f", "branch", 9, "", 2),
           DecisionPoint("src/a.ts", "g", "drop", 20, "", 1)]
    an = DpAnalysis(level="L2", backend="B3-lexical", backend_source="none",
                    note="", points=pts)
    an.assign_ids()
    self.assertEqual([p.dp_id for p in an.points],
                     ["src/a.ts#f#branch#1", "src/a.ts#f#branch#2",
                      "src/a.ts#g#drop"])
```

```python
def test_dp_key_treats_ordinal_one_as_default():
    self.assertEqual(dp_key("src/a.ts#f#drop"),
                     dp_key("src/a.ts#f#drop#1"))
    self.assertIsNone(dp_key("not-a-dp-id"))


def test_dp_key_accepts_spec_regex_forms():
    # 规格 §4.3 的正则：file 段可含 / . -，kind 段小写连字符
    self.assertIsNotNone(dp_key("src/agent-memory/x.ts#insertRow#drop"))
    self.assertIsNotNone(dp_key("src/a-b.ts#close#error-path"))
    self.assertIsNotNone(dp_key("src/a.ts#f#drop#2"))
```

```python
def test_lexical_finds_drop_and_resource():
    src = ("export function insertRow(rows) {\n"
           "  const kept = rows.filter((row) => row.id !== '');\n"
           "  client.close();\n"
           "}\n")
    pts = lexical_analyze("src/a.ts", src)
    kinds = {(p.anchor, p.kind) for p in pts}
    self.assertIn(("insertRow", "drop"), kinds)
    self.assertIn(("insertRow", "resource"), kinds)
```

```python
def test_lexical_ignores_strings_and_comments():
    src = ("export function f() {\n"
           "  // client.close();\n"
           "  const s = 'client.close()';\n"
           "  if (a >= 1) { return b; }\n"
           "}\n")
    pts = lexical_analyze("src/a.ts", src)
    self.assertEqual([p.kind for p in pts], ["boundary"])
```

```python
def test_anchor_falls_back_to_toplevel():
    src = "const x = client.close();\n"
    pts = lexical_analyze("src/a.ts", src)
    self.assertEqual(pts[0].anchor, "<toplevel>")
```

- [ ] **Step 2: 跑测试确认红**

Run: `python3 skills/attest/scripts/test_decision_points.py`
Expected: FAIL —— `ModuleNotFoundError: No module named 'decision_points'`（同目录 import，用 `sys.path.insert(0, str(Path(__file__).resolve().parent))`）。

- [ ] **Step 3: 实现 `decision_points.py` 的核心部分**

文件头写清三件事：本模块**只判定不代写期望值**；DP ID **不含行号**；L2 是启发式（有误报漏报）。

实现要点：

- `DecisionPoint` 用 `@dataclass`；`dp_id` 字段默认 `""`。
- `assign_ids()`：按 `(file, anchor, kind)` 分组 → 组内按 `line` 排序、重编 `ordinal`（1 起）→ 组大小为 1 时 `dp_id = f"{file}#{anchor}#{kind}"`，否则 `f"{file}#{anchor}#{kind}#{ordinal}"`。
- `dp_key()`：用 `#` 切分；`len(parts) < 3` ⇒ `None`；`ordinal` 段存在且是纯数字才用，否则 1。
- `lexical_analyze()`：
  - 先用 `mutation_check._strip_js_code` 之类的**等效**逻辑剥掉字符串与注释（**保持偏移量不变**），但本模块**不 import mutation_check**（避免循环依赖）⇒ 自己实现一份 `_strip_js_code`，并在注释里写明「与 mutation_check._strip_js_code 同口径，改一处要改两处」。
  - 逐行扫描，用正则找：`if`/`else if`/`switch`/`case`/`? :` ⇒ `branch`；比较运算符 ⇒ `boundary`；`throw`/`raise`/`catch` ⇒ `error-path`；调用名命中资源动词 ⇒ `resource`；赋值目标命中状态词 ⇒ `state`；`.filter(`/`.reject(`/`.slice(` ⇒ `drop`；调用名命中 I/O 词表 ⇒ `boundary-io`；`await`/`Promise.all`/并发词 ⇒ `concurrency`。
  - **整词匹配**：callee 是标识符就整词切；是成员表达式取属性名。变体归一化：`snake_case` / `kebab-case` / `camelCase` 都归一到小写无分隔再比。
  - `anchor` 由 `_anchor_at()` 取：从该行往上找最近的 `function NAME` / `const NAME =` / `class NAME` / `NAME(...) {` / `NAME: (` 形态；找不到 ⇒ `<toplevel>`。
- 词表按附录 A 抄；**`all` 不得进并发词表**（词表要按真实标识符形态写）。

- [ ] **Step 4: 跑测试确认绿**

Run: `python3 skills/attest/scripts/test_decision_points.py`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add skills/attest/scripts/decision_points.py skills/attest/scripts/test_decision_points.py
git commit -m "feat(dpe): 判定点数据模型、DP ID 规则与 L2 词法推导"
```

---

### Task 2: TS AST 后端探测 + L3 推导（P1b）

**Files:**
- Modify: `skills/attest/scripts/decision_points.py`
- Modify: `skills/attest/scripts/test_decision_points.py`

**Interfaces:**
- Consumes: Task 1 的 `DecisionPoint` / `DpAnalysis` / 词表
- Produces:
  - `decision_points.TsBackend` — dataclass：`kind: str`（`"B1"` / `"B2"`）、`label: str`、`source: str`（`"repo-local"` / `"global-npm"`）、`package_dir: str`（typescript 包目录绝对路径）
  - `decision_points.detect_backend(root: Path, prefer: str = "auto") -> TsBackend | None` —— 按 B1 → B2 顺序探测，**必须真跑一遍解析合成代码**才算可用
  - `decision_points.ast_analyze(root: Path, files: list[str], backend: TsBackend | None) -> DpAnalysis` —— 驱动后端产出 L3 的 DP
  - `decision_points._probe_script() -> str` —— 生成探测用的 `.mjs` 源码

- [ ] **Step 1: 写失败的测试**

```python
def test_detect_backend_returns_none_without_typescript():
    # 空临时目录 ⇒ 没有 TS ⇒ None
    with tempfile.TemporaryDirectory() as td:
        self.assertIsNone(detect_backend(Path(td)))


def test_l3_finds_more_kinds_than_l2_on_same_file():
    src = ("export function f(x: number) {\n"
           "  if (x >= 1) { throw new Error('e'); }\n"
           "  return x;\n"
           "}\n")
    l2 = {p.kind for p in lexical_analyze("src/a.ts", src)}
    self.assertIn("boundary", l2)   # L2 也能看见
```

（TS 后端**不可用时该用例跳过**（`skipUnless`）；可用时额外断言 L3 的 DP 里有 `boundary` 且 `anchor == "f"`。）

```python
def test_cross_backend_l2_subset_of_l3():
    """L2 ⊆ L3（按 file+kind）—— L2 的假阳性必须被 L3 覆盖。"""
    # 只在 TS 后端可用时跑；对 L2 报出的每个 (file, kind)，L3 必须有同类的 DP
```

- [ ] **Step 2: 跑测试确认红**

Run: `python3 skills/attest/scripts/test_decision_points.py`
Expected: FAIL —— `detect_backend` 不存在。

- [ ] **Step 3: 实现探测链**

1. **候选包收集**：
   - 仓库内：递归找 `node_modules/typescript/package.json`（**跟随软链**，`Path.resolve()`），跳过 `SKIP_DIRS`；
   - 全局：`npm root -g` 的输出 + `/typescript`（**必须把 `npm root -g` 加进子进程的 `NODE_PATH`**）。
2. **能力探测**：把一段 `.mjs` 写到**临时目录**（不写用户仓库），内容按候选后端分支：
   - B1：`const ts = require(abs); if (!ts.createSourceFile) 退出码 3;` 然后 `ts.createSourceFile("t.ts", "if (x >= 1) { throw new Error('e') }", ts.ScriptTarget.Latest, true)`，检查能遍历到 `IfStatement` ⇒ 打印 `{"ok":true,"kind":"B1"}`。
   - B2：`require(abs + "/unstable/ast")` + `require(abs + "/unstable/sync")`，走 `new API()` → `api.updateSnapshot()` → `snapshot.getDefaultProjectForFile(file)` → `project.program.getSourceFile(file)` → `visitNode` ⇒ 打印 `{"ok":true,"kind":"B2"}`。
   - 任何异常 ⇒ 退出码非 0 ⇒ 该候选不可用。
   - **必须用绝对路径 + `createRequire`** 加载（pnpm 的 `node_modules/typescript` 是软链，相对 `require` 解析不到）。
3. 第一个探测成功的候选胜出；`prefer` 可强制 `"b1"` / `"b2"` / `"l2"`（`"l2"` ⇒ 直接不探测，返回 `None`）。
4. 探测**不能只看版本号或路径存在**。

- [ ] **Step 4: 实现 L3 推导**

生成 `.mjs`：加载后端 → 解析目标文件 → 遍历 AST → 输出 `{"points": [...]}` 的 JSON。`cwd` = 仓库根；**只解析目标模块的文件**（不扫全仓）。

锚点栈口径（B1 经典 API）：

| 节点 | 压入的 anchor |
| --- | --- |
| `FunctionDeclaration` 有名字 | 该名字 |
| `VariableDeclaration` 的 initializer 是 `ArrowFunction` / `FunctionExpression` | 变量名 |
| `ClassDeclaration` | 类名（其方法再压方法名） |
| `MethodDeclaration` / `GetAccessor` / `SetAccessor` | 方法名 |
| `PropertyDeclaration` 的 initializer 是函数 | 属性名 |
| 对象字面量的方法 | 属性名 |
| 其他 | 不压栈 |

取「最近的具名外层函数」⇒ 栈顶；栈空 ⇒ `<toplevel>`。

kind 判定（后端无关 → 实现时映射到各自后端的判定函数）：

| kind | TS AST 节点 |
| --- | --- |
| `branch` | `IfStatement`（每个）、`SwitchStatement` 的每个 `case`/`default`、`ConditionalExpression` |
| `boundary` | `BinaryExpression` 且运算符 ∈ `>` `<` `>=` `<=` `==` `!=` `===` `!==` |
| `error-path` | `ThrowStatement`、`CatchClause` |
| `resource` | `CallExpression` 的 callee 名 ∈ `RESOURCE_VERBS` |
| `state` | 赋值目标（属性名/变量名）∈ `STATE_WORDS` |
| `drop` | `CallExpression` 的 callee 名 ∈ `{filter, reject, slice}` |
| `boundary-io` | `CallExpression` 的 callee 名 ∈ `IO_WORDS` |
| `concurrency` | `AwaitExpression`、`Promise.all/race/allSettled`、callee 名 ∈ `CONCURRENCY_WORDS`、模块级可变变量 |

- [ ] **Step 5: 跑测试确认绿**

Run: `python3 skills/attest/scripts/test_decision_points.py`
Expected: PASS（本机若无 TS，后端相关用例 skip，其余绿）。

- [ ] **Step 6: 提交**

```bash
git add skills/attest/scripts/decision_points.py skills/attest/scripts/test_decision_points.py
git commit -m "feat(dpe): TS AST 后端探测链与 L3 判定点推导"
```

---

### Task 3: Python 侧 L3 推导（P1c）

**Files:**
- Modify: `skills/attest/scripts/decision_points.py`
- Modify: `skills/attest/scripts/test_decision_points.py`

**Interfaces:**
- Consumes: Task 1 的 `DecisionPoint` / `DpAnalysis`
- Produces: `decision_points.python_ast_analyze(file: str, source: str) -> list[DecisionPoint]` —— 用标准库 `ast`，`level` 恒为 `L3`、backend 为 `"py-ast"`

- [ ] **Step 1: 写失败的测试**

```python
def test_python_ast_finds_kinds():
    src = ("def load(rows):\n"
           "    if len(rows) >= 1:\n"
           "        raise ValueError('x')\n"
           "    try:\n"
           "        client.close()\n"
           "    except OSError:\n"
           "        pass\n"
           "    return [r for r in rows if r]\n")
    pts = python_ast_analyze("a.py", src)
    got = {(p.anchor, p.kind) for p in pts}
    self.assertIn(("load", "branch"), got)
    self.assertIn(("load", "boundary"), got)
    self.assertIn(("load", "error-path"), got)
    self.assertIn(("load", "resource"), got)
    self.assertIn(("load", "drop"), got)
```

```python
def test_python_ast_anchor_is_function_name():
    pts = python_ast_analyze("a.py", "def f():\n    return client.close()\n")
    self.assertEqual(pts[0].anchor, "f")
```

- [ ] **Step 2: 跑测试确认红**

Run: `python3 skills/attest/scripts/test_decision_points.py`
Expected: FAIL —— `python_ast_analyze` 不存在。

- [ ] **Step 3: 实现**

用 `ast.parse`；锚点 = 最近的 `FunctionDef` / `AsyncFunctionDef` / `ClassDef` 名；kind 映射：

| kind | Python 节点 |
| --- | --- |
| `branch` | `If` / `Match` / `IfExp` |
| `boundary` | `Compare` |
| `error-path` | `Raise` / `ExceptHandler` |
| `resource` | `with` 语句 / 调用名 ∈ `RESOURCE_VERBS` |
| `state` | 属性赋值目标名 ∈ `STATE_WORDS` |
| `drop` | 调用名 ∈ `{filter, reject}` / 列表推导里的 `if` 过滤 |
| `boundary-io` | 调用名 ∈ `IO_WORDS` |
| `concurrency` | `Await` / `asyncio.gather` / 名字 ∈ `CONCURRENCY_WORDS` |

- [ ] **Step 4: 跑测试确认绿**

Run: `python3 skills/attest/scripts/test_decision_points.py`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add skills/attest/scripts/decision_points.py skills/attest/scripts/test_decision_points.py
git commit -m "feat(dpe): Python 标准库 ast 侧的判定点推导"
```

---

### Task 4: 计划格式升级（P2）

**Files:**
- Modify: `skills/attest/scripts/req_plan.py` — `Plan` dataclass（`:481`）、`parse_plan`（`:538`）、`_plan_pins`（`:1211`）、`check_plan` 的核心逻辑细节段（`:1468-1520`）、`cmd_new`（`:2418`）、`main`（`:2542`）
- Modify: `skills/attest/scripts/test_req_plan_r14.py`（或新开 `test_req_plan_r15.py`）

**Interfaces:**
- Consumes: Task 1 的 `decision_points`（`DpAnalysis` / `dp_key` / `DecisionPoint`）
- Produces:
  - `req_plan.Plan.detail_header: list[str]` —— 「核心逻辑细节」表的**表头格**（现在被 `continue` 掉了，要存下来）；字段声明放在 `detail_rows` 之后，用 `field(default_factory=list)`
  - `req_plan.Plan.waivers: dict[str, str]` —— 正文注释里的豁免：`DP ID → 理由`
  - `req_plan.PLAN_FORMAT_V2 = "v2"` / `PLAN_FORMAT_V1 = "v1"`
  - `req_plan.detect_plan_format(plan: Plan) -> str` —— `"v2"` / `"v1"`
  - `req_plan.detail_pin_index(plan: Plan) -> int` —— v2 ⇒ `4`；v1 ⇒ `1`
  - `req_plan.DetailRow` — dataclass：`dim: str`、`dp_id: str`、`dp_desc: str`、`invariant: str`、`pin: str`、`waiver: str`；从 `plan.detail_rows` 按当前格式解析
  - `req_plan.cmd_upgrade(args)` —— 新子命令 `upgrade`

- [ ] **Step 1: 写失败的测试**

```python
def test_detect_format_v1_and_v2():
    v1 = Plan(path=Path("p.md"), detail_header=["维度", "落点"],
              detail_rows=[["边界", "src/a.test.ts"]])
    self.assertEqual(detect_plan_format(v1), "v1")
    v2 = Plan(path=Path("p.md"), detail_header=["维度", "DP ID", "判定点",
              "归属的不变式", "落点", "豁免理由"],
              detail_rows=[["错误路径", "src/a.ts#f#drop", "缺 id 报错",
              "I1", "src/a.test.ts::rejects a row", ""]])
    self.assertEqual(detect_plan_format(v2), "v2")
```

```python
def test_format_detected_from_data_row_not_header():
    # 表头没写 DP ID，但数据行第 1 列是可解析的 DP ID ⇒ 仍算 v2
    plan = Plan(path=Path("p.md"), detail_header=["维度", "判定点", "归属", "落点"],
                detail_rows=[["src/a.ts#f#drop", "缺 id 报错", "I1",
                              "src/a.test.ts::x"]])
    self.assertEqual(detect_plan_format(plan), "v2")
```

```python
def test_v1_plan_pins_still_read_column_one():
    plan = Plan(path=Path("p.md"), detail_header=["维度", "落点"],
                detail_rows=[["边界", "src/a.test.ts"]])
    self.assertEqual(detail_pin_index(plan), 1)
```

```python
def test_v2_plan_pins_read_column_four():
    plan = Plan(path=Path("p.md"), detail_header=["维度", "DP ID", "判定点",
              "归属的不变式", "落点", "豁免理由"],
              detail_rows=[["错误路径", "src/a.ts#f#drop", "d", "I1",
                            "src/a.test.ts::x", ""]])
    self.assertEqual(detail_pin_index(plan), 4)
```

```python
def test_check_reports_unattributed_high_risk_dp():
    # 高危 DP 既无归属不变式、又无豁免理由 ⇒ 报红
```

```python
def test_check_reports_unknown_invariant_id():
    # 归属的不变式编号在不变式清单里不存在 ⇒ 报红
```

```python
def test_check_reports_dp_file_not_exists():
    # DP ID 里的文件在仓库里不存在 ⇒ 报红
```

```python
def test_check_waiver_requires_reason():
    # 豁免理由非空但没写清豁免的是什么 ⇒ 提醒（warn）
```

```python
def test_html_comment_waiver_is_accepted():
    # 正文注释豁免：<!-- attest:waive src/a.ts#f#drop reason="上游已校验" -->
    # ⇒ 该 DP 不算缺口；理由为空 ⇒ 报红
```

```python
def test_waiver_with_unknown_dp_id_is_reported():
    # 注释豁免了一个机器推导不出的 DP ID ⇒ 报红（豁免被滥用/ID 写错）
```

- [ ] **Step 2: 跑测试确认红**

Run: `python3 skills/attest/scripts/test_req_plan_r15.py`
Expected: FAIL —— `detect_plan_format` 等不存在。

- [ ] **Step 3: 实现**

1. `parse_plan`：把「核心逻辑细节」表的**表头行**存进 `plan.detail_header`（在现 `:666-673` 的 `is_header` 分支里，按 `table_kind == "detail"` 存）。
2. `parse_plan` 另扫**正文注释**里的豁免（规格 §5.3），写进 `plan.waivers`（`DP ID → 理由`）：
   - 正则 `<!--\s*attest:waive\s+(?P<id>[^\s]+)\s+reason="(?P<reason>[^"]*)"\s*-->`；
   - **理由为空 ⇒ 该豁免无效**（当没豁免处理，并报红）；
   - 豁免的 DP ID 若不在机器推导出的 DP 集合里 ⇒ 报红（豁免被滥用或 ID 写错）。
3. `detect_plan_format(plan)`：
   - 无 `detail_rows` ⇒ `"v1"`；
   - `detail_header` 里含 `"DP ID"`（去空白、大小写不敏感）⇒ `"v2"`；
   - 否则看数据行第 1 列：有任一行能被 `dp_key()` 解析 ⇒ `"v2"`；否则 `"v1"`。
   - **不许靠列数猜**。
4. `detail_pin_index(plan)`：v2 ⇒ `4`；v1 ⇒ `1`。
5. `_plan_pins`：核心逻辑细节段改用 `detail_pin_index(plan)` 取列；**DP 列永远不抽钉**。
6. `check_plan` 的核心逻辑细节段（`:1468-1520`）：
   - 读列位置按 `detail_pin_index` 分叉；v1 的既有行为**一字不改**；
   - v2 新增校验：DP ID 非空 ⇒ 必须落在某个已存在的不变式编号；DP ID 的文件必须在仓库里存在；「豁免理由」非空但不含实质内容（只有「不适用」三字）⇒ warn；DP ID 非空且既无归属又无豁免 ⇒ 按 kind 分档（高危 ⇒ error，常规 ⇒ 只计数）；
   - **豁免有两种来源**（表格的「豁免理由」列非空，或正文注释 `attest:waive`）⇒ 两者都算已豁免；
   - v1 计划 ⇒ **一律 warn**，detail 里写「本计划还是旧的模块级维度表；升级到判定点粒度后，这三条判据才会硬判」。
7. `cmd_new`：生成的「核心逻辑细节」表改成 6 列（`| 维度 | DP ID | 判定点 | 归属的不变式 | 落点（文件::用例名） | 豁免理由 |`），DP ID 与判定点两列**留占位**（`req_plan.py new` 不做 DP 推导 —— 推导需要模块的文件列表与 AST 后端，属 `decision_points` 的职责；`new` 只保证**表头与列数**正确，避免引入后端依赖）。**这一处偏离要写进 CHANGELOG**：设计 §4.1 说「`new` 自动填 DP ID 与判定点两列」，实现放在 `upgrade` 子命令里做（`new` 阶段源码还没写完，推导必然不准）。
8. `cmd_upgrade`：读旧计划 ⇒ 保留旧表为附录 ⇒ 追加一张 6 列新表，DP ID 与判定点两列用**机器推导**填好（调用 `decision_points.ast_analyze` / `lexical_analyze`，逐文件），其余三列留 `<待填>`。**不自动归属**（归属是人的判断）。

- [ ] **Step 4: 跑测试确认绿**

Run: `python3 skills/attest/scripts/test_req_plan_r15.py && for t in skills/attest/scripts/test_req_plan*.py; do python3 "$t" || exit 1; done`
Expected: 全绿（`test_req_plan_r4.py` 的已知外部依赖红除外）。

- [ ] **Step 5: 提交**

```bash
git add skills/attest/scripts/req_plan.py skills/attest/scripts/test_req_plan_r15.py
git commit -m "feat(req-plan): 判定点粒度的计划格式、格式识别与 upgrade 子命令"
```

---

### Task 5: 变异算子扩展 + 单用例判定 + 作用域（P3）

**Files:**
- Modify: `skills/attest/scripts/mutation_check.py`
- Create: `skills/attest/scripts/test_mutation_operators.py`

**Interfaces:**
- Consumes: Task 1 的 `decision_points`（L3 才有结构算子）
- Produces:
  - `mutation_check.OPERATORS: tuple[str, ...]` —— `("cmp-negate", "cmp-tighten", "in-to-hasown", "drop-throw", "drop-call", "predicate-negate", "catch-drop")`
  - `mutation_check.L2_OPERATORS: frozenset[str]` —— `{"cmp-negate", "cmp-tighten", "in-to-hasown"}`
  - `mutation_check.L3_OPERATORS: frozenset[str]` —— 其余四个
  - `mutation_check.Mutation` — 现有 dataclass 增字段 `op: str`（算子名）与 `symbol: str | None`（所属具名符号）；`kind` 字段保留（现为中文描述）
  - `mutation_check.find_structural_mutations(path: Path, limit: int = DEFAULT_MAX_PER_FILE) -> list[Mutation]` —— L3 结构算子（`drop-throw` / `drop-call` / `predicate-negate` / `catch-drop`）
  - `mutation_check.find_mutations(path: Path, limit: int = DEFAULT_MAX_PER_FILE) -> list[Mutation]` —— **保持原名与原签名**（既有调用方：`readiness.py:2477` 间接、`test_test_strength.py:83,90` 直接、`mutation_check.py:379` 的 `__main__`）⇒ **不改名**；它就是 L2 词法算子集（`cmp-negate` / `cmp-tighten` / `in-to-hasown`），本次只**扩**一个 `in-to-hasown` 算子并给 `Mutation` 补 `op` 字段
  - `mutation_check.run_mutation_check(..., case_name: str | None = None, scope_symbols: set[str] | None = None, ops: tuple[str, ...] | None = None, parse_check=None) -> dict` —— **新增三个可选关键字参数，既有调用点不改**
  - `mutation_check.match_case(case: str, full_path: str) -> bool` —— **严格匹配**（两条规则，见下）

- [ ] **Step 1: 写失败的测试**

`skills/attest/scripts/test_mutation_operators.py`：

```python
def test_cmp_negate_kills_a_bad_implementation():
    """算子自证：能杀死一个已知的坏实现。"""
    # 造一个 tmp 仓：src/a.ts 里 `if (x === 1) return true;`
    # 测试文件断言 `x === 1` 时返回 false ⇒ 变异后测试红 ⇒ 杀死
```

```python
def test_in_to_hasown_kills_prototype_chain_bug():
    # `x in MAP` → `Object.hasOwn(MAP, x)`；测试断言原型链上的键被拒 ⇒ 变异后红
```

```python
def test_drop_throw_replaces_not_deletes():
    src = "function f() { throw new Error('x'); }\n"
    muts = find_structural_mutations(path_of(src))
    self.assertTrue(muts)
    for m in muts:
        if m.op == "drop-throw":
            self.assertEqual(_apply(src, m), "function f() { void new Error('x'); }\n")
```

```python
def test_drop_call_keeps_binding_defined():
    src = "function f() { const r = g(1); return r; }\n"
    muts = find_structural_mutations(path_of(src))
    for m in muts:
        if m.op == "drop-call":
            out = _apply(src, m)
            self.assertIn("const r = undefined;", out)   # 绑定还在
            self.assertNotIn("undefined;", out.replace("const r = undefined;", ""))
```

```python
def test_predicate_negate_flips_filter():
    src = "function f(rows) { return rows.filter((r) => r.ok); }\n"
    muts = find_structural_mutations(path_of(src))
    for m in muts:
        if m.op == "predicate-negate":
            self.assertIn("!((r) => r.ok)", _apply(src, m))
```

```python
def test_catch_drop_skipped_when_binding_used_later():
    # catch 体里声明的标识符在后续代码里被引用 ⇒ 该变异点不生成
```

```python
def test_match_case_is_strict():
    self.assertTrue(match_case("failing case", "outer group > failing case"))
    self.assertTrue(match_case("outer group > failing case",
                                "outer group > failing case"))
    self.assertFalse(match_case("rejects a row",
                                 "outer group > rejects a row without id"))
```

```python
def test_l2_operators_are_subset_of_all():
    self.assertTrue(L2_OPERATORS < set(OPERATORS))
```

- [ ] **Step 2: 跑测试确认红**

Run: `python3 skills/attest/scripts/test_mutation_operators.py`
Expected: FAIL —— `find_structural_mutations` / `match_case` 不存在。

- [ ] **Step 3: 实现**

1. `match_case(case, full_path)` —— **只有两条规则**：
   - 归一化（折叠空白、去首尾）后**完全相等** ⇒ 命中；
   - 否则 `full_path` 按 `>` 切段、`case` 按 `>` 切段，取 `full_path` 的**末尾 k 段**（k = case 的段数），与 `case` **逐段完全相等** ⇒ 命中；段数不足 ⇒ 不命中。
   - **禁止**「去前缀」「包含」「去标点」等宽松化。
   - **不得复用** `req_plan.case_matches`（那是模糊匹配，会假杀死）。
2. 算子实现：
   - `cmp-negate` / `cmp-tighten`：沿用现有 `_OP_MAP` 口径（在 `find_mutations` 里**扩**，不重写）。
   - `in-to-hasown`：`x in Y` ⇒ `Object.hasOwn(Y, x)`（有界文本替换；Python 侧跳过此算子）。
   - `drop-throw`：`throw E` ⇒ `void E`（**表达式替换**）。
   - `drop-call`：`f(args)` ⇒ `undefined`（**表达式替换**，保留绑定）。
   - `predicate-negate`：`filter(P)` ⇒ `filter(x => !P(x))`。
   - `catch-drop`：`catch (e) { BODY }` ⇒ `catch (e) { }`，**且必须先做声明-使用检查**（体里声明的标识符在块外仍被引用 ⇒ 放弃该变异点）。
3. **结构算子只在 L3 路径实现**（需要语句/表达式边界）；L2 只有三个词法算子。
4. `run_mutation_check` 新增三个可选参数：
   - `case_name`：`classify` 回调仍复用 `_classify_run` 的三分口径，但**判定改成「用例 `case_name` 是否在失败列表里」** ⇒ 用 `match_case` 比对。
   - `scope_symbols`：只在这些具名符号内找变异点（§5 的可达闭包）。
   - `ops`：限定算子集合。
   - `parse_check`：一个 `(path, text) -> bool` 回调；应用变异后先跑它，**解析不过 ⇒ 该点记 `unknown`**，不跑测试、不计入分母。
5. **既有调用点不改**：`readiness.py:2477` 的调用仍按原样工作（既有 `test_test_strength.py` 不得回归）。

- [ ] **Step 4: 跑测试确认绿**

Run: `python3 skills/attest/scripts/test_mutation_operators.py && python3 skills/attest/scripts/test_test_strength.py`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add skills/attest/scripts/mutation_check.py skills/attest/scripts/test_mutation_operators.py
git commit -m "feat(mutation): 7 类变异算子、严格用例匹配与作用域限定"
```

---

### Task 6: 区分力自证（DPSP，P4）

**Files:**
- Create: `skills/attest/scripts/discriminate.py`
- Create: `skills/attest/scripts/test_discriminate.py`

**Interfaces:**
- Consumes: Task 4 的 `req_plan.parse_pins` / `test_case_names`；Task 5 的 `mutation_check`；Task 1 的 `decision_points`
- Produces:
  - `discriminate.ImportRef` — dataclass：`spec: str`（原始 specifier）、`target: str | None`（解析出的仓库相对路径）、`symbols: tuple[str, ...]`、`namespace_level: bool`（无解构 ⇒ 命名空间级）、`type_only: bool`（纯类型 import ⇒ 不算运行时覆盖）、`relative: bool`（是否相对路径 ⇒ 决定副本是否密闭）
  - `discriminate.parse_imports(source: str, file: str, root: Path) -> list[ImportRef]` —— **运行时 import 解析器**（静态具名 / 动态 / 命名空间 / 纯类型四形态）
  - `discriminate.runtime_symbols(refs: list[ImportRef]) -> set[str]` —— 运行时（非纯类型）引用的符号名；命名空间级 ⇒ 该模块的全部导出符号
  - `discriminate.match_case(case: str, full_path: str) -> bool` —— **从 `mutation_check` 导入后原样再导出**（同一个函数对象，**不重写**）
  - `discriminate.Verdict` — dataclass：`status: str`（`"pass"` / `"fail"` / `"untested"` / `"not-applicable"`）、`detail: str`、`evidence: list[str]`、`killed: int`、`survivors: list[dict]`
  - `discriminate.run_dpsp(repo: Path, ws_rel: str, invariants: list[tuple[str, str, str]], runner: list[str], test_rels: list[str], *, source_rels: list[str], timeout: int = 120, max_mutants: int = 6, level: str = "L3", parse_check=None) -> dict` —— 返回 `{"checked": int, "total": int, "killed": int, "not_applicable": int, "untested": int, "survivors": [...], "verdicts": [...], "notes": [...]}`

- [ ] **Step 1: 写失败的测试**

```python
def test_import_parser_finds_dynamic_import():
    src = "const { a, b } = await import('./mem0-engine.ts');\n"
    refs = parse_imports(src, "src/x.test.ts", ROOT)
    self.assertEqual([r.symbols for r in refs], [("a", "b")])


def test_import_parser_marks_type_only():
    src = "import type { EngineHandle } from './mem0-engine.ts';\n"
    refs = parse_imports(src, "src/x.test.ts", ROOT)
    self.assertTrue(refs[0].type_only)
    self.assertEqual(runtime_symbols(refs), set())


def test_import_parser_namespace_level():
    src = "const mod = await import('./mem0-engine.ts');\n"
    refs = parse_imports(src, "src/x.test.ts", ROOT)
    self.assertTrue(refs[0].namespace_level)


def test_mock_module_counts_as_reference():
    """mock.module('./x.ts') 点名了那个模块 ⇒ 算引用（规格 §5.1.1）。"""
    src = "mock.module('./mem0-engine.ts');\n"
    refs = parse_imports(src, "src/x.test.ts", ROOT)
    self.assertTrue(refs)
    self.assertEqual(refs[0].target, "src/mem0-engine.ts")


def test_import_parser_resolves_relative_and_alias():
    # 相对路径 → 仓库相对路径；@/x.ts → 按仓库 tsconfig 的 paths 解析
```

```python
def test_verdict_not_applicable_when_no_mutation_points():
    # 一个纯计算函数（没有比较 / throw / filter）⇒ not-applicable，不是 fail
```

```python
def test_verdict_fail_when_all_mutants_survive():
    # 钉住的用例在所有变异下都绿 ⇒ fail
```

```python
def test_verdict_pass_when_one_mutant_killed():
```

```python
def test_mutant_marker_makes_unloaded_code_not_applicable():
    """被测代码压根没被加载 ⇒ not-applicable，不是「存活」。"""
```

```python
def test_package_reachable_file_is_not_mutated():
    """经包名/别名可达的文件 ⇒ 不变异（副本不密闭）。"""
```

- [ ] **Step 2: 跑测试确认红**

Run: `python3 skills/attest/scripts/test_discriminate.py`
Expected: FAIL —— `discriminate` 模块不存在。

- [ ] **Step 3: 实现**

1. `parse_imports`：处理四种形态 —— 静态具名（**跨行**）、静态默认、静态命名空间、动态 `await import()`（取解构符号；无解构 ⇒ 命名空间级）、纯类型（`import type {…}` / `import { type X }` / `import('…')` 的 type-only 位置）。specifier 三类：相对路径（相对**测试文件所在目录**解析）、路径别名（**读目标仓库 `tsconfig` 的 `paths`**，不硬编码）、裸包名（忽略）。`relative` 标志：specifier 以 `.` 开头 ⇒ `True`。
   - **`mock.module('./x.ts')` 算引用**（规格 §5.1.1：它点名了那个模块）⇒ 产出 `ImportRef(target=解析后的路径, symbols=该模块全部导出符号, namespace_level=True)`。
   - **L3 下**能从 AST 后端直接取 import 形态（后端天然区分 type-only）；**L2 下**用正则，且**保守地把 `import type` 也算成运行时**（宁可多算覆盖，不假红）。
2. `run_dpsp` 主流程（每条不变式）：
   - 解析钉 ⇒ 测试文件 `T` + 用例名 `C`；
   - `T` 的运行时符号集合 `S`（经 `parse_imports` + `runtime_symbols`）⇒ 空 ⇒ 该不变式 **零覆盖** ⇒ 报红（`plan-alignment` 判据用，见 Task 7）；
   - 求 `S*`（相对路径可达的传递闭包）+ 每文件导出的符号 ⇒ 闭包内的 DP；
   - 在闭包内生成变异点（L3 ⇒ 结构算子 + 词法算子；L2 ⇒ 只有词法算子）；
   - **零变异点 ⇒ `not-applicable`**（不计入分母）；
   - 建**一份**副本（复用 `mutation_check._copy_repo` + `_link_deps`），逐个变异、跑完原地还原；
   - 每个变异点：应用 ⇒ **解析校验**（不过 ⇒ `unknown`）⇒ **验变异生效**（追加 `console.error("__ATTEST_MUTANT__:<relpath>")` 标记；标记不在子进程输出里 ⇒ `not-applicable`）⇒ 跑测试 ⇒ 判定**用例 `C` 是否红**（`match_case` 严格匹配）。
   - 四档：`pass`（≥1 个被杀死）/ `fail`（全部存活）/ `untested`（环境没就绪、runner 跑不起来、输出认不出）/ `not-applicable`（零变异点 或 变异未生效 或 文件非相对路径可达）。
3. **只变异相对路径可达的源文件**；经别名/包名可达 ⇒ `not-applicable` 并写明「经包路径可达，副本不密闭」。
4. **计划从原仓库读**（副本里没有 `docs/`，`mutation_check.SKIP_DIRS` 含 `docs`）⇒ 钉、豁免、不变式清单一律从 `repo` 读，**不从副本读**。
5. 预算：`不变式 ≤ 20` ⇒ 全跑；`> 20` ⇒ 抽样。**返回的 dict 必须带 `checked` / `total`**，报告要写明比例。

- [ ] **Step 4: 跑测试确认绿**

Run: `python3 skills/attest/scripts/test_discriminate.py`
Expected: PASS。

- [ ] **Step 5: 提交**

```bash
git add skills/attest/scripts/discriminate.py skills/attest/scripts/test_discriminate.py
git commit -m "feat(dpsp): 钉→符号→变异点→副本→单用例→四档结论的区分力自证"
```

---

### Task 7: 判据接入与输出契约（P5）

**Files:**
- Modify: `skills/attest/scripts/readiness.py` — `NOT_JUDGED`（`:122`）、`run_readiness`（`:3140`）、`main`（`:3371`）
- Modify: `skills/attest/scripts/test_readiness.py`（或新开 `test_readiness_dpe.py`）

**Interfaces:**
- Consumes: Task 4（计划格式）、Task 6（`run_dpsp`）、Task 1（DP 分析）
- Produces:
  - `readiness.check_plan_alignment(m: Module, plan_path: str | None, dp: DpAnalysis | None) -> Check` —— key `plan-alignment`，`evidence_kind="static"`
  - `readiness.check_decision_coverage(m: Module, plan_path: str | None, dp: DpAnalysis | None) -> Check` —— key `decision-coverage`，`evidence_kind="static"`
  - `readiness.check_pin_discriminating(m: Module, plan_path: str | None, dpsp: dict | None) -> Check` —— key `pin-discriminating`，`evidence_kind="executed"`
  - CLI 新增：`--lenient`（store_true）、`--discriminating {auto,off,quick,full}`（默认 `auto`）、`--analysis-backend {auto,b1,b2,l2}`（默认 `auto`）
  - JSON 顶层新增三键：`decision_points`、`alignment`、`discriminating`

- [ ] **Step 1: 写失败的测试**

```python
def test_no_plan_means_new_criteria_skipped_not_fail():
    # readiness 不带 --plan ⇒ 三条新判据 status == "skipped"、evidence_kind == "untested"
    # ⇒ 退出码不受影响（现有 5 个黄金 fixture 的 0/1/2 不变）
```

```python
def test_v1_plan_downgrades_new_criteria_to_warn():
    # 旧格式计划 ⇒ 三条新判据都是 warn，detail 含「升级」
```

```python
def test_lenient_downgrades_fail_to_warn():
    # --lenient ⇒ 三条新判据一律不是 fail
```

```python
def test_l2_level_never_hard_fails():
    """L2（词法回退）下三条新判据一律不硬判 —— 缺能力绝不报绿。"""
```

```python
def test_json_has_three_new_top_level_segments():
    # --json 输出里有 decision_points / alignment / discriminating 三个顶层键
    # 且 decision_points 里带 analysis_level / analysis_backend / analysis_backend_source
```

```python
def test_not_judged_gained_two_entries():
    # NOT_JUDGED 含「并发正确性」与「容量与规模行为」
```

- [ ] **Step 2: 跑测试确认红**

Run: `python3 skills/attest/scripts/test_readiness_dpe.py`
Expected: FAIL —— 新判据函数不存在。

- [ ] **Step 3: 实现**

1. `NOT_JUDGED` 追加两项：「并发正确性」「容量与规模行为」。
2. `run_readiness` 里：
   - 读计划（`args.plan`）⇒ `plan = parse_plan(pp)`；判定格式（`detect_plan_format`）；
   - 推导 DP：调 `decision_points.ast_analyze` / `lexical_analyze`（按 `--analysis-backend` 与后端可用性）⇒ `DpAnalysis`；
   - `plan-alignment`：**双向**对齐 —— 计划里列出的 DP ID 集合 vs 机器推导出的集合：`human_only`（计划有、机器无 ⇒ 报红，因为机器推导的 DP 指向的文件必须存在且该点确实存在）、`machine_only`（机器有、计划无 ⇒ 只列清单，不报红）；另外校验「不变式的钉的运行时符号集合为空 ⇒ 报红」。
   - `decision-coverage`：高危 DP（`HIGH_RISK_KINDS`）既无归属不变式又无豁免 ⇒ **fail**；常规 DP ⇒ 只计数。
   - `pin-discriminating`：调 `discriminate.run_dpsp`；四档映射到 `Check.status`：`pass` ⇒ `pass`；`fail` ⇒ `fail`（条件式阻塞成立时）；`untested` ⇒ `untested`；`not-applicable` ⇒ `skipped`。
   - **条件式阻塞**：`format == "v1"` 或 `--lenient` 或 `analysis_level == "L2"` ⇒ 三条新判据一律降为 `warn`（不 fail）。**旧计划 ⇒ warn 且写明「升级后才硬判」**。
   - 无 `--plan` ⇒ 三条新判据一律 `skipped` + `evidence_kind="untested"`。
3. **输出节**：新增「── 判定点覆盖 ──」小节（总数 / 已归属 / 豁免 / 缺口 / 高危四类分档数 / 高危缺口清单，每行带 DP ID），版式按规格 §7.2 的那段样例。
4. **JSON**：顶层加三段（**字段名逐字按规格 §7.4**）：
   - `decision_points`：`total` / `attributed` / `waived` / `gaps` / `gaps_high_risk`（每项 `id`/`kind`/`file`/`line`/`why`）/ `analysis_level` / `analysis_backend` / `analysis_backend_source` / `analysis_note`；
   - `alignment`：`machine_only`（DP ID 列表）/ `human_only`（DP ID 列表）；
   - `discriminating`：`checked` / `total` / `sample_rate` / `killed` / `not_applicable` / `untested` / `survivors`（每项 `pin`/`test`/`mutants`（`file`/`line`/`op`））/ `budget_note`。
   三段都是**顶层并列键**，不进 `conclusion`。
5. **CLI**：`--lenient` / `--discriminating` / `--analysis-backend`。

- [ ] **Step 4: 跑测试确认绿**

Run: `python3 skills/attest/scripts/test_readiness_dpe.py && for t in skills/attest/scripts/test_readiness*.py; do python3 "$t" || exit 1; done`
Expected: 全绿。

- [ ] **Step 5: 提交**

```bash
git add skills/attest/scripts/readiness.py skills/attest/scripts/test_readiness_dpe.py
git commit -m "feat(readiness): 三条判定点判据、判定点覆盖输出节与 JSON 三段"
```

---

### Task 8: 缺陷集 fixture（P6）

**Files:**
- Create: `fixtures/defect-silent-drop/`（`src/` + `plan.md` + `README.md`）
- Create: `fixtures/defect-shared-client/`
- Create: `fixtures/defect-stale-cache/`
- Create: `fixtures/defect-prototype-class/`
- Create: `fixtures/defect-mixed-subject/`
- Modify: `fixtures/run_fixtures.sh` —— `check()`（`:52-118`）与期望表（`:120-158`）
- Modify: `fixtures/README.md`
- Modify: `.github/workflows/ci.yml` —— `:57-58` 之后加装 TS

**Interfaces:**
- Consumes: Task 7 的 CLI（`--plan`）、Task 1 的 DP ID
- Produces: 五个 fixture 目录，每个含**具名函数**的被测逻辑 + 一份 `plan.md`

- [ ] **Step 1: 写 fixture（每个 fixture 是一个自包含小仓库）**

每个 fixture 的目录结构（以 `defect-silent-drop` 为例）：

```
fixtures/defect-silent-drop/
├── README.md
├── package.json          （无依赖；scripts.test = "node --test"）
├── plan.md               （不变式清单 + 6 列「核心逻辑细节」表）
└── src/
    ├── a.ts              （被测逻辑，具名函数）
    └── a.test.ts         （测试文件）
```

被测逻辑必须满足（决定断言能不能稳定命中）：

- 逻辑放在**具名函数**里（DP ID 的 `anchor` 段取函数名）；
- 判定点落在**词法层能看见**的形态上（`filter` / `close` / `in`）⇒ L2/L3 都能产出；
- **不要**把多个判定点塞进同一行。

各 fixture 的形态与期望：

| fixture | 被测逻辑形态 | 期望报出 |
| --- | --- | --- |
| `defect-silent-drop` | `filter` 掉缺 id/正文的行，不报错 | `drop` 类高危缺口；`-` 钉死 `resource` / `branch` 不出现 |
| `defect-shared-client` | `store.close()` 关掉进程内共享 client | `resource` 类缺口 |
| `defect-stale-cache` | 关闭后未清缓存，缓存握死连接 | `resource` 类缺口 + `pin-discriminating` fail |
| `defect-prototype-class` | `x in MAP` 走原型链放行非法值 | `in-to-hasown` 变异存活 ⇒ 该钉无区分力 |
| `defect-mixed-subject` | 第一人称豁免吞掉整句（含第三方部分） | `branch` 类覆盖缺口 |

每个 `plan.md` 的表头必须是 6 列：`| 维度 | DP ID | 判定点 | 归属的不变式 | 落点（文件::用例名） | 豁免理由 |`。

- [ ] **Step 2: 扩 `run_fixtures.sh` 的 `check()`**

- `check()` 现在固定跑 `python3 "$READYNESS" "$module" --repo "$dest"`（`:66`）⇒ 改成**多接一个「额外参数」位**：`check <名字> <模块路径> <期望退出码> <前置条件> [额外参数...]`，额外参数原样追加到命令尾部。既有 5 个 fixture 的调用**不变**（额外参数缺省为空）。
- 新增前置条件 `have_ts_backend`：探测本机或全局有没有可用的 TypeScript（用 `decision_points.detect_backend` 的同一套探测，但**只查全局 + fixture 目录**）⇒ 没有就跳过（退出码 3）。
- 五个新 fixture 的期望用 `+` / `-` / `?` 断言语言写死，断言串**用不含行号的 DP ID**（如 `+src/a.ts#insertRow#drop`）。

- [ ] **Step 3: 跑 fixture 看红**

Run: `bash fixtures/run_fixtures.sh`
Expected: 既有 5 个全命中；新 5 个**先红**（断言还没写对或功能没到位）⇒ 逐步对齐。

- [ ] **Step 4: 补齐断言直到 5/5 命中**

每个新 fixture 的期望至少包含：
- 一条 `+` 命中该缺陷对应的 DP ID 或类别；
- 两条 `-` 钉死**不该出现**的其他类别（误报为 0）；
- 至少一个 fixture 断言 `pin-discriminating` 的结论（证明**实测类判据能红**）；
- 四档（`pass` / `fail` / `untested` / `not-applicable`）各自至少被一个 fixture 覆盖（§8.3 第 5 条，防「四档退化成一档」）。

- [ ] **Step 5: 更新 `fixtures/README.md`**

加一节「缺陷集 fixture」，列 5 个 fixture 各自的缺陷形态与期望判定。

- [ ] **Step 6: CI 装 TS**

在 `.github/workflows/ci.yml` 的「装测试依赖」之后加：

```yaml
      - name: 装 TS 5.x（缺陷集 fixture 的 L3 后端）
        run: npm install -g typescript@5
```

**只影响 fixture 车道**，不影响 attest 运行时（attest 仍不联网、不加依赖）。

- [ ] **Step 7: 跑 fixture 确认全绿**

Run: `bash fixtures/run_fixtures.sh`
Expected: 10 个 fixture 全命中、0 不符；有 TS 时退出码 0，无 TS 时退出码 3（跳过而非不符）。

- [ ] **Step 8: 提交**

```bash
git add fixtures/defect-silent-drop fixtures/defect-shared-client fixtures/defect-stale-cache fixtures/defect-prototype-class fixtures/defect-mixed-subject fixtures/run_fixtures.sh fixtures/README.md .github/workflows/ci.yml
git commit -m "feat(fixtures): 五个缺陷集 fixture 与 L3 后端前置条件"
```

---

### Task 9: 文档同步与 SKILL.md 改动清单（P7）

**Files:**
- Modify: `README.md`
- Modify: `README.zh-CN.md`
- Modify: `CHANGELOG.md`
- Create: `docs/attest-dpe-skill-md-changes.md`
- Modify: `skills/attest/scripts/test_readme_sync.py`（判据条数 14 → 17）

**Interfaces:**
- Consumes: Task 7 的实际判据与 CLI
- Produces: 交付给集成方的 SKILL.md 改动清单

- [ ] **Step 1: 更新两份 README 的判据表**

`skills/attest/scripts/test_readme_sync.py:89` 硬编码 `expect(code_n == 14, ...)` ⇒ 改成 17。
两份 README 的判据表各加 3 行（`plan-alignment` / `decision-coverage` / `pin-discriminating`），中英各一份，**列名与顺序与代码里的 `Check` 一致**。

- [ ] **Step 2: 补两份 README 的命令行说明**

加 `--lenient` / `--discriminating {auto,off,quick,full}` / `--plan` / `--analysis-backend` 四个开关，以及「判定点覆盖」输出节的样例。

- [ ] **Step 3: 写 CHANGELOG**

加一条 `## [Unreleased]`（或接在现有 `[0.1.0]` 之后），写清：新增三条判据、判定点引擎、L2/L3 分级、条件式阻塞、缺陷集 fixture，以及 **P0 的行为变更**（嵌套环境错误从阻塞改为未实测）。

- [ ] **Step 4: 产出 SKILL.md 改动清单（**不改 SKILL.md**）**

Create `docs/attest-dpe-skill-md-changes.md`，逐条列出集成方要改什么：

1. 判据表 14 → 17 条（新增三条的 key、标题、证据类别、默认状态）；
2. 输出契约新增「判定点覆盖」一节；
3. 命令行新增 `--lenient` / `--discriminating` / `--plan` / `--analysis-backend`；
4. **修掉那句过度声称**：现文案「反向验证能覆盖实测类判据（测试是否绿、覆盖率）」与代码不符（现有 `--self-check` 只有两条**静态**注入：`drop-test` ⇒ `uncovered-exports`、`inject-sleep` ⇒ `hygiene`；实测类判据从未被证明能红）⇒ 改为「反向验证覆盖**静态**判据；实测类判据由缺陷集 fixture 证明」；
5. 「本工具不判定」清单新增两项（并发正确性 / 容量与规模行为）；
6. 计划格式升级说明（旧格式 ⇒ 三条新判据降 warn）。

- [ ] **Step 5: 跑 README 同步测试**

Run: `python3 skills/attest/scripts/test_readme_sync.py`
Expected: PASS（判据条数 17 ⇒ 中英 README 各 17 行）。

- [ ] **Step 6: 提交**

```bash
git add README.md README.zh-CN.md CHANGELOG.md docs/attest-dpe-skill-md-changes.md skills/attest/scripts/test_readme_sync.py
git commit -m "docs: 判定点引擎的 README/CHANGELOG 同步与 SKILL.md 改动清单"
```

---

### Task 10: 全量验收复测

**Files:** 无（只跑命令）

**Interfaces:**
- Consumes: Task 0–9 的全部产出
- Produces: 一份可复现的验收结论

- [ ] **Step 1: 跑全部回归脚本**

```bash
for t in skills/attest/scripts/test_*.py; do python3 "$t" || { echo "FAIL $t"; exit 1; }; done
```

Expected: 全绿（`test_req_plan_r4.py` 的外部工具 `PermissionError` 除外 —— 那是机器级 `ai-testing-system` 工具的缺陷，CI 上会跳过 ⇒ 不是本次回归；**报告给用户，不改 attest 的断言**）。

- [ ] **Step 2: 跑黄金 fixture**

```bash
bash fixtures/run_fixtures.sh
```

Expected: 10 个 fixture 全命中、0 不符。

- [ ] **Step 3: 跑 evals 与打包冒烟**

```bash
python3 evals/run_evals.py
python3 package.py --check
```

Expected: 全绿。

- [ ] **Step 4: 真实模块人工核对**

在 `service/src/agent-memory` 上跑一次新判据，人工核对报出的缺口是否与 OCR 那 5 处对应：

```bash
python3 /Users/zhangpeng/workspace/liaohe/attest/skills/attest/scripts/readiness.py \
  src/agent-memory --repo /private/tmp/lumen-pr-wt/service --plan <计划文件> --json
```

**这一步不进 CI**（依赖真实 PG/Milvus）。**不自动化**。

- [ ] **Step 5: 变更自证（每个新判据要能红）**

对每条新判据做一次「注入缺陷 ⇒ 判据翻红 ⇒ 撤掉 ⇒ 判据回绿」的往返验证，把结果写进验收记录。**只做真阳性不验假阳性是不够的** ⇒ 每条还要验一次「注入无关缺陷 ⇒ 该判据**不**翻红」。

- [ ] **Step 6: 提交验收记录**

```bash
git add docs/attest-dpe-acceptance-2026-11-15.md
git commit -m "docs: 判定点引擎的验收复测记录"
```

---

## 附：与设计规格的偏离登记（实现时若发现，逐条追加到本节）

| # | 设计说 | 实现改成 | 为什么 | 影响 |
| --- | --- | --- | --- | --- |
| 1 | §4.1「`req_plan.py new` 自动填 DP ID 与判定点两列」 | `new` 只产出 6 列表头；DP 列由 `upgrade` 子命令填 | `new` 阶段源码可能还没写完，推导必然不准；`new` 不该依赖 AST 后端 | 计划生成的用户要多跑一次 `upgrade` |
