# 命令、开关与安全须知

命令怎么跑、开关什么意思、它会动你的仓库什么，以及反向验证怎么用。

## 安全须知（`readiness.py` 会做什么）

这个工具的定位是"实测"，所以它**会执行目标仓库自己定义的脚本**。这不是副作用，
是它的本职 —— 但你得知道边界在哪：

- **默认就是执行**。不跑测试的交付判定没有意义，所以没有"必须显式开关才跑"这一层
  （brief 里要求过，改了：每次调用多打一个开关，收益不大）。它的安全边界是下面这三件套：
  **脚本白名单 + 执行前的审计行 + `--dry-run` 可预览**。
- **脚本选取分两种规则**（按生态不同，Python 那条更松）：
  - **JS 仓（`package.json`）**：**显式白名单** —— `coverage` / `test:coverage` / `cov` /
    `check:coverage`、`itest` / `test:integration`、`reconcile` / `data:reconcile`、
    `baseline:check` / `eval:baseline:check` 这些名字（精确名或冒号分段前缀）。
    `reconcile:reset`、`coverage:whatever` 这种名字里带关键词的**不会**被自动执行。
  - **Python 仓（Makefile / tox.ini）**：按**目标名里的关键词**认（`cov` / `reconcile` /
    `integration` / `itest`），且必须**成词** —— `coverage`、`test-coverage`、`py-cov`
    认，`recover`、`cover` 不认（子串匹配会把 `recover` 当成覆盖率闸门跑起来）。
    覆盖率闸门额外接受"模块目录或其上层"的 Makefile（所以全仓一个 `make coverage`
    仍然算数），对账与真后端两条车道还要求配方或命令提到本模块。
  - 不放心就用 `--dry-run` 看清楚到底会跑哪些目标。
- **执行前可以先看**：`--dry-run` 只列出将要执行的脚本与确切命令，然后退出。
  正常模式也会在报告头部回显一行「将执行的仓库脚本」，**包含仓库自己的 test 命令**
  （那是工具跑得最多的一层，逐个测试文件跑）。`--json` 时这行走 stderr，stdout 保持纯 JSON。
- **子进程只拿到白名单环境变量**（PATH、HOME、LANG、TZ、TMPDIR、SHELL、CI、
  `LC_*`、`NODE_*`、`PYTHON*`、POSTGRES_URL 等）。
  你 shell 里的**云厂商密钥**（`AWS_*` / `GCP_*` / `AZURE_*`）与**仓库凭据**
  （`NPM_TOKEN`、`NODE_AUTH_TOKEN`、`PIP_INDEX_URL` 里的密码、
  `UV_INDEX_SECRET_PASSWORD`）都**不会**传下去 ——
  `NPM_*`/`PNPM_*`/`YARN_*`/`PIP_*`/`UV_*` 这些前缀根本不在白名单里，
  `NODE_AUTH_TOKEN` 另外在拒绝名单上。仓库的 test 命令要 npm/pip 配置就用配置文件
  （`.npmrc` / `pip.conf`），别放环境变量。
- **PG 连接串不进命令行参数**：解析成 `PGHOST` / `PGPASSWORD` 等环境变量传给 `psql`，
  不出现在进程表里。注意 `POSTGRES_URL` 本身会透传给仓库自己的脚本（仓库的集成车道
  要连库）。真正能用的开关只有两个：把 `POSTGRES_URL` / `DATABASE_URL` 从环境里
  unset 掉、改用 `--database-url` 传（它不读环境变量，所以不会进子进程环境）；
  或者设 `PGHOST` / `PGUSER` / `PGDATABASE`，口令让 `psql` 从 `.pgpass` 读 ——
  只上 `.pgpass` 不够，schema 判定只读 `PG*` 变量，没有它们就判「未实测」。
- **只读为主**：`readiness.py` 不改用户仓库的任何文件。`req_plan.py scaffold` 会写测试
  文件，但写入范围被限制在 `--out-dir` 与仓库根之内；钉里带 `..` 或绝对路径 ⇒ 拒绝并
  报红，不写盘。
- **钉只能指向仓库内的文件**：越界的钉不读也不写（计划里的路径不能用来探测仓库外的
  文件）。

## 反向验证：证明这套判据抓得住东西

一个只输出绿色的判定工具，用户只能相信它的绿。`--self-check` 让工具**证明自己能失败**：

```bash
python3 "$SKILL_DIR/scripts/readiness.py" <模块路径> --repo <根> --self-check
```

它把仓库复制到临时副本，往副本里注入一个**已知缺陷**，再跑一遍判据，看它是否翻红：

| 注入 | 期望翻红的判据 |
| --- | --- |
| `drop-test` —— 清空一个引用了模块导出的测试文件 | 导出的逻辑都有测试（pass → fail） |
| `inject-sleep` —— 往测试里插一个固定等待 | 测试卫生（pass → warn） |

结论三种：**反向验证通过** / **反向验证未通过**（注入前就不绿，或注了也没翻红）
/ **没有可注入的故障目标**（模块没有测试文件 —— 那是"没法证明"，不是"证明没问题"）。
后两种退出码 1 —— 这是**工具自检**的结果，与模块本身的判定无关。

副本**默认不带 `node_modules`**，所以大部分仓库在副本里跑不动测试：这时翻红的是静态判据，
报告会明说「副本里跑不动测试」。要连实测项一起证明，加 `--self-check-deps`：

```bash
python3 "$SKILL_DIR/scripts/readiness.py" <模块路径> --repo <根> \
  --self-check --self-check-deps
```

它把 `node_modules` / `.venv` **软链**进副本（不复制体积）⇒ 副本里能真跑测试 ⇒
反向验证能覆盖**实测类**判据（测试是否绿、覆盖率），不只是静态判据。

## 常用命令

```bash
# 1. 生成需求测试计划骨架
python3 "$SKILL_DIR/scripts/req_plan.py new <feature> \
  --repo <根> --module <子路径>

# 2. 从计划生成测试工具包骨架（按仓库约定）
python3 "$SKILL_DIR/scripts/req_plan.py scaffold \
  <计划文件> --repo <根> [--out-dir <生成根>]

# 3. 核对计划 vs 代码实况
python3 "$SKILL_DIR/scripts/req_plan.py check <计划文件> --repo <根>

# 3'. 按计划的数据一致性节生成对账脚本骨架（跨存储才有意义）
python3 "$SKILL_DIR/scripts/req_plan.py reconcile <计划文件> --repo <根> \
  [--out scripts/reconcile-<模块>.ts]

# 3''. 生成基线对拍脚本骨架（比「现在」与「当初的快照」）
python3 "$SKILL_DIR/scripts/req_plan.py baseline <计划文件> --repo <根> \
  [--out scripts/baseline-<模块>.ts]
#   填完 <待填> 后在 package.json 里加一条
#   "eval:baseline:check:<模块>": "tsx scripts/baseline-<模块>.ts"
#   ⇒ readiness 才会认出这条车道并真跑它

# 4. 模块交付就绪度判定（一条命令，实测）
python3 "$SKILL_DIR/scripts/readiness.py <模块路径> --repo <根> \
  [--plan <计划文件>] [--include-itest] [--test-tenant <名字或前缀>] [--json]

# 5. 先看会执行哪些仓库脚本（不执行）
python3 "$SKILL_DIR/scripts/readiness.py <模块路径> --repo <根> --dry-run
```

`readiness.py` 的常用参数：

| 参数 | 作用 |
| --- | --- |
| `--plan <文件>` | 把需求测试计划一起核（需求覆盖到位那条） |
| `--json` | 机器可读输出（checks + conclusion 段） |
| `--dry-run` | 只列将要执行的仓库脚本，不执行 |
| `--self-check` | 反向验证：往**副本**里注入一个已知缺陷，证明判据会翻红（不改你的仓库） |
| `--include-itest` | 把真后端（itest）车道并进来（要连 PG + 向量库，慢） |
| `--test-tenant <名/前缀>` | 声明测试租户（可重复）—— 测试租户的漂移是夹具状态，不阻塞 |
| `--test-dir <目录>` | 额外指定测试目录（默认找仓库级 `test/` `tests/` `spec/` 与模块内 colocated） |
| `--min-line-coverage` / `--min-branch-coverage` | 显式指定通用阈值（默认不阻塞，仓库自己的闸门优先） |
| `--drift-pattern` / `--table-header-pattern` | 对账输出的漂移/表头识别方式（对账脚本输出英文时用） |
| `--csv-pair a.csv=b.csv` | 声明 CSV 的对照关系（默认按共同列最多的互为最佳配对） |
| `--tenant-col <列名>` | CSV 里租户列的名字（默认按 `tenant_id`/`tenant` 认） |
