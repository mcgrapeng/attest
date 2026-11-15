<div align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/mcgrapeng/attest/main/skills/attest/assets/attest-wordmark-dark.png">
    <img alt="attest" src="https://raw.githubusercontent.com/mcgrapeng/attest/main/skills/attest/assets/attest-wordmark.png" height="44">
  </picture>
  <p><strong>Turns "is this module ready to ship?" from a coverage percentage into an auditable evidence list.</strong></p>
</div>

<div align="center">

[![CI](https://github.com/mcgrapeng/attest/actions/workflows/ci.yml/badge.svg)](https://github.com/mcgrapeng/attest/actions/workflows/ci.yml)
[![skills.sh](https://skills.sh/b/mcgrapeng/attest)](https://skills.sh/mcgrapeng/attest)
[![Python](https://img.shields.io/badge/python-3.11%2B-0F766E?style=flat-square)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-0F766E?style=flat-square)](LICENSE)

[中文版](README.zh-CN.md) · [English](README.md)

</div>

```bash
npx skills add mcgrapeng/attest
```


## What it does

A coverage number tells you the code ran. It does not tell you that an
assertion would fail, that the plan's pins point at files that exist, or that
two stores have not drifted apart. `attest` answers the delivery question with
**evidence**, sorted into four kinds:

| Evidence kind | What it means |
| --- | --- |
| **Measured** | Something ran — the command, its exit code and its output are in the report |
| **Static signal** | Read out of the source, not run. **Can never support a pass on its own** |
| **Not measured** | Could not be measured, and the report names the precondition that was missing |
| **Not judged** | Out of scope by design: requirement understanding, product behaviour, performance, security, availability, operational readiness |

The tool prints these four kinds in Chinese (已实测 / 静态信号 / 未实测 / 本工具不判定); the report sample below is its real output.

**Supported today:** TypeScript / JavaScript and Python. Rust and Go are recognised
by the plan generator, but the readiness checker exits with code 2 and says so
rather than reporting on a module it cannot read.

The measurement is real, not inferred. `attest` runs the target repo's **own**
test command, its **own** coverage gate, its **own** reconciliation script, and
queries the PostgreSQL catalog for the actual schema. Where it cannot measure
something, it says so instead of passing it.

**What it is not.** It is not a "can we ship" referee, and it does not replace
the tools you already have:

| You already know | Where attest differs |
| --- | --- |
| A coverage badge (Codecov, SonarCloud) | attest **drives your repo's own coverage gate** and reads the number your CI already produces — it does not report coverage itself |
| An AI-written test plan | attest pins every invariant to a test case **that really exists in your repo**, and refuses to generate a plan when it cannot recognise your conventions |
| "I asked a chatbot whether this looks done" | attest **runs** the checks and prints the commands it ran — every verdict is re-runnable by you |
| A checklist in a PR comment | attest's report is reproducible: one command, same input, same output |

It reports evidence; the decision stays with whoever owns the delivery. The
name avoids *verify*, *certify* and *sign-off* on purpose — those promise an
approval this tool refuses to fake.

## Install

```bash
npx skills add mcgrapeng/attest
```

Three ways in, pick the one that matches you:

| You want to… | Do this |
| --- | --- |
| Use it inside your agent | `npx skills add mcgrapeng/attest` — the skills CLI copies the skill into `.agents/skills/attest/` and detects which agent you run (Claude Code, Codex, OpenCode, Cursor, Windsurf, Zed, Warp and 20 more). No clone, no Python |
| Keep the repository | `git clone … && python3 install.py` — symlinks the skill into `~/.agents/skills/attest` and the agent directories |
| Just run it once | `python3 ~/.agents/skills/attest/scripts/readiness.py <module path> --repo <repo root>` |

From a clone (this repository is the source of truth):

```bash
git clone https://github.com/mcgrapeng/attest.git && cd attest
python3 install.py
```

The installer symlinks the skill directory (`skills/attest/`) to
`~/.agents/skills/attest`, then symlinks that entry into `~/.claude/skills/`,
`~/.codex/skills/` and `~/.config/opencode/skills/`. An existing symlink with
the same name is replaced (the script says so); if the target path is a **real
directory** the script skips it and never deletes user data. Uninstall with
`python3 install.py --uninstall` — symlinks only. On Windows `os.symlink` may
need administrator rights or developer mode.

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
      - uses: mcgrapeng/attest@v0.1.3
        with:
          module: src/auth
```

| Input | Default | What it does |
| --- | --- | --- |
| `module` | — | **Required.** The module to check, relative to the repository root (`src/auth`) |
| `repo-path` | `.` | The repository root, when it is not the workspace root |
| `plan` | — | A requirement test plan to check the module against |
| `strict` | `false` | Fail the job when attest reports blocking items |
| `comment` | `true` | Post the report as a pull request comment |

It publishes the evidence list to the job summary and the PR comment, and
**does not block the merge by default** (`strict: false`) — attest reports
evidence, the repository decides. Install the repository's own dependencies
before the action runs: attest runs the repository's own test command.

## The report

Real output from attest's own fixture (`fixtures/ts-colocated`):

```
将执行的仓库脚本：测试车道（逐个测试文件跑 $ node --test <测试文件>）

交付就绪度分析 — src
仓库：<repo root>
源码 1 文件（+0 个测试支撑） / 单测 1 个 / itest 0 个
```

```
结论：0 项阻塞 · 1 项待人工确认 · 7 项无信号 · 1 项实测通过（另有 6 条静态信号）
退出码 1 = 有阻塞项，或关键判据（测试是否绿）没测到
```

<div align="center">
  <img alt="The full report: four sections (measured, static signals, not measured, not judged) and a three-tier verdict — rendered from a real run of attest's own fixture"
       src="https://raw.githubusercontent.com/mcgrapeng/attest/main/skills/attest/assets/hero.svg"
       width="574">
</div>

Look at the last line: **7 items with no signal**, not "7 passed". A criterion
that was not measured is reported as *not measured*, and the exit code is `1`
because a key criterion could not be measured at all.

`--json` gives the same thing machine-readably (checks + verdict counters).
`--dry-run` lists the repo scripts that *would* run, and executes nothing.

## What it measures

14 criteria. The full table, the four evidence kinds and the language matrix
are in **[docs/criteria.md](docs/criteria.md)**. In one paragraph: the repo's
own tests, its own coverage gate, whether its tests can actually fail, the PG
schema, cross-store drift, baseline accuracy, plus static signals about
hygiene, unused exports, over-abstraction, reinvented wheels, dependencies and
assertion independence.

## Status

**Actively developed. Released as `v0.1.3`.** The criteria and the skill
contract may still change — but they will change with a changelog entry, not
silently. MIT licensed.

Every number in this repository comes from a command you can re-run: **27 / 27** regression suites, **5 / 5** golden fixtures, **6 / 6** behaviour evals, **0** security findings outside a rule-level allowlist.

Known gaps, in full: [CHANGELOG.md](CHANGELOG.md). The three that
matter most:

- **Trigger recall is 0.5** on the machine where it was measured — that number
  is about the competition on that machine, not about the description.
- **Windows is not verified end to end.** The runtime and the installer are
  cross-platform; the repo's own dev scripts need bash.
- **The self-check copy has no `node_modules`** by default; `--self-check-deps`
  symlinks them in.

attest has also been run against five public repositories and **published the
verdicts, including the ones that made the tool look wrong**:
[docs/self-test-report.md](docs/self-test-report.md).

## Contributing

[CONTRIBUTING.md](CONTRIBUTING.md) has the setup, the gates a pull request has
to pass, and where outside help is worth the most. Security reports go to
[SECURITY.md](SECURITY.md), not to the issue tracker; everyone participating
follows the [Code of Conduct](CODE_OF_CONDUCT.md).

## License

MIT.

The last word belongs to the evidence: the verdicts attest produced on five
public repositories — **including the ones that made the tool look wrong** —
are published in [docs/self-test-report.md](docs/self-test-report.md).
