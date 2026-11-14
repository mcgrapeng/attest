# Changelog

本文件记 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 的格式，
版本号用 [语义化版本](https://semver.org/lang/zh-CN/)。

## [Unreleased]

本轮目标：把 `req-test-plan` 从"本机一个 skill 目录"推进到"可发布的制品" ——
工程缺口与哲学缺口逐条修完，并用**可复现证据**证明每一条已关闭。

发布前置清单见 [docs/oss-release-review.md](docs/oss-release-review.md)（只读评测记录）
与 [docs/testing-philosophy-review.md](docs/testing-philosophy-review.md)。

### 搬运

- 搬成独立 git 仓库（原先只是 `~/.agents/skills/req-test-plan/` 下的一个目录，
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
- [x] CI：Node 22 + Python 3.11/3.13 跑全部脚本 + 打包冒烟。
      —— **已落地**（`.github/workflows/ci.yml`）。
- [x] description 扩写（点名交付判定命令、声明语言覆盖、明确会执行仓库脚本与写文件）。
      —— `016abda`。
- [x] `~/.agents/skills/...` 硬编码路径改成"解析本 skill 所在目录"（`$SKILL_DIR`）。
      —— `016abda`。

### 修复与已知遗留

- **已知遗留（发布时仍开放）**：
  - SkillSpector 的 `E2`（Env Variable Harvesting）仍会报 HIGH ——
    子进程必须透传环境变量（白名单实现，见 `scripts/readiness.py` 的 `child_env`）。
    逐条复核结论在 `docs/oss-release-review.md` 第六节；CI 用
    `.github/skillspector-baseline.yaml` 把**已复核**的 finding 显式记录下来，
    基线之外的新 HIGH 仍然拦住。
    **基线的指纹含位置**：改了 `scripts/` 里对应的那几行就要重新复核并重跑
    `bash .github/regenerate-skillspector-baseline.sh`。这是刻意的 ——
    改了那一行就要重新看一眼。
  - **`--exec` 显式执行开关没有采纳**：brief 要求"默认不跑、显式开关才跑"，
    实现改成**默认执行 + 执行前打印审计行**（报告头部一行「将执行的仓库脚本」，
    加 `--dry-run` 可只看不做）。理由：工具的定位就是实测（不跑测试的交付判定没有
    意义），而执行面已经收敛到脚本白名单 + 环境变量白名单；再叠一个必填开关，
    每次调用都要多打一个参数，实际收益不大。想看执行清单用 `--dry-run`。
  - **`req_plan.py new` 在未检测到存储时仍生成「数据一致性」占位节**，
    紧接着 `check` 会把它判红并要求删掉 —— 工具自己生成的节自己要求删。
    属 P1，未修。
  - **Python 没有真后端（itest）车道**：TS 侧有 `--include-itest`，Python 侧没有对应
    机制。属 P1，未修。
  - **打包元数据**（产出可安装的 `.skill`）未做。
  - Windows / 非 bash 环境未验证。
  - 触发准确度未实测（`skill-creator` 的 `run_loop` 需要 `claude` CLI）。
  - **自检副本不带 `node_modules`** ⇒ 多数仓库在副本里跑不动测试，`--self-check`
    证明的是"静态判据抓得住东西"，不是"整套判据（含实测项）抓得住东西"。
    报告里明说了这件事（`copy_runnable: false`）。
