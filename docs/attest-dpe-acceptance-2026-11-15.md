# 判定点引擎（DPE）验收复测记录

**日期** 2026-11-15 · **分支** `feat/dpe-engine` · **规格** `docs/superpowers/specs/2026-11-15-attest-decision-point-engine-design.md` · **计划** `docs/superpowers/plans/2026-11-15-attest-decision-point-engine.md`

本文件是 Task 10「全量验收复测」的记录。验收口径是「在真实模块上能工作」：本轮三个引擎缺陷
正是在 fixture 全绿的同时、在真实仓库上才暴露出来的。

---

## 0. 结论

| # | 结论 | 依据 |
| --- | --- | --- |
| 1 | 三条新判据（计划对齐 / 判定点覆盖 / 钉有区分力）**红→绿往返 + 假阳性守卫全绿** | §4 |
| 2 | 引擎自身的三个 P0 缺陷已修，且做过**反向自证**（把修复退回 HEAD ⇒ 断言全红），回归文件钉得住这三个修复 | §6 |
| 3 | Python 侧区分力自证已通（此前恒「不适用」/恒「判不了」） | §7 |
| 4 | 真实模块上抓到 **1 个真阳性**：计划与代码漂移 **16 条判定点**，全部落在 `slots.ts` 本次未提交的改动上 | §5.2 |
| 5 | 规格附录 B 的 5 条金标 DP 是修复前快照：当前树上 1 条已不存在、3 条缺序号后缀、1 条锚点不同 | §5.1 |
| 6 | 20 条不变式的区分力自证：**pass 2 / fail 7 / not-applicable 11 / untested 0**。引擎如实报告「判不了」，不粉饰成「通过」 | §5.5 |
| 7 | 阻塞项 1 个：`SKILL.md` 判据表同步（**集成者专属**，本车道按约定不碰该文件） | §8 |

---

## 1. 验收范围

| 能力 | 落点 | 判据名（报告里印出来的名字） |
| --- | --- | --- |
| 机器推导判定点（8 类，L2 词法 / L3 AST） | `skills/attest/scripts/decision_points.py` | —— |
| 计划 ↔ 机器矩阵双向对齐 | `readiness.py:3500` | 计划与机器推导对齐 |
| 应有判定点覆盖到位（只报缺口，永不报 pass） | `readiness.py:3558` | 应有判定点覆盖到位 |
| 钉的区分力自证（DPSP，四档结论） | `readiness.py:3621`、`discriminate.py:704` | 钉有区分力 |

CLI：`readiness.py <模块> --repo <根> --plan <计划> [--lenient] [--discriminating auto|off|quick|full] [--analysis-backend auto|b1|b2|l2]`。

---

## 2. 回归套件

`skills/attest/scripts/test_*.py` 共 **34** 个文件，本机逐个跑：**31 PASS / 3 FAIL**。

| 红项 | 断言 | 归属 |
| --- | --- | --- |
| `test_readiness_r2.py`（2 条） | `SKILL.md 判据表 14 行 == 代码 17 个 Check`；`每条判据都在 SKILL.md 的表里（缺：计划与机器推导对齐 / 应有判定点覆盖到位 / 钉有区分力）` | **集成者专属** —— `skills/attest/SKILL.md` 同时有另一条车道在改，合并由集成方做；本车道按约定不碰该文件。改动清单见 `docs/attest-dpe-skill-md-changes.md` |
| `test_readme_sync.py`（6 条） | README 自称版本 v0.1.3 ≠ CHANGELOG 最新 v0.1.7；Action pin 指向 0.1.3 | **并行车道**的未提交改动（`README.md` / `README.zh-CN.md` / `CHANGELOG.md` / `test_readme_sync.py`），本车道不碰。回归套件数那条（34）已对上 |
| `test_req_plan_r4.py`（1 条） | `不可读目录不崩` | **外部工具的缺陷**，不是 attest 的。断言的是机器级 `~/.agents/skills/ai-testing-system/scripts/testkit_audit.py`：它在 `lang_roots()` 里对不可读目录调 `Path.exists()` ⇒ `PermissionError` ⇒ 崩。实测栈：`testkit_audit.py:106` → `pathlib.exists` → `PermissionError: [Errno 13]`。CI 上没有这个机器级工具 ⇒ 该用例 `skip` ⇒ **CI 绿** |

三条红都不是本车道引入的：前两条是并行/集成车道的文件，第三条是外部依赖的缺陷。

---

## 3. 黄金 fixture / eval / 打包

| 检查 | 命令 | 结果 |
| --- | --- | --- |
| 黄金 fixture | `bash fixtures/run_fixtures.sh` | **命中 10 / 不符 0 / 未判定 0**（五个缺陷集各 2 例） |
| eval | `python3 evals/run_evals.py` | **6 / 6 通过** |
| 打包自检 | `python3 package.py --check` | 退出 **0** |

---

## 4. 三条判据的变更自证（红→绿往返 + 假阳性守卫）

每条判据做四轮：① 干净 ⇒ 不红；② 注入该判据对应的缺陷 ⇒ 必须红；③ 撤回 ⇒ 回绿；
④ 注入**另一条**判据的缺陷 ⇒ 该判据**不**红（防止橡皮章式全红）。

探针：`/tmp/dpe_verify/probe_criteria_roundtrip.py`（夹具：一个有分支的 `classify(n)` +
一个强断言用例 + 一个弱断言用例）。

```
== 判据 1：plan-alignment（计划 ↔ 机器推导对齐）==
  ok   GREEN 计划列全机器判定点 ⇒ pass（实际 pass）
  ok   RED 计划漏掉机器推导的判定点 ⇒ fail（实际 fail）
  ok   GREEN again 撤回缺陷 ⇒ 回绿（实际 pass）
  ok   假阴性守卫：高危判定点没归属（这是 decision-coverage 的事）⇒ alignment 不红（实际 pass）

== 判据 2：decision-coverage（应有判定点覆盖到位）==
  ok   GREEN 全部归属或豁免 ⇒ 不红（实际 skipped）
  ok   RED 高危判定点没归属也没豁免 ⇒ fail（实际 fail）
  ok   GREEN again 撤回缺陷 ⇒ 不红（实际 skipped）
  ok   假阴性守卫：计划漏机器判定点（这是 plan-alignment 的事）⇒ coverage 不红（实际 skipped）

== 判据 3：pin-discriminating（钉有区分力，实测）==
    dpsp: {"checked": 1, "killed": 1, "untested": 0, "not_applicable": 0}
    · I1 pass：杀死 1 个变异
  ok   GREEN 钉能杀死变异 ⇒ pass（实际 pass）
  ok   dpsp 真的杀死了变异（killed=1）
    · I1 fail：1 个变异全部存活 ⇒ 这条钉发现不了这类缺陷
  ok   RED 弱断言杀不死变异 ⇒ fail（实际 fail）
  ok   GREEN again 撤回弱断言 ⇒ 回绿（实际 pass）
  ok   假阴性守卫：计划没归属（这是 decision-coverage 的事）⇒ discriminating 不红（实际 untested）

全部符合预期（红绿往返 + 假阴性守卫）
```

判据 2 的 GREEN 是 `skipped`。这是设计：覆盖到位这条判据永不产出 pass，它只知道哪些高危判定点
没有归属也没有豁免，不知道人该怎么判。

---

## 5. 真实模块核对（`service/src/agent-memory`）

对象：lumen 工作区 `/private/tmp/lumen-pr-wt`，模块 `service/src/agent-memory`（26 个源文件），
计划副本 `/tmp/dpe_verify/plan-v2-mono.md`（`req_plan.py upgrade` 的产物，**没有写回 lumen 仓库**）。

### 5.1 规格附录 B 的金标 DP：5 条里有 5 条对不上

规格附录 B 给了 5 条「缺陷 ⇒ 金标 DP」的对照表。逐条用引擎在当前树上重推：

| 附录 B | 引擎在当前树上的实际 ID | 差异性质 |
| --- | --- | --- |
| ① `mem0-vector-store.ts#createMem0VectorStore#drop` | `mem0-vector-store.ts#insert#drop` | **锚点不同**。Rule B 取「最近的具名作用域」，`insert` 是 `createMem0VectorStore` 返回的对象字面量上的方法 ⇒ 锚点是 `insert` |
| ② `vector-store-milvus.ts#close#resource` | **不存在** | 缺陷②的修复把 `close()` 里的 `client.close()` 整段删掉了（方法体现在只剩注释）⇒ 那个资源操作点随之消失 |
| ③ `mem0-engine.ts#closeMemoryEngine#resource` | `…#closeMemoryEngine#resource#1` / `#2` | **缺序号后缀** |
| ④ `classification.ts#resolveClassification#branch` | `…#resolveClassification#branch#1` / `#2` / `#3` | **缺序号后缀** |
| ⑤ `slots.ts#hasThirdPartySubject#branch` | `…#hasThirdPartySubject#branch#1` … `#5` | **缺序号后缀** |

序号后缀来自规格 §2.3 自己的规定：`file#anchor#kind[#ordinal]`，「同一 `(file, anchor, kind)`
内的第几个，从 1 起；唯一时省略」。附录 B 那 5 条按 §2.3 读，③④⑤ 都带多个同类判定点，必须带序号。

勘误结论：附录 B 是修复前的快照，不能当「当前树上应该长什么样」的验收基线。② 那条现在根本
不存在，因为缺陷②的修法就是删掉那个调用。缺陷①的 `drop` 判定点还在（`insert` 里的
`rows.filter(...)`），但缺陷本身已修（现在 `throw` 而不是静默丢弃）。

这与 §2.3.1 的设计一致：ID 不含行号、只随判定点本身变化，修复落地时判定点会消失或改名，
这本身就是信号。

### 5.2 计划 ↔ 代码漂移：16 条判定点（真阳性）

`plan-alignment` 在真实模块上：**fail**。

```
16 条判定点机器推导出但计划里没有；0 条计划里的 DP 指向不存在的文件；0 条钉不直接引用目标模块
```

16 条**全部**落在 `service/src/agent-memory/slots.ts`：

```
service/src/agent-memory/slots.ts#hasThirdPartySubject#boundary-io
service/src/agent-memory/slots.ts#hasThirdPartySubject#branch#4
service/src/agent-memory/slots.ts#hasThirdPartySubject#branch#5
service/src/agent-memory/slots.ts#labelTailValue#branch
service/src/agent-memory/slots.ts#slotLabelParts#branch#4
service/src/agent-memory/slots.ts#slotLabelParts#branch#5
service/src/agent-memory/slots.ts#valueAfterCorrection#boundary#1
service/src/agent-memory/slots.ts#valueAfterCorrection#boundary#2
service/src/agent-memory/slots.ts#valueAfterCorrection#boundary#3
service/src/agent-memory/slots.ts#valueAfterCorrection#boundary#4
service/src/agent-memory/slots.ts#valueAfterCorrection#boundary-io
service/src/agent-memory/slots.ts#valueAfterCorrection#branch#1
service/src/agent-memory/slots.ts#valueAfterCorrection#branch#2
service/src/agent-memory/slots.ts#valueAfterCorrection#branch#3
service/src/agent-memory/slots.ts#valueAfterCorrection#branch#4
service/src/agent-memory/slots.ts#valueAfterCorrection#drop
```

对照 `git diff service/src/agent-memory/slots.ts`：该文件在 lumen 工作区是未提交状态，本次改动
引入了 `labelTailValue` / `valueAfterCorrection` 两个新函数、给 `slotLabelParts` 加了新分支、
给 `hasThirdPartySubject` 加了第三方人称/指示词判定。这 16 条就是这次改动引入的判定点，
而计划是在改动之前生成的。

判定点引擎在这里给出的是一条真实结论：计划已经过期了。

### 5.3 计划侧缺陷：混了两个根

| 列 | 用的根 | 例子 |
| --- | --- | --- |
| DP ID 列 | monorepo 根相对 | `service/src/agent-memory/attribution.ts#salientTokens#drop` |
| 落点列 | monorepo 根相对 | `service/src/agent-memory/r5-contract.itest.ts::…` |

`req_plan.py:1522` 把落点按 `repo.root / fpath` 解析，同一个 `--repo` 下 DP ID 列与落点列必须用
同一个根。引擎的约定（计划里的一切都相对 `--repo`）没错，要改的是计划：人类手写的落点列用了
monorepo 根，机器生成的 DP ID 列用了工作区根（`src/agent-memory/…`）。用 `--repo .../service`
跑时落点列整列解析不到，于是报「不变式没落到真实用例」「钉判不了」。

跑之前先确认这两列的根是不是同一个。不一致就把 `--repo` 指到落点列用的那个根，或把 DP ID 列
也改成那个根。

### 5.4 钉的区分力自证在真实模块上的分布

见 §5.5。

### 5.5 20 条不变式的四档结论

`quick` 档只抽样 8/20，所以这里把预算放大到 20 条全判、每条最多 3 个变异。实跑 **2878.6s（≈48 min）**。

| 结论 | 条数 | 含义 |
| --- | --- | --- |
| `pass` | **2** | 钉真的杀死了变异 ⇒ 这条不变式被真用例钉住了 |
| `fail` | **7** | 变异全部存活 ⇒ **这条钉发现不了这类缺陷** |
| `not-applicable` | **11** | 判不了：零变异点 / 变异没生效 / 没有相对路径可达的源文件 |
| `untested` | **0** | 环境没就绪或输出认不出 |

逐条：

| 钉 | 结论 | 引擎给的理由 |
| --- | --- | --- |
| I10 / I15 | **pass** | 各杀死 3 个变异 |
| I1 / I3 / I5 / I9 / I17 / I18 / I19 | **fail** | 1 个变异全部存活 ⇒ 这条钉发现不了这类缺陷 |
| I2 / I7 / I8 / I11 | not-applicable | 没有相对路径可达的源文件；测试文件经包名引用 `@lumen/service-api` / `drizzle-orm` / `ai` ⇒ 经包路径可达 ⇒ 副本不密闭 ⇒ 被引模块不变异 |
| I4 / I12 / I13 / I14 / I16 / I20 | not-applicable | 没有可判的变异点（零变异点或变异没生效） |
| I6 | not-applicable | 没有相对路径可达的源文件 |

这组数字怎么读：

- 7 条 fail 是真结论，不是环境问题。变异跑起来了、测试跑了、测试没红，说明钉住的用例断言太弱，
  比如只断言「不抛异常」而不是断言具体值。
- 11 条 not-applicable 里有 5 条同一个根因。lumen 的测试文件用包名引用 `@lumen/service-api`、
  `drizzle-orm`、`ai` 等 workspace 依赖，而变异引擎只对相对路径可达的源文件注入变异，跨包引用的
  模块根本没被变异，所以判不了。这是「变异在副本里做、副本不密闭」的固有限制，要判这些得让副本里
  的包路径也指向副本内的实体。
- 另 6 条是「零变异点 / 变异没生效」：这些不变式钉的源文件里，7 个算子找不到可变异的判定点，
  典型是「恒等映射」「纯查表」「配置读取」这类没有比较和分支的代码。
- untested = 0 说明「环境没就绪」和「输出认不出」这两类假阴性已经被前面的修复堵住了（`-rA` 暴露
  pytest 捕获的 stderr，`_classify_run` 兜底 pytest 摘要）。剩下的 not-applicable 都是如实报告
  判不了，不是坏掉的管道。

对「当前最新版本的 attest 能测出这些修复吗」这个问题，这一列就是答案：20 条里只有 2 条给出了
「这条钉真的有效」的正面证据，7 条给出了「这条钉无效」的负面证据，11 条说判不了。引擎没有把
「判不了」粉饰成「通过」，这是本轮最重要的行为约定。

## 6. 引擎自身的三个 P0 缺陷（fixture 全绿时看不见）

| # | 缺陷 | 机制 | 后果 | 修法 |
| --- | --- | --- | --- | --- |
| A | 大 JSON 被截断 | `decision_points.py` 里 `process.stdout.write(JSON)` 紧跟 `process.exit(0)`；stdout 是管道时 write 是异步的，exit 把没刷出去的数据丢掉。实测：管道缓冲上限正好 65536 字节 | JSON 解析失败 ⇒ 静默回退 L2 词法 ⇒ 三条判据只 warn 不硬判 ⇒ **整套判定点引擎在真实仓库上等于没开** | stdout/stderr 改落**文件**；JS 侧 `emit()` 用写回调再退出；`_parse_ts_json` 区分「输出被截断」与「后端起不来」 |
| B | 每文件上限截断在作用域过滤之前 | `find_mutations` / `find_structural_mutations` 内部各自 `break` 在 `DEFAULT_MAX_PER_FILE = 3`，而作用域过滤在其后 | 作用域相关的变异点永远轮不到 ⇒ 报出假结论「没有可判的变异点」 | 候选先不限上限收齐 → 作用域过滤 → 最后才施加每文件 3 个的上限 |
| C | 解析闸判的是文件不是变异 | `_default_parse_check` 用括号配平判断「原文能不能解析」，对真实 TS 误判（`memory-r5-cleanup.ts` 原文 `{}` 89/87、`[]` 64/63） | 每个变异都被判「解析不过」⇒ 报出假结论「判不了」 | 改成判变异：Python 走 `compile()`；JS/TS 比对变异前后的括号结构差值 |

这三条在 fixture 上全绿时看不见。五个缺陷集 fixture 每个只有一个源文件，JSON 远不到 64KB，
所以验收口径必须是真实模块。

复核方式如下（不采信代理自报）：

| 检查 | 工具 | 结果 |
| --- | --- | --- |
| 四项独立探针 | `/tmp/dpe_verify/probe_green.py` | 全绿：A 真路径 L3/1600 条；B 不限上限时作用域内 1 个（带上限时 0 个）；C 原文 True / 砍闭合括号 False / `===→!==` True |
| **反向自证** | 三份文件退回 HEAD 后跑 `test_dpe_engine_regressions.py` | **4 条断言全红**（大 stdout 恰好卡在 65536 字节＝管道缓冲上限）⇒ 回归文件钉得住这三个修复 |
| 全量 `test_*.py` | 34 个文件 | 31 PASS / 3 FAIL，与修复前基线逐项一致，无新增失败 |

---

## 7. Python 侧的区分力自证：三个洞

规格 §5.1 要求 Python 也要精确支持。此前 Python 这条路上有三个洞，任何一个不通，「钉有区分力」
就会变成恒绿橡皮章，比测不出更坏，因为它看起来在工作：

| 洞 | 现象 | 修法 |
| --- | --- | --- |
| 变异点作用域恒为 `<toplevel>` | Python 里 `_anchor_at` 按大括号找包围函数，Python 没有大括号 ⇒ 作用域过滤把 Python 变异全丢掉 | `mutation_check._py_scopes` / `_py_scope_at` 走 `ast` |
| 钉住用例的运行时引用恒为空 | `parse_imports` 只认 JS/TS ⇒ Python 用例的符号集恒为空 ⇒ 结论恒「不适用」 | `discriminate.python_analysis`（Python 版作用域分析） |
| pytest 的 node id 认不出 | node id 是 `tests/test_x.py::test_x`，`match_case` 只按 `>` 分段 | `match_case` 支持第三种 `::` 规则（含参数化 id） |

修完又暴露**两个**环境层的问题，都已修：

| 问题 | 现象 | 修法 |
| --- | --- | --- |
| pytest 默认捕获 stderr ⇒ 注入的变异标记被吞 | 结论恒「不适用」（一个变异都没生效） | 跑 pytest 时加 `-rA`（保留捕获语义，但把摘要里的 stderr 暴露出来） |
| `_classify_run` 读不懂 pytest 的 `1 passed in 0.01s` | 结论恒「判不了（环境没就绪）」 | `_classify_run` 在 node 系摘要之外加 pytest/tox 摘要兜底 |

回归：`skills/attest/scripts/test_dpsp_python.py`（12 项，全绿）。其中「弱断言杀不死变异 ⇒ fail」
那条是真的把 `assert classify(0) == "zero"` 换成 `assert isinstance(classify(0), str)` 再跑出来的。

---

## 8. 阻塞项与未验证项

| # | 事项 | 状态 |
| --- | --- | --- |
| 1 | `skills/attest/SKILL.md` 的判据表同步（14 → 17 条） | **阻塞在集成者**。本车道按约定不碰该文件；逐条改动清单见 `docs/attest-dpe-skill-md-changes.md` |
| 2 | 计划升 v2 + 补钉（lumen 侧） | 本车道只证明「引擎升上去之后机器看得见」；改计划是 lumen 侧的动作，且那里有并行车道在跑 |
| 3 | OCR 缺陷⑥（reconciliation 脚本 ENOBUFS）与⑦（`?? []`「取不到就当空」） | **不在现有 7 个算子的形态覆盖范围内** ⇒ 补钉也解决不了，要新算子或新判据。本轮不做 |
| 4 | `discriminate.run_dpsp` 的三元组契约 | `run_dpsp` 收 `(id, pin_cell, text)`，`plan.invariants` 是 `(id, desc, pin)`。顺序传错时不报错，只是全部落进「钉解析不出来」的 untested 档。本次踩过一次（`readiness._dpe_run_dpsp` 里有一行显式重排）。没有加断言，列为遗留 |
| 5 | 变异副本**不密闭** ⇒ 跨包引用的模块判不了 | 20 条里有 5 条因此落进 not-applicable：测试文件经**包名**引用 `@lumen/service-api` / `drizzle-orm` / `ai`，而变异只对相对路径可达的源文件生效 ⇒ 被引模块根本没被变异。要修得让副本里的包路径指向副本内实体。**本轮未做**（超出「判定点引擎」的范围，属变异基础设施） |

---

## 9. 复现命令

```bash
# 回归套件
for t in skills/attest/scripts/test_*.py; do python3 "$t" || echo "FAIL $t"; done
bash fixtures/run_fixtures.sh
python3 evals/run_evals.py
python3 package.py --check

# 三条判据的红→绿往返
python3 /tmp/dpe_verify/probe_criteria_roundtrip.py \
  /Users/zhangpeng/workspace/liaohe/attest/skills/attest/scripts

# 真实模块核对
python3 /tmp/dpe_verify/probe_alignment.py     # plan-alignment 全量结论
python3 /tmp/dpe_verify/probe_appendix_b2.py   # 附录 B 5 条金标逐条重推
python3 /tmp/dpe_verify/probe_dpsp20.py        # 20 条不变式全部进区分力自证
```

探针都在 `/tmp/dpe_verify/`（不进仓库）。注意 `req_plan.py upgrade` 是就地写的，对着 lumen 仓库里的
计划跑之前先 `cp` 到 `/tmp`。
