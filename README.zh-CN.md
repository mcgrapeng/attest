<div align="center">
  <img src="assets/logo.svg" width="96" height="96" alt="attest">
  <h1>attest</h1>
  <p><strong>把交付前提变成可机读、可核对、可审计的证据清单。</strong></p>
  <p><em>它不是「能不能交付」的裁判。它给证据，决策归人。</em></p>
  <p>
    <a href="https://github.com/mcgrapeng/attest/actions/workflows/ci.yml"><img alt="CI" src="https://img.shields.io/github/actions/workflow/status/mcgrapeng/attest/ci.yml?branch=main&style=flat-square&label=CI"></a>&nbsp;&nbsp;
    <a href="https://github.com/mcgrapeng/attest/blob/main/LICENSE"><img alt="License" src="https://img.shields.io/badge/license-MIT-0F766E?style=flat-square"></a>&nbsp;&nbsp;
    <a href="https://github.com/mcgrapeng/attest"><img alt="Python" src="https://img.shields.io/badge/python-3.11%2B-0F766E?style=flat-square"></a>&nbsp;&nbsp;
    <a href="https://github.com/mcgrapeng/attest"><img alt="Stars" src="https://img.shields.io/github/stars/mcgrapeng/attest?style=flat-square&label=stars&color=0F766E"></a>&nbsp;&nbsp;
    <a href="https://github.com/mcgrapeng/attest/blob/main/README.md"><img alt="English" src="https://img.shields.io/badge/English-0F766E?style=flat-square"></a>
  </p>
</div>

## 60 秒上手

skill 是纯 Python 3.11+ 标准库，除了它本身没有别的要装。

```bash
python3 install.py                 # 装进四个 agent 的 skills 目录
python3 ~/.agents/skills/attest/scripts/readiness.py <模块路径> --repo <项目根>
```

第二条命令会实跑目标仓库自己的测试与覆盖率闸门、查 PG catalog、驱动仓库自己的对账脚本，然后给你一份分层的证据清单。**它不会替你下「能不能交付」的结论。**

装到别的 agent、手工安装、Windows 注意事项见[「装」](#装)。

## 它怎么运作

```
  需求
   │
   ▼
  计划 ────── 每条不变式钉在一个真实存在的用例上
   │
   ▼
  readiness.py ──▶ 实跑仓库自己的测试 · 覆盖率闸门 · PG catalog · 对账脚本
   │
   ▼
  证据清单 ──▶ 已实测 │ 静态信号 │ 未实测 │ 本工具不判定
```

## 它测什么：14 条判据

工具的输出正文是中文，下表第一列是它**实际打印的判据名**（不是改写），`key` 是 `--json` 里的机器可读标识。

**测测试本身（5 条）**

| 判据 | key | 证据类别 | 怎么测 |
| --- | --- | --- | --- |
| 测试全绿 | `tests-green` | 实测 | 用**项目自己的** test 命令跑模块测试，列出失败用例（**只认断言失败**；setup 报错 = 环境没就绪 ⇒ 未实测） |
| 核心逻辑测试到位 | `coverage` | 实测 | **驱动仓库自己的覆盖率闸门**；没闸门才用 `--min-line-coverage` 显式指定（默认不阻塞） |
| 测试能发现缺陷 | `test-strength` | 实测 | 把源码改坏再跑测试：测试红了 = 杀得死，测试还绿 = **这套测试发现不了这类缺陷**（`--mutation-check`，默认不跑 ⇒ 默认未实测） |
| 测试卫生（不留残留、不靠 sleep） | `hygiene` | 静态 | 读测试源码：固定等待（flaky 源）与不清理的写库测试（残留源） |
| 导出的逻辑都有测试 | `uncovered-exports` | 静态 | 读测试源码找引用：导出的函数/类零引用 ⇒ 点名（TS 导出 / Python 公开 def·class / Rust `pub fn`） |

**测数据与存储（4 条）**

| 判据 | key | 证据类别 | 怎么测 |
| --- | --- | --- | --- |
| 表结构基本合规 | `schema` | 实测 | 查 PG catalog：主键 / 唯一约束 / 租户列 / RLS+FORCE / 时间戳带时区（单租户、配置表、追加表有豁免） |
| 向量集合属性自洽 | `vector-schema` | 静态 | 同一集合在不同调用点的维度/度量/分区键是否一致；分区键不能是向量字段；写入与检索的集合是不是同一个 |
| 数据流一致（跨存储对账） | `data-flow` | 实测 | **实跑**模块自己的对账脚本，解析漂移；**按租户口径**判；字段级比对并入这条 |
| 数据准确（基线对拍） | `data-accuracy` | 实测 | **实跑**项目的基线对拍脚本，解析指标差异 |

**测需求与设计（3 条）**

| 判据 | key | 证据类别 | 怎么测 |
| --- | --- | --- | --- |
| 需求覆盖到位 | `requirement` | 静态 | 读计划与测试文件：每条不变式的钉 → 真实存在的用例（`--plan`）；计划未经人工确认时降为提醒级 |
| 不过度抽象 | `extensibility` | 静态 | 导出符号全仓几乎没有调用点（规则之三） |
| 没有重复造轮子 | `no-wheels` | 静态 | 全仓同名实现（跨域同名降为提醒） |

**测依赖与断言（2 条）**

| 判据 | key | 证据类别 | 怎么测 |
| --- | --- | --- | --- |
| 使用主流开源库 | `open-source` | 静态 | 模块真实依赖清单（排除路径别名与标准库） |
| 断言的期望值独立 | `oracle` | 静态 | 期望值取自被测对象自身的断言（恒真的同义反复单独点出）⇒ 提醒 |

**证据类别说的是「这条判据靠什么得出」**（跑出来的 vs 读源码读出来的）。它同时决定报告里它落在哪一节：**静态信号永远不能单独支撑「通过」**。类别按**每次结果**标：需要数据库的判据连不上库时会标成「未实测」而不是「实测」。

## 输出长什么样

下面是仓库自带 fixture（`fixtures/ts-colocated`）的真实输出：

```
交付就绪度分析 — src
仓库：<项目根>
源码 1 文件（+0 个测试支撑） / 单测 1 个 / itest 0 个

── 已实测 ───────────────────────────────────────────
  ✓ 测试全绿 — 没有失败用例
  — 核心逻辑测试到位 — 仓库没有覆盖率闸门，缺少判据（实测平均行覆盖 100% / 分支 100%）；通用阈值默认不阻塞

── 静态信号（不能单独支撑「通过」） ──────────────────────────────
  ✓ 测试卫生（不留残留、不靠 sleep） — 没有固定等待，也没发现不清理的写库测试
  ✓ 导出的逻辑都有测试 — 2 个运行时导出都有测试引用
  · 使用主流开源库 — 模块没有第三方依赖 —— 确认这是有意的（简单优先）
      → 如果需求领域有成熟库，别自己写

── 未实测 ───────────────────────────────────────────
  — 测试能发现缺陷 — 未实测：加 --mutation-check 才会把源码改坏再跑测试
  — 表结构基本合规 — 没连上数据库（或模块不碰 PG）
  — 数据流一致（跨存储对账） — 模块只碰了 PG 一个存储 —— 没有派生层要跨存储校对
  — 需求覆盖到位 — 没给计划文件（--plan）

── 本工具不判定 ────────────────────────────────
  需求是否被正确理解 · 产品行为是否正确 · 性能 · 安全 · 可用性 · 运维就绪
  上面只是测试就绪度的实测结果；「能不能交付」仍要人判断。

结论：0 项阻塞 · 1 项待人工确认 · 7 项无信号 · 1 项实测通过（另有 6 条静态信号）
```

注意最后那段：**7 项无信号**，不是「7 项通过」。没测到的判据写「未实测」，不写「通过」。

## 怎么用：四步

1. **出计划骨架**：`req_plan.py new` 扫目标模块已有的 helper 与项目自己的测试车道，生成不变式清单与覆盖矩阵的空表。开工时做。
2. **填不变式与矩阵**：每条不变式写成一句**可证伪**的话（例「导出结果包含本月全部已完成订单，不多不少」），每行矩阵给出对应测试文件。这是人读的部分。
3. **核对计划 vs 代码**：`req_plan.py check` 逐条核：钉指向的文件存在吗、钉的用例真在那个文件里吗。报红退出码 1。
4. **判定就绪度**：`readiness.py` 实测模块的交付前提，产出上面那份证据清单。**这一步不需要计划也能跑**（只是「需求覆盖到位」会判未实测）。

第 2 步之后还可以 `req_plan.py scaffold` 按仓库自己的测试约定生成测试文件骨架。认不出约定时它**拒绝生成**并报红，不猜。

## 装

```bash
python3 install.py              # 跨平台（纯标准库，没有 bash 依赖）
# 或者：bash install.sh         # 同一份实现的薄壳
```

它把本仓库软链到 `~/.agents/skills/attest`，再软链到四个 agent 的 skills 目录：

| 目标 | 路径 |
| --- | --- |
| 稳定入口 | `~/.agents/skills/attest` |
| Claude Code | `~/.claude/skills/attest` |
| Codex | `~/.codex/skills/attest` |
| OpenCode | `~/.config/opencode/skills/attest` |

已存在的同名软链会被覆盖（会打印提示）；目标位置若是**真实目录**则跳过，不删用户数据。装完会自检一次（`readiness.py --help` 能跑通）。

卸载：`python3 install.py --uninstall`（或 `bash uninstall.sh`），只删软链。

Windows 上 `os.symlink` 可能需要管理员或开发者模式；失败时会打印等价的手工命令，不删用户数据。

手工装等价于：

```bash
ln -s "$(pwd)" ~/.agents/skills/attest
ln -s ~/.agents/skills/attest ~/.claude/skills/attest
ln -s ~/.agents/skills/attest ~/.codex/skills/attest
ln -s ~/.agents/skills/attest ~/.config/opencode/skills/attest
```

`~/.agents/skills/attest` 是稳定入口，`~/.agents/testkit/doctor.sh` 从这里跑回归套件。

## 打包（.skill）

```bash
python3 package.py            # → dist/attest.skill
python3 package.py --check    # 只看会打进包的内容，不写文件
```

`.skill` 是社区通行的分发格式（一个 zip，第一层是 skill 目录）。打包脚本**自包含**，不依赖别的工具。排除：`.git` / `.github` / `dist` / `fixtures` / `evals` / `.omx` / 字节码，以及 `scripts/test_*.py`（回归套件是仓库的开发资产，不是 skill 的运行时）。

**打进包前会检查有没有字节码**，有就中止：字节码既泄漏作者环境，又让用户拿到不可读的产物。

## 常用开关

`readiness.py` 的：

| 开关 | 干什么 |
| --- | --- |
| `--plan <文件>` | 要核对的需求测试计划（驱动「需求覆盖到位」这条） |
| `--json` | 机器可读输出（checks + 结论计数） |
| `--dry-run` | **只列出**将要执行的仓库脚本与命令，然后退出（不执行）。跑之前先看它要干什么 |
| `--self-check` | 反向验证：往**临时副本**里注入一个已知缺陷，证明判据会翻红。不改你的仓库 |
| `--self-check-deps` | 配合 `--self-check`：把 `node_modules` / `.venv` 软链进副本，让副本能真跑测试（这样才验得到**实测类**判据，不只是静态判据） |
| `--mutation-check` | 测试强度：把源码改坏，看**测试会不会红**。要复制工作区并多次跑测试，很贵 ⇒ **默认不跑**（不跑时那条判据是「未实测」） |
| `--include-itest` | 把真后端 itest 车道也算进来（要连 PG + 向量库，慢） |
| `--test-tenant <名字或前缀>` | 声明测试租户，可重复 —— 测试租户的漂移是夹具状态，报 warn 不阻塞 |
| `--test-tenant-prefix <前缀>` | 追加测试租户前缀（默认认 `itest-` `memtest-` `mock-` `eval-` 等） |
| `--test-dir <路径>` | 额外的测试目录（`test/` `tests/` `spec/` 之外还有别处放测试时用），可重复 |
| `--drift-pattern <正则>` | 对账输出里「漂移行」的样子（默认认 `漂移` / `❌` / `drift` / `mismatch`） |
| `--table-header-pattern <正则>` | 对账输出里表头/分隔行的样子（默认认 `一致性` / `租户` / `tenant` / `---`） |
| `--csv-pair a.csv=b.csv` | CSV 对照关系，可重复 —— 按声明配对，不做两两配对 |
| `--tenant-col <列名>` | CSV 里租户列的名字（默认按 `tenant_id`/`tenant` 认） |
| `--min-line-coverage` / `--min-branch-coverage` | 显式给覆盖率门槛。**默认不用通用阈值**（仓库没有闸门时判 skipped，不判 fail） |
| `--database-url <连接串>` | PG 连接串（默认取环境里的 `POSTGRES_URL`） |
| `--verbose` | 把测试命令的原始输出也打出来 |

`req_plan.py` 的子命令与额外开关：`new` 接 `--out`（默认 `docs/<feature>-test-plan.md`）与 `--force`；`scaffold` 接 `--out-dir`（默认仓库根）、`--framework`（`node:test` / `vitest` / `jest` / `mocha`）与 `--force`；`check` 只接 `<计划文件>` 与 `--repo`。

## 判据是怎么测的

- **门槛归仓库所有**。仓库有覆盖率闸门就驱动它，判定看它的退出码；通用阈值只在仓库没有闸门时兜底（且默认不阻塞）。
- **实测优先**。每条判据要么有实测证据，要么明说环境没就绪（缺什么条件），不拿静态推断冒充实测。
- **跑不起来 ≠ 不达标**。setup 报错、缺产物、连不上中间件，都判 skipped 并说清缺什么。
- **判交付看生产租户**。测试租户的漂移是夹具状态，报 warn 不阻塞。
- **跨存储字段级比对**。同一个 id 在权威库与派生库里的同一个字段值对不上，点名 id 与字段。
- **覆盖率高 ≠ 测试有劲**。覆盖率只说明代码被执行过，不说明断言会失败 ⇒ 另有「测试能发现缺陷」这条。
- **环境没就绪 ≠ 杀死了**。变异点跑出的是环境错误而不是断言失败时，那个点判**未判定**，不算「测试发现了缺陷」。
- **PG 侧与集合侧对称**。表结构有五条自有规则，向量集合也有对应的一条：同一集合的属性在调用点之间必须自洽。

### 输出契约

报告分四段，结论**不是**一句「可以交付 / 不可以交付」：

| 段 | 意思 |
| --- | --- |
| **已实测** | 实测出来的结果（跑过测试、跑过覆盖率、查过 PG） |
| **静态信号** | 静态分析的信号 —— 标注了「不能单独支撑「通过」」 |
| **未实测** | 没测出来的（环境没就绪、找不到测试文件、没有可分析的导出符号） |
| **本工具不判定** | 固定清单，见下 |

结论分三档：

| 档 | 意思 |
| --- | --- |
| **阻塞项** | 实测出来不达标的 —— 有它就不能交付 |
| **待人工确认** | 实测了，但结论要人拿主意（正文类字段不一致、旁路存储是不是权威数据…） |
| **无信号** | 没测出来的 —— 环境没就绪、找不到测试文件、这个模块没有可分析的导出符号 |

静态信号永远不能单独支撑「通过」。工具固定列出一段 **本工具不判定** 的清单：

> 需求是否被正确理解 · 产品行为是否正确 · 性能 · 安全 · 可用性 · 运维就绪

这六项**不在**本工具的判定范围内。交付决策仍然由人承担。

退出码：

| 码 | 意思 |
| --- | --- |
| 0 | 没有阻塞项 |
| 1 | 有阻塞项，或关键判据（测试是否绿）没测到 —— 不能判「达标」 |
| 2 | 认不出这个模块（语言不在支持范围、路径不存在、参数非法）—— 说清原因，不给假结论 |

`--self-check` 失败也退出 1，但那是**工具自测**失败（判据没抓住注入的缺陷），不是对模块的结论。`req_plan.py check` 计划有错时退出 1；`new` 遇到已存在的计划文件且没给 `--force` 时退出 1。

## 语言支持

| | 计划生成 | 覆盖率实测 | 导出引用判据 | 跨存储对账 | 真后端 itest 车道 |
| --- | --- | --- | --- | --- | --- |
| TypeScript / JavaScript | ✅ | ✅ node / vitest / jest | ✅ | ✅ | ✅ |
| Python | ✅ | ✅ pytest-cov | ✅ 模块级 `def`/`class` | ✅ | ✅ |
| Rust / Go | 部分（复用侦察） | — 识别不到模块 | — | — | — |

计划生成器在复用侦察里认 Rust 与 Go 的导出（导出索引认 Rust `pub fn` 与 Go 顶层函数），`req_plan.py scaffold` 也有 Rust / Go 分支。停在语言边界上的是**就绪度判定器**。

**车道与对账脚本怎么找**：JS 仓从 `package.json` 的 scripts 认；Python 仓从 `Makefile` 目标与 `tox.ini` 环境认（例 `make coverage`、`make integration-store`、`make reconcile-store`、`[testenv:integration]`）。两条路都要满足两点：

1. 目标名里带关键词（`cov` / `reconcile` / `integration` / `itest`）；
2. 目标体或命令里提到**这个模块**（路径或表名）。工具要确认这是该模块的车道，不是仓库里别人的。

Rust / Go 模块上 `readiness.py` **退出码 2** 并说清「当前只实现了 TS/JS/Python」，不给假结论：「支持了但没数据」与「认不出来」是两回事，只有后者是真的。一个满屏 ✓ 但其实什么都没测的报告，比报错更糟。

## 安全须知

`readiness.py` 会**执行目标仓库自己定义的脚本**（`package.json` 里的 `test`、`coverage`、`reconcile`、`eval:baseline:check` 等）。三件事要知道：

1. **先看它要跑什么**。`--dry-run` 只列出候选脚本与将执行的命令，不执行。正常模式下工具也会在执行前把那几行（白名单命中的脚本 + 确切命令）打印出来。那是审计入口。脚本选取走**显式白名单**（精确脚本名或白名单前缀），名字里带关键词的脚本（例如 `reconcile:reset`）不会被自动执行。
2. **子进程只拿到白名单环境变量**：`PATH`、`HOME`、`LANG`/`LC_*`、`TMPDIR`、`NODE_*`、`PYTHON*`，以及工具自己需要的 `POSTGRES_URL` / `DATABASE_URL` / `MEMORY_ITEST` / `REQ_PLAN_RECONCILE_OUT_DIR`。**其余环境变量一律不透传**：你的云厂商密钥、npm token 之类不会进目标仓库的脚本。注意那两个连接串**会**透传：仓库自己的集成车道要连库，这是刻意的取舍 —— 不想要就把它们从白名单里去掉，或改用 `.pgpass`。`psql` 的口令走 `PGPASSWORD` 环境变量，不出现在命令行参数里。
3. **信任一个仓库的 `test` 脚本，就等于把它交给了你的环境变量**。这一层的风险等级 = 它所运行的每一个脚本的风险等级。

`req_plan.py scaffold` 会向仓库写测试文件。写入前有两道闸：钉的路径必须规范化后仍在 `--out-dir` 之下（越界的钉不读也不写），用例名按语言做引号转义（计划 markdown 不会被变成可执行代码）。认不出仓库的测试约定时它**拒绝生成**并报红，不猜。

`readiness.py` 也会读计划里的钉，规则相同：指向仓库外的钉既不读也不写。

## 已知缺口

诚实清单：完整版见 [CHANGELOG.md](CHANGELOG.md) 的「已知遗留」，逐条评审记录见 [docs/oss-release-review.md](docs/oss-release-review.md) 与 [docs/testing-philosophy-review.md](docs/testing-philosophy-review.md)（只读）。发布时仍开放的是这七条：

| 项 | 状态 |
| --- | --- |
| SkillSpector 的 `E2`（Env Variable Harvesting）仍报 HIGH | 子进程必须透传环境变量（白名单实现）。CI 用 `.github/skillspector-allowlist.yaml` 把**已复核的规则**显式放行，每条带理由，allowlist 之外的新 HIGH 仍然拦住。放行是**规则级、不是逐条 finding**：逐条基线按位置指纹匹配，改一行代码就失效 ⇒ 对贡献者是持续的假红。规则级放行不依赖位置，而**新规则**仍然会被拦。代价写在 allowlist 文件头，不藏着 |
| `--exec` 显式执行开关没有采纳 | 实现是**默认执行 + 执行前打印审计行** + `--dry-run` 可只看不做。工具的定位就是实测（不跑测试的交付判定没有意义），执行面已收敛到脚本白名单 + 环境变量白名单 |
| Python 没有真后端（itest）车道 | TS 侧有 `--include-itest`，Python 侧没有对应机制 |
| Windows 未端到端验证 | skill **运行时**是纯 Python、**安装**走 `install.py`（跨平台），这两块与平台无关。但仓库自己的开发脚本依赖 bash，在 Windows 上要 WSL 或 Git Bash |
| 触发准确度 | 已实测：精确率 1.0（近邻反例一条都没误触发）、召回率 0.5、F1 0.667。数据与解读见 [evals/README.md](evals/README.md)。**这个数测的是本机 60 多个 skill 的竞争环境**，OSS 用户 skill 集合不同、不可直接迁移 |
| 自检副本默认不带 `node_modules` | 多数仓库在副本里跑不动测试，`--self-check` 默认证明的是「静态判据抓得住东西」。报告里明说了这件事（`copy_runnable: false`）。加 `--self-check-deps` 会把依赖**软链**进副本，那时能验到实测类判据 |
| 向量集合与**线上真实租户**是否一致 | 不判。这条要连活的向量库并用项目自己的凭据 —— 本工具不替项目保管向量库凭据，也不替项目猜集合命名规范。现在判的是**代码侧自洽** |

另有一条不在 CHANGELOG「已知遗留」里的：`SKILL.md` 正文仍是中文。README 有中文版，skill 正文没有。

## 开发与验证

本仓库把自己的工具用在了自己身上，证据可复现：

| 闸门 | 命令 | 结果 |
| --- | --- | --- |
| 回归套件 | `for t in scripts/test_*.py; do python3 "$t"; done` | 24/24 全绿 |
| 黄金 fixture | `bash fixtures/run_fixtures.sh` | 5/5 命中，0 不符，0 未判定 |
| 行为 eval | `python3 evals/run_evals.py` | 6/6 通过 |
| 安全扫描 | `bash .github/check-skillspector.sh` | 47 条 HIGH/CRITICAL，未放行 **0** 条 |
| 环境收口 | `python3 scripts/check_env_inheritance.py` | 生产代码没有整份继承 `os.environ` |
| 反向验证 | `python3 scripts/selfcheck.py src --repo fixtures/ts-colocated` | 结论：反向验证通过 |

**黄金 fixture** 是 5 个自包含的小仓库，每个带**钉死的期望判定**，是「这个工具会不会假绿」的可复现证据（例 `no-tests` fixture 钉死「结论不得是『达到交付条件』」）。每个 fixture 目录里的 `README.md` 写明**为什么有它**（挡的是评审里的哪一条质疑）。

**evals** 是 6 条「用户这么说 ⇒ 工具这样回」的行为断言，每条带可机检的判据（期望退出码 / 必须出现的字符串 / 必须不出现的字符串）。跑法：

```bash
python3 evals/run_evals.py            # 跑全部
python3 evals/run_evals.py --only 3   # 只跑某一条
python3 evals/run_evals.py --json     # 机器可读
```

退出码：0 = 全部通过 / 1 = 有断言不通过 / 2 = 没有可跑的 eval（例如 `--only` 给了不存在的 id）。

CI（`.github/workflows/ci.yml`）跑四个 job：字节码卫生 / 回归+fixture+eval / 打包冒烟 / 安全扫描。

## License

MIT
