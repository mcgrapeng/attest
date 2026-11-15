# Changelog

本文件记 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 的格式，
版本号用 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

（暂无）

## [0.1.3] - 2026-11-15

**Python 覆盖率车道说清原因**（自测报告里的第四个发现）。pytest 自己没跑起来时
（收集错误 / 缺依赖 / 参数用错），工具报的是「覆盖率报告没解析出来（No such file or
directory: .../cov.json）」—— 读者只知道有个文件找不到，不知道**仓库缺依赖**。
实测复现：`pallets/itsdangerous` 的测试要 `freezegun`（仓库没声明）⇒ pytest 收集失败
⇒ pytest-cov 根本没写 cov.json。现在报「pytest 没跑起来（No module named
'freezegun'）」，并点名缺哪个模块。

**Action 真机冒烟**：加 `.github/workflows/action-smoke.yml`，用 `uses: ./` 在真实
ubuntu runner 上跑一遍 action（strict: true）—— 本地逐步模拟过 composite 的每一步，
但「composite action 在 GitHub 上真的能跑」只有真跑一次才算数。

回归断言：`skills/attest/scripts/test_python_coverage_lane.py`。

## [0.1.2] - 2026-11-15

**修三个在公开仓库上实测到的误报**（证据见
[docs/self-test-report.md](docs/self-test-report.md)，issue #1 / #2 / #3）。这三个都是
**误报阻塞项** —— 工具给出一个不存在的理由说"你不该交付"。判据不该说这种话。

1. **测试目录里无后缀的文件不被发现**（#1）。之前只认 `.test.`/`.spec.` 后缀，于是
   chalk / express 的 `test/basic.js` 这种文件一个都认不出 ⇒ 判成"没有测试" ⇒
   「导出的逻辑都有测试」把每个导出都报成零引用 ⇒ 阻塞。现在：测试目录里的
   js/ts 文件**不带后缀也算测试**；但 `support/` `fixtures/` `helpers/` 这类辅助子目录
   与 `conftest.py` / `setup.js` 这类文件不算（跑它们只会得到噪音）。
2. **逐文件命令追加到复合命令末尾**（#2）。chalk 的 test 脚本是
   `xo && c8 node --test test/[!_]*.js && tsc --noEmit`，文件被追加到末尾 ⇒ 变成
   `tsc` 的参数 ⇒ runner 根本没跑。现在：复合脚本会被拆开，文件追加到**真正跑测试的
   那一段**（`c8 node --test`）；一条都认不出来 ⇒ 逐文件车道诚实地说"没认出 runner"，
   不拿猜出来的命令去跑别人的仓库。单条命令保持旧行为（有些仓库的 test 脚本就是自写的
   runner，认不出它是不是测试 runner，那就照原样跑）。
3. **`.d.ts` / `.js` 被当成派生存储**（#3）。命中的是源码注释里 GitHub 链接的
   `/blob/` 这个路径段，以及 Python 标准库 `textwrap.py` 里的一句 "blob" ⇒ 两个仓库
   被误判成"有派生存储却没有对账机制"。现在：匹配存储线索前先把 URL 剔掉，"blob"
   这个词本身不再算证据（对象存储由 S3Client / putObject / minio / objectStore 认）。

回归断言：`skills/attest/scripts/test_repo_layout.py`（26 条套件之一）。

## [0.1.1] - 2026-11-15

### 自测报告与市场收录（2026-11-15 追加）

**自测报告**：拿 5 个公开仓库（chalk / express / type-fest / click / itsdangerous，
外加一条负对照）跑判据，把**原样输出**与结论公开在
[docs/self-test-report.md](docs/self-test-report.md)。这一轮在工具自身发现 4 个缺陷：
测试文件无后缀不被发现（→ #1，误报阻塞项）、逐文件测试命令追加到复合命令末尾（→ #2）、
`.d.ts` + `.js` 被当成派生存储（→ #3）、Python 覆盖率报告路径找不到（未立案，待诊断）。

**市场收录**：仓库加 `.claude-plugin/marketplace.json`（Claude Code 与 Codex 认同同一份
清单 ✓ 两端都实测装得上、skill 路径能解析 ✓）与 `skills.sh.json`（仓库页分组）。
Anthropic 官方 skills 仓库的 **Partner Skills** 段已提 PR（anthropics/skills#2224）。
OpenCode 没有 skill 市场 —— 它读 `.agents/skills/`，一行安装已经覆盖 ✓。

**topics / description**：topics 扩到 12 个（加 attestation / ci / github-actions /
mutation-testing / requirements）；description 改成
"An evidence list for delivery readiness, built from your repo's own tests, coverage
gates and reconciliation scripts — in CI or as an agent skill."（146 字符）。

## [0.1.0] - 2026-11-15

首个发布版。本轮目标：把 `attest` 从"本机一个 skill 目录"推进到"可发布的制品" ——
工程缺口与哲学缺口逐条修完，并用**可复现证据**证明每一条已关闭。

发布前置清单见 [docs/oss-release-review.md](docs/oss-release-review.md)（只读评测记录）
与 [docs/testing-philosophy-review.md](docs/testing-philosophy-review.md)。

### 分发与共建：让工具能被找到、被一起建

工具存在，但几乎没人找得到它 —— 这一轮补的是「被找到」与「能一起建」两件事。

**布局下沉**：`SKILL.md` + `scripts/` + `assets/` 整体搬进 `skills/attest/`。这不是审美
选择：skills.sh 的索引器与 Anthropic 官方 marketplace 的模板都只扫**子目录**里的
`SKILL.md` —— 实测首页收录的 18 个仓库**无一例外**，仓库根的 `SKILL.md` 一个都搜不到，
而 `skills.sh.json` 的 schema 明写「the CLI ignores … the repository root」。
放在根目录等于工具存在但无人能发现。仓库根保留 GitHub 门面：README、docs、fixtures、
evals、CI；`install.py` 的稳定入口改指 `skills/attest/`，`.skill` 打包的第一层仍是
skill 目录本身。

**一行安装**：`npx skills add mcgrapeng/attest` —— 不用 clone、不用装 Python，skills CLI
自动识别 20+ 个 agent（Claude Code / Codex / OpenCode / Cursor / Windsurf / Zed / …）。

**社区健康信号**（新人第一眼找的东西，也是「长期维护」该有的样子）：`SECURITY.md`
（GitHub 私有漏洞报告已开启）、`CODE_OF_CONDUCT.md`（Contributor Covenant 2.1 原文）、
issue 模板（bug / feature）与 PR 模板。

**路线图**：[docs/roadmap.md](docs/roadmap.md) —— 四个阶段（能被找到 → 能进 CI → 能对
真实仓库举证 → 成为标准），每项写清「谁能帮上」。README 加「Get involved / 参与共建」段。

**GitHub Action**：仓库根加 `action.yml`（composite action），把判据跑进 PR —— 报告写进
job summary 与 PR 评论，**默认不拦合并**（`strict: false`）：工具给证据，仓库自己决定。
输入：`module`（要检查的模块，`.` 表示整仓）/ `repo-path` / `plan` / `strict` / `comment`。
守卫新增一条断言：action.yml 引用的脚本路径真实存在、输入名与两份 README 一致（防漂移）。

### 改名：req-test-plan → attest

工具的产出本身就是一份 **attestation**（证据清单）。它做的是这件事里**机械的那一半**，
并且当真的做：失败的用例点名、覆盖率实测、漂移归到租户，有阻塞项就退出码 1。
它**不做**的是打出「可以交付」这个结论 —— 那个结论还压在需求是否被理解对、行为对不对、
快不快、安不安全这几件代码里读不出来的事上（每次报告都列在「本工具不判定」里）。
所以名字刻意避开 certify / sign-off 这类**承诺了批准**的词 —— 这个工具不肯替人做批准。`attest` 与 SLSA 供应链里的
*attestation* 同源，正是「出处证明」的意思。改动 72 处（frontmatter、安装脚本、
打包器、评测器、两版 README、本文件与文档）；四份历史评测报告**正文不改**（那是当时的
事实记录），只在开头加命名说明。

仓库的 **description 也改成英文**（与英文默认的 README 对齐 —— 搜索结果里中英混排会
削弱可信度）。写法不套批量工具的四段式，而是参照头部开源项目的惯例（ruff / uv / fd /
pre-commit / pytest：单句、点明品类与产出、不用暗示「通过 / 不通过」的词）：

> An evidence list for delivery readiness, built from your repo's own tests,
> coverage gates and reconciliation scripts.

刻意避开 checker / verifier / validator 这类词 —— 它们都承诺一个「通过 / 不通过」的
判定，而这个工具只给证据、不做批准。

### 搬运

- 搬成独立 git 仓库（原先只是 `~/.agents/skills/attest/` 下的一个目录，
  没有版本历史、没有 LICENSE 正文、没有发布骨架）。
- 补 LICENSE 正文（MIT，与 `SKILL.md` frontmatter 的 `license: MIT` 一致）。
- 仓库根加 `AGENTS.md` 级的工作约定：每个工作区自己的依赖边界、
  API 契约的唯一来源、验证要求。

### 发布骨架

- **CI**（`.github/workflows/ci.yml`）：Node 22 × Python 3.11/3.13 矩阵；
  逐个跑**全部** `scripts/test_*.py`（文件名有跳号，不假设有序）；
  "仓库里存在 `__pycache__` / `*.pyc` 即失败"；
  打包冒烟（装进临时目录跑 `req_plan.py new|check` 与 `readiness.py --json`）；
  SkillSpector 扫描（基线之外不得有 HIGH / CRITICAL）；黄金 fixture 回归。
- **黄金 fixture**（`fixtures/`）：5 个自包含小仓库 + 钉死的期望判定 +
  `run_fixtures.sh`（期望 vs 实际的差异输出，任一不符即退出码非 0）。
  覆盖 TS colocated、Python（pytest）、Rust（应得退出码 2）、
  输出英文的对账脚本（应暴露漂移而不是假绿）、零测试模块（不得判"达到交付条件"）。
- **evals**（`evals/evals.json`）：6 条 eval，断言可机检
  （期望退出码 / 必须出现的字符串 / 必须不出现的字符串）。
- **`install.sh` / `uninstall.sh`**：软链到 `~/.agents/skills/` 与三个 agent 的
  skills 目录；卸载只删软链，绝不删真实目录。
- **README**：语言支持矩阵按实际实现重写；Security 一节补 `--dry-run` / `--exec`
  与"子进程只拿到白名单环境变量"；输出契约改成三档结论 + 固定一段"本工具不判定"清单；
  已知缺口逐条标注已关闭 / 仍开放；补 fixture 与回归套件怎么跑。
- **CHANGELOG**（本文件）、**`.gitignore`**（字节码与运行态产物）。

### 修了哪些发布阻塞项

下面逐条对应 [docs/oss-release-review.md](docs/oss-release-review.md) 第十节的
P0/P1 清单，以及 [docs/testing-philosophy-review.md](docs/testing-philosophy-review.md)
第五节的哲学门槛 R1–R4。**已修的条目标 `[x]` 并给出提交号**；仍开放的写在
「修复与已知遗留」里。

#### P0（不做就不能发）

- [x] 零测试假绿：`check_tests_green` 在"没找到任何测试文件"时判 `skipped`，
      不判 `pass`；整体结论不得因此变成"达到交付条件"。
      —— `7969bf8`（判据层）+ `e4bc632`（结论层改成三档）。
      回归断言 `scripts/test_readiness_r2.py`、`scripts/test_readiness_philosophy.py`。
- [x] 测试发现作用域：仓库级 `test/` `tests/` `spec/` 与模块内 colocated 都算，
      另有 `--test-dir`。 —— `7969bf8`。
- [x] `scaffold` 路径穿越：钉规范化后必须仍在 `out_dir` 之下；越界的钉不读也不写。
      —— `0648694`（写入面 + `check` 读取面）。回归断言 `scripts/test_req_plan_scaffold_safety.py`。
- [x] `scaffold` 代码注入：插值进用例名的引号必须转义。
      —— `0648694`（`_js_str` / `_comment_safe`）。
- [x] `scaffold` 语言感知：Python / Rust / Go 分支；认不出约定时**拒绝生成**；
      从 package.json 推断 node:test / vitest / jest / mocha（推断不出 ⇒ 拒绝并报红）。
      —— `0648694`。
- [x] 回归套件独立：r3/r4 的家目录绝对路径改为从 `__file__` 推导，
      `ai-testing-system` 缺失时清晰 skip。 —— `0648694`。
- [x] SKILL.md 的范围声明与实现对齐（"能不能交付客户"降级为
      "哪些已被实测证明、哪些还没被证明、哪些本工具判不了"）。
      —— `016abda`。
- [x] 本轮新增（发布骨架）：CI / fixture / evals / install / uninstall / CHANGELOG
      —— 已落地。
- [x] LICENSE（MIT 正文）/ README / `.gitignore` —— `97d55e9` + 发布骨架车道。
- [x] 字节码不入包：`.gitignore` 排除 `__pycache__`/`*.pyc`，CI 的 hygiene job
      单独跑（跑测试会现场生成字节码），SkillSpector 基线脚本扫的是干净副本。
      —— 发布骨架车道。

#### P1

- [x] 通用阈值 80/70 与文档矛盾：无仓库闸门时判未实测，
      或改成显式 opt-in（`--min-line-coverage`）。 —— `7969bf8`。
- [x] itest 车道默认行为与文档统一（默认不跑，`--include-itest` 才跑）。
      —— `7969bf8` + `016abda`（文档）。
- [x] 「具备扩展性」/「没有重复造轮子」加 Python 分支；零分析时必须 `skipped`。
      —— `7969bf8`（改名「不过度抽象」）。
- [x] 依赖抽取覆盖 `require('x')` / `import 'x'` / Python 的 `import x` 与
      `from x import y`（并排除标准库与相对导入）。 —— `7969bf8`。
- [x] 对账层参数化（漂移/表头/租户前缀/CSV 对照关系可配）；找不到可比对的 CSV 对时
      报"无法比对"而不是"0 对无差异"。 —— `7969bf8`。
- [x] 脚本选取从子串匹配改成显式白名单；加 `--dry-run`；
      子进程环境收敛为白名单 + 显式透传。 —— `7969bf8`。
      （`--exec` 显式开关未采纳，见「修复与已知遗留」。）
- [x] `psql` 不用 argv 传连接串（解析成 `PG*` 环境变量，解析失败安全降级）。
      —— `7969bf8`。
- [x] 表设计判据改名并给租户列/唯一约束加豁免条件。 —— `7969bf8`。
- [x] `req_plan.py new` 在无存储时不生成「数据一致性」节（跨存储才生成），
      模板里重复的第二个「## 数据一致性」标题一并去掉（它的内容其实是
      「这个需求明确不做的事」，标题与内容对不上）。 —— 集成方。
- [x] 判据数对齐（文档 12 条 == 代码 12 个 Check）。 —— `016abda`。
- [x] Python 的 colocated `test_*.py` 计入导出引用（之前整类仓库被误判成零引用）。
      —— `e4bc632`。
- [x] readiness 侧的钉越界读取面（之前只封了写入面）。 —— `e4bc632`。

#### 哲学门槛 R1–R4

- [x] R1 输出契约：证据分节 + 三档结论 + 「本工具不判定」清单 + 退出码语义写清。
      —— `e4bc632`。
- [x] R2 反向验证 `--self-check`：注入已知缺陷，证明判据会翻红。
      —— `016abda`（新增 `scripts/selfcheck.py` + 60 条断言）。
- [x] R3 oracle 独立性检查（静态提醒级，接为第 12 条判据）。
      —— `517436a`（新增 `scripts/oracle_check.py` + 30 条断言）。
- [x] R4 判据带证据类别（executed/static/untested）并按类别分节输出。 —— `e4bc632`。

#### P2

- [x] `evals/`：至少 5 个黄金 fixture。 —— **已落地**（`fixtures/` + `evals/evals.json`）。
- [x] `evals/evals.json` 的 6 条 eval **可执行**：补 `fixture` / `command` /
      `check_file` 字段与 `evals/run_evals.py`（断言 DSL：
      `exit_code=` / `exit_code_any=` / `contains:` / `not_contains:` / `any_contains:`）。
      之前那 6 条只有声明没有 runner，跑不了 —— 等于装饰品。接入 CI。
- [x] CI：Node 22 + Python 3.11/3.13 跑全部脚本 + 打包冒烟。
      —— **已落地**（`.github/workflows/ci.yml`）。
- [x] description 扩写（点名交付判定命令、声明语言覆盖、明确会执行仓库脚本与写文件）。
      —— `016abda`。
- [x] `~/.agents/skills/...` 硬编码路径改成"解析本 skill 所在目录"（`$SKILL_DIR`）。
      —— `016abda`。

### 修复与已知遗留

### 复评后修的（`606d9ea`）

独立复评抓到一条会**直接推翻工具存在理由**的缺陷，已修：

- **TS 车道的假绿**：`_failed_tests` 只认 TAP 的 `not ok N -`，而 jest / vitest / mocha
  都不吐 TAP ⇒ 那些仓库里失败的用例一个都认不出来 ⇒ 报「✓ 测试全绿」、退出码 0。
  实测复现过一个 jest 风格的失败套件拿到绿色裁决。现在按「runner 退出码 + 输出」
  一起判：真失败⇒阻塞、环境没就绪⇒未实测、**输出格式认不出⇒未实测（绝不判绿）**。
  配套扩了失败用例识别与用例汇总解析（jest / vitest / mocha 三种格式），TS 车道补上
  `setup_errors` 通道，删掉死代码 `_run_coverage_single`。
  回归：scripts/test_readiness_runners.py（6 组）。
- **`index_repo` 只索引 TS 的 export** ⇒ Python/Rust/Go 仓的「重复造轮子」判据整条
  是死的。现在按语言分别认 Python 模块级 def/class、Rust pub fn、Go 顶层 func/type。
- **README 说 `DATABASE_URL` 不会透传**，代码里它在白名单里 ⇒ 文档改成"会透传"并说明取舍。
- **`.omx/` 不再入库**（OMX 运行态与目标账本不进发布包），brief 移到
  `docs/development-brief.md`。
- **安装入口跨平台**：新增 `install.py`（纯标准库，没有 bash 依赖），`install.sh` /
  `uninstall.sh` 改成它的薄壳 —— 之前只有 bash 脚本，Windows 用户装不了。
- **打包元数据**（产出可安装的 `.skill`）已做：新增自包含的 `package.py`
  （不依赖外部 skill 的打包脚本；排除字节码/`.git`/`fixtures/`/回归测试；
  打进包前检查有没有字节码）。实测产出 20 个文件、191 KB。
- **Python 仓库的真后端车道与对账脚本认不到**：之前只扫 `package.json` ⇒ Python 仓库的跨存储对账判据基本用不上。现在 `find_itest_scripts` 与 `find_reconcilers` 也接上 Python 兜底（Makefile 目标 / tox 环境，按配方体判归属）—— 覆盖率闸门那条早就有这个模式，现在三条路一致了。
- **规则级 allowlist 的漏洞**（日后悄悄多出 HIGH 不被拦）补一道精确闸门：
  `scripts/check_env_inheritance.py` —— 生产代码不得整份继承 `os.environ`。
  回归：scripts/test_env_inheritance.py（12 条）。

### 判据增补：测试强度与向量集合（2026-11-15）

补的是上一轮盘点出来的两处**不对称**，各自对应一个真实缺口。

#### ① 「测试能发现缺陷」—— 覆盖率不说明测试有劲

覆盖率只说明代码被执行过，不说明**断言会失败**。一个"杀不死缺陷"的测试套件，
覆盖率再高也不说明问题。

- 新增 `scripts/mutation_check.py`：把源码改坏（只改比较运算符 `==`/`!=`/`===`/
  `!==`/`>=`/`<=`，不改常量、不改字符串、不改控制流），再跑该模块自己的测试。
  测试红了 ⇒ 杀得死；测试还绿 ⇒ **杀不死** ⇒ 测试套件有洞。
- 跑在**仓库副本**里（依赖目录软链），绝不改用户仓库。
- 接为第 13 条判据「测试能发现缺陷」，**默认不跑**（`--mutation-check` 打开）——
  它要复制工作区并多次跑测试，很贵；不跑时这条判据是「未实测」，不是"通过"。
- 判据**不设分数线**：杀不杀得死多少算够，门槛归项目。工具只报事实与位置：
  `0 个杀死 ⇒ fail`、`部分存活 ⇒ warn（列出位置）`、`全部杀死 ⇒ pass（并注明样本有限）`。

#### ② 「向量集合属性自洽」—— PG 侧有五条规则，集合侧一条没有

- 新判据判的是**调用点之间是否自相矛盾**，不猜项目的命名规范：
  1. 同一集合在不同调用点的维度/度量/分区键不一致 ⇒ 报
     （写入用 1536、检索用 768 这种，工具看不出来就等于没测）；
  2. 分区键指向一个向量字段 ⇒ 报（向量库不允许按向量字段分区）；
  3. 写入的集合与检索的集合不是同一个 ⇒ 提醒
     （若本意是同一份数据，这就是"写入 A、检索 B"的缺陷）。
- 集合名支持常量解析（`collection_name: COLLECTION` → 解析成字面量）。
- 判为**静态信号**：它读源码，不能单独支撑「通过」。
- 仍**不判**：集合命名规范、集合与**线上真实租户**是否一致 —— 前者要项目约定，
  后者要连活的向量库并用项目自己的凭据（见「已知遗留」）。

#### ③ 计划里加「人工确认」节（需求理解对不对这一环留痕）

`check` 会提醒；判定报告里「需求覆盖到位」在**未经人工确认**时降成提醒级并明说
"需求是否被正确理解未获保证（本工具不判这一项，只能由人签）"；确认过才给 ✓。

#### ④ 两条判据自身的假绿点（自查出来的）

- **变异检查把"环境没就绪"当成"杀死了"**：它只看退出码，而工具本来就有三分口径
  （`_classify_run`：真失败／环境没就绪／输出格式认不出）。一个专测"测试有没有劲"的
  工具自己假绿，比没有这条判据更糟 ⇒ 变异检查改接同一套分类：环境错误与认不出的
  输出判成**未判定**（既不算杀死，也不算存活）。
- **pgvector 列的维度没人查**：`check_schema` 只查主键/租户/唯一/时区/RLS。
  ⇒ 补「同一个向量列名在不同地方维度不一致」（1536 / 768）⇒ 提醒。
  同一张表里不一致就是缺陷；不同表可以不同，提醒里说清了。

回归：`scripts/test_test_strength.py`（31 条新断言）。

### 文档：README 重做

- **默认语言改成英文**：`README.md` 是英文（GitHub 默认入口），中文移到
  `README.zh-CN.md`（GitHub 会自动出语言切换）。原先的 `README.en.md` 删掉。
  两份章节一一对应（14 节），内容同步。
- **加标识与头图**：新增 `assets/logo.svg`（几何印章标记：teal 圆角方块 + 浅色对勾 +
  内嵌细边），README 顶部改成居中头图 —— 标记 / 标题 / 一句话定位 / badge 行。
- **加「它怎么运作」流程图**：ASCII 管线图，一屏看清
  需求 → 计划 → readiness.py → 证据清单。
- 排版按 `tech-doc-style-chinese` / `md-table-formatter` / `humanizer` 过了一遍：
  破折号密度降到与旧版持平、表格列数一致、围栏配平、内部链接与锚点全部有效。

### 视觉重做：换掉 logo，补「它给你什么」

- **logo 换风格**：原来是「teal 圆角方块 + 白色对勾」。对勾这个形状被否掉了
  （它也让工具看起来像在盖章批准，与「本工具不判交付」相矛盾）。
  现在是**几何单线体小写 a**（圆碗 + 竖干），配 `assets/logo-dark.svg` 深色版，
  README 里用 `<picture>` + `prefers-color-scheme` 自动切换 —— 线稿在 GitHub
  深色主题下发暗，单版不够。
- **头部按 `just` / `ruff` 的骨架重排**：一句话定位 → 亮点列表 → 真实输出 → 深入文档。
  新增「What it gives you」七条亮点（实测优先 / 四档分层 / 钉得住的计划 / 跨存储漂移 /
  测试强度 / 结论诚实 / 不替代你的仓库）。
- 设计方向由 `ui-ux-pro-max` 的资料库给（Phosphor 线性图标语言、JetBrains Mono /
  IBM Plex Sans 的开发者工具字体配对、深板岩 + 绿强调色），不是随手挑的。

### README 按 alibaba/open-code-review 的信息架构重排

参考 [alibaba/open-code-review](https://github.com/alibaba/open-code-review)（51.5k stars）
的 README 骨架重排，两份 README 各 20 节一一对应：

- 头部改成「居中 logo + h1 → 项目 badge 行 → 平台/agent badge 行 → 语言行 → `---`」，
  平台行如实标出 Claude Code / Codex / OpenCode 已支持、Windows 仅部分支持。
- 新增「Measured on this repo」：一个 blockquote 讲清每个数字怎么来的（含
  「触发召回测的是当时那台机器的竞争环境，不可迁移」这句诚实限定），配一张
  「测的是什么 / 为什么要在意」两列说明表。
- 新增「Why attest?」：先讲「看起来是绿的」的三种失效（覆盖率不等于测试有劲、
  聊天记录里的清单不是证据、两个存储没人盯着），再讲核心设计
  ——**能测的实测（硬约束）／该人判的留给人**。
- 「How to use」按 Prerequisites / Install / Distribute / Quick start / Switches 分层，
  Quick start 改成四步编号 + 加粗小标题。

### README 审计（ce-doc-review 四视角 + 逐条实跑）

按 `ce-doc-review` 的 persona 选择规则组队：coherence + feasibility（常驻）+
security-lens（README 写了凭据透传与子进程执行）+ design-lens（README 是渲染页）；
再按 `code-documenter` 的纪律**把 README 里的每个命令真跑一遍**。

**找到并修掉的三个真问题：**

1. **`doctor.sh` 只跑了 14/24 个测试**。README 说「`~/.agents/testkit/doctor.sh`
   跑回归套件」，但那个文件（机器本地，不在仓库里）**硬编码**了 14 个测试文件名，
   新增的 10 个从来没在这台机器上跑过 ⇒ 改成 glob（与仓库 CI 同一口径）。
2. **「其余环境变量一律不透传」这句话不准确**。实际白名单还有 `TZ`/`TEMP`/`TMP`/
   `SHELL`/`USER`/`LOGNAME`/`TERM`/`CI`/`SYSTEMROOT`/`COMSPEC` 与
   `NPM_*`/`PNPM_*`/`YARN_*`/`PIP_*`/`UV_*` 前缀 —— 这话让白名单看起来比实际更严，
   审自己环境的人会被误导 ⇒ 两份 README 改成逐项列全。
3. **打包排除项列举不全**（漏了 `node_modules`/虚拟环境/工具缓存）⇒ 改成
   「排除的是仓库自己的工作文件」。

**新增 `scripts/test_readme_sync.py`**（守卫这次审计发现的漂移类）：

- 中英两份 README 的章节数、层级、顺序必须一致；
- 判据表行数 == 代码里的 Check 数（14）；
- README 里的回归套件数 / fixture 数 / eval 数 == 仓库实况；
- README 引用的仓库内路径必须存在；
- README 列的环境白名单 == `readiness.py` 的 `ENV_ALLOW_EXACT`。

它上线当场就抓到一处过期：回归套件数已经是 25（多了本次新增的这个脚本），
两份 README 还写着 24 ⇒ 已更新，并写进 CI。

### README 审计：四个视角的评审结果（2026-11-15）

`ce-doc-review` 组队：coherence + feasibility（常驻）+ security-lens（README 写了
凭据透传与子进程执行）+ design-lens（README 是渲染页，视觉与可读性就是它的功能）。
cross-model pass 因**宿主模型族未认证**被跳过（该步附加且不阻塞，见 Coverage）。

**代码侧（两条真缺陷，不是文档问题）：**

1. **Python 车道按关键词子串匹配** ⇒ `make recover`（名字里含 "cov"）会被当成
   覆盖率闸门**自动执行**。改成**成词匹配**：`coverage` / `test-coverage` / `py-cov`
   认，`recover` / `cover` 不认。回归断言 6 条。
2. **审计行与 `--dry-run` 漏掉仓库自己的 test 命令** —— 那是工具跑得最多的一层
   （逐个测试文件跑），「跑之前先看它要干什么」却看不到它。现在两处都列，
   且 `--json` 时审计行走 **stderr**，stdout 保持纯 JSON（回归断言 4 条）。

**文档侧（六处不实或缺失）：**

3. **「npm token 不会进目标仓库的脚本」是假的** —— `NPM_TOKEN` / `NODE_AUTH_TOKEN` /
   `NPM_CONFIG_*` / `PIP_INDEX_URL` / `UV_INDEX_SECRET_PASSWORD` 都命中白名单前缀，
   会被复制进子进程环境。三份文档（两份 README + SKILL.md）同一句一起改实。
4. **「脚本选取走显式白名单」只对 JS 仓成立** —— Python 仓（Makefile / tox.ini）是
   关键词成词匹配，规则更松。现在两份 README 与 SKILL.md 都写清两条规则。
5. **「不想要就把连接串从白名单里去掉，或改用 .pgpass」——那两个开关不存在**。
   真有的只有：unset 环境变量 + 用 `--database-url` 传（不读环境变量 ⇒ 不进子进程
   环境），或设 `PGHOST`/`PGUSER`/`PGDATABASE` 让 `psql` 读 `.pgpass`；
   **只上 .pgpass 不够**，schema 判定只读 `PG*` 变量。
6. **输出样例是我删节过的** —— 块里只有 9 条判据，结论行却写着「7 项无信号 / 6 条
   静态信号」，**块内自相矛盾**。两份 README 都换成**逐字真实输出**（34 行，14 条
   判据全在），并把该一致性做成守卫。
7. 另修：已知缺口的 CHANGELOG 只有六条（README 说七条）⇒ 补齐；「Python 没有真后端
   车道」的说法与实现矛盾（车道**会被执行**，只是 pytest 输出不被解析 ⇒ 永远判
   「未实测」）⇒ 改准；`--tenant-col` 默认值英文版漏了；`--csv-pair` 的自动配对默认
   两份都漏了；安装前缺 clone 步骤；agent badge 宣传的用法在正文里没有入口；
   已知缺口表的状态列没法扫读 ⇒ 每行给状态词；Python badge 指向本页自身；
   语言表头首格是空的；闸门命令的前置工具（`pytest-cov` / `skillspector`）没写；
   `doctor.sh` 是作者本机文件、不在仓库里 ⇒ 说清。

**守卫 `scripts/test_readme_sync.py` 加了两条**：输出样例必须与真实运行逐字一致；
中英两份章节数/层级/顺序必须一致。

### 三条评审遗留决策的落地（2026-11-15）

| 决策 | 落地 |
| --- | --- |
| **去掉仓库凭据前缀**（用户决定） | `ENV_ALLOW_PREFIX` 去掉 `NPM_*` / `PNPM_*` / `YARN_*` / `PIP_*` / `UV_*`；`NODE_AUTH_TOKEN` 另进 `ENV_DENY`（它落在 `NODE_*` 前缀里）。于是「云厂商密钥与仓库凭据都不进子进程」这句话**第一次是真话**。代价：仓库的 test 命令要 npm/pip 配置时得用配置文件（`.npmrc` / `pip.conf`）而不是环境变量。三份文档 + 守卫同步 |
| **Python 车道的选取规则**（我决定） | **保留关键词成词匹配**（`coverage` / `test-coverage` / `py-cov` 认，`recover` / `cover` 不认），另外加一条：**提到本模块的目标排前面**。不收窄成显式白名单 —— Python 仓库的闸门名字五花八门，收窄会把真闸门也挡掉，代价大于收益。残余风险写进 README：名字像闸门的目标会被跑起来，`--dry-run` 能看到 |
| **解析 pytest 输出**（我决定） | 新增 `_pytest_summary()` + pytest 的 `FAILED`/`ERROR` 短摘要行识别。Python 仓的真后端车道（tox / make integration）此前跑完了也只能判「未实测」—— 它其实跑了，输出我们没读懂；现在给出「实测通过 / 不通过」，README 的「已知缺口」那条随之改写 |

回归：`test_readiness_r2.py` 新增 20 条断言（关键词成词匹配、候选排序、pytest 解析、
凭据不进子进程），`test_readme_sync.py` 新增白名单前缀/拒绝名单/凭据前缀的校验。

### README 按 readme-optimization 的评分表重写（2026-11-15）

用 `samber/developer-relations-skills@readme-optimization` 的机械审计 + 评分表打分，
再按它给的顺序修。审计前后的硬数据：

| 项 | 之前 | 之后 |
| --- | --- | --- |
| 第一个可复制命令的位置 | 第 713 词 | **第 29 词**（评分表要求 ≤400 词） |
| 章节数 | 21（参考中位数 7，50% 区间 5–12） | **12** |
| 首屏 badge 数 | 8 | **4**（评分表上限 4） |
| "状态/成熟度" 内容 | 缺失（A4 阻塞项） | 有独立的 `## Status` 段 |
| 内容类别覆盖 | 缺 What / When / Contribution | **7 类全中** |

具体改动：

- **头部**：居中字标（`assets/attest-wordmark.png` + 深色版，`<picture>` 自动切）+
  一句话定位 + 4 个 assessment badge；**紧接着就是 clone → install → 跑起来的三行命令**。
- **logo 换风格**：原来的圆角方块 + 对勾整个不要了（对勾既丑，又与「本工具不判交付」
  的立场矛盾）。改成 **JetBrains Mono 800 字标**，teal；用 Chrome headless 渲染成透明底
  PNG（深浅两版），由 `svg-logo-designer` 的流程比选了 5 个字体、11 个概念后选定。
- **新增 `CONTRIBUTING.md`**（评分表里唯一还缺的类别），并把 README 的一致性检查写进去。
- **内部评审历史移出 README**：「已知缺口」从 7 行长表格压成 7 条短句 + 指向
  CHANGELOG；14 条判据从 4 张表合成 1 张；16 行开关表压成 5 个最该先知道的 + 指向
  SKILL.md。
- **状态写实**：明说「在维护中，尚未发正式版本，判据与契约在打 tag 前仍可能变」。

守卫 `test_readme_sync.py` 加了两条：两份 README 的章节数/层级/顺序必须一致；
两份 README 引用的路径必须存在。

### 头图：把真实输出渲染成终端 hero

`readme-optimization` 的判断：CLI 工具的 **output 就是产品**，terminal demo 应该顶在
头图，而不是埋在文档中段。

所以头图不再只有一个字标 —— 加了一张**用工具真实输出渲染的终端图**
（`assets/hero.png`）：命令行走真实调用形式，段标题蓝色、✓ 绿色、结论行按
阻塞/待确认/无信号/实测通过分色。渲染是 Chrome headless 出透明底 PNG，
**不是 AI 生成的图** —— 没有 slop 风险，内容也是真的。

守卫加了一条：README 用到的三个资源文件（hero + 深浅字标）必须存在。

### 已知遗留（发布时仍开放）

- **仍然开放**：
  - SkillSpector 的 `E2`（Env Variable Harvesting）仍会报 HIGH ——
    子进程必须透传环境变量（白名单实现，见 `scripts/readiness.py` 的 `child_env`）。
    逐条复核结论在 `docs/oss-release-review.md` 第六节；CI 用
    `.github/skillspector-allowlist.yaml` 把**已复核的规则**显式放行（每条带理由），
    allowlist 之外的新 HIGH 仍然拦住。
    **放行是规则级、不是逐条 finding**：原来的做法用扫描器的 baseline（按位置指纹
    匹配），改了 `scripts/` 里那一行就失效 ⇒ 对开源贡献者是持续的假红（每次改代码
    都要先"重新生成基线"，久而久之就没人看红灯了）。规则级不依赖位置，改代码不会
    误红，而**新规则**仍然会被拦。代价（同一个已放行规则再出现不会被拦）写在
    allowlist 文件头，不藏着。
    本地复现：`bash .github/check-skillspector.sh`。
  - **`--exec` 显式执行开关没有采纳**：brief 要求"默认不跑、显式开关才跑"，
    实现改成**默认执行 + 执行前打印审计行**（报告头部一行「将执行的仓库脚本」，
    加 `--dry-run` 可只看不做）。理由：工具的定位就是实测（不跑测试的交付判定没有
    意义），而执行面已经收敛到脚本白名单 + 环境变量白名单；再叠一个必填开关，
    每次调用都要多打一个参数，实际收益不大。想看执行清单用 `--dry-run`。
  - **Windows 未端到端验证**：skill 的**运行时**是纯 Python、**安装**走 `install.py`
    （跨平台，纯标准库）—— 这两块与平台无关。仍需 bash 的是仓库自己的开发脚本
    （`fixtures/run_fixtures.sh`、`.github/*.sh`），在 Windows 上要 WSL 或 Git Bash。
  - **触发准确度已实测，有边界**：精确率 1.0（10 条近邻反例一条都没误触发），
    召回率 0.5，F1 0.667。数据与解读见 `evals/README.md`。
    **这个数测的是本机 60 多个 skill 的竞争环境，不是描述的绝对质量** —— OSS 用户
    装上时 skill 集合不同，数不可直接迁移。别对着它反复调 description
    （v1 这么干过，把召回从 0.4 调到 0.3）。
  - **自检副本默认不带 `node_modules`** ⇒ 多数仓库在副本里跑不动测试，`--self-check`
    默认证明的是"静态判据抓得住东西"，不是"整套判据（含实测项）抓得住东西"。
    报告里明说了这件事（`copy_runnable: false`）。
    加 `--self-check-deps` 会把依赖**软链**进副本（不复制体积），那时能验到实测类判据；
    默认不开，因为软链让副本里的测试连到真实依赖。
  - **Python 仓的真后端（itest）车道只跑不判**：Makefile / tox 里的真后端目标**能被找到
    并被 `--include-itest` 执行**（会连 PG 与向量库），但 pytest 的输出既不是 TAP 也不是
    vitest/jest/mocha 形态 ⇒ 解析不出结果，那条判据永远落在「未实测」，
    而不是一次「实测通过 / 不通过」。
  - **向量集合与线上真实租户是否一致**：不判。现在判的是**代码侧自洽**
    （同一集合的维度/度量/分区键在调用点之间是否一致、分区键是不是向量字段、
    写入与检索的集合是不是同一个）。要判线上一致性，得连活的向量库并用项目自己的
    凭据 —— 本工具不替项目保管向量库凭据，也不替项目猜集合命名规范。
    可行的路子：项目自己出一个「集合 schema 快照」脚本，工具去跑它（与现有
    对账脚本同一条路子）。
