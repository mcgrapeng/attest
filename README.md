<div align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/mcgrapeng/attest/main/skills/attest/assets/attest-wordmark-dark.png">
    <img alt="attest" src="https://raw.githubusercontent.com/mcgrapeng/attest/main/skills/attest/assets/attest-wordmark.png" height="44">
  </picture>
  <p><strong>Turns "is this module ready to ship?" from a coverage percentage into an auditable evidence list.</strong></p>
  <p><sub>18 criteria · four kinds of evidence · one command</sub></p>
</div>

<div align="center">

[![CI](https://github.com/mcgrapeng/attest/actions/workflows/ci.yml/badge.svg)](https://github.com/mcgrapeng/attest/actions/workflows/ci.yml)
[![skills.sh](https://skills.sh/b/mcgrapeng/attest)](https://skills.sh/b/mcgrapeng/attest)
[![Python](https://img.shields.io/badge/python-3.11%2B-0F766E?style=flat-square)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-0F766E?style=flat-square)](LICENSE)

<sub><b>English</b> · <a href="README.zh-CN.md">中文</a> — two languages, one page: same sections, same order, same numbers.</sub>

</div>

```bash
npx skills add mcgrapeng/attest
```

<div align="center">
  <img alt="attest's two lines: line A writes the requirement test plan (req_plan.py new → fill the invariants and the decision-point ownership → scaffold + check), line B verifies the delivery (readiness.py runs the target repository's own test lane, coverage gate and reconciliation script, then reports four kinds of evidence)"
       src="https://raw.githubusercontent.com/mcgrapeng/attest/main/skills/attest/assets/attest-flow.en.svg"
       width="820">
</div>

## Your five questions

Most people meet attest with a broad question like "can this module ship?". The
useful question is narrower, and it comes in five parts: **which tests should
this requirement have, do those tests really exist, is the suite complete
enough, can those tests actually catch a defect, and can you run them?** Those
five are what attest is built around, and it answers each one with evidence
rather than with a verdict.

| Your question | What attest does | Where you see it |
| --- | --- | --- |
| **Which tests should this requirement have?** | It reverse-derives the **decision points** in the module — branches, error paths, resource limits, state changes — and reconciles them, in both directions, with the **invariants** the plan lists. The four high-risk kinds (error path, resource, drop, state) each need an owning invariant or a written exemption; the rest are counted. | `req_plan.py upgrade` / `assign`; the *plan matches the machine-derived decision points*, *the decision points are covered* and *every invariant reaches a decision point* criteria |
| **Do those tests really exist?** | Every pin in the plan must point at a file in your repository and at a test case that exists in it. `check` verifies each one; a missing file, or a case name that is not there, is red. | `req_plan.py check`; the *requirement is covered* criterion |
| **Is the suite complete enough?** | Two directions: every high-risk decision point needs an owning invariant or a written exemption, and every invariant needs a decision point that claims it. On top of that it drives your repository's own coverage gate. | the *decision points are covered*, *every invariant reaches a decision point* and *core logic is covered* criteria |
| **Can those tests actually catch a defect?** | It breaks your source on purpose and runs the pinned test: red means the suite catches that class of defect, still green means it does not. A pin also has to prove it kills the defect **at its own location**. | `--mutation-check` and `--discriminating`; the *the tests can find defects*, *the pin has discriminating power* and *assertion oracles are independent* criteria |
| **Can you run them and cover the requirement?** | It runs your repository's own test lanes and prints, for each of the 18 criteria, either the evidence — the command, its exit code, its output — or the precondition that was missing. | `readiness.py` |

Two halves of the first question, and only one of them is attest's. The **code**
side is derived: attest reads your source and finds the decision points in it. The
**requirement** side is written into the plan by whoever read the requirement —
attest then holds the two against each other and names what is missing. The
derivation is only as good as its backend: with a TypeScript AST backend it is
L3, without one it falls back to lexical heuristics that may over-report, and in
that case the three decision-point criteria warn instead of failing.

The mutation check sets no score. How many mutants have to die is your
project's threshold, not attest's call — and a sample of dead mutants is not the
same as a strong suite, which the report says rather than hides.

**What this does not settle.** A pin proves that a behaviour contract hangs on a
test case that exists. It does not prove that the contract is the right reading
of the requirement. Requirement understanding, product behaviour, performance,
security, availability, operational readiness, concurrency correctness and
behaviour at scale are out of scope by design: the report lists all eight every
time and asks who owns each one. A static signal never carries a pass on its own,
and anything that could not be measured is reported as *not measured*, with the
missing precondition named.

**So what is attest?** The auditor and the designer of a test suite. It derives
what the code says should be tested, holds the plan and the code against each
other, proves that the pinned tests can fail on a real defect, then runs
everything and reports what is proven, what is not, and what it refuses to
judge. The delivery decision stays with whoever owns it — the name avoids
*verify*, *certify* and *sign-off* on purpose.

## The four kinds of evidence

A coverage number tells you the code ran. It does not tell you that an assertion
would fail, that the plan's pins point at files that exist, or that two stores
have not drifted apart. `attest` answers the delivery question with **evidence**,
sorted into four kinds:

<div align="center">
  <img alt="The four kinds of evidence attest reports: measured, static signal, not measured, not judged"
       src="https://raw.githubusercontent.com/mcgrapeng/attest/main/skills/attest/assets/evidence-kinds.en.svg"
       width="794">
</div>

| Evidence kind | What it means |
| --- | --- |
| **Measured** | Something ran: the command, its exit code and its output are in the report |
| **Static signal** | Read out of the source, not run. **Can never support a pass on its own** |
| **Not measured** | Could not be measured, and the report names the precondition that was missing |
| **Not judged** | Out of scope by design: requirement understanding, product behaviour, performance, security, availability, operational readiness |

attest prints these four kinds in Chinese. The English↔Chinese name mapping is in
[references/criteria.md](skills/attest/references/criteria.md); this page uses
the English names throughout.

The measurement is real, not inferred. `attest` runs the target repo's **own**
test command, its **own** coverage gate, its **own** reconciliation script, and
queries the PostgreSQL catalog for the actual schema. Where it cannot measure
something, it says so instead of passing it.

**Supported today:** the **readiness checker** measures TypeScript / JavaScript and
Python. (Python's real-backend itest lane can be found and run with
`--include-itest`, but pytest's output is not a shape attest can parse, so that
lane stays *not measured* instead of becoming a verdict.) The **plan generator**
also reads Rust and Go in its reconnaissance and scaffolds for them (`.rs` → cargo
test, `.go` → go test), but the readiness checker stops at the language boundary:
on a Rust or Go module it exits with code 2 and says that only TS/JS/Python is
implemented, rather than reporting on a module it cannot see.

**What it is not.** It is not a "can we ship" referee, and it does not replace
the tools you already have:

| You already know | Where attest differs |
| --- | --- |
| A coverage badge (Codecov, SonarCloud) | attest **drives your repo's own coverage gate** and reports the number your CI already produces; only when a repository has no gate of its own does attest measure coverage itself, as a fallback |
| An AI-written test plan | attest pins every invariant to a test case **that really exists in your repo**, and when it cannot infer your test framework it refuses to scaffold and tells you (`--framework` is the way out) |
| "I asked a chatbot whether this looks done" | attest **runs** the checks and prints the commands it ran: every verdict is re-runnable by you |
| A checklist in a PR comment | attest's report is reproducible: one command, same input, same output |

## The two commands

**Line A writes the plan** — `skills/attest/scripts/req_plan.py`:

| Step | Command | What it does |
| --- | --- | --- |
| 1 | `new <feature> --repo . --module src/x` | Scans the module: the helpers it already has, the stores it touches, your test lanes, and the decision points in the code |
| 2 | *fill the plan* | Write the invariants and the coverage matrix; pin each one to a real test case |
| 3 | `upgrade <plan> --repo .` | Re-granularises the plan: one row per decision point, with three columns to fill — which invariant owns it, which case proves it, or why it needs no test |
| 4 | `assign <plan> --repo . --limit 30` | Hands out the decision points still to be answered, as a JSONL worklist (that is what it does by default); `--apply answers.jsonl` checks the answers and writes them back |
| 5 | `scaffold <plan> --repo .` | Writes the test files the plan asks for, in your conventions; the assertions are left as TODO for you to fill |
| 6 | `check <plan> --repo .` | Verifies every pin: does the file exist, does the case exist, is the invariant covered |
| 7 | `reconcile <plan> --repo .` | Generates the cross-store reconciliation script from the plan's data-consistency section — the table and collection names come from the plan, and the slots that are still empty make the script refuse to run, so it cannot silently pass |
| 8 | `baseline <plan> --repo .` | Generates the baseline-comparison script from the same section — the same contract: empty slots refuse to run, and the plan's original text is kept for the agent to fill from |

Step 4 is what makes the three columns fillable by an agent instead of by hand: in
its default mode `assign` hands out each decision point's code excerpt, the
candidate test files and the plan's own invariants; `--apply` takes the answers
back and writes them in. Anything that does not check out — an invariant that is
not in the list, a waiver that says nothing, a pin to a case that does not exist
— is rejected and left for a human.

**Line B verifies the delivery** — `skills/attest/scripts/readiness.py`:
`readiness.py <module> --repo .` runs your repository's own test lane, coverage
gate, reconciliation script and schema queries, then prints the four kinds of
evidence and one exit code. `--plan <plan>` adds the decision-point criteria;
without it those three are reported as *not measured*, not as passed.

**Nobody has to be in the loop.** An agent that reads the requirement can drive
both lines end to end: derive the invariants from the requirement, fill the plan
in batches with `assign`, write the assertions that `scaffold` leaves as TODO,
confirm the plan (its confirmation section accepts an agent, and the report says who
confirmed it), claim the eight items this tool does not judge, and run the
readiness check. The report ends with a **delivery precondition** summary — the
evidence is green, the plan is confirmed, and nothing is unclaimed — which is
what "may go into the delivery pipeline" means. It is a summary, not another
criterion: the criteria count and the exit code do not change.

## Install

```bash
npx skills add mcgrapeng/attest
```

Three ways in, pick the one that matches you:

| You want to… | Do this |
| --- | --- |
| Use it inside your agent | `npx skills add mcgrapeng/attest`: the skills CLI copies the skill into `.agents/skills/attest/` and detects which agent you run. No clone, no Python |
| Keep the repository | `git clone … && python3 install.py`: symlinks the skill into `~/.agents/skills/attest` and the agent directories |
| Just run it once | `python3 ~/.agents/skills/attest/scripts/readiness.py <module path> --repo <repo root>` |

Those two routes leave the skill in different places: the skills CLI copies it
into the project (`.agents/skills/attest/`), `install.py` symlinks it into your
home (`~/.agents/skills/attest/`). That command is the home path; from the skills
CLI, use `.agents/skills/attest/scripts/readiness.py` instead.

From a clone (this repository is the source of truth):

```bash
git clone https://github.com/mcgrapeng/attest.git && cd attest
python3 install.py
```

The installer symlinks the skill directory (`skills/attest/`) to
`~/.agents/skills/attest`, then symlinks that entry into `~/.claude/skills/`,
`~/.codex/skills/` and `~/.config/opencode/skills/`. An existing symlink with the
same name is replaced (the script says so); if the target path is a **real
directory** the script skips it and never deletes user data. Uninstall with
`python3 install.py --uninstall`: symlinks only. On Windows `os.symlink` may
need administrator rights or developer mode.

Python 3.11+ and nothing else: the tool has **no third-party dependencies**,
and it drives your repository's own toolchain (node, pytest, …) rather than
installing one of its own.

Then, against any repository:

```bash
python3 ~/.agents/skills/attest/scripts/readiness.py <module path> --repo <repo root>
```

### In CI

```yaml
name: attest
on: [pull_request]

jobs:
  attest:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: '22'
      - run: npm ci
      - uses: mcgrapeng/attest@v0.1.31
        with:
          module: src/auth
```

| Input | Default | What it does |
| --- | --- | --- |
| `module` | — | **Required.** The module to check, relative to the repository root (`src/auth`) |
| `repo-path` | `.` | The repository root, when it is not the workspace root |
| `plan` | — | A requirement test plan to check the module against |
| `strict` | `false` | Fail the job when attest exits 1 (blocking items, or a key criterion it could not measure) |
| `comment` | `true` | Post the report as a pull request comment |

It publishes the evidence list to the job summary and the PR comment, and
**does not block the merge by default** (`strict: false`): attest reports
evidence, the repository decides. The job stays green even when attest exits 1
(blocking items or an unmeasurable key criterion); with `strict: true` that
exit code fails the job instead. Exit code 2 is the exception: attest could not
read the module, and the action fails the job whatever `strict` is set to.
Install the repository's own dependencies before the action runs: attest runs
the repository's own test command.

## Command-line flags

The switches you are most likely to need:

| Flag | What it does |
| --- | --- |
| `--plan <file>` | The requirement test plan to compare against. **Without it the three decision-point criteria are skipped**, not passed |
| `--database-url <url>` | The PostgreSQL to read the schema from, when it is not the default |
| `--test-tenant <name>` | A tenant that is **not** production data. Repeat it for every test tenant, or the reconciliation counts them as drift |
| `--include-itest` | Include the repository's real-backend itest lane in the measurement (it needs PostgreSQL and the vector store, and it is slow) |
| `--json` | The same report, machine-readable: every check plus the verdict counters |
| `--dry-run` | List the repository scripts that *would* run, and run nothing |
| `--min-line-coverage`, `--min-branch-coverage` | Fallback coverage bars, used only when the repository has no coverage gate of its own |
| `--mutation-check` | Break the source and check that the tests catch it (copies your code first, so your tree is untouched) |
| `--self-check` | Reverse verification: inject a known defect into a copy and prove the criteria turn red (your repository is not touched) |

Three more switches belong to the decision-point engine (the fourth, `--plan`, is
above). None of them can turn a red into a green: a missing capability degrades
to a warning or to *not measured*.

| Flag | What it does |
| --- | --- |
| `--discriminating {auto,off,quick,full}` | Whether to break the source at each pin and check that the pinned test case catches it. `auto` runs it only for decision-point-grained plans, `off` skips it, `quick`/`full` widen the sample (expensive) |
| `--analysis-backend {auto,b1,b2,l2}` | How the decision points are derived. `auto` uses the TypeScript AST when it is available; `l2` forces the lexical fallback, which **downgrades the three criteria to warnings** — a missing capability never reports green |
| `--lenient` | Warn only: every red from the three decision-point criteria becomes a warning |

## The report

A real run against attest's own fixture (`fixtures/ts-colocated`). attest prints
in Chinese: the criterion names, the four evidence sections and the verdict line
are what you read in your terminal. The English↔Chinese name mapping is in
[references/criteria.md](skills/attest/references/criteria.md). This page states
the same run in English:

| Part of the report | What it says |
| --- | --- |
| Repo header | Which repository, which module, how many source files and test files it found |
| **Measured** | Criteria attest actually ran: the command, its exit code and its output are in the report |
| **Static signal** | Criteria read out of the source; they can never carry a pass on their own |
| **Not measured** | Criteria it could not measure, each with the missing precondition named |
| **Not judged** | The eight things this tool refuses to judge, by design |
| Verdict | `0 blocking · 1 awaiting human review · 11 with no signal · 1 measured pass (plus 6 static signals)` |

Look at the verdict line: **11 items with no signal**, not "11 passed". A criterion
that was not measured is reported as *not measured*. The rule behind that line is
not this run's verdict: **A blocking item, or a key criterion that could not
be measured at all** is what makes attest exit `1`. This run had neither, so it
ended with exit code `0`. The counters in that line overlap: the item awaiting
human review is also one of the static signals, so they do not add up to the
number of criteria. Without that overlap it is 1 measured pass + 11 with no
signal + 6 static signals.

`--json` gives the same thing machine-readably (checks + verdict counters).
`--dry-run` lists the repo scripts that *would* run, and executes nothing.

With `--plan`, the report grows a **decision-point coverage** section — the
decision points reverse-derived from the source, counted against the plan's own
list. From the same run: 2 decision points, 1 claimed, 0 waived, 1 gap; the
high-risk gaps are drop 0, error-path 0, resource 1, state 0; the derivation
level is L3 (backend B2, from global-npm); the discrimination self-check is
1/1 (100%), with 1 killed.

That gap line is the point: a high-risk decision point that **no invariant
owns and no exemption explains**.

## What it measures

The tool prints all 18 criteria in Chinese; the names below are the English
names for the same 18, one-to-one (the mapping is in
[references/criteria.md](skills/attest/references/criteria.md)). **Measured**
means attest ran something; **static signal** means it read the source, and a
static signal can never carry a pass on its own.

The last three criteria are one mechanism seen from three sides: **reconciliation
in both directions**, between the plan and the code, and between the decision
points and the invariants. Each side of each reconciliation is written by one
party and derived by the other, so a gap on either side is named in the report.

<div align="center">
  <img alt="attest's two-way reconciliation: the plan's decision-point list against the decision points derived from the code, high-risk decision points against the invariants that claim them, and invariants against the decision points that claim them — each side marked human-written or machine-derived"
       src="https://raw.githubusercontent.com/mcgrapeng/attest/main/skills/attest/assets/reconcile-2way.en.svg"
       width="880">
</div>

| Criterion | How attest measures it |
| --- | --- |
| tests green | **Measured.** Runs the module's tests with the repo's own command; only assertion failures count — a setup error means the environment was not ready, not that the code is wrong |
| core logic is covered | **Measured.** Drives the repo's own coverage gate; `--min-line-coverage` is only for repositories that have no gate |
| the tests can find defects | **Measured.** Breaks the source on purpose: red means the suite caught it, still green means this suite cannot find this class of defect (`--mutation-check`) |
| schema is basically compliant | **Measured.** Queries the PostgreSQL catalog: primary key, unique constraint, tenant column, RLS+FORCE, timestamps with a timezone. Single-tenant modules, global config tables and append-only tables are exempt from the tenant column and the unique constraint |
| data agrees across stores | **Measured.** Runs the module's own reconciliation script and judges the drift per tenant kind; the field-level comparison is part of this one |
| data matches the baseline | **Measured.** Runs the repo's own baseline script and parses the metric differences |
| the pin has discriminating power | **Measured.** Breaks the source at the pinned point and runs the pinned case: red means the pin kills this class of defect, still green means it does not |
| requirement is covered | **Static signal.** Reads the plan and the test files: every invariant's pin points at a case that really exists |
| vector collection attributes agree | **Static signal.** Whether one collection's dimension, metric and partition key agree across its call sites |
| test hygiene | **Static signal.** Fixed waits (a source of flakiness) and database writes without cleanup (a source of leftovers). A test file that really does not write to a database declares it — `/* attest:no-db-writes reason="…" */` — and the reason shows up in the report |
| every export is referenced by a test | **Static signal.** Exported functions and classes with zero references in the tests are named |
| not over-abstracted | **Static signal.** Exported symbols with almost no call sites anywhere in the repository |
| no reinvented wheels | **Static signal.** Same-named implementations elsewhere in the repository |
| uses mainstream open-source libraries | **Static signal.** The module's real dependency list, with path aliases and the standard library excluded |
| assertion oracles are independent | **Static signal.** Assertions whose expected value comes from the system under test |
| the plan matches the machine-derived decision points | **Static signal.** The decision points are reverse-derived from the source and compared with the plan in both directions |
| the decision points are covered | **Static signal.** Every high-risk decision point has an owning invariant or a written exemption, and the pin really exists |
| every invariant reaches a decision point | **Static signal.** The other direction of the same reconciliation: every invariant has at least one decision point claiming it — one with none is only pinned by a single test case. A composite invariant that has no claiming decision point by nature can be exempted in the plan with `<!-- attest:invariant-exempt I3 reason="…" -->`; the exemption needs a real reason and is reported, never silent |

## Where the line is

The boundaries are deliberate, and most of them are printed in the report rather
than buried in a document:

- **Eight things it does not judge**: requirement understanding, product
  behaviour, performance, security, availability, operational readiness,
  concurrency correctness, behaviour at scale. The report lists all eight every
  time. The plan asks who owns each one, and the delivery preconditions section
  names the ones nobody has claimed.
- **It does not write assertions.** `scaffold` writes the structure and the case
  names in your conventions; the expected values belong to whoever read the
  requirement.
- **It generates reconciliation and baseline scripts as templates.** The table and
  collection names come from the plan, but the comparison logic is yours to wire.
  A template with unfilled slots refuses to run rather than reporting "no drift".
- **The decision-point criteria judge hard only on a decision-point-granularity
  plan** (`req_plan.py upgrade`) with an L3 derivation. On an older plan they
  warn instead of failing, and say so.
- **The mutation-based checks only run when you ask.** `--mutation-check` and
  `--discriminating auto|quick|full` copy your code and break it on purpose, which
  is worth the time only when you want the proof.
- **It reads TS/JS and Python.** For Rust and Go it stops at the plan.
- **Exit code 0 means "no blocking item and the key criterion was measured".** It
  is not a delivery verdict; that is what the name means.

## Status

**Actively developed. Released as `v0.1.31`.** The criteria and the skill
contract may still change: but they will change with a changelog entry, not
silently. MIT licensed.

Every number in this repository comes from a command you can re-run: **39 / 39** regression suites, **10 / 10** golden fixtures, **6 / 6** behaviour evals, **0** security findings outside a rule-level allowlist.

Known gaps, in full: [CHANGELOG.md](CHANGELOG.md). The three that
matter most:

- **Trigger recall is 0.5** on the machine where it was measured: that number
  is about the competition on that machine, not about the description.
- **Windows is not verified end to end.** The runtime and the installer are
  cross-platform; the repo's own dev scripts need bash.
- **The self-check copy has no `node_modules`** by default; `--self-check-deps`
  symlinks them in.

attest has also been run against five public repositories — **including verdicts
that made the tool look wrong**. That report left the repository together with
the other internal documents, and is still in the git history.

## FAQ

**What do I need to run it?**
Python 3.11+ and nothing else. attest has no third-party dependencies: it drives
your repository's own toolchain (node, pytest, …) instead of installing one of
its own. The target repository needs its own dependencies installed, because
attest runs that repository's test command.

**My test script is compound (`xo && c8 node --test … && tsc`). Does attest cope?**
Yes. It splits the script and runs the component that actually invokes a test
runner, with your file appended there. If it cannot tell which component runs
the tests, it says so instead of guessing.

**What does exit code 1 mean in my CI?**
Either a measured criterion is below the bar, or a key criterion could not be
measured at all. The GitHub Action does not fail the job on it by default
(`strict: false`); with `strict: true` it does. In `--self-check` mode a third
case exists: the reverse verification failing is about the tool, not your
module, and attest says so in its own words.

**Does attest write to my repository?**
It reads your repository and runs your scripts. The plan commands write **the plan
file** — `new` creates it, `upgrade` and `assign --apply` rewrite it — and
`req_plan.py scaffold` writes test files under `--out-dir` (which defaults to the
repository root). `--self-check` and `--mutation-check` copy your code into a
temporary directory first, so they leave your working tree alone.

**Does it replace my coverage dashboard?**
No, it reads it. attest drives your repository's own coverage gate and reports
the number your CI already produces; with no gate of its own, it measures
coverage itself as a fallback.

**Can it fill the plan for me?**
The three columns of a decision-point plan are a judgment call, and attest does
not make it for you. But `req_plan.py assign` makes it fillable in batches: it
hands out the open decision points as a JSONL worklist, and writes the answers
back after checking each one — an invariant that is not in the plan, a waiver
that says nothing, or a pin to a case that does not exist is rejected and left
for a human.

**Is this maintained?**
It is released, versioned, and the changes to it are published:
[CHANGELOG.md](CHANGELOG.md).

## Contributing

[CONTRIBUTING.md](CONTRIBUTING.md) has the setup, the gates a pull request has
to pass, and where outside help is worth the most. Security reports go to
[SECURITY.md](SECURITY.md), not to the issue tracker; everyone participating
follows the [Code of Conduct](CODE_OF_CONDUCT.md).

## License

MIT.

---

<div align="center">
<sub>Read this page in another language: <a href="README.zh-CN.md">中文</a> ·
The two pages are one page: same sections, same order, same numbers.</sub>
</div>
