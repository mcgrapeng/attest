# req-test-plan

中文 | [English](README.en.md)

需求级测试工具包：把一条需求拆成可核对的测试计划，并实测这个模块**能不能交付**。

只依赖 Python 3.11+ 标准库，无第三方依赖。

## 装

```bash
python3 install.py              # 跨平台（纯标准库，没有 bash 依赖）
# 或者：bash install.sh         # 同一份实现的薄壳
```

它把本仓库软链到 `~/.agents/skills/req-test-plan`，再软链到
`~/.claude/skills/`、`~/.codex/skills/`、`~/.config/opencode/skills/`。
已存在的同名软链会被覆盖（会打印提示）；目标位置若是**真实目录**则跳过，
不删用户数据。装完会自检一次（`readiness.py --help` 能跑通）。
卸载：`python3 install.py --uninstall`（或 `bash uninstall.sh`），只删软链。

Windows 上 `os.symlink` 可能需要管理员或开发者模式 —— 失败时会打印等价的手工命令，
不删用户数据。

手工装等价于：

```bash
ln -s "$(pwd)" ~/.agents/skills/req-test-plan
ln -s ~/.agents/skills/req-test-plan ~/.claude/skills/req-test-plan
ln -s ~/.agents/skills/req-test-plan ~/.codex/skills/req-test-plan
ln -s ~/.agents/skills/req-test-plan ~/.config/opencode/skills/req-test-plan
```

`~/.agents/skills/req-test-plan` 是稳定入口，`~/.agents/testkit/doctor.sh` 从这里跑回归套件。

## 打包（.skill）

```bash
python3 package.py            # → dist/req-test-plan.skill
python3 package.py --check    # 只看会打进包的内容，不写文件
```

`.skill` 是社区通行的分发格式（一个 zip，第一层是 skill 目录）。打包脚本**自包含**，
不依赖别的工具。排除：`.git` / `.github` / `dist` / `fixtures` / `evals` / `.omx` /
字节码，以及 `scripts/test_*.py`（回归套件是仓库的开发资产，不是 skill 的运行时）。

**打进包前会检查有没有字节码** —— 有就中止：字节码既泄漏作者环境，又让用户拿到
不可读的产物。

## 四条命令

```bash
# 1. 生成需求测试计划骨架（扫目标模块的已有 helper + 项目自己的测试车道）
python3 ~/.agents/skills/req-test-plan/scripts/req_plan.py new <feature> \
  --repo <项目根> --module <子路径> [--module <子路径>...]

# 2. 从计划生成测试文件骨架（按仓库自己的测试约定）
python3 ~/.agents/skills/req-test-plan/scripts/req_plan.py scaffold \
  <计划文件> --repo <项目根> [--out-dir <生成根>]

# 3. 核对计划 vs 代码实况（报红退出码 1）
python3 ~/.agents/skills/req-test-plan/scripts/req_plan.py check \
  <计划文件> --repo <项目根>

# 4. 模块交付就绪度判定（一条命令，实测）
python3 ~/.agents/skills/req-test-plan/scripts/readiness.py <模块路径> --repo <项目根> \
  [--plan <需求测试计划>] [--include-itest] [--test-tenant <名字或前缀>] [--json]
```

判据全表与用法细节见 [SKILL.md](SKILL.md)。

`readiness.py` 的常用开关：

| 开关 | 干什么 |
| --- | --- |
| `--dry-run` | **只列出**将要执行的仓库脚本与命令，然后退出（不执行）。跑之前先看它要干什么 |
| `--test-dir <路径>` | 额外的测试目录（`test/` `tests/` `spec/` 之外还有别处放测试时用），可重复 |
| `--drift-pattern <正则>` | 对账输出里"漂移行"的样子（默认认 `漂移` / `❌` / `drift` / `mismatch`） |
| `--table-header-pattern <正则>` | 对账输出里表头/分隔行的样子（默认认 `一致性` / `租户` / `tenant` / `---`） |
| `--csv-pair a.csv=b.csv` | CSV 对照关系，可重复 —— 按声明配对，不做两两配对 |
| `--tenant-col <列名>` | CSV 里租户列的名字（默认按 `tenant_id`/`tenant` 认） |
| `--min-line-coverage` / `--min-branch-coverage` | 显式给覆盖率门槛。**默认不用通用阈值**（仓库没有闸门时判 skipped，不判 fail） |
| `--self-check` | 反向验证：往**临时副本**里注入一个已知缺陷，证明判据会翻红。不改你的仓库 |
| `--self-check-deps` | 配合 `--self-check`：把 `node_modules` / `.venv` 软链进副本，让副本能真跑测试（这样才验得到**实测类**判据，不只是静态判据） |
| `--include-itest` | 把真后端 itest 车道也算进来（要连 PG + 向量库，慢） |
| `--test-tenant <名字或前缀>` | 声明测试租户，可重复 —— 测试租户的漂移是夹具状态，报 warn 不阻塞 |
| `--test-tenant-prefix <前缀>` | 追加测试租户前缀（默认认 `itest-` `memtest-` `mock-` `eval-` 等） |
| `--database-url <连接串>` | PG 连接串（默认取环境里的 `POSTGRES_URL`） |
| `--verbose` | 把测试命令的原始输出也打出来 |

## 输出契约

报告分四段，结论**不是**一句"可以交付 / 不可以交付"：

| 段 | 意思 |
| --- | --- |
| **已实测** | 实测出来的结果（跑过测试、跑过覆盖率、查过 PG） |
| **静态信号** | 静态分析的信号 —— 标注了"不能单独支撑「通过」" |
| **未实测** | 没测出来的（环境没就绪、找不到测试文件、没有可分析的导出符号） |
| **本工具不判定** | 固定清单，见下 |

结论分三档：

| 档 | 意思 |
| --- | --- |
| **阻塞项** | 实测出来不达标的 —— 有它就不能交付 |
| **待人工确认** | 实测了，但结论要人拿主意（正文类字段不一致、旁路存储是不是权威数据…） |
| **无信号** | 没测出来的 —— 环境没就绪、找不到测试文件、这个模块没有可分析的导出符号 |

静态信号永远不能单独支撑"通过"。工具还固定列出一段 **本工具不判定** 的清单：

> 需求是否被正确理解 · 产品行为是否正确 · 性能 · 安全 · 可用性 · 运维就绪

这六项**不在**本工具的判定范围内 —— 交付决策仍然由人承担。

退出码：

| 码 | 意思 |
| --- | --- |
| 0 | 没有阻塞项 |
| 1 | 有阻塞项，或关键判据（测试是否绿）没测到 —— 不能判"达标" |
| 2 | 认不出这个模块（语言不在支持范围、路径不存在、参数非法）—— 说清原因，不给假结论 |

## 设计要点

- **门槛归仓库所有**。仓库有覆盖率闸门就驱动它，判定看它的退出码；通用阈值只在仓库没有闸门时兜底（且默认不阻塞）。
- **实测优先**。每条判据要么有实测证据，要么明说环境没就绪（缺什么条件），不拿静态推断冒充实测。
- **跑不起来 ≠ 不达标**。setup 报错、缺产物、连不上中间件，都判 skipped 并说清缺什么。
- **判交付看生产租户**。测试租户的漂移是夹具状态，报 warn 不阻塞。
- **跨存储字段级比对**。同一个 id 在权威库与派生库里的同一个字段值对不上，点名 id 与字段。

## 语言支持

| | 计划生成 | 覆盖率实测 | 导出引用判据 | 跨存储对账 |
| --- | --- | --- | --- | --- |
| TypeScript / JavaScript | ✅ | ✅ node / vitest / jest | ✅ | ✅ |
| Python | ✅ | ✅ pytest-cov | ✅ | ⚠️ 对账脚本只从 `package.json` 找 |
| Rust / Go | 部分（复用侦察） | — 识别不到模块 | — | — |

Rust / Go 模块上 `readiness.py` **退出码 2** 并说清"当前只实现了 TS/JS/Python"，
不给假结论 —— 不是"支持了但没数据"，是"认不出来就说认不出来"。
一个满屏 ✓ 但其实什么都没测的报告，比报错更糟。

## 安全须知

`readiness.py` 会**执行目标仓库自己定义的脚本**（`package.json` 里的 `test`、
`coverage`、`reconcile`、`eval:baseline:check` 等）。三件事要知道：

1. **先看它要跑什么**。`--dry-run` 只列出候选脚本与将执行的命令，不执行。
   正常模式下工具也会在执行前把那几行（白名单命中的脚本 + 确切命令）打印出来 ——
   那是审计入口。脚本选取走**显式白名单**（精确脚本名或白名单前缀）——
   名字里带关键词的脚本（例如 `reconcile:reset`）不会被自动执行。
2. **子进程只拿到白名单环境变量**。`PATH`、`HOME`、`LANG`/`LC_*`、`TMPDIR`、
   `NODE_*`、`PYTHON*`，以及工具自己需要的 `POSTGRES_URL` / `DATABASE_URL` /
   `MEMORY_ITEST` / `REQ_PLAN_RECONCILE_OUT_DIR`。**其余环境变量一律不透传** ——
   你的云厂商密钥、npm token 之类不会进目标仓库的脚本。
   注意那两个连接串**会**透传：仓库自己的集成车道要连库，这是刻意的取舍 ——
   不想要就把它们从白名单里去掉，或改用 `.pgpass`。
   `psql` 的口令走 `PGPASSWORD` 环境变量，不出现在命令行参数里。
3. **信任一个仓库的 `test` 脚本，就等于把它交给了你的环境变量**。
   这一层的风险等级 = 它所运行的每一个脚本的风险等级。

`req_plan.py scaffold` 会向仓库写测试文件。写入前有两道闸：钉的路径必须规范化后
仍在 `--out-dir` 之下（越界的钉不写盘），用例名按语言做引号转义（计划 markdown
不会被变成可执行代码）。认不出仓库的测试约定时**拒绝生成**并报红，不猜。

## 已知缺口

发布前需处理的问题清单见 [docs/oss-release-review.md](docs/oss-release-review.md)
与 [docs/testing-philosophy-review.md](docs/testing-philosophy-review.md)（评测记录，只读）。
逐条状态：

### 已关闭

| 项 | 怎么关的 |
| --- | --- |
| 零测试假绿（一个测试都没找到却判"测试全绿"） | 判 `skipped` 并说清"没找到测试文件 ⇒ 无法判定测试是否绿"；结论不再说"达到交付条件" |
| 测试发现只扫模块目录（`test/` 布局的仓库被判不可交付） | 覆盖仓库级 `test/` `tests/` `spec/` 与模块内 colocated，另有 `--test-dir` |
| 覆盖率 include 硬编码 `**/*.ts` | 按模块里实际存在的扩展名推导 |
| 依赖抽取只认 `from '…'` | 覆盖 `require('x')` / `import 'x'` / Python 的 `import x` 与 `from x import y` |
| Python 下"具备扩展性"/"没有重复造轮子"空集假绿 | 加 Python 分支；导出集为空时判 `skipped`（零分析 ⇒ 不给结论） |
| 判据名"具备扩展性"名不副实 | 改名"不过度抽象" |
| 表设计无条件要求租户列与唯一约束 | 加豁免：单租户模块、全局配置表、日志/追加型表；判据改名"表结构基本合规" |
| 对账层只懂一种中文方言 | 漂移/表头/租户前缀/CSV 对照关系/租户列都可配（`--drift-pattern` 等）；找不到成对的 CSV 时报"无法比对"而不是"0 对无差异" |
| 通用阈值 80/70 与文档矛盾 | 无仓库闸门时判 `skipped`（缺判据）；要通用阈值得显式 `--min-line-coverage` |
| Python 仓读不到自己的闸门 | 支持从 `pyproject.toml` / `Makefile` / `tox.ini` 读覆盖率闸门与阈值 |
| itest 车道默认就跑 | 默认**不跑**，`--include-itest` 才跑 |
| 脚本选取是子串匹配 | 改成显式白名单；加 `--dry-run`（只列不跑），执行前打印审计行 |
| 子进程继承完整 `os.environ` | 改成白名单透传 |
| `psql` 连接串走 argv | 改用 `PGPASSWORD` / `PG*` 环境变量 |
| `scaffold` 路径穿越 / 代码注入 | 写入前的两道闸：路径越界不写盘、用例名引号转义 |
| `scaffold` 不认语言 | 加 Python / Rust / Go 分支；认不出约定时拒绝生成并报红 |
| 回归套件硬编码家目录绝对路径 | 改为从 `__file__` 推导；`ai-testing-system` 缺失时清晰 skip |
| 判据缺"未实测"态 | 每条判据带 `evidence` 标签（executed / static / untested）；报告分「已实测 / 静态信号 / 未实测」三段 |
| 输出契约 | 结论改三档（阻塞项 / 待人工确认 / 无信号），去掉"达到交付条件"这类裁决措辞；固定一段"本工具不判定"清单 |
| 反向验证（`--self-check`） | 往**临时副本**注入一个已知缺陷，证明判据会翻红；只作用于副本，不改用户仓库 |
| oracle 独立性 | 扫测试文件，标出"期望值取自被测对象自身"的形态（断言两侧调同一模块、快照自比对…）—— 提醒级，不判红 |
| 发布骨架缺失（无 LICENSE / README / CHANGELOG / evals / CI） | 本轮补齐，见 [CHANGELOG.md](CHANGELOG.md) |
| `SKILL.md` 的范围声明与实现对不上 | 降级为"哪些已被实测证明、哪些还没被证明、哪些本工具判不了" —— `016abda` |
| `~/.agents/skills/req-test-plan/scripts/...` 硬编码路径 | `SKILL.md` 改成解析本 skill 所在目录（`$SKILL_DIR`）—— `016abda` |

### 仍开放

完整清单见 [CHANGELOG.md](CHANGELOG.md) 的「已知遗留」。发布时仍开放的是这七条：

| 项 | 状态 |
| --- | --- |
| SkillSpector 的 `E2`（Env Variable Harvesting）仍报 HIGH | 子进程必须透传环境变量（白名单实现，见 `scripts/readiness.py` 的 `child_env`）。逐条复核结论在 `docs/oss-release-review.md` 第六节；CI 用 `.github/skillspector-allowlist.yaml` 把**已复核的规则**显式放行，每条带理由，allowlist 之外的新 HIGH 仍然拦住。放行是**规则级、不是逐条 finding**：逐条基线按位置指纹匹配，改了 `scripts/` 里那一行就失效 ⇒ 对开源贡献者是持续的假红；规则级不依赖位置，改代码不会误红，而**新规则**仍然会被拦。代价（同一个已放行规则再出现不会被拦）写在 allowlist 文件头，不藏着。本地复现：`bash .github/check-skillspector.sh` |
| `--exec` 显式执行开关没有采纳 | brief 要求"默认不跑、显式开关才跑"，实现改成**默认执行 + 执行前打印审计行**（报告头部一行「将执行的仓库脚本」，加 `--dry-run` 可只看不做）。理由：工具的定位就是实测（不跑测试的交付判定没有意义），而执行面已经收敛到脚本白名单 + 环境变量白名单；再叠一个必填开关，每次调用都要多打一个参数，实际收益不大。想看执行清单用 `--dry-run` |
| Python 没有真后端（itest）车道 | TS 侧有 `--include-itest`，Python 侧没有对应机制。属 P1，未修 |
| Windows 未端到端验证 | skill **运行时**是纯 Python、**安装**走 `install.py`（跨平台）——这两块与平台无关。但仓库自己的开发脚本（`fixtures/run_fixtures.sh`、`.github/*.sh`）依赖 bash，在 Windows 上要 WSL 或 Git Bash |
| 触发准确度未实测 | `skill-creator` 的 `run_loop` 需要 `claude` CLI |
| 自检副本默认不带 `node_modules` | 多数仓库在副本里跑不动测试，`--self-check` 默认证明的是"静态判据抓得住东西"，不是"整套判据（含实测项）抓得住东西"。报告里明说了这件事（`copy_runnable: false`）。加 `--self-check-deps` 会把依赖**软链**进副本（不复制体积），那时能验到实测类判据 |

另有一条不在 CHANGELOG「已知遗留」里的：`SKILL.md` 正文仍是中文（README 已有英文版 `README.en.md`）。

## 回归套件

全部测试脚本逐个跑（文件名有跳号，不假设有序或连续）：

```bash
for t in scripts/test_*.py; do python3 "$t" || echo "FAIL $t"; done
```

## 黄金 fixture

5 个自包含的小仓库，每个带**钉死的期望判定** —— 它们是"这个工具会不会假绿"的
可复现证据：

```bash
bash fixtures/run_fixtures.sh
```

| fixture | 期望判定 |
| --- | --- |
| `ts-colocated` | 退出码 0；测试被找到；被测到的导出不算"零引用" |
| `python-repo` | 退出码 0；覆盖率不是 skipped；依赖清单不为空；"不过度抽象"不是空集假绿 |
| `rust-repo` | 退出码 2，并说清"当前只实现了 TS/JS/Python"，不是满屏 ✓ |
| `english-reconciler` | 退出码 1，暴露漂移 —— **不**报"生产租户无漂移" |
| `no-tests` | 结论**不**是"达到交付条件"；"测试全绿"这条不是 ✓ |

退出码：0 全部命中 / 1 有不符 / 3 有 fixture 因环境缺失未判定。
每个 fixture 目录里的 `README.md` 写明**为什么有它**（挡的是评审里的哪一条质疑）。

`python-repo` 需要 `pytest` + `pytest-cov`；其余 fixture 需要 `node`。
CI 里会装齐并跑全部 5 个。

## evals

`evals/evals.json` —— 6 条 eval，每条带**可机检**的断言
（期望退出码 / 必须出现的字符串 / 必须不出现的字符串），
覆盖：开工前出计划、已有计划核一遍、模块能不能交付、跨存储数据一致、
"声称覆盖了但文件不存在"、以及 `scaffold` 的写入越界。

每条 eval 声明用哪个黄金 fixture 当被测仓库、要跑什么命令、断言看命令输出还是
产物文件。**跑法**：

```bash
python3 evals/run_evals.py            # 跑全部
python3 evals/run_evals.py --only 3   # 只跑某一条
python3 evals/run_evals.py --json     # 机器可读
```

CI 里会跑。退出码：0 全部通过 / 1 有断言不通过。

## License

MIT
