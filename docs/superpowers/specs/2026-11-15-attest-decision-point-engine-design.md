# attest 判定点引擎设计（DPE）

- 日期：2026-11-15
- 状态：待评审
- 作者：与 zhangpeng 的三问三答（判定来源 / 验收基准 / 落地节奏）后成文
- 前置：`docs/testing-philosophy-review-v2.md`（哲学门槛，只读）、`docs/development-brief.md`（G001–G005，只读）

---

## 0. 为什么做这件事

### 0.1 触发事件

2026-11-15，对 `service/src/agent-memory/` 做 OCR 全量评审（55 个文件，324 条 findings）。
OCR 报出 5 处真缺陷，每处都配了回归钉：

1. 向量镜像静默丢行（`mem0-vector-store.ts`：落不了主键/正文的行被 filter 掉，mem0 把 insert 成功当成「已写入」）
2. 共享 Milvus client 被 `store.close()` 关掉（`vector-store-milvus.ts`：client 是进程内单例，所有租户共用）
3. 引擎缓存握着已关闭的 client（`mem0-engine.ts`：`closeMemoryEngine` 没清 `engineCache`）
4. 分类比较走原型链（`classification.ts`：`'constructor' in SEVERITY` 为真）
5. 「我是X」豁免吞掉整句（`slots.ts`：混合句的第三方部分被整句豁免）

同一天，attest 对这个模块的交付就绪判定是**零阻塞**。

### 0.2 这不是误判

5 处缺陷都做过变异自证（回退生产改动 ⇒ 新断言红 ⇒ 恢复 ⇒ 全绿）。它们是真的。
所以问题不是「attest 判错了」，而是「attest 对这 5 处完全沉默，而它的绿被读成了覆盖」。

### 0.3 三条根因（逐条核到代码）

| 根因 | 机制 | 证据 |
| --- | --- | --- |
| R-A 清单完整性没人管 | `check` 只验「不变式 → 钉 → 用例」的闭合，不验「需求 → 不变式」的完整性。缺陷 2/3/4 在 20 条不变式里**根本没有对应条目** | `docs/agent-memory-delivery-test-plan.md` 全文无「分类 / 关闭 / 缓存 / 生命周期」；`req_plan.py` 的 `check` 无此校验 |
| R-B 变异算子只看比较运算符 | `mutation_check.py` 的算子集只有 `== != === !== >= <=` 六种。缺陷 1/2/3/5 都是**结构性**缺陷（filter→throw、语句删除、正则改写），不在算子射程内 | `scripts/mutation_check.py:47-54`（`_OP_MAP`）、`:60`（`_OP_RE`） |
| R-C 变异「杀死」答的不是这个问题 | 变异杀死 = 套件对某个改动敏感；它从不回答「当前生产代码对不对」。缺陷 1 处把 `!==` 变异成 `===` 会被现有测试杀死 ⇒ 报「测试能发现缺陷 ✓」，而缺陷当时是活的 | `skills/attest/scripts/mutation_check.py` 模块文档字符串（讲的是「测试能发现缺陷」这条判据）；`run_mutation_check` 的 `killed`/`unknown`/`survivors` 三分口径（`:252-271`） |

外加一条：**缺陷 1 处，attest 的对账判据其实有机会抓到，但它报的是实话。**
2026-11-14 对账报告 default 租户 49 行、缺向量 0、✅ 一致 —— 缺陷 1 要有一条正文为空的
记忆才触发，真实数据里没有 ⇒ 确实没有漂移 ⇒ 判绿是**正确的**。
触发条件从未出现时，症状检测器永远沉默。

### 0.4 这与工具的哲学门槛什么关系

`docs/testing-philosophy-review-v2.md`：

- **标尺 3**：「覆盖率是代理指标，『这套测试能发现缺陷吗』才是真问题」—— 现状**部分关闭**：
  `--self-check` 只证明了两条**静态**判据能翻红，实测类判据一条都没证明。
- **§4.1**：「『能失败』的证明：**部分**——实测判据（『测试全绿』）从未被证明能红」
- **§4.2**：工具层达成度「大约六到七成」，缺的是「能失败的完整证明与 runner 输出解析的健壮性」

**本设计要做的正是把标尺 3 从「文件级、静态、抽查」升级为「逐条不变式、实测、可复现」。**

---

## 1. 目标 / 非目标 / 反目标

### 1.1 目标

| # | 目标 | 可验收 |
| --- | --- | --- |
| G1 | 从代码机械枚举「应有判定点」，产出应有矩阵 | 在 `service/src/agent-memory` 上枚举出可核对的 DP 集合 |
| G2 | 人工计划 ↔ 机器矩阵双向对齐，缺口可见 | 机器有/人工无 ⇒ 报红；人工有/机器无 ⇒ 保留待人签 |
| G3 | 逐条不变式的「钉」做区分力自证 | 注入缺陷后，钉住的那**一个用例**变红 ⇒ 该钉有区分力 |
| G4 | 变异算子扩展到结构性缺陷 | 算子集从 6 个比较运算符扩到 7 类（含语句删除、`in→hasOwn`、谓词取反） |
| G5 | 缺陷集 fixture 作验收基准 | 5 个 fixture 全部 5/5 独立报出，误报 0 |

### 1.2 非目标（明确不做）

- **不生成断言的期望值。** attest 负责「判定测到位没有」，期望值由人/AI 填。
  理由：自动推导的期望值极易从被测对象自身取 ⇒ 恒真 ⇒ 正是 `oracle_check` 要抓的东西。
- **不支持 Rust/Go 的判定点分析。** 没有可用的纯标准库 AST ⇒ 该判据 `skipped`，
  不是 `pass`。（现有「Rust/Go 识别不出来 ⇒ 退出码 2」的口径不变。）
- **不判需求语义正确性。** 仍在「本工具不判定」清单里。
- **不替人做交付裁决。** 输出仍是证据清单。

### 1.3 反目标（做成即失败）

| 反目标 | 形态 | 防线 |
| --- | --- | --- |
| **噪音机** | 一次报 500 条静态项，没人看 | 缺口只报「未归属且未豁免」；fixture 上误报必须为 0（硬断言） |
| **判定点剧场** | 把「覆盖率剧场」换成「判定点剧场」 | 每个 DP 必须能被复现（file:line + 复现命令）；区分力自证是真跑测试 |
| **计划双重劳动** | 人写一遍、机器写一遍 | `req_plan.py new` 自动填 DP 列；人只补「归属」与「豁免」两列 |
| **假绿升级** | 缺了能力却说「已覆盖」 | DP 推导不可用 ⇒ 降级 warn；宁可降级不许假绿 |

---

## 2. 核心概念：判定点（Decision Point, DP）

### 2.1 定义

> **一次「走哪条路」的选择，或一次「资源/错误归属」的决定；
> 该选择若走错，会产生与需求相关的可观察差异。**

不是「一行代码」，是「一个选择」。一行代码可能不含选择（常量赋值、类型标注），
一个选择可能跨多行（多分支的 `switch`）。

### 2.2 分类（kind）

| kind | 触发特征 | 语义 | 典型缺陷形态 |
| --- | --- | --- | --- |
| `branch` | `if` / `else` / `switch` case / 三元 / 早返回（guard clause） | 条件分叉 | 条件写反、分支漏写 |
| `boundary` | `>` `<` `>=` `<=` `==` `!=` 及其等值形式 | 阈值与等值判定 | 边界含不含 |
| `error-path` | `throw` / `raise` / `reject` / `catch` 体 | 错误传播 | 错误被吞、被降级成空结果 |
| `resource` | `create` / `close` / `open` / `acquire` / `release` / `dispose` | 资源归属 | 谁负责关、关几次、关了别人的 |
| `state` | 写入状态字段（`status` / `state` / `phase`） | 状态转移 | 转移漏一步、重复一步 |
| `drop` | `filter` / `reject` / 丢弃元素的分支 | 静默丢数据 | 丢行不报错（缺陷 1 的形态） |
| `boundary-io` | 外部调用（网络 / 文件 / DB / 向量库） | 外部效应 | 失败不处理、重试无界 |
| `concurrency` | `lock` / `await` / `Promise.all` / 共享可变状态 | 并发 | 竞态、串号 |

### 2.3 标识

**`file#anchor#kind[#ordinal]`** —— 人类可读、可 grep、可直接被 fixture 断言匹配。

| 段 | 取值 | 为什么这样取 |
| --- | --- | --- |
| `file` | **仓库相对路径**（`service/src/agent-memory/mem0-vector-store.ts`） | 与钉的路径口径一致（`_pin_violation` 只认仓库内相对路径）⇒ DP 列天然过越界闸，且**计划 ↔ 机器矩阵 ↔ fixture 断言**三处用同一个键 |
| `anchor` | 所在**函数 / 方法 / 类成员名**；不在任何函数内 ⇒ `<toplevel>` | **不用行号**（见 §2.3.1） |
| `kind` | 8 类之一（§2.2） | |
| `ordinal` | 同一 `(file, anchor, kind)` 内的第几个，从 1 起；唯一时**省略** | 解决同一符号内同类 DP 撞 ID（§2.3.2） |

例：`service/src/agent-memory/mem0-vector-store.ts#insertRow#drop`

**行号不进 ID，但进报告**：行号作为独立字段（`line`）出现在「判定点覆盖」节与 JSON 里，
供人复现。**ID 里没有行号 ⇒ 代码增删几行不会让 ID 漂移。**

#### 2.3.1 为什么 ID 里不能带行号（builder-check 提出的硬问题）

带行号的直接后果：生产代码**任何一处无关改动**都会让后面所有 DP 的行号偏移
⇒ 计划里自动填好的 DP 列整列失效 ⇒ 双向对齐把每个移过行的 DP 判成「机器有 / 人工无」
⇒ 高危档**批量报红**。这与 §1.3 的反目标「噪音机」直接冲突。

⇒ 决定：**ID 不含行号；行号只是报告里的一个字段。**
代码改了、函数没改名 ⇒ ID 不变 ⇒ 对齐结果不变 ⇒ 只有**真的改了判定点本身**
（增删分支、改了资源归属）才会让 ID 变或数量变 ⇒ 那才是真的该重新对齐。

代价：同一个函数里同类的多个判定点，要靠 `ordinal` 区分（§2.3.2）。

#### 2.3.2 同一符号内同类 DP 撞 ID（builder-check 提出的硬问题）

`if (x === 1 && y === 2)` 一行里两个 `boundary`；`if (a) { if (b) {} }` 写在一行两个 `branch`。
若 ID 只到 `kind` 为止 ⇒ 两个 DP 的 ID 完全相同 ⇒ 静默合并成一条 ⇒ 计数错、归属错。

⇒ 决定：**加 `ordinal` 段**（同一 `(file, anchor, kind)` 内从 1 开始编号，唯一时省略）。
`ordinal` 的稳定性代价：在同一函数里**前面**插入一个同类 DP 会让后面的 ordinal 顺移
⇒ 这时候要重跑 `upgrade` 重生成 DP 列（这是合理的：判定点集合真的变了）。

**归一化**：报告与断言里比较 DP ID 时，先做**归一化**（`#` 分段、每段去空白、kind 段小写），
`ordinal` 段可有可无（`x#f#drop` 与 `x#f#drop#1` 视为同一个 —— 前者是「唯一那个」的简写）。

### 2.4 与「核心逻辑细节」维度表的关系

**现有维度表（边界 / 错误路径 / 并发 / 重试 / 幂等 / 降级 / 跨租户隔离 / 权限 / 资源上限 / 数据对账）
是模块级粗粒度触发，判定点是函数级细粒度。**

新设计**不新增计划节**，而是把现有「核心逻辑细节」这一节的粒度换掉：

```
现状：每行一个「维度」，落点是一个测试文件
新版：每行一个「判定点」，归属到具体不变式与具体用例
```

好处：计划生成时就带上了应有矩阵 ⇒ `check` 时的双向对齐是天然的，不新增仪式。

---

## 3. 判定点的推导技术路线

### 3.1 硬约束

- **只用 Python 3.11+ 标准库，不加任何第三方依赖。**（`docs/development-brief.md` 硬约束 1）
- **不引入网络访问。**（硬约束 2）

### 3.2 三级推导，逐级降级，**报告里必须标明用了哪级**

| 级别 | 手段 | 适用 | 精度 | 判据降级 |
| --- | --- | --- | --- | --- |
| **L3** | AST | Python 源码 ⇒ Python 自带 `ast` 模块（标准库）<br>TS/JS 源码 ⇒ 目标仓库里**存在可用的 TS AST 后端**（见 §3.3） | 精确 | 正常判定 |
| **L2** | 词法扫描（正则 + 轻量状态机，跳过注释与字符串字面量） | 找不到可用 TS AST 后端 | 有误报漏报 | **降级 warn**，标注「词法推导，可能有误报」 |
| **L1** | 不支持 | Rust / Go / 其他 | — | **skipped** + 说明「当前只实现了 TS/JS/Python」 |

### 3.3 L3 的 TS 路径不违反纯标准库约束

attest **不引入**第三方 Python 依赖，而是**驱动目标仓库自己的工具链** ——
与它现在跑仓库自己的 test 命令、仓库自己的覆盖率闸是同一套路。

#### 3.3.1 已核事实（2026-11-15，lumen monorepo 实测）

**不能假定 `require('typescript')` 暴露 `createSourceFile`。** 实测：

| 位置 | typescript 版本 | `require('typescript')` 暴露什么 |
| --- | --- | --- |
| `service/`、`rag/`、`users/`、`service-api/`、`workflow-engine/` | **7.0.2** | 只有 `version` / `versionMajorMinor` —— **没有 `createSourceFile`** |
| `frontend/` | 5.9.2 | 经典 JS API（含 `createSourceFile`） |

TypeScript 7 是 Go 原生版，npm 包里只带 `tsc` 二进制（`lib/` 下只有 `tsc.js` / `version.cjs`）。
但它**另带一套 AST 导出**：

- `typescript/unstable/ast` ⇒ 全套节点判定（`isIfStatement` / `isBinaryExpression` / `isCatchClause` /
  `isThrowStatement` / `isAwaitExpression` / `isCallExpression` / `isSwitchStatement` /
  `isConditionalExpression` …）+ `SyntaxKind` + `visitNode` / `visitEachChild` / `forEachChildOfJSDocParameterTag`；
- `typescript/unstable/sync` ⇒ `API` / `Snapshot` / `Project` / `Program`，
  路径是 `new API()` → `api.updateSnapshot()` → `snapshot.getDefaultProjectForFile(file)` →
  `project.program.getSourceFile(file)` → 得到可 `visitNode` 的 `SourceFile`。

**注意 `unstable/` 是 unstable**：API 形状会随版本变 ⇒ 探测必须**运行时验能力**，不能按版本号写死。

#### 3.3.2 后端探测链（必须按顺序试，第一个「能真解析」的胜出）

| 序 | 后端 | 探测方式 | 备注 |
| --- | --- | --- | --- |
| B1 | TS 5.x 经典 API | `require(ts).createSourceFile` 存在 | 最省事，**先找仓库里任何一份 5.x**（lumen 的 `frontend/` 就是 5.9.2） |
| B2 | TS 7 项目 API | `typescript/unstable/ast` + `unstable/sync` 的 `API`/`Snapshot`/`Program` | 需要项目配置，比 B1 重；**可行性由实现者验证**，本设计只要求「能解析就算可用」 |
| B3 | L2 词法回退 | 总是可用 | 降级 warn |

**探测范围＝整个仓库，不只是模块所在的工作区。**
理由（实测）：lumen 的 `service/`（目标模块所在）只有 TS 7，而 `frontend/` 有 TS 5.9.2 ——
只看模块所在工作区会误判「没有 AST 后端」。

**探测必须验能力，不能验版本**：
拿一段合成代码（`if (x >= 1) { throw new Error('e') }`）真跑一遍后端，
能解析出节点才算可用。`version` 字段不可靠（7.0.2 只暴露 version，没有 API）。

#### 3.3.3 实现要点

1. **探测**：在仓库内找 `node_modules/typescript`（含 pnpm 的 `.pnpm/` 布局 ⇒ 要顺着软链找），
   以及各工作区的 `package.json` 里声明的 `typescript`。逐个试 §3.3.2 的后端。
2. **生成**：把一段 `.mjs` 写到**临时目录**（**不写进用户仓库**），内容是
   加载后端 → 解析目标文件 → 遍历 AST → 输出 DP 的 JSON。
3. **执行**：`node <tmp>.mjs <目标文件...>`，`cwd` = 仓库根。
4. **解析**：读 stdout 的 JSON。
5. **降级**：任何一步失败（没找到后端 / 后端解析不了该文件 / 语法错误）⇒ **退回 L2**，
   并在报告里写明降级原因与用了哪级。

**性能**：B1 的 `createSourceFile` 不需要 `tsconfig`、不需要类型检查 ⇒ 快。
B2 需要项目配置 ⇒ 可能慢；实测后若太慢，**先只解析目标模块的文件**，不要全仓扫。

### 3.4 DP 的 AST 提取规则（TS 与 Python 各一张表）

必须逐条实现、可单测。TS 侧用**后端无关**的描述（不同后端的节点类型名不同：
5.x 是 `ts.SyntaxKind.IfStatement`，7.x 是 `isIfStatement(...)` 谓词）——
**规则表用「节点概念」写，实现时映射到各自后端的判定函数**。

| kind | AST 节点 |
| --- | --- |
| `branch` | `IfStatement`（每个 `then`/`else` 分支各一条）、`SwitchStatement` 的每个 `case`/`default`、`ConditionalExpression` |
| `boundary` | `BinaryExpression` 中运算符为 `>` `<` `>=` `<=` `==` `!=` `===` `!==` |
| `error-path` | `ThrowStatement`、`CatchClause`（含其 body 的每个分支） |
| `resource` | `CallExpression` 的 callee 名命中资源动词表（`create/close/open/acquire/release/dispose/destroy/shutdown/free/unref/end/stop`） |
| `state` | 赋值目标的属性名或变量名命中状态词表（`status/state/phase/stage/step`） |
| `drop` | `filter`/`reject`/`slice` 的谓词参数，或 `Array.prototype.filter` 的调用 |
| `boundary-io` | `CallExpression` 的 callee 名命中 I/O 词表，或目标模块导入的外部模块的调用 |
| `concurrency` | `AwaitExpression`、`Promise.all/race/allSettled`、`lock`/`mutex`/`Semaphore` 词命中、模块级可变变量 |

Python 侧（`ast` 模块）：`If` / `Match` / `IfExp` / `Compare` / `Raise` / `ExceptHandler` /
`with` / `await` / `asyncio.gather` / 状态属性赋值。

**资源动词表与 I/O 词表是词表**（可扩充），不是正则魔法。词表初始内容见 §附录 A。

### 3.5 DP 的归属：哪些 DP「值得报缺口」

不是所有 DP 都需要被单独钉住 —— 否则噪音爆炸。分两档：

- **高危档**（缺口 ⇒ 报红）：`error-path`、`resource`、`drop`、`state`
  —— 这四类正是「错了会静默产生坏数据或坏连接」的形态，对应本次 5 处缺陷中的 4 处。
- **常规档**（缺口 ⇒ 汇总计数，不逐条报红）：`branch`、`boundary`、`boundary-io`、`concurrency`
  —— 它们的覆盖情况由「计划的核心逻辑细节」逐条回答（人工判断），机器只报总数与抽样。

**这是控制噪音的关键**：机器的硬判据集中在「静默坏数据」四类上，其余以统计与抽样呈现。

---

## 4. 计划格式升级

### 4.1 新版「核心逻辑细节」表

**保留原有的「维度」列** —— 它承载现有的 `_DETAIL_DIMENSIONS` 覆盖判据（§4.4），
丢掉它等于回退一条既有判据。新表是 **6 列**：

```markdown
| 维度 | DP ID | 判定点 | 归属的不变式 | 落点（文件::用例名） | 豁免理由 |
| --- | --- | --- | --- | --- | --- |
| 错误路径 | service/src/agent-memory/mem0-vector-store.ts#insertRow#drop | 缺主键/正文的行必须报错而非丢弃 | I1 | service/src/agent-memory/mem0-vector-store.test.ts::rejects a row without id or text | |
| 资源上限 | service/src/agent-memory/vector-store-milvus.ts#close#resource | store.close 不得关闭共享 client | I21 | service/src/agent-memory/vector-store-milvus.test.ts::does not tear down the shared client | |
| 资源上限 | service/src/agent-memory/mem0-engine.ts#closeMemoryEngine#resource | 关闭后必须清空引擎缓存 | I22 | service/src/agent-memory/mem0-engine.test.ts::clears the engine cache so no handle keeps a closed client | |
| 权限 |  | （该维度不对应任何判定点） |  | service/src/agent-memory/guards.test.ts::rejects a cross-tenant read | 不适用：权限由网关拦截，不在本模块判定 |
```

- `req_plan.py new` **自动填 DP ID 与判定点两列**（机器推导所得），人只填后三列。
- **DP ID**：机器推导所得，格式见 §2.3。允许为空（该维度不对应任何判定点）。
- **归属的不变式**：填不变式编号（`I1`）；该编号必须在不变式清单里存在。
- **落点**：填 `测试文件::用例名`；工具核对用例真实存在（与现有「钉」同规格）。
- **豁免理由**：非空即视为已豁免。

### 4.2 校验规则（`req_plan.py check`）

| 情况 | 级别 |
| --- | --- |
| 高危档 DP 既无归属不变式、又无豁免理由 | **报红** |
| 常规档 DP 既无归属不变式、又无豁免理由 | **汇总计数**（不报红，报告里写明数） |
| 归属的不变式编号在不变式清单里不存在 | **报红** |
| 落点指向的用例在文件里不存在 | **报红** |
| 落点只有文件、没有用例名 | 提醒（与现有「钉只到文件」同档） |
| 豁免理由非空但没写清豁免的是什么 | 提醒 |
| DP ID 指向的文件在仓库里不存在 | **报红**（机器推导出来的 ID 指向不存在的文件 ⇒ 推导器有 bug） |

**DP 列豁免「钉存在性/越界」检查**（与 builder-check 的问题①对齐）：
`_plan_pins` 只从**落点列**抽钉，DP 列不参与 ⇒ DP 列不会引入新的写入面，
也不会因为路径写法不同被判成「越界」。但 DP ID 里的文件部分**必须真实存在**（上表末行），
否则说明推导器有 bug。

### 4.3 新旧格式识别（builder-check 的问题③：判据的判据）

「计划已是判定点粒度吗」这个开关决定三条新判据是否硬判，**必须能机械判定**：

| 输入 | 判定 |
| --- | --- |
| 表头含 `DP ID` 列 | 新格式 |
| 无「核心逻辑细节」表 | 旧格式 ⇒ 三条新判据一律 **warn** |
| 有该表但表头无 `DP ID`，且数据行的第 1 列**不是**可解析的 DP ID | 旧格式 ⇒ warn |
| 有该表，表头无 `DP ID`，但数据行第 1 列**是**可解析的 DP ID | 新格式（按数据行判，不信表头） |

「可解析的 DP ID」= 匹配 `^[^#]+#[^#]+#[a-z-]+(#\d+)?$`（归一化后）。

**没有 `--plan` 时**（`readiness.py` 不带计划参数）：
三条新判据一律 **`skipped`（证据类别 `untested`）**，**不是 fail**。
理由与现有 `check_requirement` 一致（`readiness.py:2971`：无计划 ⇒ skipped + untested），
且**现有 5 个黄金 fixture 都不传 `--plan`、期望退出码 0/1/2 不变** ⇒ 新判据在无计划时必须不是 fail。

### 4.4 老计划兼容（用户已选「条件式阻塞」）

- `check` 与 `readiness` 都能识别旧格式（维度表 ⇒ 无 DP 列）。
- 旧计划 ⇒ 三条新判据一律降为 **warn**，并明确写：
  「本计划还是旧的模块级维度表；升级到判定点粒度后，这三条判据才会硬判」
- 提供 `req_plan.py upgrade <计划文件>`：把旧表保留为附录，新增 DP 列（机器推导填好 DP ID 与判定点两列），
  人工补后三列。**不自动归属** —— 归属是人的判断。
- **升级是「代码改了判定点集合才需要重跑」**，不是「代码动一行就要重跑」
  （ID 不含行号 ⇒ 无关改动不需要重跑；§2.3.1）。

### 4.5 `_plan_pins` 与维度覆盖判据的改法（已核代码）

**已核事实**（`skills/attest/scripts/req_plan.py`）：
- `_plan_pins`（`:1229-1233`）现在从「核心逻辑细节」表的 **第 1 列**（索引 1）抽钉
  —— 旧表是 2 列（`维度 | 落点`）。
- 维度覆盖判据（`:1470-1496`）同样读 `row[0]`（维度）与 `row[1]`（落点），
  并用 `_DETAIL_DIMENSIONS` 做子串匹配。
- `_pin_violation` 只认**仓库内相对路径**（越界闸）。

**改法**：
1. 落点列从索引 1 移到索引 **4** ⇒ `_plan_pins` 与维度覆盖判据都要**按格式分叉**：
   新格式读 `row[4]`，旧格式读 `row[1]`。**两套都要认** ⇒ 旧计划不会被误判成「钉缺失」。
2. 新增列 3（DP ID / 判定点 / 归属 / 豁免）**不引入新的写入面** —— 它们不参与抽钉。
3. 维度覆盖判据在**新格式**下额外多一条：DP ID 非空 ⇒ 该行必须落在某个可变式；
   DP ID 为空 ⇒ 仍按旧规则要求「落点」或「不适用 + 理由」。
4. 格式分叉的判据用 §4.3 那张表，**不许靠列数猜**（列数会随人加列变）。

---

## 5. 归属映射：不变式 ↔ 判定点

### 5.1 机械映射链

```
不变式 I_k
  └─ 钉（测试文件::用例名）            ← parse_pins() 已有，可直接用
       └─ 该测试文件 import 的生产符号（限目标模块内）
            └─ 这些符号的**可达闭包**内的 DP
                 ⇒ I_k 覆盖这些 DP
```

- **钉的解析**：`req_plan.py::parse_pins(cell)`（`:816`）已把单元格解析成 `(文件路径, 用例名|None)`，
  且 `::` 之后的整段都算用例名。**直接复用，别重写。**
- **运行时 import**：见 §5.1.1（三种形态，动态 import 是主导形态）。
- **可达闭包**：模块内调用图上的传递闭包。
  **L3**：AST 后端给出的调用图（精确）。
  **L2**：文件内 `f(` 形式的调用正则 + 导出符号的入口 ⇒ 保守上近似（多算不少算）。
  Python：`ast` 的调用图（精确）。
- 保守上近似是**有意的**：宁可多算覆盖（少报缺口），也不要把无关 DP 塞给人。

#### 5.1.1 import 解析要新写，`_import_specs` 用不了（已核代码）

`readiness.py:278` 的 `_import_specs(text, suffix)` 是**第三方依赖扫描器**：
它对 JS/TS **显式跳过相对导入**（`spec.startswith(".")`）与路径别名（`@/`、`~`），
且只返回**包名集合，不返回被导入的符号名**。
而归属映射需要的是「**相对/别名路径 → 目标文件 → 被导入的符号名**」⇒ **口径相反，用不上**。

需要新写一个 import 解析器。**但解析器的形态不能照抄「静态 import」的直觉** ——
实测（2026-11-15，`service/src/agent-memory/` 25 个测试文件）发现三种形态并存，
且**动态 import 是主导形态**：

| 形态 | 实测例子 | 归属含义 |
| --- | --- | --- |
| **静态具名 import**（可跨多行） | `classification.test.ts:3` `import {\n  a,\n  b,\n} from './classification.ts';` | 取花括号里的符号名 |
| **动态 import**（主导形态，25 个文件里约 17 个用） | `mem0-engine.test.ts:104` `} = await import('./mem0-engine.ts');`<br>`agent-pool.test.ts:8` `const { poolColumnFilter } = await import('./service.ts');` | 同静态：**优先取解构出的符号名**；**无解构 ⇒ 命名空间级**（整个模块的公开面都算引用） |
| **纯类型 import** | `mem0-engine.test.ts:4` `import type { EngineHandle } from './mem0-engine.ts';`<br>`zz-vector-repro.unit.test.ts:90` `type Mem0Api = import('./mem0-engine.ts').Mem0Api;` | **运行时被擦除 ⇒ 不构成运行时覆盖，不计入** |

**这条「纯类型 import 不计入」不是细节，是硬要求**：
`mem0-engine.test.ts` 对 `./mem0-engine.ts` 只有一处 `import type`（第 4 行），
真正的运行时引用是第 104 行的**动态** import。若解析器只认静态 import，
它会把这个模块**最核心的测试文件**判成「零覆盖」⇒ §5.2 直接假红。
反过来 `mem0-glue.test.ts:10` 与 `mem0-vector-store.test.ts:8` 对 `./vector-store.ts`
**只有** `import type` ⇒ 判「零覆盖」是**正确**的（它们确实没在运行时碰那段代码）。

**解析器要处理的 specifier 三类**（已核 lumen `service/` 的 `tsconfig`）：

| 类别 | 规则 | 实测例子 |
| --- | --- | --- |
| 相对路径 | 后缀即文件名，直接相对**测试文件所在目录**解析 | `'./mem0-engine.ts'`、`'../agent-sensitive-data/protection-context.ts'` |
| 路径别名 | 按目标仓库 `tsconfig` 的 `paths` 映射。**别名规则要从目标仓库的 `tsconfig` 读，不硬编码 lumen 的** | `@/chat/telemetry.ts` → `./src/chat/telemetry.ts`；`@config/env.ts`、`@lib/api-error.ts` |
| 裸包名 | 第三方 ⇒ **忽略**（但注意：`@lumen/service-api` 是**作用域包名**不是路径别名，别把它当别名解） | `node:test`、`drizzle-orm/pg-core`、`@lumen/service-api`、`fastify-type-provider-zod` |

lumen `service/` 的 `tsconfig` 实测值：`moduleResolution: "bundler"`、
`allowImportingTsExtensions: true`（⇒ **import 直接带 `.ts` 后缀**）、
`paths` 有 `@/*`、`@config/*`、`@lib/*`、`@middleware/*` 四组。

**符号名的取法**：具名 import 的花括号（`import { a, b as c }` ⇒ `a`、`c`）、
默认 import、命名空间 import（`import * as ns` ⇒ 命名空间级）、
动态 import 的解构模式（`const { a, b } = await import(...)` ⇒ `a`、`b`；
无解构 ⇒ 命名空间级）。

**实现约束**：解析必须**跨行**（具名 import 的花括号列表常换行），
且必须能区分 `import type` / `import { type X }`（类型）与其余（运行时）。
纯正则要小心 —— **L3 路径下这一整套应该从 AST 后端取**（后端天然区分 type-only import），
**L2 回退路径下才用正则**，且 L2 的类型/运行时区分不可靠 ⇒
L2 下**保守地把 `import type` 也算进去**（宁可多算覆盖，不假红）。

#### 5.1.2 「这个文件导出了哪些符号」

- **L3**：AST 后端解析时顺手产出每个文件的导出符号表（精确）。
- **L2**：`req_plan.py::module_helpers`（`:406`）已有一套正则导出扫描
  （`export function|const|class|…` / `export { a, b }` / 解构导出 / Python `def`/`class`），
  但它按**模块**聚合、不按文件 ⇒ L2 路径下需要一版按文件的（逻辑可复用，粒度要改）。

### 5.2 「钉不碰目标模块」要报

若某条不变式的钉**引用的生产符号集合为空**（测试文件没 import 目标模块的任何东西），
则它对目标模块零覆盖 ⇒ **报红**：「这条不变式的钉不直接引用目标模块的任何代码」。

「引用」的口径按 §5.1.1 定：
**运行时 import 才算**（静态具名 / 动态 / 命名空间），
**纯类型 import 不算**（运行时被擦除），
`mock.module('./x.ts')` 算引用（它点名了那个模块）。

这一条能抓「钉指向的用例其实测的是别的东西」（缺陷 ④ 的形态）。

**已知误报面**：覆盖可能经由**间接调用**发生（测试只 import 了一个 helper，helper 内部调目标模块）。
本工具的归属链**只做模块内可达闭包，不跨文件追 helper** ⇒ 这种情形会被误报成零覆盖。
处置：**默认报红，但必须能用 §5.3 的豁免机制豁免**，报告文案要写明
「若是通过间接调用覆盖，请豁免并写理由」。**不许**因为怕误报就默认放过 —— 那等于回到今天的盲区。

**这一条的实测代价与收益（2026-11-15 实测）**：
在 `service/src/agent-memory` 上扫 25 个测试文件，
`mem0-glue.test.ts` 与 `mem0-vector-store.test.ts` 对 `./vector-store.ts`
**只有** `import type` ⇒ 这两个文件对 `vector-store.ts` 判零覆盖是**正确**的，
不是误报。这条判据在真实模块上**没有出现误报面**。

### 5.3 豁免语法

两种都接受，取其一：

- 表格的「豁免理由」列非空；
- 计划正文里的注释（**ID 格式与 §2.3 一致：不含行号**）：
  `<!-- attest:waive service/src/agent-memory/x.ts#insertRow#drop reason="该分支由上游校验拦截" -->`

豁免**必须给理由**（非空字符串），否则报红。豁免在报告里逐条列出（让人能复核是否滥用了豁免）。

---

## 6. 区分力自证（DPSP, Discriminating Power Self-Proof）

这是本设计的核心，也是标尺 3 要的那把尺子。

### 6.1 对每条有归属的不变式做什么

1. 解析钉 → 测试文件 `T` + 用例名 `C`。
2. 求 `T` 引用的目标模块生产符号集合 `S`，及其可达闭包 `S*`。
3. 在 `S*` 的函数体里生成变异点（§6.3 的算子集）。
4. **建一份副本**（复用 `mutation_check.py` 的 `_copy_repo` + `_link_deps`；
   副本默认不带 `node_modules`，`readiness.py --self-check-deps` 才软链；
   **计划从原仓库读，副本里没有 `docs/`**），
   然后**在副本里逐个变异、跑完原地还原**（复用 `run_mutation_check` 的做法）。
   **不每个变异点复制一份**（builder-check 的问题⑦）：
   磁盘与复制时间降 ~6×，而每个变异点**都要重跑一次测试**（这才是真成本，省不掉）。
5. 对每个变异点 `m`：
   - 应用 `m`（只改副本）
   - **先验变异体可解析**（§6.3.2）⇒ 解析不过 ⇒ 该点记 `unknown`，不跑测试
   - **再验变异确实生效**（§6.4.1）⇒ 没生效 ⇒ 记 `not-applicable`，不跑测试
   - 跑测试，判定**用例 `C` 本身**是否变红（复用现有 `classify` 三分口径 + §6.2.1 的严格匹配）
   - 原地还原副本里的文件
6. 结论：

| 情况 | 判定 | 说明 |
| --- | --- | --- |
| 至少 1 个变异被 `C` 杀死 | **pass** | 这条钉能发现这类缺陷 |
| 全部变异存活 | **fail**（条件式阻塞下） | 列出存活变异点位置：`file:line` + 算子 |
| 环境没就绪（runner 跑不起来 / 解析不出用例名） | **untested** | 不是 fail —— 沿用现有「跑不起来 ≠ 不达标」口径 |
| **闭包里一个变异点都没有**（纯计算函数，没有比较、没有 throw、没有 filter） | **not-applicable** | builder-check 的问题④：**不是 fail**。判 fail ⇒ 纯函数一律被报「钉无区分力」⇒ 噪音机；判 pass ⇒ 假绿。这一档**不计入杀死率分母**，并在报告里写明「该不变式的可达闭包内没有可变异点」 |

四档的口径统一为：**只有「全部存活」这一档报红**。其余三档都不报红。

**为什么必须新增第四档**：三档表（pass / fail / untested）里，「零变异点」只能被硬塞进 fail 或 untested，
两个都不对 —— fail 是假红，untested 把「这条钉本来就没被变异算子覆盖」说成「没测到」，
会把工具自己的覆盖面不足伪装成项目的问题。**第四档是必需的，不是可选的。**

### 6.2 「只跑钉住的那一个用例」—— 关键细节

若跑整个测试文件，别的用例红会掩盖「这个钉到底有没有区分力」。所以必须精确定位到 `C`。

**主路径（runner 无关，推荐）**：跑**整个测试文件**，然后从 runner 输出里解析出
**失败的用例名列表**，判断 `C` 是否在其中。

⚠️ **这里踩到一个既有缺陷 ⇒ 必须先修（P0，见 §10）**：

`readiness.py::_failed_tests_detail`（`:722`）的正则是
`re.match(r"^not ok \d+ - (.+)$", ln)` —— **锚在行首**。
node:test 的 TAP 输出里，**嵌在 `describe` 里的用例行有缩进**
（实测：`    not ok 2 - failing case`），**匹配不到**；只有顶层套件聚合行
（`not ok 1 - outer group`）在行首、匹配得到。

**实测后果（2026-11-15，node 22 + `node --test`）**：

```
输入：describe('outer group') 里一个 ECONNREFUSED 的失败用例
现有 _failed_tests_detail ⇒ ['outer group | 1 subtest failed']   ← 套件名，不是用例名
现有 _classify_run        ⇒ 失败用例非空、未实测原因为空 ⇒ 判「产品缺陷（阻塞）」
正确判定                   ⇒ 环境没就绪（未实测），不阻塞
```

⇒ **嵌套在 `describe` 里的环境错误，今天会被误判成产品缺陷（假红）**。
`service/src/agent-memory` 的所有测试都包在 `describe` 里 ⇒ 这条在目标模块上是活的。

**修法（已原型验证）**：

1. 匹配前**去缩进**（`ln.strip()` 后再匹配）；
2. 用 `# Subtest: <名字>` 行的**缩进栈**重建**全路径**
   （`# Subtest: outer group` 在缩进 0，`# Subtest: failing case` 在缩进 4
   ⇒ 全路径 = `outer group > failing case`）；
3. 用紧随其后的 YAML 块里的 `type: 'suite'` **排除套件聚合行**（否则套件名会混进列表）；
4. 失败用例的判定见 §6.2.1（**严格匹配**，不能用现有的模糊匹配）。

原型实测结果：
```
修复后失败用例全路径 ⇒ ['outer group > failing case', 'outer group']
去掉 type:'suite' 后   ⇒ ['outer group > failing case']
```

**既有测试不回归**：`scripts/test_readiness_runners.py::test_env_error_not_blocking`
用的是**平铺**的 `not ok 1 - connect ECONNREFUSED ...`（行首）⇒ 去缩进后仍匹配 ⇒ 仍绿。
但它**没有覆盖嵌套情形** ⇒ 修的时候要**补一条嵌套用例**（新缺陷 ⇒ 必测）。

**这条修复会改变一条既有判据的行为**（「环境没就绪 vs 产品缺陷」的判定），
方向是**消除假红**，不是放松门槛 ⇒ 在 §12 DoD 里显式声明，不藏在「行为不变」底下。

**快路径（可选加速）**：已知 runner 且支持单用例筛选时（node:test 的 `--test-name-pattern`、
pytest 的 `-k`、go 的 `-run`），直接筛。**前提是必须能确认筛的是同一个用例**
（筛完的 runner 输出里要能看到该用例名）。确认不了就回退主路径。

#### 6.2.1 用例名比对必须**严格**（builder-check 的问题⑤/Q6）

**不能复用 `req_plan.py::case_matches`（`:792`）**。它是**模糊匹配**：
完全相同，或 ≥8 字符的前/后缀落在词边界上就算命中。拿它判「C 变红了没」会**假杀死**：

```
C = "rejects a row"
失败用例全路径 = "outer group > rejects a row without id"
case_matches ⇒ True   ← 假：这不是 C 红了，是**另一个**用例红了
⇒ 报「这条钉有区分力」⇒ 假绿
```

⇒ 决定：**新写一个严格匹配函数 `match_case(case, full_path)`**，规则只有两条：

1. 归一化（折叠空白、去首尾空格）后**完全相等** ⇒ 命中；
2. 否则把 `full_path` 按 `>` 切成段，把 `case` 也按 `>` 切成段，
   取 `full_path` 的**末尾 k 段**（k = `case` 的段数）⇒ 与 `case` **逐段完全相等** ⇒ 命中；
   段数不足 ⇒ 不命中。

效果：
```
"failing case"                vs "outer group > failing case"            ⇒ 命中（末段相等）
"outer group > failing case"  vs "outer group > failing case"            ⇒ 命中（完全相等）
"rejects a row"               vs "outer group > rejects a row without id" ⇒ 不命中 ✔ 堵住假杀死
```
**规则只有这两条，没有第三条** —— 不做「去前缀」「包含」「去标点」等任何宽松化。

**计划里写哪一种用例名算数**：`node:test` 的 TAP 给的是**内层**用例名，
而 `req_plan.py::test_case_names`（`:748`）对 `describe` 嵌套同时登记「内层名」与「外层 > 内层」全名。
⇒ 两种都算命中（规则 1 与规则 2 天然覆盖）。**计划里写全名更保险**（能钉住是哪个 `describe` 下的用例）。

### 6.3 变异算子集（扩展后）

| 算子 | 变换 | 覆盖的缺陷形态 | 需要哪一级后端 |
| --- | --- | --- | --- |
| `cmp-negate` | `==`↔`!=`、`===`↔`!==` | 比较写反（现有） | **L2 即可**（现有 `_OP_RE`） |
| `cmp-tighten` | `>=`→`>`、`<=`→`<` | 边界含不含（现有） | **L2 即可**（现有 `_OP_RE`） |
| `in-to-hasown` | `x in Y` → `Object.hasOwn(Y, x)`（Python 侧跳过此算子） | 原型链绕过（缺陷 4 形态） | **L2 即可**（有界文本替换） |
| `drop-throw` | `throw E` → `void E`（**表达式替换**，不是删整行） | 错误被吞（缺陷 4 形态） | **仅 L3**（要语句/表达式边界） |
| `drop-call` | `f(args)` → `undefined`（**表达式替换**，不是删整行） | 关键动作被跳过（缺陷 3 形态） | **仅 L3** |
| `predicate-negate` | `filter(P)` → `filter(x => !P(x))` | 过滤方向错（缺陷 1 形态） | **仅 L3**（要谓词参数边界） |
| `catch-drop` | `catch (e) { BODY }` → `catch (e) { }` | 错误被吞成空结果 | **仅 L3** |

**「规则不变」这句话必须改写（builder-check 的问题①：原文自相矛盾）**：

原文写的是「不改常量、不改字符串、**不改控制流结构**」，而 `drop-throw` / `drop-call` / `catch-drop`
三个算子**本质上就是改控制流** —— 一句话同时说了两件矛盾的事。

⇒ 改写为两条明确规则：

1. **（保留）不改常量、不改字符串、不改注释；字符串与注释里的运算符不碰。**
2. **（新）允许改控制流 —— 这正是新算子的目的**，但必须同时满足：
   - **变异体仍能解析**（§6.3.2）；
   - **不产生未定义引用**（§6.3.3）。

**分级的原因（也是 L2 下的口径）**：语句级手术在词法层做不可靠 ⇒
**结构算子只在 L3（AST）路径下实现**。L2 路径只保留三个可做词法的算子
（`cmp-negate` / `cmp-tighten` / `in-to-hasown`）。
⇒ **L2 下某条不变式的可达闭包里若只有结构算子 ⇒ 零变异点 ⇒ 落在 §6.1 的 `not-applicable` 档**，
既不报红也不假装测过。**这比「在 L2 下硬做结构算子」诚实得多。**

每个新算子必须能被自证（**该算子能杀死一个已知的坏实现**）—— 否则它可能只是制造编译错误。

#### 6.3.1 「删语句」为什么改成「表达式替换」

`drop-call` 若真把 `const r = await f(x)` 整行删掉 ⇒ `r` 未定义 ⇒ 后续用到 `r` 的地方
抛 `ReferenceError` ⇒ 测试红了 ⇒ 被判「杀死」—— **但死因与被测语义无关** ⇒ 假绿。

⇒ 决定：**用表达式替换代替删语句**：
- `drop-call`：`f(args)` → `undefined`（保留绑定，`r` 变成 `undefined` 而不是「未定义」）；
- `drop-throw`：`throw E` → `void E`（保留 `E` 的求值副作用，只是不再抛）。

这样变异体**不会引入未定义引用**，被测代码也不会因为「变量不存在」而崩。

`catch-drop` 是例外：它的语义就是「catch 体不执行」，只能清空块体 ⇒ 必须靠 §6.3.3 的守卫。

#### 6.3.2 变异体必须仍能解析（否则不算「杀死」）

一个编译不过的变异体，跑测试必然红 ⇒ 会被判「杀死」⇒ **假绿**（死因是语法错误，不是被测语义）。

⇒ 决定：**应用变异后先做一次解析校验**：
- TS/JS：用 §3.3 的 AST 后端解析该文件；
- Python：`ast.parse`。
解析失败 ⇒ 该变异点记 **`unknown`（判不了）**，**不跑测试、不计入分母**，并在报告里写明原因。

#### 6.3.3 删块体不得留下未定义引用

`catch-drop` 清空 catch 体后，若体里声明了标识符（`const e = ...`）且该标识符在**后续代码**里被引用
⇒ 未定义引用 ⇒ 同上会产生与语义无关的红。

⇒ 决定：`catch-drop` 在生成变异点前先做**声明-使用检查**：
catch 体里声明的标识符，若在文件后续（块外）仍被引用 ⇒ **放弃该变异点**（记 `not-applicable`）。
（实现：AST 取 catch 体的绑定名，在文件其余部分做标识符引用检查。）

### 6.4 与现有 `mutation_check.py` 的关系

**事实校准**（已核代码，2026-11-15）：

- `skills/attest/scripts/mutation_check.py` 是**库**，不是 CLI。它自己的 argparse 只接一个源码文件（单文件调试用）。
  对外入口是 `run_mutation_check(repo, ws_rel, source_rels, test_rels, runner, max_mutants, timeout, classify)`，
  由 `readiness.py:2477` 调用。**所以 DPSP 的新参数是函数级参数，不是命令行开关。**
- 副本机制已存在：`_copy_repo`（`shutil.copytree` + `SKIP_DIRS`）+ `_link_deps`（软链 `node_modules` /
  `.venv` / `venv`）+ `_has_deps`。`readiness.py` 的 `--self-check-deps` 控制是否软链。
- **`SKIP_DIRS` 含 `docs`** ⇒ 副本里没有 `docs/` ⇒ **计划文件不在副本内**。
  DPSP 一律从**原仓库**读计划，从副本读代码。
- **`classify` 回调已经实现三分口径**（真失败⇒杀死 / 环境没就绪或输出认不出⇒**判不了**）。
  这正是 DPSP 要的口径，**必须复用**，不要另起一套 —— 另起一套就会把环境错误当成「杀死」，那是假绿。

**复用**：副本创建、依赖软链、脚本执行、runner 输出解析、变异点定位、三分分类。

**新增**：

| 位置 | 新增 | 说明 |
| --- | --- | --- |
| `mutation_check.py` | 7 类算子（§6.3） | 现有 6 个比较运算符保留 |
| `mutation_check.py` | `run_mutation_check(..., case_name=None)` | 给了 `case_name` 时判定「**这个用例**是否红」，而不是「整个文件是否红」 |
| `mutation_check.py` | 限定变异点作用域的参数（只在目标符号的可达闭包内找变异点） | 具体签名由实现者定 |
| 新文件 `skills/attest/scripts/discriminate.py` | 钉 → 符号 → 变异点 → 副本 → 单用例 → 结论 | 编排层，不重复造轮子 |
| `readiness.py` | `--discriminating {auto,off,quick,full}`、`--lenient` | CLI 开关挂在 readiness（与 `--mutation-check` 同级） |

**保留**：原有的「按文件跑一遍」用法不变（现有测试脚本 `test_test_strength.py` 等不得回归）。

#### 6.4.1 副本必须**密闭**，且要**验变异真的生效**（builder-check 的问题⑨d）

**已核事实**（`mutation_check.py:176-197`）：`_link_deps` 把 `node_modules` **软链**进副本，
链接指向**原仓库**的绝对路径。⇒ pnpm workspace 里
`service/node_modules/@lumen/xxx` 会解析回**原仓库**的包，**不是副本里的**。

⇒ 两条规则：

1. **只对「测试文件经相对路径可达」的源文件做变异。**
   经路径别名（`@/…`）或包名（`@lumen/…`）可达的文件 ⇒ **不变异**，
   记 `not-applicable` 并写明「经包路径可达，副本不密闭」。
   （这条可判定：§5.1.1 的 import 解析器已经知道每条 import 的形态。）
2. **每个变异点要验「变异确实生效」**。做法：在副本里被测文件的**末尾**追加一行标记
   `console.error("__ATTEST_MUTANT__:<relpath>")`（不改语义，只是打印），
   跑测试后检查子进程输出里有没有这个标记。
   - **有标记** ⇒ 副本里的文件确实被加载了 ⇒ 变异有效 ⇒ 判定成立；
   - **无标记** ⇒ 被测代码**根本没被加载**（或是被测代码来自原仓库）⇒ 该变异点无效
     ⇒ 记 `not-applicable`，**既不算杀死也不算存活**。

   为什么必须做这一步：没有它，「测试压根没跑到这段代码」会被算成「存活」
   ⇒ 报「这条钉是橡皮章」—— 那是**假红**（真实原因是代码没被执行，不是断言不严）。
   标记只打在**副本**里，不影响用户仓库。

**密闭性自检（先做，不做就等于在测原仓库）**：改造完成后必须在真实模块上验证
「副本里被测文件被加载的是副本」—— 判据＝标记出现在子进程输出里。
若标记不出现 ⇒ 说明软链把代码引回了原仓库 ⇒ 此时**所有 DPSP 结论一律作废**（记 `untested`）。

### 6.5 预算与抽样（用户已选「自适应 + 明写抽样比例」）

```
预算 = f(不变式条数, DP 密度, 模块规模)
  不变式 ≤ 20 条 ⇒ 全跑（每条 ≤ 6 个变异点）
  不变式 > 20 条 ⇒ 抽样
```

**报告必须写明**：

```
── 区分力自证 ───────────────────────────────
  判了 37/120 条不变式（31%）
  未判的 83 条：预算上限（--discriminating full 可全跑）
  杀死率 22/37（59%）
  存活的钉（15 条）见下表
```

**缺口自己说出来** —— 抽样后报绿会被读成「全绿」，那是 Goodhart 陷阱。
`--discriminating {auto,off,quick,full}` 控制；默认 `auto`。

---

## 7. 新增判据与输出契约

### 7.1 三条新判据

| key | 标题 | 证据类别 | 默认状态 |
| --- | --- | --- | --- |
| `plan-alignment` | 计划与机器推导双向对齐 | static | 条件式阻塞（旧计划 ⇒ warn） |
| `decision-coverage` | 应有判定点覆盖到位 | **static** | 条件式阻塞 |
| `pin-discriminating` | 每条不变式的钉有区分力 | **executed** | 条件式阻塞 |

**证据类别怎么定（builder-check 的问题⑦）**：
`Check` 结构只有一个 `evidence_kind` 字段，而 `decision-coverage` 天然是混合的
（DP 推导是静态的，覆盖情况靠静态归属表 ⇒ 静态；「自证」那部分由 `pin-discriminating` 承担）。

⇒ 决定：**拆清** ——
- `decision-coverage` = **static**（只看「应有矩阵 vs 计划的归属/豁免」）。它**不声称**任何东西被实测过。
- `pin-discriminating` = **executed**（真的跑测试、真的看用例红不红）。

这样每条判据只有一个证据类别，不出现「static + executed 混在一张卡里」。
**代价与口径**：`decision-coverage` 是静态信号 ⇒ 按 R1「静态信号永远不能单独支撑『通过』」，
它**不产生 pass**，只产生缺口清单；「实测覆盖」由 `pin-discriminating` 支撑。
（这与现有判据表的口径一致：静态类判据只报事实，不报绿。）

「条件式阻塞」的判定逻辑：

```
新判据 ⇒ 计划已是判定点粒度？
           ├─ 是 ⇒ fail ⇒ 退出码 1
           └─ 否 ⇒ warn「计划还是旧的模块级维度表，升级后才硬判」
--lenient ⇒ 一律降为 warn
```

### 7.2 输出契约新增一节

```
── 判定点覆盖 ───────────────────────────────
  判定点总数 128 ｜ 已归属 91 ｜ 豁免 12 ｜ 缺口 25
  高危四类缺口：error-path 3 ｜ resource 4 ｜ drop 1 ｜ state 0
  缺口清单（只列高危档）：
    service/src/agent-memory/vector-store-milvus.ts#close#resource  共享 client 的关闭归属未声明
    …
```

### 7.3 「本工具不判定」清单新增

现有六项（需求理解 / 产品行为 / 性能 / 安全 / 可用性 / 运维就绪）之外增加：

- **并发正确性**（竞态、串号）
- **容量与规模行为**

理由：DP 引擎能枚举并发原语，但**证明没有竞态**需要模型检查/压力测试，不是静态分析能给的。

实现位置：`readiness.py` 的模块级 `NOT_JUDGED` 元组（当前 6 项）。它同时被文本输出与
`conclusion.not_judged` 渲染 ⇒ 只改这一处，两种输出都跟着变。

### 7.4 JSON 输出新增段

现状（已核 `readiness.py:3249-3263`）：顶层是 `module` / `tables` / `checks` / `conclusion`，
其中 `checks` 是 `Check.__dict__` 的数组、`conclusion` 装 blocking / needs_human / no_signal /
measured_pass / static_signals / not_judged / exit_rule。

新增三段作为 **顶层并列键**（不进 `conclusion`）：

```json
{
  "decision_points": {
    "total": 128, "attributed": 91, "waived": 12, "gaps": 14,
    "gaps_high_risk": [
      {"id": "service/src/agent-memory/vector-store-milvus.ts#close#resource",
       "kind": "resource",
       "file": "service/src/agent-memory/vector-store-milvus.ts", "line": 436,
       "why": "未归属且未豁免"}
    ],
    "analysis_level": "L3",
    "analysis_backend": "B1-ts5-classic",
    "analysis_backend_source": "repo-local",
    "analysis_note": ""
  },
  "alignment": {
    "machine_only": ["service/src/agent-memory/x.ts#insertRow#drop"],
    "human_only": ["I21"]
  },
  "discriminating": {
    "checked": 37, "total": 120, "sample_rate": 0.31,
    "killed": 22,
    "not_applicable": 5, "untested": 3,
    "survivors": [
      {"pin": "I7", "test": "quota.itest.ts::net zero",
       "mutants": [{"file": "quota.ts", "line": 88, "op": "cmp-tighten"}]}
    ],
    "budget_note": "自适应预算；未判 83 条（预算上限）"
  }
}
```

**必须写明「用了哪个后端、从哪来」**（builder-check 的问题③）：
`analysis_level`（L3/L2）、`analysis_backend`（B1/B2/B3）、`analysis_backend_source`
（`repo-local` / `global-npm`）三个字段缺一不可。

理由：**同一份仓 + 同一份计划，在 CI（有全局 TS）与在本机（没装 TS）上必须能被区分开。**
没有这三个字段，两边的绿长得一模一样，出了事没法归因。

**判定口径（关键）**：**L2 下三条新判据一律不硬判（warn）** ⇒
「装了 TS 的机器报红、没装 TS 的机器报黄」是**安全的方向**（缺能力 ⇒ 绝不报绿）。
要拿严格结论就显式指定后端（§8.4）。

### 7.5 退出码

沿用现有语义：**有阻塞项，或关键判据（测试是否绿）没测到 ⇒ 1；否则 0。**
新判据在条件式阻塞成立时算阻塞项。

---

## 8. 缺陷集 fixture（验收基准）

### 8.1 复用现有 fixture 机制，不另造一套

**事实校准**（已核 `fixtures/`）：仓库已有 5 个「黄金 fixture」（`ts-colocated` / `python-repo` /
`rust-repo` / `english-reconciler` / `no-tests`），机制是：

- 每个 fixture 是一个**自包含小仓库**（`package.json` 或 `pyproject.toml` + 源码 + 测试）+ `README.md`；
- `fixtures/run_fixtures.sh` 的 `check()` 把 fixture 复制到临时目录再跑，
  断言用一套**钉死的文本语言**：`+文本`（必须出现）/ `-文本`（必须不出现）/ `?A;;B`（至少一个）；
- 退出码：`0` 全部命中 / `1` 有不符 / `3` 有环境缺失未判定；
- CI 步骤「跑黄金 fixture（5 个期望判定）」调的就是它。

**因此本设计不新增 `fixtures/golden/`、不新增 `skills/attest/scripts/test_golden_defects.py`** ——
新缺陷集就是现有机制的**第 6–10 个 fixture**，断言继续用 `+`/`-`/`?` 语言。

需要扩的是 `check()`：现在它固定跑
`python3 "$READYNESS" <module> --repo "$dest"`（`fixtures/run_fixtures.sh:66`），
**不带 `--plan`**。
缺陷集 fixture 必须带计划文件 ⇒ `check()` 要多接一个「额外参数」位（放在模块路径之后或用环境变量，
具体形式实现者定），并把 fixture 里的计划文件路径传进去。

> **路径现状（2026-11-15 实测）**：仓库在本设计成文期间被重构成 skill 布局 ——
> `SKILL.md` → `skills/attest/SKILL.md`，`scripts/` → `skills/attest/scripts/`，
> `fixtures/`、`evals/`、`README.md`、`CHANGELOG.md` 仍在仓库根。
> `fixtures/run_fixtures.sh:18` 已指向 `$REPO_ROOT/skills/attest/scripts/readiness.py`。
> **文件内容与行号未变（纯重命名）** ⇒ 本设计里所有行号引用仍然有效。

**命名**：现有 README 把它们叫「黄金 fixture」。新的一批叫**缺陷集 fixture**（`fixtures/<name>/`），
`fixtures/README.md` 的期望判定表加一节「缺陷集 fixture」，避免两套叫法混。

### 8.2 五个缺陷集 fixture

每个 fixture 除了小仓库，还要带一份**计划文件**（`plan.md`），内容是「有不变式清单、但清单漏了
对应条目」或「钉指向的用例不碰这段逻辑」——这样才能触发新判据。

| fixture | 复现的缺陷形态 | 期望报出（断言写法） |
| --- | --- | --- |
| `defect-silent-drop` | 适配器 `filter` 掉缺 id/正文的行，不报错 | `+` 出现 `drop` 类高危缺口；`-` 不出现「结论：达到交付条件」 |
| `defect-shared-client` | `store.close()` 关掉进程内共享 client | `+` 出现 `resource` 类缺口 |
| `defect-stale-cache` | 关闭后未清缓存，缓存握死连接 | `+` 出现 `resource` 类缺口 |
| `defect-prototype-class` | `x in MAP` 走原型链放行非法值 | `+` 出现 `in-to-hasown` 变异存活 ⇒ 该钉无区分力 |
| `defect-mixed-subject` | 第一人称豁免吞掉整句（含第三方部分） | `+` 出现 `branch` 类覆盖缺口 |

「误报为 0」的断言形态：在每个 fixture 的期望里加 `-` 行，把**该 fixture 不该有的其他类别**钉死
（例如 `defect-silent-drop` 里加 `-resource`、`-branch`）。这正是现有 `-文本` 机制能干的事。

**fixture 代码的写法要求**（决定断言能不能稳定命中）：
- 被测逻辑必须放在**具名函数**里（DP ID 的 `anchor` 段取函数名 ⇒ 断言里能写出来）；
- 判定点必须落在**词法层能看见**的形态上（`filter` / `close` / `in`）⇒ L2/L3 都能产出；
- **不要**把多个判定点塞进同一行（`if (a === 1 && b === 2)`）⇒ `ordinal` 段会漂。

### 8.3 验收断言

1. 5 个缺陷集 fixture **全部 5/5** 命中；
2. **误报为 0** —— 用 `-` 行钉死不该出现的类别；
3. 每条发现必须带 DP ID ⇒ 断言里用 `+service/src/x.ts#insertRow#drop` 这类稳定串
   （**不含行号** —— §2.3.1 的理由：行号会漂，函数名不会）。
   注意这与现有 `fixtures/README.md`「不用行号做断言主键」的规矩**方向一致**，不冲突；
4. 判定点推导级别在报告里标明（L3 / L2）⇒ 断言一条稳定串；
5. **判定结论要分档断言**：`pin-discriminating` 的四档（pass / fail / untested / not-applicable）
   各自至少被一个 fixture 覆盖 ⇒ 防止「四档退化成一档」这种橡皮章。

**这就是「attest 能证明自己能失败」的完整版**：现在的 `--self-check` 只能证明静态判据能红，
新 fixture 能证明**新的实测判据能红**（`pin-discriminating` 在 `defect-prototype-class` 上必须报 fail）。

### 8.4 AST 后端的 fixture 覆盖（不许留「没人测的测具」）

现有 fixture **都没有 `node_modules/typescript`**（`ts-colocated/package.json` 无依赖，靠 node 22 直接跑 `.ts`）
⇒ 缺陷集 fixture 默认会走 **L2**。
若 L3 路径只在 CI 之外被跑过，就等于**没人测测具** —— 与本设计的立意相反。

处置（三条都要）：

1. **CI 里装一份 TS 5.x**（`npm i -g typescript@5`）—— 与 CI 现有的 `pip install pytest pytest-cov` 对称，
   **只影响 fixture 车道，不影响 attest 运行时**（attest 本身仍然不联网、不加依赖）。
2. **缺陷集 fixture 走「有 TS 才跑，没 TS 就跳过」**（不是「没 TS 就红」）。
   做法与现有 `run_fixtures.sh` 的 `have_pytest_cov` 前置条件**同构**：
   新增一个 `have_ts_backend` 前置条件 ⇒ 没装 TS 时该 fixture 记「跳过（环境不满足）」，
   汇总里退出码是 **3**（未判定），**不是 1（不符）**。
   理由：L3 与 L2 的**结论强度不同**（L2 下结构算子不可用 ⇒ 可能落到 `not-applicable`），
   让没装 TS 的开发机跑出「不符」是**环境问题冒充产品问题**。
   ⇒ **CI 装 TS ⇒ 5 个缺陷集 fixture 全部真跑；开发机没装 TS ⇒ 明确显示「跳过」**。
3. **断言要后端无关**：缺陷集 fixture 的期望 DP 只用**两种后端都会产出**的那几条
   （fixture 代码故意写简单：具名函数 + `filter` / `close` / `in`）。
   再加一条**交叉一致性测试**（在 `skills/attest/scripts/test_decision_points.py` 里）：
   当 TS 后端可用时，断言 **L2 的每个 DP 都能在 L3 里找到同 `(file, kind)` 的 DP**
   （即 L2 的假阳性必须被 L3 覆盖），**允许 L3 多出 L2 看不到的**。

   **为什么不是「集合完全相等」**（builder-check 的问题⑨）：§3.2 明说 L2 是「有误报漏报」的启发式 ⇒
   全集相等等于要求 L2 无漏报，**一般情况下不可达**。
   「L2 ⊆ L3（按 file+kind）」才是可达且有意义的断言：
   它抓的是「L2 报了个 L3 根本不认的 DP」= L2 的假阳性。

**全局 TS 的探测要处理 `NODE_PATH`（builder-check 指出）**：
npm 的全局包装**不在** `NODE_PATH` 里，`require('typescript')` 仍会失败。
⇒ 探测时：把 `npm root -g` 的结果加进探测进程的 `NODE_PATH`（只加给子进程，不改用户环境），
并用 `createRequire` 从**绝对路径**加载（pnpm 下 `node_modules/typescript` 是指向 `.pnpm/` 的软链，
相对 `require` 解析不到）。**探测必须验能力（真跑一遍解析合成代码），不能只看版本号或路径存在。**

**探测结论要进报告**（§7.4 的 `analysis_backend_source`）：`repo-local` / `global-npm` / `none`。

### 8.5 与真实模块的关系

缺陷集是**工具的回归基准**（小、快、确定）。
真实模块 `service/src/agent-memory` 是**人工核对项**：改造完成后，在该模块上跑一次，
人工核对新判据是否报出与 OCR 那 5 处对应的缺口。这一步不自动化进 CI（依赖真实 PG/Milvus）。

---

## 9. 假阳性控制（反目标：核弹级 ≠ 噪音机）

| 机制 | 做法 |
| --- | --- |
| 分档 | 只有 `error-path`/`resource`/`drop`/`state` 四类高危 DP 的缺口才逐条报红；其余只报汇总 |
| 豁免 | 计划里可写豁免 + 理由；豁免逐条列进报告供复核 |
| 可复现 | 每条发现必须带 `file:line` + 类别 + 一句「怎么复现」 |
| 误报硬断言 | 缺陷集 fixture 上用 `-` 行钉死不该出现的类别 |
| 不许假绿 | DP 推导不可用 ⇒ 降级 warn 并说明；Rust/Go ⇒ skipped；都**不报 pass** |
| 不重复劳动 | `new` 自动填 DP 列；`upgrade` 把旧表转成新表 |

---

## 10. 实施顺序

| 阶段 | 内容 | 文件 | 完成判据 |
| --- | --- | --- | --- |
| **P0** | **修 `_failed_tests_detail` 的嵌套 TAP 解析**（§6.2）—— 这是**既有缺陷**，不是新功能 | 改 `skills/attest/scripts/readiness.py`；补 `skills/attest/scripts/test_readiness_runners.py` 的嵌套用例 | 嵌套 `describe` 里的环境错误判「未实测」不阻塞；嵌套用例名能报出全路径；既有 6 条 runner 用例仍绿 |
| P1 | DP 推导引擎 | 新增 `skills/attest/scripts/decision_points.py` | TS/Python 各能枚举 8 类 DP；L2 回退可用；单测全绿 |
| P2 | 计划格式升级 | 改 `skills/attest/scripts/req_plan.py` | `new` 填 DP 列；`check` 校验归属/豁免/旧格式识别；`upgrade` 子命令 |
| P3 | 变异算子扩展 + 单用例判定 | 改 `skills/attest/scripts/mutation_check.py` | 7 类算子各能被自证（能杀死已知坏实现）；`run_mutation_check(..., case_name=)` 可用 |
| P4 | 区分力自证 | 新增 `skills/attest/scripts/discriminate.py` | 钉→符号→变异点→副本→单用例→结论全链通 |
| P5 | 判据接入与输出 | 改 `skills/attest/scripts/readiness.py` | 3 条新判据 + 「判定点覆盖」节 + JSON 三段 + 条件式阻塞 + `--lenient` |
| P6 | 缺陷集 fixture | 新增 `fixtures/defect-*/`（含 `plan.md`）；改 `fixtures/run_fixtures.sh` 的 `check()` 以接额外参数；改 `fixtures/README.md` | 5/5 报出，误报 0；`run_fixtures.sh` 退出码 0 |
| P7 | 文档 | 改 `README.md`、`README.zh-CN.md`、`CHANGELOG.md`；**`skills/attest/SKILL.md` 只产出「要改什么」的清单，不动文件** | README/CHANGELOG 与代码一致；SKILL.md 改动清单已交付集成方 |

**依赖关系**：**P0 → P1** → P2/P3 → P4 → P5 → P6 → P7。
P0 独立可先落地（它本身就是一个真缺陷修复），但 DPSP（P4）依赖它 ⇒ 必须排在 P4 之前。

---

## 11. 风险与取舍

| # | 风险 | 缓解 |
| --- | --- | --- |
| R1 | DP 推导有误报（L2 词法回退时尤甚） | L3 精确；L2 降级 warn；词表可扩充；fixture 上误报硬断言 |
| R2 | DPSP 成本高（反复跑测试） | 自适应预算 + 抽样比例明写；`--discriminating` 三档；先整文件跑、只判单个用例结果 |
| R3 | 「钉是橡皮章」被判红，但修起来要改测试 | 这正是要的效果 —— 报的是「这个钉杀不死缺陷」，修法是补断言 |
| R4 | 目标仓库没有可用的 TS AST 后端（TS 7 且无 5.x 副本）⇒ 退回 L2 ⇒ 精度降 | 报告显式标注级别与降级原因；L2 下新判据不硬判；**探测范围是整个仓库**（lumen 的 `frontend/` 就有 5.9.2） |
| R5 | 计划变长（DP 表可能有上百行）⇒ 维护成本上升 | `new` 自动生成；只报未归属的；豁免机制；DP 表按类别折叠展示 |
| R6 | Rust/Go 不支持 ⇒ 用户以为「没报＝没问题」 | 该判据 `skipped` + 明说；输出契约里写明「本工具不判定」新增项 |
| R7 | 判据越多 ⇒ Goodhart 风险越高 | 新判据全部条件式阻塞；报告永远列出未判/抽样部分；不产出「达到交付条件」类措辞 |
| R8 | P0 修掉嵌套 TAP 假红 ⇒ 有人读成「attest 变松了」 | 变更**只影响**「环境错误 vs 产品缺陷」的归类，**不放宽任何阈值**；新旧对照用例钉住；DoD §12-4 显式声明 |
| R9 | **判定结论依赖机器环境**（装了全局 TS 的机器报红，没装的报黄） | L2 下新判据一律不硬判（缺能力 ⇒ 绝不报绿）；报告与 JSON 写明 `analysis_level` / `analysis_backend` / `analysis_backend_source` 三项；缺陷集 fixture 没 TS 就**跳过**（退出码 3）而不是红 |
| R10 | **副本不密闭** ⇒ 测的是原仓库不是副本 | 只变异「相对路径可达」的源文件；每个变异点用标记行验「副本里的代码真的被加载了」；标记不出现 ⇒ 结论作废（`untested`），不报红 |
| R11 | **变异体编译不过** ⇒ 被误判成「杀死」（假绿） | 应用变异后先做解析校验（TS 走 AST 后端、Python 走 `ast.parse`）⇒ 解析失败记 `unknown`，不计入分母 |
| R12 | **DP ID 随代码漂移** ⇒ 每次无关改动都批量报红 | ID 不含行号（§2.3.1）⇒ 无关改动 ID 不变；只有改了判定点集合才需重跑 `upgrade` |

---

## 12. 验收标准（Definition of Done）

1. **缺陷集 fixture**：5 个新 fixture 5/5 独立报出，误报 0（`bash fixtures/run_fixtures.sh` 全命中）
2. **实测判据能红**：**新缺陷集 fixture** 能证明**实测类**判据（`pin-discriminating`）翻红
   （标尺 3 的缺口关闭）。
   事实校准：现有 `--self-check` 只有两种注入（`drop-test` ⇒ `uncovered-exports`、
   `inject-sleep` ⇒ `hygiene`，见 `skills/attest/scripts/selfcheck.py:40-46`）⇒ **全是静态判据**，
   实测类判据（`tests-green`）从未被证明能红。**本设计的实测自证由缺陷集 fixture 承担，
   不挂在 `--self-check` 名下**（`--self-check` 的两条静态注入不动，避免动既有行为）。
   ⇒ 连带修 `skills/attest/SKILL.md` 里「反向验证能覆盖实测类判据（测试是否绿、覆盖率）」这句**过度声称**
   （现与代码不符）。
3. **真实模块人工核对**：在 `service/src/agent-memory` 上，新判据报出的缺口与 OCR 那 5 处对应
4. **无回归**：现有全部测试脚本（`skills/attest/scripts/test_*.py`）与 14 条既有判据行为不变。
   **唯一声明的行为变更**：P0 修掉嵌套 `describe` 里环境错误被误判成产品缺陷的假红（§6.2）
   —— 变的是「误判为阻塞」变成「未实测」，**门槛不放宽**，且有新旧对照用例钉住。
5. **约束不被破坏**：无新增第三方 Python 依赖；不联网；不写用户仓库（除 `scaffold`/`upgrade`
   的既有受控写入范围）
6. **文档一致**：`README.md`、`README.zh-CN.md` 与 `CHANGELOG.md` 同步；
   `skills/attest/SKILL.md` 的改动**清单**已交付集成方（该文件由集成方统一改，本车道不编辑）——
   清单内容：判据表 14→17 条、新判据的输出节、命令行的 `--lenient` / `--discriminating` / `--plan` / `--analysis-backend`、
   以及 §12.2 里指出的那句过度声称。
7. **fixture 退出码**：`fixtures/run_fixtures.sh` 5 个既有 + 5 个缺陷集 fixture 全命中（10 个）

---

## 附录 A：初始词表

**匹配口径（三条都要守，否则词表会炸出噪音）**：

1. **按 callee 的标识符整词匹配**，不是子串匹配
   （`close` 要命中 `client.close()`，**不能**命中 `disclose()` / `closedAt`）。
   TS 侧：`callee` 是标识符就按标识符切；是成员表达式就取**属性名**。
2. **变体规则**：`snake_case` / `kebab-case` / `camelCase` 归一化后再比
   （词表写 `teardown`，代码写 `tearDown` / `tear_down` 都要命中）。
3. **排除项**：注释与字符串字面量里的词**不算**（L2 词法扫描必须跳过它们）。

**资源动词**（`resource` 类触发）：
`create, close, open, acquire, release, dispose, destroy, shutdown, free, unref, end, stop, init, setup, teardown`

**I/O 词**（`boundary-io` 类触发）：
`fetch, request, query, select, insert, update, delete, upsert, read, write, send, post, put, patch, connect, exec, commit, rollback`

**状态词**（`state` 类触发）：
`status, state, phase, stage, step, flag, mode`

**并发词**（`concurrency` 类触发）：
`lock, unlock, mutex, semaphore, wait, waitFor, acquire, promise, allSettled, race, concurrent, atomic, transaction`

> **词表里的坑（初稿已犯）**：`all` 不能进并发词表 ——
> 它是 `Promise.all` 的一部分，但**整词匹配时 `all` 根本不是独立标识符**，
> 而子串匹配又会把 `call` / `small` / `install` 全部误命中 ⇒ 噪音爆炸。
> 同理 `query` 曾被写了两遍。**词表要按「真实出现的标识符形态」写，不是按英文词根写。**

---

## 附录 B：判定点推导的示例（以本次 5 处缺陷为例）

| 缺陷 | 相关 DP（ID 格式见 §2.3，锚点取**实测的**所在函数/方法名） | attest 怎么抓到 |
| --- | --- | --- |
| ① 镜像静默丢行 | `service/src/agent-memory/mem0-vector-store.ts#createMem0VectorStore#drop` | DP 存在但无归属不变式 ⇒ 高危缺口 ⇒ 报红 |
| ② 共享 client 被关 | `service/src/agent-memory/vector-store-milvus.ts#close#resource` | DP 存在但无归属 ⇒ 报红；`drop-call` 变异会杀死「不关 client」的实现 |
| ③ 引擎缓存握死连接 | `service/src/agent-memory/mem0-engine.ts#closeMemoryEngine#resource` | 同上；`drop-call`（把 `engineCache.clear()` 替换成 `undefined`）不被任何钉杀死 ⇒ 钉无区分力 |
| ④ 分类走原型链 | `service/src/agent-memory/classification.ts#resolveClassification#branch` | `in-to-hasown` 变异存活 ⇒ 分类相关钉无区分力 |
| ⑤「我是X」吞整句 | `service/src/agent-memory/slots.ts#hasThirdPartySubject#branch` | 混合路径未单列 ⇒ 覆盖缺口；`predicate-negate` 存活 ⇒ 钉无区分力 |

> **锚点名的实测来源**（2026-11-15，`service/src/agent-memory/`）：
> `createMem0VectorStore`（`mem0-vector-store.ts:62`）、
> `close`（`vector-store-milvus.ts:436`，类方法）、
> `closeMemoryEngine`（`mem0-engine.ts:168`）、
> `resolveClassification`（`classification.ts:42`）、
> `hasThirdPartySubject`（`slots.ts:436`）。
>
> 这条实测也说明**锚点必须是真实存在的标识符** ⇒ 推导器要能取到「最近的具名外层函数」，
> 拿不到就退 `<toplevel>`（并在报告里标注，避免两个 `<toplevel>` 撞成一条）。
