<div align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/logo-dark.svg">
    <img alt="attest" src="assets/logo.svg" width="96" height="96">
  </picture>
  <h1>attest</h1>
</div>

<p align="center">
  <a href="https://github.com/mcgrapeng/attest/actions/workflows/ci.yml"><img alt="CI" src="https://img.shields.io/github/actions/workflow/status/mcgrapeng/attest/ci.yml?branch=main&style=flat-square&label=CI"></a>
  <a href="https://github.com/mcgrapeng/attest/blob/main/LICENSE"><img alt="License" src="https://img.shields.io/badge/license-MIT-0F766E?style=flat-square"></a>
  <a href="#prerequisites"><img alt="Python" src="https://img.shields.io/badge/python-3.11%2B-0F766E?style=flat-square"></a>
  <a href="https://github.com/mcgrapeng/attest"><img alt="Stars" src="https://img.shields.io/github/stars/mcgrapeng/attest?style=flat-square&label=stars&color=0F766E"></a>
</p>

<p align="center">
  <a href="#install"><img alt="Claude Code" src="https://img.shields.io/badge/Claude_Code-supported-0F766E?style=flat-square"></a>
  <a href="#install"><img alt="Codex" src="https://img.shields.io/badge/Codex-supported-0F766E?style=flat-square"></a>
  <a href="#install"><img alt="OpenCode" src="https://img.shields.io/badge/OpenCode-supported-0F766E?style=flat-square"></a>
  <a href="#known-gaps"><img alt="Windows" src="https://img.shields.io/badge/Windows-partial-8A8F98?style=flat-square"></a>
</p>

<p align="center">
  <a href="README.md">English</a> | <a href="README.zh-CN.md">简体中文</a>
</p>

---

## What is attest?

attest is a requirement-level test toolkit for delivery readiness. It takes one
requirement and produces a test plan whose every invariant is pinned to a test
case that really exists, then **measures** the module's delivery preconditions:
which ones are proven, which are only signals read out of the source, which were
never measured, and which it refuses to judge.

It drives the target repo's own scripts — its test command, its coverage gate,
its reconciliation script — instead of replacing them, and it queries the
PostgreSQL catalog for the real schema. Nothing is judged from reading source
alone.

**It is not a "can we ship" referee.** It produces evidence; the decision stays
with whoever owns the delivery.

## Measured on this repo

> Every number below comes from a command you can re-run. The trigger-accuracy
> figures measure the competition on the machine where they were taken (60+
> installed skills); on your machine the skill set differs, so those two do not
> transfer.

| Gate | What it measures | Why it matters |
| --- | --- | --- |
| **25 / 25** regression scripts | The tool's own behaviour, including every false green it has been known to produce | A criterion that cannot fail is not a criterion |
| **5 / 5** golden fixtures | Whether the tool reports the same verdict on small repos with pinned expectations | Reproducible proof against false greens |
| **6 / 6** behaviour evals | Whether "the user says X ⇒ the tool answers Y" holds | The skill contract, not just the scripts |
| **14** criteria | Every criterion carries an evidence kind: measured, static signal, or not measured | Nothing is presented as measured unless it was |
| **1.0 / 0.5** trigger precision / recall | Whether the skill fires on the right requests and stays quiet on near misses | The low recall is the competition's fault, not the description's |
| **0** unallowed HIGH / CRITICAL findings | The security scan, with a rule-level allowlist | Reviewed rules are named; new ones still block the build |

## Why attest?

### The problem with "it looks green to me"

- **Coverage is not strength.** 90% line coverage says the code ran, not that an
  assertion would fail. A suite whose assertions are tautological stays green
  through every defect.
- **A checklist in a chat thread is not evidence.** "Wrote the test, marked it
  done" cannot be checked later, and the pin may point at a file that never
  existed.
- **Two stores, one truth, nobody watching.** Postgres and the vector store
  drift apart silently, and the drift surfaces when a customer sees it.

### Core design: measurement, not judgment

**Measurement — hard constraints**

- **The repo's own scripts run.** The tool drives your test command, your
  coverage gate, your reconciliation script. It does not reimplement your stack.
- **Every criterion carries an evidence kind.** Measured (something ran),
  static signal (read out of the source), or not measured (it could not run). A
  static signal can never support a pass on its own.
- **Pins are checked against reality.** A plan that pins an invariant to a test
  case that is not in the file fails `check`, with exit code 1.

**Judgment — left to a human**

- **Requirement understanding.** The tool reports whether each invariant is
  pinned to a real case; it cannot tell you the invariant is the right reading
  of the requirement. The generated plan carries a `人工确认` section for that,
  and until someone fills it in, every report says the plan was not reviewed.
- **Delivery.** Performance, security, availability and operational readiness
  are outside its scope, and it prints that list on every report.

## How to use

### Prerequisites

- **Get the code** — the install command below lives in the repository:

  ```bash
  git clone https://github.com/mcgrapeng/attest.git && cd attest
  ```

- **Python 3.11+** — the tool itself is standard library only, but it runs your
  repo's own scripts, so your repo's runtime has to be present.

### Install

```bash
python3 install.py              # cross-platform (stdlib only, no bash needed)
# or: bash install.sh           # a thin shell wrapper around the same code
```

It symlinks this repository to `~/.agents/skills/attest`, then symlinks that
entry into `~/.claude/skills/`, `~/.codex/skills/` and
`~/.config/opencode/skills/`. An existing symlink with the same name is
replaced (the script says so); if the target path is a **real directory** the
script skips it and never deletes user data. Uninstall with
`python3 install.py --uninstall` (or `bash uninstall.sh`) — symlinks only.

Manual install is equivalent to:

```bash
ln -s "$(pwd)" ~/.agents/skills/attest
ln -s ~/.agents/skills/attest ~/.claude/skills/attest
ln -s ~/.agents/skills/attest ~/.codex/skills/attest
ln -s ~/.agents/skills/attest ~/.config/opencode/skills/attest
```

`~/.agents/skills/attest` is the stable entry point. On the machine this was
developed on, `~/.agents/testkit/doctor.sh` runs the regression suite from that
entry point; that file is part of the author's machine-level toolchain, not of
this repository, so run the suite directly instead:

```bash
for t in scripts/test_*.py; do python3 "$t"; done
```

### Distribute as a `.skill`

```bash
python3 package.py            # → dist/attest.skill
python3 package.py --check    # list what would be packaged, write nothing
```

`.skill` is the community's distribution format (a zip whose first level is the
skill directory). The packager is **self-contained**: it needs no external
tooling. Excluded are the repo's own working files — `.git` / `.github` /
`dist` / `fixtures` / `evals` / `.omx` / `node_modules` / virtualenvs / tool
caches / bytecode — and `scripts/test_*.py` (the regression suite belongs to the
repo, not to the skill's runtime). **It refuses to package bytecode** — compiled artifacts
leak the author's environment and give users an unreadable bundle.

### Quick start

**1. Plan the requirement**

```bash
python3 ~/.agents/skills/attest/scripts/req_plan.py new <feature> \
  --repo <repo root> --module <subpath> [--module <subpath>...]
```

Scans the target module for existing helpers and for the repo's own test lanes,
then writes empty invariant and coverage-matrix tables.

**2. Fill in the invariants**

Every invariant is one falsifiable sentence ("the export contains every
completed order of this month, no more and no less"); every matrix row names
the test file that covers it. This is the part a human reads.

**3. Check the plan against the code**

```bash
python3 ~/.agents/skills/attest/scripts/req_plan.py check <plan file> \
  --repo <repo root>
```

Verifies each pin: does the file exist, does the test case really live in it.
Exits 1 on errors.

**4. Measure the readiness**

```bash
python3 ~/.agents/skills/attest/scripts/readiness.py <module path> \
  --repo <repo root> \
  [--plan <requirement test plan>] [--include-itest] [--test-tenant <name or prefix>] [--json]
```

Runs the repo's own tests and coverage gate, queries the PG catalog, drives the
repo's own reconciliation script, and prints the evidence list. `--dry-run`
lists the scripts it would execute without running them.

Optionally, `req_plan.py scaffold <plan> --repo <root>` generates test file
skeletons that follow the repo's own conventions; when it cannot recognise them
it refuses to generate and reports red instead of guessing.

### Use it from your agent

The skill itself is what an agent loads: [SKILL.md](SKILL.md) is the contract
that says when the skill fires and what it must do. Once it is installed, the
agent runs the same one command the CLI does:

```bash
python3 "$SKILL_DIR/scripts/readiness.py" <module path> --repo <repo root>
```

`$SKILL_DIR` is the skill's own directory — `~/.agents/skills/attest` with the
installer above. The CLI form (with the full `~/.agents/skills/attest/scripts/...`
path) is the same tool.

### Switches

Of `readiness.py`:

| Switch | What it does |
| --- | --- |
| `--plan <file>` | The requirement test plan to check the module against (drives the 需求覆盖到位 criterion) |
| `--json` | Machine-readable output (checks + the verdict counters) |
| `--dry-run` | **Only lists** the repo scripts and commands that would be executed — including the repo's own `test` command — then exits (executes nothing). Look before you run |
| `--self-check` | Reverse verification: inject a known defect into a **temporary copy** to prove the criteria turn red. Your repo is not touched |
| `--self-check-deps` | With `--self-check`: **symlink** `node_modules` / `.venv` into the copy (no copying), so the copy can really run the tests and the measured criteria get verified too |
| `--mutation-check` | Test strength: break the source and see whether **the tests turn red**. It copies the workspace and runs the tests many times, so it is expensive ⇒ **off by default** (without it that criterion is "not measured") |
| `--include-itest` | Count the real-backend itest lane too (needs PostgreSQL + a vector store; slow) |
| `--test-tenant <name or prefix>` | Declare a test tenant (repeatable) — drift in a test tenant is fixture state, so it does not block |
| `--test-tenant-prefix <prefix>` | Add a test-tenant prefix (defaults recognise `itest-` `memtest-` `mock-` `eval-` …) |
| `--test-dir <path>` | Extra test directory (for repos that keep tests outside `test/` `tests/` `spec/`); repeatable |
| `--drift-pattern <regex>` | What a "drift row" looks like in the reconciliation output (default: `漂移` / `❌` / `drift` / `mismatch`) |
| `--table-header-pattern <regex>` | What a header/separator row looks like in the reconciliation output (default: `一致性` / `租户` / `tenant` / `---`) |
| `--csv-pair a.csv=b.csv` | CSV pairing, repeatable — declared pairs win; with nothing declared the tool auto-pairs the remaining CSVs by the pair sharing the most columns (mutual best), never all-pairs |
| `--tenant-col <name>` | Which CSV column holds the tenant (defaults to `tenant_id` / `tenant`) |
| `--min-line-coverage` / `--min-branch-coverage` | Explicit coverage thresholds. **No generic threshold by default** (no repo gate ⇒ judged `skipped`, not `fail`) |
| `--database-url <url>` | PostgreSQL connection string (defaults to `POSTGRES_URL`) |
| `--verbose` | Print the raw test output too |

`req_plan.py` sub-commands and their extra flags: `new` takes `--out` (default
`docs/<feature>-test-plan.md`) and `--force`; `scaffold` takes `--out-dir`
(default: the repo root), `--framework` (`node:test` / `vitest` / `jest` /
`mocha`) and `--force`; `check` takes only `<plan>` and `--repo`.

## What it measures: 14 criteria

The tool's own output is in Chinese; the first column below is the criterion
name it **actually prints** (not a rename), and `key` is the machine-readable
identifier in `--json`.

**The tests themselves (5)**

| Criterion | key | Evidence kind | How it is measured |
| --- | --- | --- | --- |
| 测试全绿 (tests green) | `tests-green` | Measured | Runs the module's tests with the **repo's own** test command and lists the failures (**assertion failures only**; a setup error means the environment is not ready ⇒ not measured) |
| 核心逻辑测试到位 (core logic is covered) | `coverage` | Measured | **Drives the repo's own coverage gate**; only without a gate do you use `--min-line-coverage` explicitly (and that does not block by default) |
| 测试能发现缺陷 (the tests can find defects) | `test-strength` | Measured | Breaks the source on purpose and runs the tests: red ⇒ the mutant was killed, still green ⇒ **this suite cannot find this class of defect** (`--mutation-check`; off by default ⇒ not measured by default) |
| 测试卫生（不留残留、不靠 sleep）(hygiene: no leftovers, no fixed sleeps) | `hygiene` | Static signal | Reads the test sources: fixed waits (a source of flakiness) and database writes without cleanup (a source of leftovers) |
| 导出的逻辑都有测试 (every export is referenced by a test) | `uncovered-exports` | Static signal | Finds references in the test sources: exported functions/classes with zero references are named (TS exports / Python public def·class / Rust `pub fn`) |

**Data and stores (4)**

| Criterion | key | Evidence kind | How it is measured |
| --- | --- | --- | --- |
| 表结构基本合规 (schema is basically compliant) | `schema` | Measured | Queries the PG catalog: primary key / unique constraint / tenant column / RLS+FORCE / timestamps with a timezone (single-tenant, config and append-only tables are exempt) |
| 向量集合属性自洽 (vector collection attributes agree) | `vector-schema` | Static signal | Whether one collection's dimension/metric/partition key agree across call sites; the partition key must not be a vector field; whether the write and the read collection are the same one |
| 数据流一致（跨存储对账）(data agrees across stores) | `data-flow` | Measured | **Runs** the module's own reconciliation script and parses the drift; judged **per tenant kind**; the field-level comparison is part of this one |
| 数据准确（基线对拍）(data matches the baseline) | `data-accuracy` | Measured | **Runs** the repo's own baseline script and parses the metric differences |

**Requirements and design (3)**

| Criterion | key | Evidence kind | How it is measured |
| --- | --- | --- | --- |
| 需求覆盖到位 (requirement is covered) | `requirement` | Static signal | Reads the plan and the test files: every invariant's pin → a test case that really exists (`--plan`); an unreviewed plan downgrades this to advisory |
| 不过度抽象 (not over-abstracted) | `extensibility` | Static signal | Exported symbols with almost no call sites anywhere in the repo (rule three) |
| 没有重复造轮子 (no reinvented wheels) | `no-wheels` | Static signal | Same-named implementations across the repo (same name across domains downgraded to advisory) |

**Dependencies and assertions (2)**

| Criterion | key | Evidence kind | How it is measured |
| --- | --- | --- | --- |
| 使用主流开源库 (uses mainstream open-source libraries) | `open-source` | Static signal | The module's real dependency list (path aliases and the standard library excluded) |
| 断言的期望值独立 (assertion oracles are independent) | `oracle` | Static signal | Assertions whose expected value comes from the system under test (tautological self-comparisons are called out) ⇒ advisory |

**The evidence kind says what a criterion is derived from** (something that ran
vs something read out of the source). It also decides which section of the
report the criterion lands in: **a static signal never supports a pass on its
own.** The kind is assigned **per run**: a criterion that needs a database is
marked "not measured" when there is no connection, never "measured".

## The output

Real output from the repo's own fixture (`fixtures/ts-colocated`):

```
将执行的仓库脚本：测试车道（逐个测试文件跑 $ node --test <测试文件>）

交付就绪度分析 — src
仓库：<repo root>
源码 1 文件（+0 个测试支撑） / 单测 1 个 / itest 0 个

── 已实测 ───────────────────────────────────────────
  ✓ 测试全绿 — 没有失败用例
  — 核心逻辑测试到位 — 仓库没有覆盖率闸门，缺少判据（实测平均行覆盖 100% / 分支 100%）；通用阈值默认不阻塞 （用项目自己的 test 命令） 
      · calc.ts: 行100% 分支100% 函数100%

── 静态信号（不能单独支撑「通过」） ──────────────────────────────
  ✓ 测试卫生（不留残留、不靠 sleep） — 没有固定等待，也没发现不清理的写库测试
  ✓ 导出的逻辑都有测试 — 2 个运行时导出都有测试引用
  ✓ 不过度抽象 — 2 个导出符号都有多个调用点
  ✓ 没有重复造轮子 — 模块的导出符号在全仓都是独一份
  · 使用主流开源库 — 模块没有第三方依赖 —— 确认这是有意的（简单优先）
      → 如果需求领域有成熟库，别自己写
  ✓ 断言的期望值独立 — 扫了 1 个测试文件，没发现期望值取自被测对象自身的断言

── 未实测 ───────────────────────────────────────────
  — 测试能发现缺陷 — 未实测：加 --mutation-check 才会把源码改坏再跑测试（要复制工作区并多次跑测试，很贵）
  — 表结构基本合规 — 没连上数据库（或模块不碰 PG）
  — 向量集合属性自洽 — 这个模块没有向量集合调用点（判不了）
  — 数据流一致（跨存储对账） — 模块只碰了 PG 一个存储 —— 没有派生层要跨存储校对
  — 数据准确（基线对拍） — 项目里没有基线对拍脚本
  — 需求覆盖到位 — 没给计划文件（--plan）

── 本工具不判定 ────────────────────────────────
  需求是否被正确理解 · 产品行为是否正确 · 性能 · 安全 · 可用性 · 运维就绪
  上面只是测试就绪度的实测结果；「能不能交付」仍要人判断。

结论：0 项阻塞 · 1 项待人工确认 · 7 项无信号 · 1 项实测通过（另有 6 条静态信号）
退出码 1 = 有阻塞项，或关键判据（测试是否绿）没测到
```

Look at the last line: **7 items with no signal**, not "7 passed". A criterion
that was not measured is reported as "not measured".

### Output contract

The report has four sections. The verdict is **not** one sentence of "can ship
/ cannot ship":

| Section | Meaning |
| --- | --- |
| **已实测** (Measured) | Results that came from running something (tests run, coverage measured, PostgreSQL queried) |
| **静态信号** (Static signal) | Signals from static analysis — marked as "cannot support a pass on their own" |
| **未实测** (Not measured) | What was not measured (environment not ready, no test file found, no analysable exports) |
| **本工具不判定** (Not judged by this tool) | A fixed list, see below |

The verdict has three tiers:

| Tier | Meaning |
| --- | --- |
| **阻塞项** (Blocking) | Measured and below the bar — with one of these you do not hand over |
| **待人工确认** (Needs a human) | Measured, but a person has to make the call (body fields disagree, is the side store authoritative?) |
| **无信号** (No signal) | Not measured — environment not ready, no test file found, no analysable exports in this module |

A static signal never supports "pass" on its own. The tool also prints a fixed
**not judged by this tool** list:

> 需求是否被正确理解 · 产品行为是否正确 · 性能 · 安全 · 可用性 · 运维就绪
> (whether the requirement was understood correctly · whether product behaviour
> is correct · performance · security · availability · operational readiness)

Those six are **out of scope** for this tool. The delivery decision stays with
a human.

Exit codes:

| Code | Meaning |
| --- | --- |
| 0 | No blocking items |
| 1 | There are blocking items, or a key criterion (whether the tests are green) was not measured — "meets the bar" cannot be claimed |
| 2 | The module could not be recognised (language out of scope, path does not exist, invalid argument) — it says why instead of giving a false verdict |

A failed `--self-check` also exits 1. That is a failure of the tool's own
self-test (the criteria did not catch the injected defect), not a verdict about
the module. `req_plan.py check` exits 1 when the plan has errors; `new` exits 1
when the plan file already exists and `--force` was not given.

## Language support

| Language | Plan generation | Coverage measurement | Export-reference criterion | Cross-store reconciliation | Real-backend itest lane |
| --- | --- | --- | --- | --- | --- |
| TypeScript / JavaScript | ✅ | ✅ node / vitest / jest | ✅ | ✅ | ✅ |
| Python | ✅ | ✅ pytest-cov | ✅ module-level `def`/`class` | ✅ | ✅ |
| Rust / Go | partial (reconnaissance is reused) | — module not recognised | — | — | — |

The plan generator handles Rust and Go exports in its reconnaissance (the export
index recognises Rust `pub fn` and Go top-level functions), and
`req_plan.py scaffold` has Rust and Go branches. It is the readiness checker
that stops at the language boundary.

**How the lanes and the reconciliation script are found**: in a JS repo from the
`package.json` scripts; in a Python repo from `Makefile` targets and `tox.ini`
environments (e.g. `make coverage`, `make integration-store`,
`make reconcile-store`, `[testenv:integration]`). Both routes need two things:

1. a keyword in the target name (`cov` / `reconcile` / `integration` / `itest`);
2. the target body or command mentioning **this module** (a path or a table
   name). The tool has to confirm this is this module's lane, not someone
   else's.

On a Rust or Go module `readiness.py` **exits with code 2** and says that only
TS/JS/Python is implemented. It does not give a false verdict: "recognised but
no data" and "not recognised" are different, and only the second one is true.
A report full of ✓ where nothing was measured is worse than an error.

## Security notes

`readiness.py` **executes scripts the target repo defines itself** (the `test`,
`coverage`, `reconcile` and `eval:baseline:check` entries in `package.json`).
Three things to know:

1. **See what it will run first**. `--dry-run` lists the candidate scripts and
   the exact commands without executing them. In normal mode the tool prints
   that line before it runs anything — it is the audit trail, and it includes
   the repo's own `test` command, which is the largest thing the tool runs (it
   is executed once per test file).

   Script selection has **two** rules, and they differ by ecosystem:

   - **JS repos (`package.json`)** — an **explicit allowlist** of script names
     (exact names or allowlisted prefixes). A script that merely has a keyword
     in its name (for example `reconcile:reset`) is never auto-executed.
   - **Python repos (Makefile / tox.ini)** — matched by **keyword in the target
     name** (`cov` / `reconcile` / `integration` / `itest`), as a whole word:
     `coverage`, `test-coverage` and `py-cov` qualify; `recover` and `cover` do
     not. The coverage gate additionally accepts a Makefile anywhere at or above
     the module directory, so a repo-wide `make coverage` still counts; the
     reconcile and itest lanes additionally require the recipe or the command
     to mention this module.

   The Python rule is the looser one: a target whose name looks like a coverage
   gate will be run. The tool never runs anything the repo has not declared
   itself, and `--dry-run` shows exactly which targets would run — so if you do
   not want a target picked up, do not give it a gate-like name.
2. **Child processes get an allowlisted environment only**: `PATH`, `HOME`,
   `LANG`/`LANGUAGE`, `TZ`, `TMPDIR`/`TEMP`/`TMP`, `SHELL`, `USER`/`LOGNAME`,
   `TERM`, `CI`, `SYSTEMROOT`/`COMSPEC` (Windows), the prefixes `LC_*`,
   `NODE_*`, `PYTHON*`, plus the `POSTGRES_URL` / `DATABASE_URL` / `MEMORY_ITEST` /
   `MEMORY_RECONCILE_OUT_DIR` / `REQ_PLAN_RECONCILE_OUT_DIR` variables the tool
   itself needs.

   **Nothing outside that list is passed through**: your cloud provider keys
   (`AWS_*` / `GCP_*` / `AZURE_*`) and your registry credentials (`NPM_TOKEN`,
   `NODE_AUTH_TOKEN`, `PIP_INDEX_URL` with a password in it,
   `UV_INDEX_SECRET_PASSWORD`) do not reach the target repo's scripts. The
   `NPM_*` / `PNPM_*` / `YARN_*` / `PIP_*` / `UV_*` prefixes are not on the
   allowlist at all, and `NODE_AUTH_TOKEN` is on an explicit **deny** list even
   though `NODE_*` is allowed. If a repo's test command needs npm or pip
   configuration, put it in a config file (`.npmrc`, `pip.conf`) rather than in
   the environment.

   The two connection strings *are* passed through: a repo's own integration
   lane has to reach its database, and that trade-off is deliberate. The levers
   that exist: unset `POSTGRES_URL` / `DATABASE_URL` in the environment and pass
   the connection string with `--database-url` (it is not read from the
   environment, so it never enters the child environment), or set `PGHOST` /
   `PGUSER` / `PGDATABASE` and let `psql` read the password from `.pgpass`.
   `.pgpass` alone is not enough: the schema query reads only the `PG*`
   variables, so without them the schema criterion degrades to "not measured"
   instead of being measured through `.pgpass`. The `psql` password travels in
   the `PGPASSWORD` environment variable, never in a command-line argument.
3. **Trusting a repo's `test` script means handing it your environment**. The
   risk level of that layer equals the risk level of every script it runs.
   With `--self-check-deps` the copy's `node_modules` / `.venv` are **symlinks
   to the real ones**, so a test that writes inside a dependency directory
   still writes to the real one.

`req_plan.py scaffold` writes test files into the repo. Two gates run before
anything is written: a pinned path must still be under `--out-dir` after
normalisation (a pin that escapes it is neither read nor written), and test
case names are quote-escaped per language (the plan's markdown is not turned
into executable code). When it cannot recognise the repo's test conventions it
**refuses to generate** and reports red instead of guessing.

`readiness.py` reads pins from a plan file too, and applies the same rule: a
pin that points outside the repo is neither read nor written.

## Known gaps

The honest list: the full version is in [CHANGELOG.md](CHANGELOG.md) under
"已知遗留" (known leftovers), and the per-item review records are in
[docs/oss-release-review.md](docs/oss-release-review.md) and
[docs/testing-philosophy-review.md](docs/testing-philosophy-review.md)
(read-only). These are the seven items still open at release time:

| Item | Status |
| --- | --- |
| SkillSpector's `E2` (Env Variable Harvesting) still reports HIGH | **Mitigated.** Child processes must inherit environment variables (allowlist implementation). CI uses `.github/skillspector-allowlist.yaml` to allowlist the **reviewed rules** explicitly, each with a reason, and a new HIGH outside the allowlist still fails the build. The allowlist is **rule-level, not finding-level**: the old baseline matched by source location, so changing one line of code invalidated it, which is a permanent false red for contributors. Rule-level allowlisting does not depend on location, while a **new rule** is still blocked. The cost is written in the allowlist header rather than hidden |
| The `--exec` explicit execution switch was not adopted | **Not adopted.** The implementation is **execute by default, print an audit line before executing**, plus `--dry-run` to look without doing. The tool's purpose is measurement (a delivery verdict that does not run the tests is meaningless), and the execution surface is already narrowed to a script allowlist plus an environment-variable allowlist |
| Python repos' real-backend (itest) lane | **Fixed.** The lane is discovered through Makefile / tox and is really executed when `--include-itest` is passed (it connects to PostgreSQL and the vector store). pytest's output is now parsed, so the lane reports a measured pass or fail instead of always "not measured" |
| Windows not verified end to end | **Open.** The skill's **runtime** is pure Python and its **install** goes through `install.py` (cross-platform), so neither depends on the platform. But the repo's own dev scripts need bash: on Windows that means WSL or Git Bash |
| Trigger accuracy | **Measured, with a boundary.** Precision 1.0 (not one of the near-miss queries triggered it), recall 0.5, F1 0.667. Data and reading in [evals/README.md](evals/README.md). **The number measures the competition on this machine (60+ installed skills)** — an OSS user has a different skill set, so it does not transfer directly |
| The self-check copy does not include `node_modules` by default | **Mitigated.** Most repos therefore cannot run their tests in the copy, so `--self-check` proves that "the static criteria catch things", not that "the whole criterion set (including the measured ones) catches things". The report says so explicitly (`copy_runnable: false`). Add `--self-check-deps` to symlink the dependencies into the copy (no copying) and the measured criteria get verified too |
| Whether a vector collection matches the **live production tenant** | **Not judged.** That needs a live vector store and the project's own credentials — this tool does not hold vector-store credentials for the project, and does not guess the project's naming convention. What is judged today is **agreement on the code side** |

One more item that is not in the CHANGELOG's "known leftovers" list: the body of
`SKILL.md` is still Chinese. This README has a Chinese version; the skill body
does not.

## Documentation

- [SKILL.md](SKILL.md) — the full skill contract: when the skill fires, the
  workflow, and the criteria table
- [CHANGELOG.md](CHANGELOG.md) — what changed, and the known leftovers
- [docs/publication-status.md](docs/publication-status.md) — the release status
  and how it was verified
- [docs/testing-philosophy-review.md](docs/testing-philosophy-review.md) — the
  review of its testing philosophy, and what it changed

## Development

This repo runs its own tool on itself, and the evidence is reproducible:

| Gate | Command | Result |
| --- | --- | --- |
| Regression suite | `for t in scripts/test_*.py; do python3 "$t"; done` | 25/25 green |
| Golden fixtures | `bash fixtures/run_fixtures.sh` (needs `node`; the `python-repo` fixture also needs `pytest-cov`, otherwise it is skipped and the script exits 3 with one fixture undecided) | 5/5 hit, 0 mismatches, 0 undecided |
| Behaviour evals | `python3 evals/run_evals.py` | 6/6 passed |
| Security scan | `bash .github/check-skillspector.sh` (needs `skillspector`; without it the script exits 2 because the scan never ran) | 47 HIGH/CRITICAL, **0** outside the allowlist |
| Environment containment | `python3 scripts/check_env_inheritance.py` | No production code inherits the whole `os.environ` |
| Reverse verification | `python3 scripts/selfcheck.py src --repo fixtures/ts-colocated` | Reverse verification passed |

The **golden fixtures** are 5 self-contained mini repositories, each with a
**pinned expected verdict**. They are the reproducible evidence for "does this
tool go falsely green" (the `no-tests` fixture, for instance, pins "the
conclusion must not be 'meets the delivery conditions'"). Each fixture directory
has a `README.md` explaining **why it exists** (which review objection it
blocks).

The **evals** are 6 "user says this ⇒ tool answers that" behavioural assertions,
each with a machine-checkable expectation (expected exit code / strings that must
appear / strings that must not appear). How to run them:

```bash
python3 evals/run_evals.py            # all of them
python3 evals/run_evals.py --only 3   # just one
python3 evals/run_evals.py --json     # machine-readable
```

Exit codes: 0 = all passed / 1 = an assertion failed / 2 = there was no eval to
run (for example `--only` was given an id that does not exist).

CI (`.github/workflows/ci.yml`) runs four jobs: bytecode hygiene / regression +
fixtures + evals / packaging smoke / security scan.

## License

MIT
