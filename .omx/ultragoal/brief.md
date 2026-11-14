# req-test-plan 发布就绪推进 brief

## 目标

把 `req-test-plan` skill 推进到**可发布**：修完 `docs/oss-release-review.md` 的工程缺口与
`docs/testing-philosophy-review.md` 的哲学缺口，并用**可复现证据**证明每一条已关闭。

终极目标按用户的原话是"一条命令、基于实测，判定这个模块能不能直接交付给客户"。
经哲学审（见 `docs/testing-philosophy-review.md` 第四节），该表述本身不可达，本轮采用的
可诚实交付的替代表述是：

> 这个模块的交付前提里，哪些已被实测证明、哪些还没被证明、哪些本工具判不了。

工具的输出契约必须体现这个表述。

## 硬约束

1. 只用 Python 3.11+ 标准库，**不加任何第三方依赖**。生产脚本（`readiness.py`、
   `req_plan.py`）必须保持 stdlib-only。
2. **不得引入网络访问**。
3. 不新增运行时依赖；不引入 cron/daemon/后台进程。
4. 测试文件沿用现有风格：`expect()` + `FAILURES` 列表 + 结尾打印"全部通过"，退出码非 0 表示失败。
   新测试文件名用 `test_<主题>.py`，**必须能在干净机器上独立跑通**（不得依赖另一个 skill 的绝对路径）。
5. 旧的测试断言**不许删**，除非断言本身与新契约矛盾（那种情况要在提交信息里逐条说明理由）。
6. 不得改动 `docs/oss-release-review.md` 与 `docs/testing-philosophy-review.md` 里已有的
   结论与证据（那是评测记录，只能追加关闭状态）。
7. 每个修复必须留下**回归断言**。不允许"顺手重构"造成的无关 diff。
8. Python 源码与文档一律中文散文，标识符/命令/路径保持原样。测试套件名与用例标题用简洁英文。

## 目标清单

### G001 交付判定正确性与执行面收敛（owner: `scripts/readiness.py`）

必须逐条闭合下列缺口，每条都要有回归断言：

1. **零测试假绿**（`readiness.py:1170-1179` + `430-431`）。当模块**一个测试文件都没找到**时，
   `check_tests_green` 必须返回 `skipped`（说明"没有测试文件 ⇒ 无法判定测试是否绿"）或 `fail`，
   **不得**返回 `pass`。整体结论也不得因此变成"达到交付条件"。
2. **测试发现作用域**（`readiness.py:104`）。测试发现必须覆盖仓库级 `test/` `tests/` `spec/`
   目录（以及模块内 colocated 的 `*.test.*` `*.spec.*` `*.itest.*`），并提供 `--test-dir`
   参数让用户显式指定。当前行为（测试在 `test/` 下 ⇒ 报"3/3 个导出零引用"）必须消失。
3. **覆盖率 include glob 硬编码 `**/*.ts`**（`readiness.py:440,442,444,509,511,513`）。
   必须按 `CODE_EXT`（`readiness.py:27-28`）推导实际存在的扩展名。JavaScript 项目必须能
   拿到覆盖率，不再出现"没能拿到覆盖率"。
4. **依赖抽取只认 `from '…'`**（`readiness.py:140-152`）。必须覆盖 `require('x')`、
   `import 'x'`、Python 的 `import x` 与 `from x import y`。Python 仓库不得再报
   "模块没有第三方依赖"。
5. **Python 下空集假绿**（`check_extensibility` `readiness.py:1647-1659`、
   `check_no_wheels` `1662-1683`）。两者必须加 Python 分支（复用 `python_exports`）；
   在加分支之前，Python 必须返回 `skipped` + 说明，**不得**输出"模块的导出符号在全仓都是
   独一份""0 个导出符号都有多个调用点"这类零分析的正面结论。
6. **表设计无条件要求租户列与唯一约束**（`readiness.py:1551-1552`）。必须给豁免条件：
   单租户项目（无租户列体系的仓库）、全局配置表、日志/追加型表不得假红。判据名从
   "表设计符合主流"改成能反映实际内容的名字（例如"表结构基本合规"），并在证据里说清查了哪几条。
7. **判据"具备扩展性"名不副实**（`readiness.py:1647-1659`）。改名"不过度抽象"。
8. **对账层只懂一种中文方言**（`readiness.py:806-807` 的 `"漂移" in ln and "❌" in ln`、
   `810` 的中文"一致性"表头排除、`934-939` 的租户列位置、`783-784` 的 lumen 专属
   `MEMORY_RECONCILE_OUT_DIR`、`911-914` 的硬编码测试租户前缀）。
   要求：漂移/租户/表头的识别可配置（参数或 JSON 输出）；测试租户前缀可配置；
   输出目录用工具自设自读的通用变量并兼容旧变量；**找不到可比对的 CSV 对时必须报"无法比对"**，
   不得报"0 对无差异"。
9. **CSV 两两配对 + 全字段比对**（`readiness.py:847-848`）。必须按声明的对照关系配对，
   排除时间戳类字段与正文类字段；正文不一致仍按原口径报给人判断，**不得**自动判 fail。
10. **通用阈值 80/70 与文档矛盾**（`readiness.py:1421-1422`、`1359-1360` vs
    `SKILL.md:100-103`）。无仓库闸门时必须 `skipped` 并说明"仓库没有覆盖率闸门，缺少判据"，
    **或**改成显式 opt-in（`--min-line-coverage`）且默认不阻塞。**不得**默认用 80/70 判 fail。
11. **Python 仓库读不到仓库自己的闸门**（`find_gate_scripts` `readiness.py:624-676` 只读
    `package.json`）。必须支持从 `pyproject.toml` / `Makefile` / `tox.ini` 读到仓库声明的
    覆盖率闸门与阈值，阈值归仓库所有。
12. **itest 车道默认行为与文档矛盾**（`readiness.py:1733` vs `SKILL.md:87,145-147`）。
    默认不跑真后端车道，`--include-itest` 才跑；文档同步改。
13. **脚本选取是子串匹配**（`readiness.py:635` "coverage"、`753` "reconcile"、
    `689` "integration"/"itest"、`1044-1046` "baseline"+"check"）。必须改为**显式白名单**
    （只认精确脚本名，或脚本名 + 命令同时命中），并且新增 `--dry-run`（只列出候选脚本与
    将执行的命令后退出，不执行）与 `--exec`（显式开关执行）。
14. **子进程无条件继承完整 `os.environ`**（`readiness.py:447,515,540,714,783,1061`）。
    必须改为**白名单透传**（PATH、HOME、LANG、LC_*、TMPDIR、NODE_*、PYTHON*、以及工具
    明确需要的 `POSTGRES_URL` / `MEMORY_ITEST` / `MEMORY_RECONCILE_OUT_DIR` 等），
    其余环境变量不透传。
15. **`psql` 连接串走 argv**（`readiness.py:552-556`）。必须改用 `PGPASSWORD` 或
    `PG*` 环境变量 / `.pgpass`，**不得**把含口令的连接串放进命令行参数。
16. **判据表条数与代码不一致**（`SKILL.md:27-38` 12 行 vs 代码 11 个 Check）。
    文档与代码必须一致（合并"字段级跨存储比对"进"数据流一致"，或让它成为独立 Check）。
17. **`Check.status` 缺"未实测"态**（`readiness.py:59`）。为 G004 预留：每条判据带
    `evidence` 标签（executed / static），G004 会用它分层输出。

新增测试文件：`scripts/test_readiness_r2.py`。

### G002 写入安全与语言感知（owner: `scripts/req_plan.py`）

1. **`scaffold` 路径穿越**（`req_plan.py:1715` 的 `target = out_dir / fpath`，
   `_PIN_RE` `req_plan.py:45-48` 允许 `..` 与 `/`）。必须校验 `fpath` 规范化后仍在
   `out_dir` 之下；含 `..`、绝对路径、或越出仓库边界的钉必须**拒绝并报红**，不得写盘。
2. **`scaffold` 代码注入**（`req_plan.py:1556` 把用例名原样插进 `it('...')`）。
   必须做单引号转义（或改用双引号包裹并转义），生成的代码里不得出现可执行副作用。
3. **scaffold 不认语言**（`detect_conventions` `req_plan.py:838` 只遍历
   `{.ts,.tsx,.js,.mjs}`，默认落回 `node:test`/`node:assert` `req_plan.py:815-816`）。
   必须加 Python / Rust / Go 分支；**在实现之前，遇到仓库里没有 JS/TS 测试文件时必须
   拒绝生成并报错**，不得把 TypeScript 写进 `.py` 路径。
4. **r3/r4 硬编码家目录**（`test_req_plan_r3.py:122,142,173`、
   `test_req_plan_r4.py:14,16` 指向 `/Users/zhangpeng/.agents/skills/ai-testing-system/scripts/`）。
   必须改为从 `__file__` 推导；`ai-testing-system` 不存在时**跳过相关断言并打印清晰的 skip
   提示**，不得崩溃、不得静默失败。
5. **`req_plan.py:9` 的"只读代码，不改任何东西"**与 `scaffold` 写文件矛盾。改准确。

新增测试文件：`scripts/test_req_plan_scaffold_safety.py`。

### G003 发布骨架与可复现证据（owner: `.github/**`, `evals/**`, `fixtures/**`,
`CHANGELOG.md`, `install.sh`, `uninstall.sh`, `README.md`）

1. **CI**：`.github/workflows/ci.yml`，Node 22 + Python 3.11/3.13，跑全部测试脚本；
   加一条"存在 `.pyc` / `__pycache__` 即失败"的检查；加一条打包冒烟（装进临时目录跑
   `req_plan.py new|check` 与 `readiness.py --json`）。
2. **黄金 fixture**：至少 5 个，每个都有钉死的期望判定：
   - TS 仓（测试 colocated）
   - Python 仓（pytest 系）
   - Rust 仓 ⇒ 期望退出码 2 且说明"当前只实现了 TS/JS/Python"
   - **输出英文的对账脚本** ⇒ 期望暴露漂移而不是假绿
   - **无任何测试的模块** ⇒ 期望**不得**判"达到交付条件"
   fixture 放 `fixtures/<name>/`，每个带 `README` 说明期望与为什么。
3. **`evals/evals.json`**：至少 5 条 eval，每条含 `id` / `prompt` / `expected_output` /
   `assertions`，断言必须可机检（期望退出码、期望输出里必须出现的字符串、必须不出现的字符串）。
4. **CHANGELOG.md**：本次变更的条目。
5. **install.sh / uninstall.sh**：安装到三个 agent 的 skills 目录（软链），卸载要能干净还原。
6. **README.md** 更新：语言支持矩阵（新）、Security 一节（保留并补 `--dry-run`/`--exec` 说明）、
   R1-R4 之后的输出契约说明、已知缺口清单（逐条标注已关闭 / 仍开放）、fixture 用法。

### G004 哲学达标：输出契约与反向验证（owner: `scripts/readiness.py` +
`scripts/test_readiness_philosophy.py`）

必须同时满足 `docs/testing-philosophy-review.md` 第五节的 R1-R4：

- **R1 输出契约**：报告分两层（**已实测** / **静态信号**）；静态信号永远不能单独支撑"通过"；
  结论改成三档（阻塞项 N 项 / 待人工确认 N 项 / 无信号 N 项），去掉"达到交付条件"这类
  裁决措辞；固定一段"**本工具不判定**"清单（需求正确性、性能、安全、可用性、运维就绪）；
  退出码保留（CI 需要）但语义改为"是否存在阻塞项"，与证据类别一致。
- **R2 反向验证 `--self-check`**：对目标模块注入一个**已知缺陷**（改动一行让某条不变式
  失效），跑一遍判据，展示它翻红。**默认关闭**，只在 `--self-check` 下启用，且**只作用于
  临时副本**，绝不修改用户仓库的文件。
- **R3 oracle 独立性启发式检查**：扫测试文件，标出"期望值取自被测对象自身"的常见形态
  （断言两侧调同一模块、快照自比对、把 SUT 输出当基线）。启发式即可，判**提醒**不判红。
- **R4 判据带证据类别**：每条判据在代码里带 `evidence` 标签（executed / static），
  报告按标签分节。顺带把"使用主流开源库"这条空判据处理掉：要么实现真判定，要么降级为
  静态信号。

### G005 发布前独立复评与全闸门通过（owner: 只读复评 + 全量验证）

必须逐条核对两份评测报告里的缺口，逐条给出"已关闭 / 仍开放 + 证据"，并产出
`docs/oss-release-review-v2.md` 与 `docs/testing-philosophy-review-v2.md`。

## 验收闸门（全绿才算完成）

1. 全部测试脚本（含新增）在**干净临时目录**里逐字通过，退出码 0。
2. `skillspector scan --no-llm --format json` **无 HIGH** finding。
3. 5 个黄金 fixture 的期望判定全部命中（逐个核对退出码与输出关键串）。
4. `--self-check` 能让至少一条判据从通过翻成不通过。
5. `~/.agents/skills/req-test-plan/scripts/*.py` 仍然存在且可跑（软链路径未断）。
6. `git status` 干净，所有改动已提交。

## 文件所有权（并行车道不得越界）

| 车道 | 可改文件 |
| --- | --- |
| A | `scripts/req_plan.py`, `scripts/test_req_plan_r3.py`, `scripts/test_req_plan_r4.py`, `scripts/test_req_plan_scaffold_safety.py` |
| B | `.github/**`, `evals/**`, `fixtures/**`, `CHANGELOG.md`, `install.sh`, `uninstall.sh`, `README.md` |
| C | `scripts/readiness.py`, `scripts/test_readiness_r2.py` |
| D（G004，排在 C 之后） | `scripts/readiness.py`, `scripts/test_readiness_philosophy.py` |

任何车道**不得**改 `docs/oss-release-review.md`、`docs/testing-philosophy-review.md`
（评测记录只读）、`SKILL.md`（统一由集成方改，避免冲突）。
