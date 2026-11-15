[![CI](https://github.com/mcgrapeng/attest/actions/workflows/ci.yml/badge.svg)](https://github.com/mcgrapeng/attest/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-0F766E?style=flat-square)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-0F766E?style=flat-square)](https://www.python.org/)
[![skills.sh](https://skills.sh/b/mcgrapeng/attest)](https://skills.sh/mcgrapeng/attest)
[![Stars](https://img.shields.io/github/stars/mcgrapeng/attest?style=flat-square&label=stars&color=0F766E)](https://github.com/mcgrapeng/attest/stargazers)

<div align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/mcgrapeng/attest/main/skills/attest/assets/attest-wordmark-dark.png">
    <img alt="attest" src="https://raw.githubusercontent.com/mcgrapeng/attest/main/skills/attest/assets/attest-wordmark.png" height="56">
  </picture>
  <p><strong>Turns a module's delivery preconditions into a machine-checkable, auditable evidence list.</strong></p>
</div>

<div align="center">
  <img alt="attest 的报告：分「已实测 / 静态信号 / 未实测」三段，最后一行是结论（0 项阻塞 · 1 项待人工确认 · 7 项无信号）" src="https://raw.githubusercontent.com/mcgrapeng/attest/main/skills/attest/assets/hero.png" width="1000">
</div>

```bash
npx skills add mcgrapeng/attest        # install — no clone, no Python
python3 ~/.agents/skills/attest/scripts/readiness.py <module path> --repo <repo root>
```

## What is attest?

`attest` is a requirement-level test toolkit. It takes one requirement and
produces a test plan whose every invariant is pinned to a test case that really
exists, then **measures** the module's delivery preconditions and reports what
it found.

The measurement is real, not inferred. `attest` runs the target repo's own test
command, its own coverage gate, its own reconciliation script, and queries the
PostgreSQL catalog for the actual schema. Where it cannot measure something, it
says so instead of passing it.

It is **not** a "can we ship" referee. It produces evidence; the decision stays
with whoever owns the delivery.

## Status

**Actively developed, pre-1.0.** The tool runs on real repositories every day,
but the criteria and the skill contract may still change before a tagged
release. MIT licensed.

Every number below comes from a command you can re-run.

| Gate | What it measures | Result |
| --- | --- | --- |
| Regression suite | The tool's own behaviour, including every false green it has been known to produce | 25/25 green |
| Golden fixtures | Whether the tool reports the same verdict on small repos with pinned expectations | 5/5 hit, 0 mismatches |
| Behaviour evals | Whether "the user says X ⇒ the tool answers Y" holds | 6/6 passed |
| Security scan | SkillSpector findings against a rule-level allowlist | **0** findings outside the allowlist |
| Environment containment | Whether any production script inherits the whole `os.environ` | No |
| Reverse verification | Whether a deliberately injected defect turns the criteria red | Passed |

The trigger-accuracy figures (precision 1.0, recall 0.5) are in
[evals/README.md](evals/README.md). They measure the competition on the machine
where they were taken — 60+ installed skills — so they do not transfer to a
different skill set.

## Why attest

The question behind every review — "is this module ready?" — usually gets
answered by reading a coverage number and trusting a checklist. Both are
unreliable in ways that are easy to miss:

- **Coverage is not strength.** 90% line coverage says the code ran, not that an
  assertion would fail. A suite whose assertions are tautological stays green
  through every defect.
- **A checklist in a chat thread is not evidence.** "Wrote the test, marked it
  done" cannot be checked later, and the pin may point at a file that never
  existed.
- **Two stores, one truth, nobody watching.** Postgres and the vector store
  drift apart silently, and the drift surfaces when a customer sees it.

`attest` answers with four tiers instead of one verdict: **measured**,
**static signal**, **not measured**, and **not judged**. A static signal can
never support a pass on its own, and the tool prints a fixed list of what it
refuses to judge — requirement understanding, product behaviour, performance,
security, availability, operational readiness.

**Why the name.** *Attest* means to testify formally: to put a claim on the
record as something you have checked. The tool does the mechanical half of that
job, and it takes that half seriously — a failing test is named, coverage is
measured, drift is attributed to a tenant, and the exit code is 1 whenever a
blocking item exists or a key criterion could not be measured at all.

What it will not do is print "ready to ship". That verdict also rests on things
no tool can read off the code: whether the requirement was read correctly,
whether the behaviour is right, how fast and how safe it is. Those sit on every
report under **not judged by this tool**, and the call stays with whoever owns
the delivery. That is why the name avoids *verify*, *certify* and *sign-off*:
all three promise an approval this tool refuses to fake. (The name also shares
a root with SLSA's *attestation*, which is the same idea — a claim plus its
provenance.)

### How it is measured

- **The threshold belongs to the repo.** If the repo has a coverage gate, the
  tool drives it and judges by its exit code. Generic thresholds are only a
  fallback when the repo has no gate, and they do not block by default.
- **Measurement first.** Every criterion either has measured evidence or states
  which precondition is missing. A static inference never passes as a
  measurement.
- **"Cannot run" is not "below the bar".** Setup errors, missing artifacts and
  unreachable middleware are judged `skipped`, with the missing condition named.
- **Delivery is judged on the production tenant.** Drift in a test tenant is
  fixture state: reported as a warning, never blocking.
- **Field-level comparison across stores.** When the same id has different values
  for the same field in the authoritative store and in a derived store, the
  report names the id and the field.
- **High coverage ≠ strong tests.** Coverage only says the code was executed,
  not that an assertion would fail ⇒ there is a separate criterion for that.
- **Environment not ready ≠ the mutant was killed.** When a mutated point fails
  because of an environment error rather than an assertion failure, that point is
  judged **not decided**, not "the tests found the defect".

## What it measures

The tool's own output is in Chinese; the first column is the criterion name it
**actually prints**, and `key` is the machine-readable identifier in `--json`.

| Criterion | key | Evidence kind | How it is measured |
| --- | --- | --- | --- |
| 测试全绿 (tests green) | `tests-green` | Measured | Runs the module's tests with the **repo's own** test command, listing failures (**assertion failures only**; a setup error means the environment is not ready ⇒ not measured) |
| 核心逻辑测试到位 (core logic is covered) | `coverage` | Measured | **Drives the repo's own coverage gate**; only without a gate do you use `--min-line-coverage` explicitly |
| 测试能发现缺陷 (the tests can find defects) | `test-strength` | Measured | Breaks the source on purpose and runs the tests: red ⇒ killed, still green ⇒ **this suite cannot find this class of defect** (`--mutation-check`; off by default ⇒ not measured) |
| 表结构基本合规 (schema is basically compliant) | `schema` | Measured | Queries the PG catalog: primary key / unique constraint / tenant column / RLS+FORCE / timestamps with a timezone (single-tenant, config and append-only tables exempt) |
| 数据流一致（跨存储对账）(data agrees across stores) | `data-flow` | Measured | **Runs** the module's own reconciliation script and parses the drift; judged **per tenant kind**; field-level comparison is part of this one |
| 数据准确（基线对拍）(data matches the baseline) | `data-accuracy` | Measured | **Runs** the repo's own baseline script and parses the metric differences |
| 需求覆盖到位 (requirement is covered) | `requirement` | Static signal | Reads the plan and the test files: every invariant's pin → a test case that really exists (`--plan`); an unreviewed plan downgrades this to advisory |
| 向量集合属性自洽 (vector collection attributes agree) | `vector-schema` | Static signal | Whether one collection's dimension/metric/partition key agree across call sites; the partition key must not be a vector field |
| 测试卫生（不留残留、不靠 sleep）(hygiene) | `hygiene` | Static signal | Fixed waits (a source of flakiness) and database writes without cleanup (a source of leftovers) |
| 导出的逻辑都有测试 (every export is referenced by a test) | `uncovered-exports` | Static signal | Exported functions/classes with zero references in the tests are named |
| 不过度抽象 (not over-abstracted) | `extensibility` | Static signal | Exported symbols with almost no call sites anywhere in the repo |
| 没有重复造轮子 (no reinvented wheels) | `no-wheels` | Static signal | Same-named implementations across the repo |
| 使用主流开源库 (uses mainstream open-source libraries) | `open-source` | Static signal | The module's real dependency list (path aliases and the standard library excluded) |
| 断言的期望值独立 (assertion oracles are independent) | `oracle` | Static signal | Assertions whose expected value comes from the system under test ⇒ advisory |

**The evidence kind says what a criterion is derived from** (something that ran
vs something read out of the source). It also decides which section of the
report the criterion lands in, and it is assigned **per run**: a criterion that
needs a database is marked "not measured" when there is no connection, never
"measured".

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

The report has four sections — **已实测** (measured), **静态信号** (static
signal), **未实测** (not measured) — plus a fixed **本工具不判定** (not judged)
list. The verdict itself has three tiers: **阻塞项** (blocking: measured and
below the bar), **待人工确认** (needs a human: measured, but a person has to
call it) and **无信号** (no signal: not measured). Exit code `0` means no
blocking items, `1` means there are blocking items or a key criterion was not
measured, and `2` means the module could not be recognised — it says why
instead of giving a false verdict.

## Install and use

**One command — no clone, no Python:**

```bash
npx skills add mcgrapeng/attest
```

That is the [skills CLI](https://skills.sh). It copies the skill into
`.agents/skills/attest/` of the current project (or your global scope) and
detects which agent you run: Claude Code, Codex, OpenCode, Cursor, Windsurf,
Zed, Warp and 20 more. The skill is self-contained — `SKILL.md`, the scripts
and the assets travel with it.

**From a clone** (this repository is the source of truth):

```bash
git clone https://github.com/mcgrapeng/attest.git && cd attest
python3 install.py
```

The installer symlinks the skill directory (`skills/attest/`) to
`~/.agents/skills/attest`, then symlinks that entry into `~/.claude/skills/`,
`~/.codex/skills/` and `~/.config/opencode/skills/`, so **Claude Code, Codex
and OpenCode** pick it up. An existing symlink with the same name is replaced
(the script says so); if the target path is a **real directory** the script
skips it and never deletes user data. Uninstall with `python3 install.py
--uninstall` (or `bash uninstall.sh`) — symlinks only. On Windows `os.symlink`
may need administrator rights or developer mode; on failure the script prints
the equivalent manual commands.

The skill contract — when the skill fires and what it must do — lives in
[SKILL.md](skills/attest/SKILL.md). Once installed, the agent runs the same one
command the CLI does:

```bash
python3 "$SKILL_DIR/scripts/readiness.py" <module path> --repo <repo root>
```

The four commands, and every switch, are documented in
[SKILL.md](skills/attest/SKILL.md).
The ones worth knowing first:

| Switch | What it does |
| --- | --- |
| `--dry-run` | **Only lists** the repo scripts and commands that would be executed — including the repo's own `test` command — then exits (executes nothing) |
| `--plan <file>` | The requirement test plan to check the module against |
| `--json` | Machine-readable output (checks + the verdict counters) |
| `--self-check` | Reverse verification: inject a known defect into a **temporary copy** to prove the criteria turn red. Your repo is not touched |
| `--mutation-check` | Test strength: break the source and see whether **the tests turn red**. Expensive, so it is off by default |

Distribute it as a `.skill` bundle:

```bash
python3 package.py            # → dist/attest.skill
python3 package.py --check    # list what would be packaged, write nothing
```

## Language support

| | Plan generation | Coverage measurement | Export-reference criterion | Cross-store reconciliation | Real-backend itest lane |
| --- | --- | --- | --- | --- | --- |
| TypeScript / JavaScript | ✅ | ✅ node / vitest / jest | ✅ | ✅ | ✅ |
| Python | ✅ | ✅ pytest-cov | ✅ module-level `def`/`class` | ✅ | ✅ |
| Rust / Go | partial (reconnaissance is reused) | — module not recognised | — | — | — |

The plan generator handles Rust and Go exports in its reconnaissance (the export
index recognises Rust `pub fn` and Go top-level functions), and
`req_plan.py scaffold` has Rust and Go branches. It is the readiness checker
that stops at the language boundary — it exits with code 2 and says that only
TS/JS/Python is implemented. "Recognised but no data" and "not recognised" are
different, and only the second one is true. A report full of ✓ where nothing was
measured is worse than an error.

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
     reconcile and itest lanes additionally require the recipe or the command to
     mention this module.

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
   risk level of that layer equals the risk level of every script it runs. With
   `--self-check-deps` the copy's `node_modules` / `.venv` are **symlinks to the
   real ones**, so a test that writes inside a dependency directory still writes
   to the real one.

`req_plan.py scaffold` writes test files into the repo. Two gates run before
anything is written: a pinned path must still be under `--out-dir` after
normalisation (a pin that escapes it is neither read nor written), and test case
names are quote-escaped per language (the plan's markdown is not turned into
executable code). When it cannot recognise the repo's test conventions it
**refuses to generate** and reports red instead of guessing.

## Known gaps

Seven items are still open at release time. The full list with the reasoning
behind each is in [CHANGELOG.md](CHANGELOG.md) under "已知遗留"; the review
records are in [docs/oss-release-review.md](docs/oss-release-review.md) and
[docs/testing-philosophy-review.md](docs/testing-philosophy-review.md).

- **SkillSpector's `E2`** still reports HIGH. It is mitigated by a rule-level
  allowlist in CI: reviewed rules are named, new ones still block the build.
- **No `--exec` switch.** The tool executes by default and prints an audit line
  before doing so; `--dry-run` shows the execution list.
- **Python's real-backend lane** was discovered, executed and then judged only
  after pytest output parsing was added.
- **Windows is not verified end to end.** The runtime and the installer are
  cross-platform; the repo's own dev scripts need bash.
- **Trigger recall is 0.5** on the machine where it was measured — the
  competition, not the description.
- **The self-check copy has no `node_modules`** by default; `--self-check-deps`
  symlinks them in.
- **Whether a vector collection matches the live production tenant** is not
  judged: that needs a live vector store and the project's own credentials.

## Development and contributing

This repo runs its own tool on itself.

| Gate | Command |
| --- | --- |
| Regression suite | `for t in skills/attest/scripts/test_*.py; do python3 "$t"; done` |
| Golden fixtures | `bash fixtures/run_fixtures.sh` |
| Behaviour evals | `python3 evals/run_evals.py` |
| Security scan | `bash .github/check-skillspector.sh` (needs `skillspector`) |
| Environment containment | `python3 skills/attest/scripts/check_env_inheritance.py` |
| Reverse verification | `python3 skills/attest/scripts/selfcheck.py src --repo fixtures/ts-colocated` |
| README consistency | `python3 skills/attest/scripts/test_readme_sync.py` |

See [CONTRIBUTING.md](CONTRIBUTING.md) for the setup and the conventions a
pull request is expected to follow — including the README consistency check
that catches drift between the two languages.

## Get involved

attest is one day old, and it is meant to be built by more than one person. The
[roadmap](docs/roadmap.md) says what is being built next and where outside help
is worth the most.

- **Run it on a repository you own** and report what it got wrong — in either
  direction. A false green and a false alarm are both bugs.
- **Argue with a criterion.** A criterion that cannot be argued with is a bug.
- **Take a *good first issue*** from the issue tracker.
- **Port it** — another agent, another language, another CI.

## License

MIT
