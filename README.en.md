# attest

[中文](README.md) | English

**A requirement-level test toolkit that turns delivery preconditions into a
machine-checkable, auditable evidence list.**

It turns one requirement into a **plan you can pin down** — every invariant pinned
to a test case that actually exists, every row of the coverage matrix checked
against the repo — then measures which of a module's delivery preconditions are
**proven** (已实测), which are only **static signals** read out of the source
(静态信号), which were **never measured** (无信号), and which it does not judge
at all (本工具不判定: whether the requirement was understood, performance,
security, availability, operational readiness).

**It is not a "can we ship" referee.** It produces evidence; the decision is yours.

Python 3.11+ standard library only — there is nothing to install.

## Install

```bash
python3 install.py              # cross-platform (stdlib only, no bash needed)
# or: bash install.sh           # a thin shell wrapper around the same code
```

It symlinks this repository to `~/.agents/skills/attest`, then symlinks
that entry to `~/.claude/skills/`, `~/.codex/skills/` and
`~/.config/opencode/skills/`. An existing symlink with the same name is
replaced (the script says so); if the target path is a **real directory** the
script skips it and never deletes user data. Uninstall with `bash uninstall.sh`
(symlinks only).

Doing it by hand is equivalent to:

```bash
ln -s "$(pwd)" ~/.agents/skills/attest
ln -s ~/.agents/skills/attest ~/.claude/skills/attest
ln -s ~/.agents/skills/attest ~/.codex/skills/attest
ln -s ~/.agents/skills/attest ~/.config/opencode/skills/attest
```

## Packaging (`.skill`)

```bash
python3 package.py            # → dist/attest.skill
python3 package.py --check    # list what would be packaged, write nothing
```

`.skill` is the community's distribution format (a zip whose first level is the
skill directory). The packager is **self-contained** — it needs no external
tooling. Excluded: `.git` / `.github` / `dist` / `fixtures` / `evals` / `.omx` /
bytecode, and `scripts/test_*.py` (the regression suite belongs to the repo, not
to the skill's runtime).

**It refuses to package bytecode.** Compiled artifacts leak the author's
environment and give users an unreadable bundle.

`~/.agents/skills/attest` is the stable entry point.
`~/.agents/testkit/doctor.sh` runs the regression suite from there.

## The four commands

```bash
# 1. Generate a requirement test plan skeleton (scans the target module for
#    existing helpers and for the repo's own test lanes)
python3 ~/.agents/skills/attest/scripts/req_plan.py new <feature> \
  --repo <repo root> --module <subpath> [--module <subpath>...]

# 2. Generate test file skeletons from the plan (follows the repo's own
#    test conventions)
python3 ~/.agents/skills/attest/scripts/req_plan.py scaffold \
  <plan file> --repo <repo root> [--out-dir <generation root>]

# 3. Check the plan against the code as it actually is (exit code 1 on errors)
python3 ~/.agents/skills/attest/scripts/req_plan.py check \
  <plan file> --repo <repo root>

# 4. Delivery-readiness verdict for a module (one command, it measures)
python3 ~/.agents/skills/attest/scripts/readiness.py <module path> \
  --repo <repo root> \
  [--plan <requirement test plan>] [--include-itest] [--test-tenant <name or prefix>] [--json]
```

Usage details live in [SKILL.md](SKILL.md); the twelve criteria are listed below.

`req_plan.py` sub-commands and their extra flags: `new` takes `--out` (default
`docs/<feature>-test-plan.md`) and `--force`; `scaffold` takes `--out-dir`
(default: the repo root), `--framework` (`node:test` / `vitest` / `jest` /
`mocha`) and `--force`; `check` takes only `<plan>` and `--repo`.

Common switches of `readiness.py`:

| Switch | What it does |
| --- | --- |
| `--plan <file>` | Requirement test plan to check the module against (drives the 需求覆盖到位 criterion) |
| `--json` | Machine-readable output (checks + the verdict counters) |
| `--dry-run` | **Only lists** the repo scripts and commands that would be executed, then exits (executes nothing). Look before you run |
| `--self-check` | Reverse verification: inject a known defect into a **temporary copy** to prove the criteria turn red. Your repo is not touched. The copy has no `node_modules` by default, so what this proves is that the **static** criteria catch the defect — not that the measured ones do |
| `--self-check-deps` | With `--self-check`: **symlink** `node_modules` / `.venv` into the copy (no copying), so the copy can really run the tests and the measured criteria get verified too |
| `--include-itest` | Count the real-backend itest lane too (needs PostgreSQL + a vector store; slow) |
| `--test-tenant <name>` | Declare a test tenant (repeatable; exact name or prefix) — drift in a test tenant is fixture state, so it does not block |
| `--test-tenant-prefix <prefix>` | Add a test-tenant prefix (defaults recognise `itest-` `memtest-` `mock-` `eval-` …) |
| `--test-dir <path>` | Extra test directory (for repos that keep tests outside `test/` `tests/` `spec/`); repeatable |
| `--drift-pattern <regex>` | What a "drift row" looks like in the reconciliation output (default: `漂移` / `❌` / `drift` / `mismatch`) |
| `--table-header-pattern <regex>` | What a header/separator row looks like in the reconciliation output (default: `一致性` / `租户` / `tenant` / `---`) |
| `--csv-pair a.csv=b.csv` | CSV pairing, repeatable — pairs are declared, not matched pairwise |
| `--tenant-col <name>` | Which CSV column holds the tenant |
| `--min-line-coverage` / `--min-branch-coverage` | Explicit coverage thresholds. **No generic threshold by default** (no repo gate ⇒ judged `skipped`, not `fail`) |

`--database-url` sets the PostgreSQL connection string (defaults to
`POSTGRES_URL`); `--verbose` prints the raw test output.

## The twelve criteria

The tool's output copy is Chinese. The titles below are the literal strings the
tool prints — the English is a gloss, not a rename.

| # | Criterion | Key | Evidence kind |
| --- | --- | --- | --- |
| 1 | 测试全绿 (tests green) | `tests-green` | Executed — *Untested* when no test file is found or nothing ran |
| 2 | 测试卫生（不留残留、不靠 sleep）(test hygiene: no leftovers, no fixed sleeps) | `hygiene` | Static signal |
| 3 | 核心逻辑测试到位 (core logic is covered) | `coverage` | Executed |
| 4 | 导出的逻辑都有测试 (every export is referenced by a test) | `uncovered-exports` | Static signal — *Untested* when the module exports nothing |
| 5 | 表结构基本合规 (schema is basically compliant) | `schema` | Executed — *Untested* when there is no database connection |
| 6 | 数据流一致（跨存储对账）(data agrees across stores) | `data-flow` | Executed — *Untested* when the module has no reconciliation script |
| 7 | 数据准确（基线对拍）(data matches the baseline) | `data-accuracy` | Executed — *Untested* when the repo has no baseline script |
| 8 | 需求覆盖到位 (requirement is covered) | `requirement` | Static signal — *Untested* without `--plan`; *Executed* when a pin points outside the repo |
| 9 | 不过度抽象 (not over-abstracted) | `extensibility` | Static signal — *Untested* when the module exports nothing |
| 10 | 没有重复造轮子 (no reinvented wheels) | `no-wheels` | Static signal — *Untested* when the module exports nothing |
| 11 | 使用主流开源库 (uses mainstream open-source libraries) | `open-source` | Static signal |
| 12 | 断言的期望值独立 (assertion oracles are independent) | `oracle` | Static signal — *Untested* when there are no test files |

The evidence kind decides which section of the report a criterion lands in, and
it decides whether the criterion may support a positive conclusion: a static
signal never can.

## Output contract

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

This tool is **not** the judge of "can this ship". It answers a narrower
question: of the preconditions for handing this module over, which ones are
proven by measurement, which are not proven yet, and which it cannot judge at
all.

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

## Design notes

- **The threshold belongs to the repo**. If the repo has a coverage gate, the
  tool drives it and judges by its exit code. Generic thresholds are only a
  fallback when the repo has no gate, and they do not block by default.
- **Measurement first**. Every criterion either has measured evidence or states
  which precondition is missing. A static inference never passes as a
  measurement.
- **"Cannot run" is not "below the bar"**. Setup errors, missing artifacts and
  unreachable middleware are judged `skipped`, with the missing condition named.
- **Delivery is judged on the production tenant**. Drift in a test tenant is
  fixture state: reported as a warning, never blocking.
- **Field-level comparison across stores**. When the same id has different
  values for the same field in the authoritative store and in a derived store,
  the report names the id and the field.

## Language support

| | Plan generation | Coverage measurement | Export-reference criterion | Cross-store reconciliation |
| --- | --- | --- | --- | --- |
| TypeScript / JavaScript | ✅ | ✅ node / vitest / jest | ✅ | ✅ |
| Python | ✅ | ✅ pytest-cov | ✅ | ⚠️ the reconciler is only found through `package.json` |
| Rust / Go | partial (reconnaissance is reused) | — module not recognised | — | — |

On a Rust or Go module `readiness.py` **exits with code 2** and says that only
TS/JS/Python is implemented. It does not give a false verdict: "recognised but
no data" and "not recognised" are different, and only the second one is true.
A report full of ✓ where nothing was measured is worse than an error.

The plan generator handles Rust and Go exports in its reconnaissance (the
export index recognises Rust `pub fn` and Go top-level functions), and
`req_plan.py scaffold` has Rust and Go branches. It is the readiness checker
that stops at the language boundary.

## Security notes

`readiness.py` **executes scripts the target repo defines itself** (the `test`,
`coverage`, `reconcile` and `eval:baseline:check` entries in `package.json`).
Three things to know:

1. **See what it will run first**. `--dry-run` lists the candidate scripts and
   the exact commands without executing them. In normal mode the tool also
   prints those lines (the allowlisted scripts plus the exact commands) before
   it runs anything — that line is the audit trail. Script selection uses an
   **explicit allowlist** (exact script names or allowlisted prefixes), so a
   script that merely has a keyword in its name (for example `reconcile:reset`)
   is never auto-executed.
2. **Child processes get an allowlisted environment only**: `PATH`, `HOME`,
   `LANG`/`LC_*`, `TMPDIR`, `NODE_*`, `PYTHON*`, and the `POSTGRES_URL` /
   `DATABASE_URL` / `MEMORY_ITEST` / `REQ_PLAN_RECONCILE_OUT_DIR` variables the
   tool itself needs. **Nothing else is passed through** — your cloud provider
   keys and npm tokens do not reach the target repo's scripts. Note that those
   two connection strings *are* passed through: a repo's own integration lane
   has to reach its database, and that trade-off is deliberate. Drop them from
   the allowlist, or use `.pgpass`, if you do not want that. The `psql`
   password travels in the `PGPASSWORD` environment variable, never in a
   command-line argument.
3. **Trusting a repo's `test` script means handing it your environment**. The
   risk level of that layer equals the risk level of every script it runs.

`req_plan.py scaffold` writes test files into the repo. Two gates run before
anything is written: a pinned path must still be under `--out-dir` after
normalisation (a pin that escapes it is neither read nor written), and test
case names are quote-escaped per language (the plan's markdown is not turned
into executable code). When it cannot recognise the repo's test conventions it
**refuses to generate** and reports red instead of guessing.

`readiness.py` reads pins from a plan file too, and applies the same rule: a
pin that points outside the repo is neither read nor written.

## Known gaps

The pre-release issue list lives in [docs/oss-release-review.md](docs/oss-release-review.md)
and [docs/testing-philosophy-review.md](docs/testing-philosophy-review.md)
(evaluation records, read-only).

### Closed

| Item | How it was closed |
| --- | --- |
| False green with zero tests (no test file found, yet "tests green") | Judged `skipped` with "no test file found ⇒ cannot judge whether the tests are green"; the verdict no longer says "meets the delivery conditions" |
| Test discovery only scanned the module directory (repos with a `test/` layout were judged undeliverable) | Repo-level `test/` `tests/` `spec/` and colocated module tests are all counted; `--test-dir` adds more |
| Coverage include was hardcoded to `**/*.ts` | The include patterns are derived from the extensions the module actually contains |
| Dependency extraction only understood `from '…'` | Also `require('x')`, `import 'x'`, and Python's `import x` and `from x import y` |
| "Extensibility" / "no reinvented wheels" were false green on an empty set in Python | Python branch added; an empty export set is judged `skipped` (zero analysis ⇒ no conclusion) |
| The criterion named "具备扩展性" did not match what it measured | Renamed to 不过度抽象 (not over-abstracted) |
| The table criterion demanded a tenant column and a unique constraint unconditionally | Exemptions for single-tenant modules, global config tables and log/append-only tables; criterion renamed 表结构基本合规 (schema basically compliant) |
| The reconciliation layer only understood one Chinese dialect | Drift, header, tenant prefix, CSV pairing and tenant column are all configurable (`--drift-pattern` and friends); when no matching CSV pair is found it reports "cannot compare" instead of "0 pairs, no differences" |
| The generic 80/70 thresholds contradicted the docs | With no repo gate the criterion is judged `skipped` (missing criterion); use `--min-line-coverage` explicitly to get generic thresholds |
| Python repos could not read their own gate | Coverage gates and thresholds are now read from `pyproject.toml` / `Makefile` / `tox.ini` |
| The itest lane ran by default | It does **not** run by default; `--include-itest` turns it on |
| Script selection was a substring match | Now an explicit allowlist; `--dry-run` (list only) was added, and an audit line is printed before execution |
| Child processes inherited the whole `os.environ` | Now an allowlist |
| The `psql` connection string travelled in argv | Now passed as `PGPASSWORD` / `PG*` environment variables |
| `scaffold` path traversal / code injection | Two gates before writing: out-of-bounds paths are not written, test case names are quote-escaped |
| `scaffold` did not know languages | Python / Rust / Go branches added; when the convention is unknown it refuses to generate and reports red |
| The regression suite hardcoded home directory paths | Derived from `__file__` now; it skips clearly when `ai-testing-system` is missing |
| Criteria had no "not measured" state | Every criterion carries an `evidence` tag (executed / static / untested); the report splits into "measured / static signal / not measured" |
| The output contract | The verdict is now three tiers (blocking / needs a human / no signal); "meets the delivery conditions" style wording is gone; there is a fixed "not judged by this tool" list |
| Reverse verification (`--self-check`) | Injects a known defect into a **temporary copy** to prove the criteria turn red; it only touches the copy, never your repo |
| Oracle independence | Scans test files and flags patterns where the expected value comes from the system under test (both sides of an assertion call the same module, a snapshot compared against itself…) — advisory, it does not turn red |
| The release skeleton was missing (no LICENSE / README / CHANGELOG / evals / CI) | Filled in this round, see [CHANGELOG.md](CHANGELOG.md) |
| `SKILL.md`'s scope statement did not match the implementation | Downgraded to "which preconditions are proven by measurement, which are not proven yet, which this tool cannot judge" — `016abda` |
| Hardcoded `~/.agents/skills/...` paths | `SKILL.md` now resolves its own directory via `$SKILL_DIR` instead of a fixed path — `016abda` |

### Still open

The full list is in [CHANGELOG.md](CHANGELOG.md) under "已知遗留（发布时仍开放）"
(known leftovers). These are the items that are still open at release time:

| Item | Status |
| --- | --- |
| SkillSpector's `E2` (Env Variable Harvesting) still reports HIGH | Child processes must inherit environment variables (the allowlist implementation, see `child_env` in `scripts/readiness.py`). The per-rule review is in section 6 of `docs/oss-release-review.md`; CI uses `.github/skillspector-allowlist.yaml` to allowlist the **reviewed rules** explicitly, each with a reason, and a new HIGH outside the allowlist still fails the build. The allowlist is **rule-level, not finding-level**: the old baseline matched by source location, so changing one line of `scripts/` invalidated it — a permanent false red for open-source contributors. Rule-level allowlisting does not depend on location, so editing code does not cause a false red, while a **new rule** is still blocked. The cost (the same already-allowed rule appearing again is not blocked) is written in the allowlist header rather than hidden. Reproduce locally: `bash .github/check-skillspector.sh` |
| The `--exec` explicit execution switch was not adopted | The brief asked for "do not run by default, run only when an explicit switch is given". The implementation is **execute by default, print an audit line before executing** (a "repository scripts that will be executed" line at the top of the report, plus `--dry-run` to look without doing). Reason: the tool's purpose is measurement (a delivery verdict that does not run the tests is meaningless), and the execution surface is already narrowed to a script allowlist plus an environment-variable allowlist; adding a mandatory switch means one more argument on every call for little gain. Use `--dry-run` to see the execution list |
| Python has no real-backend (itest) lane | The TS side has `--include-itest`; Python has no equivalent mechanism. P1, not fixed |
| Windows not verified end to end | The skill's **runtime** is pure Python and its **install** goes through `install.py` (cross-platform), so neither depends on the platform. But the repo's own dev scripts (`fixtures/run_fixtures.sh`, `.github/*.sh`) need bash — on Windows that means WSL or Git Bash |
| Trigger accuracy | Measured: precision 1.0 (not one of the 10 near-miss queries triggered it), recall 0.5, F1 0.667. Data and reading in [evals/README.md](evals/README.md). **The number measures the competition on this machine (60+ installed skills)** — an OSS user has a different skill set, so it does not transfer directly |
| The self-check copy does not include `node_modules` by default | Most repos therefore cannot run their tests in the copy, so `--self-check` proves that "the static criteria catch things", not that "the whole criterion set (including the measured ones) catches things". The report says so explicitly (`copy_runnable: false`). Add `--self-check-deps` to symlink the dependencies into the copy (no copying) and the measured criteria get verified too |

One more item that is not in the CHANGELOG's "known leftovers" list: the body of
`SKILL.md` is still Chinese only. This README has an English version
(`README.en.md`); `SKILL.md` does not.

## Regression suite

Run every test script one by one (the file names have gaps, so do not assume
they are ordered or contiguous):

```bash
for t in scripts/test_*.py; do python3 "$t" || echo "FAIL $t"; done
```

21 test files live in `scripts/`.

## Golden fixtures

5 self-contained mini repositories, each with a **pinned expected verdict** —
they are the reproducible evidence for "does this tool go falsely green":

```bash
bash fixtures/run_fixtures.sh
```

| Fixture | Expected verdict |
| --- | --- |
| `ts-colocated` | Exit code 0; the tests are found; the exported symbol under test does not count as "zero references" |
| `python-repo` | Exit code 0; coverage is not skipped; the dependency list is not empty; "not over-abstracted" is not a false green on an empty set |
| `rust-repo` | Exit code 2, saying that only "TS/JS/Python" is implemented — not a screen full of ✓ |
| `english-reconciler` | Exit code 1, exposing drift — it does **not** report "no drift in the production tenant" |
| `no-tests` | The conclusion is **not** "meets the delivery conditions"; "tests green" is not ✓ |

Exit codes: 0 = every fixture hit its expectation / 1 = a fixture did not match
(the output shows expectation vs actual) / 3 = a fixture was not judged because
the environment was missing (for example `pytest-cov` is not installed).
Each fixture directory has a `README.md` explaining **why it exists** (which
review objection it blocks).

`python-repo` needs `pytest` and `pytest-cov`; the other fixtures need `node`.
CI installs them and runs all 5.

## evals

`evals/evals.json` — 6 evals, each with **machine-checkable** assertions
(expected exit code / strings that must appear / strings that must not appear).
They cover: writing a plan before starting work, re-checking an existing plan,
whether a module can be handed over, cross-store data consistency, "claims
coverage but the file does not exist", and `scaffold` writing outside its
output directory.

Each eval declares which golden fixture is the repo under test, what command to
run, and whether the assertion looks at the command output or at a produced
file. **How to run**:

```bash
python3 evals/run_evals.py            # all of them
python3 evals/run_evals.py --only 3   # just one
python3 evals/run_evals.py --json     # machine-readable
```

CI runs them. Exit codes: 0 = all passed / 1 = an assertion failed /
2 = there was no eval to run (for example `--only` was given an id that does
not exist).

## License

MIT
