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
| R-B 变异算子只看比较运算符 | `mutation_check.py` 的算子集只有 `== != === !== >= <=` 六种。缺陷 1/2/3/5 都是**结构性**缺陷（filter→throw、语句删除、正则改写），不在算子射程内 | `scripts/mutation_check.py:44-51` |
| R-C 变异「杀死」答的不是这个问题 | 变异杀死 = 套件对某个改动敏感；它从不回答「当前生产代码对不对」。缺陷 1 处把 `!==` 变异成 `===` 会被现有测试杀死 ⇒ 报「测试能发现缺陷 ✓」，而缺陷当时是活的 | `scripts/mutation_check.py:11-16` |

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

`file:line:kind` —— 人类可读、可 grep、可直接被 fixture 断言匹配。

例：`service/src/agent-memory/mem0-vector-store.ts:128:drop`

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

```markdown
| DP ID | 判定点 | 归属的不变式 | 落点（文件::用例名） | 豁免理由 |
| --- | --- | --- | --- | --- |
| src/mem0-vector-store.ts:128:drop | 缺主键/正文的行必须报错而非丢弃 | I1 | mem0-vector-store.test.ts::rejects a row without id or text | |
| src/vector-store-milvus.ts:436:resource | store.close 不得关闭共享 client | I21 | vector-store-milvus.test.ts::does not tear down the shared client | |
| src/mem0-engine.ts:173:resource | 关闭后必须清空引擎缓存 | I22 | mem0-engine.test.ts::clears the engine cache so no handle keeps a closed client | |
```

- `req_plan.py new` **自动填前两列**（机器推导所得），人只填后三列。
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

**实现提示（已核代码）**：`req_plan.py::_plan_pins`（约 1229 行）现在从「核心逻辑细节」表的
**第 1 列**（索引 1）抽钉。新表把落点放在**索引 3** ⇒ 这一处的列号要跟着改，
并且要同时认旧表（索引 1）与新表（索引 3），否则旧计划会被误判成「钉缺失」。
`_plan_pins` 还要继续对所有节的钉做仓库越界闸，新表新增的两列不得引入新的写入面。

### 4.3 老计划兼容（用户已选「条件式阻塞」）

- `check` 与 `readiness` 都能识别旧格式（维度表 ⇒ 无 DP 列）。
- 旧计划 ⇒ 三条新判据一律降为 **warn**，并明确写：
  「本计划还是旧的模块级维度表；升级到判定点粒度后，这三条判据才会硬判」
- 提供 `req_plan.py upgrade <计划文件>`：把旧表保留为附录，新增 DP 列（机器推导填好前两列），
  人工补后三列。**不自动归属** —— 归属是人的判断。

---

## 5. 归属映射：不变式 ↔ 判定点

### 5.1 机械映射链

```
不变式 I_k
  └─ 钉（测试文件::用例名）
       └─ 该测试文件 import 的生产符号（限目标模块内）
            └─ 这些符号的**可达闭包**内的 DP
                 ⇒ I_k 覆盖这些 DP
```

- **可达闭包**：模块内调用图上的传递闭包。
  TS：文件内 `f(` 形式的调用正则 + 导出符号的入口 ⇒ 保守上近似（多算不少算）。
  Python：`ast` 的调用图（精确）。
- **可复用件（已核代码）**：`readiness.py:278` 的 `_import_specs(text, suffix)` 已能抽测试文件的
  import 规格；`req_plan.py` 侧**没有**同类工具（只 import 了 `re`）⇒ 归属映射的实现位置由实现者定，
  但 `_import_specs` 的行为口径（抽什么、抽到什么粒度）要复用，别另写一套。
- 保守上近似是**有意的**：宁可多算覆盖（少报缺口），也不要把无关 DP 塞给人。

### 5.2 「钉不碰目标模块」要报

若某条不变式的钉**引用的生产符号集合为空**（测试文件没 import 目标模块的任何东西），
则它对目标模块零覆盖 ⇒ **报红**：「这条不变式的钉不碰目标模块的任何代码」。

这一条能抓「钉指向的用例其实测的是别的东西」。

### 5.3 豁免语法

两种都接受，取其一：

- 表格的「豁免理由」列非空；
- 计划正文里的注释：`<!-- attest:waive src/x.ts:12:drop reason="该分支由上游校验拦截" -->`

豁免**必须给理由**（非空字符串），否则报红。豁免在报告里逐条列出（让人能复核是否滥用了豁免）。

---

## 6. 区分力自证（DPSP, Discriminating Power Self-Proof）

这是本设计的核心，也是标尺 3 要的那把尺子。

### 6.1 对每条有归属的不变式做什么

1. 解析钉 → 测试文件 `T` + 用例名 `C`。
2. 求 `T` 引用的目标模块生产符号集合 `S`，及其可达闭包 `S*`。
3. 在 `S*` 的函数体里生成变异点（§6.3 的算子集）。
4. 对每个变异点 `m`：
   - **复制工作区**（复用 `mutation_check.py` 的 `_copy_repo` + `_link_deps`；
     副本默认不带 `node_modules`，`readiness.py --self-check-deps` 才软链；
     **计划从原仓库读，副本里没有 `docs/`**）
   - 应用 `m`（只改副本）
   - 跑测试，判定**用例 `C` 本身**是否变红（复用现有 `classify` 三分口径）
   - 还原/丢弃副本
5. 结论：

| 情况 | 判定 | 说明 |
| --- | --- | --- |
| 至少 1 个变异被 `C` 杀死 | **pass** | 这条钉能发现这类缺陷 |
| 全部变异存活 | **fail**（条件式阻塞下） | 列出存活变异点位置：`file:line` + 算子 |
| 环境没就绪（runner 跑不起来 / 解析不出用例名） | **untested** | 不是 fail —— 沿用现有「跑不起来 ≠ 不达标」口径 |

### 6.2 「只跑钉住的那一个用例」—— 关键细节

若跑整个测试文件，别的用例红会掩盖「这个钉到底有没有区分力」。所以必须精确定位到 `C`。

**主路径（runner 无关，推荐）**：跑**整个测试文件**，然后用现有的 `_failed_tests_detail`
解析出失败的用例名列表，判断 `C` 是否在其中。
—— 完全复用现成的 runner 输出解析（`_node_summary` / `_pytest_summary` / `_failed_tests_detail`），
   对任何 runner 都成立。

**快路径（可选加速）**：已知 runner 且支持单用例筛选时（node:test 的 `--test-name-pattern`、
pytest 的 `-k`、go 的 `-run`），直接筛。**前提是必须能确认筛的是同一个用例**
（筛完的 runner 输出里要能看到该用例名）。确认不了就回退主路径。

### 6.3 变异算子集（扩展后）

| 算子 | 变换 | 覆盖的缺陷形态 |
| --- | --- | --- |
| `cmp-negate` | `==`↔`!=`、`===`↔`!==` | 比较写反（现有） |
| `cmp-tighten` | `>=`→`>`、`<=`→`<` | 边界含不含（现有） |
| `drop-throw` | 删掉一条 `throw`/`raise` | 错误被吞（缺陷 4 形态） |
| `drop-call` | 删掉一条 `await f(...)` / `f(...)` 调用语句 | 关键动作被跳过（缺陷 3 形态） |
| `in-to-hasown` | `x in Y` → `Object.hasOwn(Y, x)`（Python：`x in Y` → `hasattr`→`Y.__contains__` 语义等价物不适用 ⇒ Python 侧跳过此算子） | 原型链绕过（缺陷 4 形态） |
| `predicate-negate` | `filter`/`map` 谓词取反 | 过滤方向错（缺陷 1 形态） |
| `catch-drop` | `catch` 块体清空 | 错误被吞成空结果 |

**规则不变**（继承 `mutation_check.py` 现状）：不改常量、不改字符串、不改控制流结构；
字符串与注释里的运算符不碰；改完仍是同类语义、不会死循环。
每个新算子必须能被自证（**该算子能杀死一个已知的坏实现**）—— 否则它可能只是制造编译错误。

### 6.4 与现有 `mutation_check.py` 的关系

**事实校准**（已核代码，2026-11-15）：

- `scripts/mutation_check.py` 是**库**，不是 CLI。它自己的 argparse 只接一个源码文件（单文件调试用）。
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
| 新文件 `scripts/discriminate.py` | 钉 → 符号 → 变异点 → 副本 → 单用例 → 结论 | 编排层，不重复造轮子 |
| `readiness.py` | `--discriminating {auto,off,quick,full}`、`--lenient` | CLI 开关挂在 readiness（与 `--mutation-check` 同级） |

**保留**：原有的「按文件跑一遍」用法不变（现有测试脚本 `test_test_strength.py` 等不得回归）。

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
| `decision-coverage` | 应有判定点覆盖到位 | static（推导）+ executed（自证合成） | 条件式阻塞 |
| `pin-discriminating` | 每条不变式的钉有区分力 | executed | 条件式阻塞 |

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
    src/vector-store-milvus.ts:436:resource  共享 client 的关闭归属未声明
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
      {"id": "src/vector-store-milvus.ts:436:resource", "kind": "resource",
       "file": "src/vector-store-milvus.ts", "line": 436, "why": "未归属且未豁免"}
    ],
    "analysis_level": "L3-ast",
    "analysis_note": ""
  },
  "alignment": {
    "machine_only": ["src/x.ts:12:drop"],
    "human_only": ["I21"]
  },
  "discriminating": {
    "checked": 37, "total": 120, "sample_rate": 0.31,
    "killed": 22, "survivors": [
      {"pin": "I7", "test": "quota.itest.ts::net zero",
       "mutants": [{"file": "quota.ts", "line": 88, "op": "cmp-tighten"}]}
    ],
    "budget_note": "自适应预算；未判 83 条（预算上限）"
  }
}
```

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

**因此本设计不新增 `fixtures/golden/`、不新增 `scripts/test_golden_defects.py`** ——
新缺陷集就是现有机制的**第 6–10 个 fixture**，断言继续用 `+`/`-`/`?` 语言。

需要扩的是 `check()`：现在它固定跑 `python3 readiness.py <module> --repo <dest>`，**不带 `--plan`**。
缺陷集 fixture 必须带计划文件 ⇒ `check()` 要多接一个「额外参数」位（放在模块路径之后或用环境变量，
具体形式实现者定），并把 fixture 里的计划文件路径传进去。

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

### 8.3 验收断言

1. 5 个缺陷集 fixture **全部 5/5** 命中；
2. **误报为 0** —— 用 `-` 行钉死不该出现的类别；
3. 每条发现必须带 `file:line` ⇒ 断言里用 `+src/x.ts:128:drop` 这类稳定字符串
   （现有 `fixtures/README.md` 已写明「不用行号做断言主键」的规矩 —— 那是说**不要用报告格式里的行号**；
   DP ID 里带行号是**内容**不是**格式**，改动 DP 就该让 fixture 变红，这是有意的）；
4. 判定点推导级别在报告里标明（L3 / L2）⇒ 断言一条稳定串。

**这就是「attest 能证明自己能失败」的完整版**：现在的 `--self-check` 只能证明静态判据能红，
新 fixture 能证明**新的实测判据能红**（`pin-discriminating` 在 `defect-prototype-class` 上必须报 fail）。

### 8.4 AST 后端的 fixture 覆盖（不许留「没人测的测具」）

现有 fixture **都没有 `node_modules/typescript`**（`ts-colocated/package.json` 无依赖，靠 node 22 直接跑 `.ts`）
⇒ 缺陷集 fixture 默认会走 **L2**。
若 L3 路径只在 CI 之外被跑过，就等于**没人测测具** —— 与本设计的立意相反。

处置（两条都要）：

1. **CI 里装一份 TS 5.x**（`npm i -g typescript@5`）—— 与 CI 现有的 `pip install pytest pytest-cov` 对称，
   **只影响 fixture 车道，不影响 attest 运行时**（attest 本身仍然不联网、不加依赖）。
   探测时也要看**全局安装**（`npm root -g` / `NODE_PATH`），不能只看仓库内的 `node_modules`。
2. **断言要后端无关**：缺陷集 fixture 的期望 DP 只用**两种后端都会产出**的那几条
   （fixture 代码故意写简单：`filter` / `close` / `in` / 一条正则，都是词法层能看见的）。
   再加一条**交叉一致性测试**（在 `scripts/test_decision_points.py` 里）：
   当 TS 后端可用时，对 fixture 源码断言 L2 与 L3 产出的 DP 集合**一致** ——
   不一致就是两个后端之一有 bug，当场抓住。

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
| P1 | DP 推导引擎 | 新增 `scripts/decision_points.py` | TS/Python 各能枚举 8 类 DP；L2 回退可用；单测全绿 |
| P2 | 计划格式升级 | 改 `scripts/req_plan.py` | `new` 填 DP 列；`check` 校验归属/豁免/旧格式识别；`upgrade` 子命令 |
| P3 | 变异算子扩展 + 单用例判定 | 改 `scripts/mutation_check.py` | 7 类算子各能被自证（能杀死已知坏实现）；`run_mutation_check(..., case_name=)` 可用 |
| P4 | 区分力自证 | 新增 `scripts/discriminate.py` | 钉→符号→变异点→副本→单用例→结论全链通 |
| P5 | 判据接入与输出 | 改 `scripts/readiness.py` | 3 条新判据 + 「判定点覆盖」节 + JSON 三段 + 条件式阻塞 + `--lenient` |
| P6 | 缺陷集 fixture | 新增 `fixtures/defect-*/`（含 `plan.md`）；改 `fixtures/run_fixtures.sh` 的 `check()` 以接额外参数；改 `fixtures/README.md` | 5/5 报出，误报 0；`run_fixtures.sh` 退出码 0 |
| P7 | 文档 | 改 `SKILL.md`、`README.md`、`CHANGELOG.md` | 判据表 14+3=17 条与代码一致；新节写入 SKILL.md |

**依赖关系**：P1 → P2/P3 → P4 → P5 → P6 → P7。

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

---

## 12. 验收标准（Definition of Done）

1. **缺陷集 fixture**：5 个新 fixture 5/5 独立报出，误报 0（`bash fixtures/run_fixtures.sh` 全命中）
2. **实测判据能红**：`--self-check` 现在能证明**实测**类判据翻红（标尺 3 的缺口关闭）
3. **真实模块人工核对**：在 `service/src/agent-memory` 上，新判据报出的缺口与 OCR 那 5 处对应
4. **无回归**：现有全部测试脚本（`scripts/test_*.py`）与 14 条既有判据行为不变
5. **约束不被破坏**：无新增第三方 Python 依赖；不联网；不写用户仓库（除 `scaffold`/`upgrade`
   的既有受控写入范围）
6. **文档一致**：`SKILL.md` 判据表 17 条与代码一致；`README.md` 与 `CHANGELOG.md` 同步
7. **fixture 退出码**：`fixtures/run_fixtures.sh` 5 个既有 + 5 个缺陷集 fixture 全命中（10 个）

---

## 附录 A：初始词表

**资源动词**（`resource` 类触发）：
`create, close, open, acquire, release, dispose, destroy, shutdown, free, unref, end, stop, init, setup, teardown`

**I/O 词**（`boundary-io` 类触发）：
`fetch, request, query, select, insert, update, delete, upsert, read, write, send, post, put, patch, connect, exec, query`

**状态词**（`state` 类触发）：
`status, state, phase, stage, step, flag, mode`

**并发词**（`concurrency` 类触发）：
`lock, mutex, semaphore, wait, waitFor, acquire, promise, all, race, concurrent, atomic, transaction`

---

## 附录 B：判定点推导的示例（以本次 5 处缺陷为例）

| 缺陷 | 相关 DP | attest 怎么抓到 |
| --- | --- | --- |
| ① 镜像静默丢行 | `mem0-vector-store.ts:128:drop` | DP 存在但无归属不变式 ⇒ 高危缺口 ⇒ 报红 |
| ② 共享 client 被关 | `vector-store-milvus.ts:436:resource` | DP 存在但无归属 ⇒ 报红；`drop-call` 变异会杀死「不关 client」的实现 |
| ③ 引擎缓存握死连接 | `mem0-engine.ts:173:resource` | 同上；`drop-call`（删 `engineCache.clear()`）不被任何钉杀死 ⇒ 钉无区分力 |
| ④ 分类走原型链 | `classification.ts:70:branch` | `in-to-hasown` 变异存活 ⇒ 分类相关钉无区分力 |
| ⑤「我是X」吞整句 | `slots.ts:415:branch` | 混合路径未单列 ⇒ 覆盖缺口；`predicate-negate` 存活 ⇒ 钉无区分力 |
