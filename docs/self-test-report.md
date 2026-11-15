# Self-test report — attest on five public repositories

**Date:** 2026-11-15 · **attest:** v0.1.0 (`0be80c7`) · **Machine:** macOS, Python 3.11+

This is the tool's own test of itself. Five public repositories, one command
each, verdicts printed verbatim. Where the tool got things wrong, that is
written down here as well, with the issue that tracks the fix.

Reproduce it:

```bash
bash tools/run_self_test.sh /tmp/attest-self-test
```

The script shallow-clones each repository, pins the commit, installs that
repository's own dependencies, runs the readiness criteria against the
repository's module path, and keeps the tool's output unmodified.

## The runs

| Repository | Language | Module | ★ | Commit | Exit |
| --- | --- | --- | --- | --- | --- |
| chalk/chalk | JS | `source` | 23,340 | `7211001` | 1 |
| expressjs/express | JS | `lib` | 69,692 | `43fca52` | 1 |
| sindresorhus/type-fest | TS | `source` | 17,505 | `47ef070` | 1 |
| pallets/click | Python | `src/click` | 17,825 | `e38188e` | 1 |
| pallets/itsdangerous | Python | `src/itsdangerous` | 3,143 | `672971d` | 1 |
| chalk/chalk — negative control | JS | `.` | 23,340 | `7211001` | 1 |

Exit code 1 means "there are blocking items, or a key criterion could not be
measured" — not "this project is broken".

## The one run where the tool had something to measure

`pallets/click`, module `src/click`, dependencies installed in a venv. Verbatim:

```
将执行的仓库脚本：测试车道（逐个测试文件跑 $ pytest <测试文件>）

交付就绪度分析 — src/click
仓库：/private/tmp/attest-self-test-work-AF50Dp/click
源码 17 文件（+0 个测试支撑） / 单测 43 个 / itest 0 个

── 已实测 ───────────────────────────────────────────
  ✓ 测试全绿 — 没有失败用例
  — 核心逻辑测试到位 — 仓库没有覆盖率闸门，缺少判据（pytest-cov 实测平均行覆盖 84%）；通用阈值默认不阻塞
  ✗ 导出的逻辑都有测试 — 39/103 个公开 def/class 在测试里零引用
  ✗ 数据流一致（跨存储对账） — 模块有派生存储却没有对账机制 —— PG 与派生层的数据漂了没人知道
  — 测试能发现缺陷 — 未实测：加 --mutation-check 才会把源码改坏再跑测试（要复制工作区并多次跑测试，很贵）

── 静态信号（不能单独支撑「通过」） ──────────────────────────────
  ...

── 未实测 ───────────────────────────────────────────
  — 表结构基本合规 — 没连上数据库（或模块不碰 PG）
  — 向量集合属性自洽 — 这个模块没有向量集合调用点（判不了）
  — 数据准确（基线对拍） — 项目里没有基线对拍脚本
  — 需求覆盖到位 — 没给计划文件（--plan）

── 本工具不判定 ────────────────────────────────
  需求是否被正确理解 · 产品行为是否正确 · 性能 · 安全 · 可用性 · 运维就绪
  上面只是测试就绪度的实测结果；「能不能交付」仍要人判断。

结论：2 项阻塞 · 3 项待人工确认 · 6 项无信号 · 1 项实测通过（另有 7 条静态信号）
  ✗ 导出的逻辑都有测试：39/103 个公开 def/class 在测试里零引用
  ✗ 数据流一致（跨存储对账）：模块有派生存储却没有对账机制 —— PG 与派生层的数据漂了没人知道
退出码 1 = 有阻塞项，或关键判据（测试是否绿）没测到
```

Two criteria were **really measured** here: the suite ran and passed
(`测试全绿 — 没有失败用例`), and coverage came out of `pytest-cov` at 84% line
coverage (`实测平均行覆盖 84%`). The rest is either a static signal, not
measured, or a false alarm (see below).

## The other five verdicts

| Repository | Verdict | What was measured |
| --- | --- | --- |
| chalk/chalk | 2 blocking · 1 needs a human · 8 no signal | nothing measured; **both blocking items are false alarms** ([#1](#1-test-files-without-a-suffix-are-not-discovered), [#3](#3-a-derived-store-that-does-not-exist)) |
| expressjs/express | 0 blocking · 0 needs a human · 12 no signal | nothing measured — and it said so, without inventing a pass |
| sindresorhus/type-fest | 2 blocking · 0 needs a human · 7 no signal | the runner (`tsd`) is not a runner the tool can read ⇒ *not measured* ([#1](#1-test-files-without-a-suffix-are-not-discovered), [#3](#3-a-derived-store-that-does-not-exist)) |
| pallets/itsdangerous | 1 blocking · 1 needs a human · 8 no signal | coverage report path not found ⇒ *not measured* |
| chalk/chalk — negative control | 2 blocking · 2 needs a human · 8 no signal | module path `.` instead of `source`: 22 lines differ, the same two false alarms remain |

## What this run found

Four defects in the tool itself. The first three are filed with the evidence
that produced them; the fourth is not filed yet because it needs a diagnosis
first.

### 1. Test files without a suffix are not discovered

`chalk/chalk` keeps its tests in `test/` as `basic.js`, `colors.js`,
`template.js` — no `.test.`/`.spec.` suffix. The tool only recognises a file
inside a test directory when the **file name** carries a suffix, so the
repository looks like it has no tests at all:

```
源码 9 文件（+0 个测试支撑） / 单测 0 个 / itest 0 个
```

The same repository's `type-fest`, whose files *are* named `*.test.js`, is found
correctly. The suffix, not the layout, is the trigger.

The consequence is worse than "not measured": the `uncovered-exports` criterion
then reports **every** runtime export as untested, and that is a **blocking**
item.

→ [#1](https://github.com/mcgrapeng/attest/issues/1)

### 2. A derived store that does not exist

The cross-store criterion reports that the module has a derived store with no
reconciliation mechanism — naming `index.d.ts` and `index.js`. Those are one
store: the declaration file and its implementation. `chalk/chalk` and
`pallets/click` are both flagged, and both have no database at all, so the
criterion asserts a PG-versus-derived-layer drift that cannot happen.

→ [#3](https://github.com/mcgrapeng/attest/issues/3)

### 3. The per-file test command lands on the wrong sub-command

When a repository's test command is compound, the test file is appended to the
**end** of it:

```
逐个测试文件跑 $ xo && c8 node --test test/[!_]*.js && tsc --noEmit --types node source/index.d.ts <测试文件>
```

The file becomes an argument to `tsc`, not to the runner. The repository's own
command is used precisely because it knows the runner — appending to the end of
it breaks exactly that part.

→ [#2](https://github.com/mcgrapeng/attest/issues/2)

### 4. Not filed yet: Python coverage report not found

On `pallets/itsdangerous` the coverage criterion degraded with
`覆盖率报告没解析出来（[Errno 2] No such file or directory: '.../cov.json'）`.
That needs a diagnosis before it becomes an issue.

## What it means

**The conservative half held.** No run reported a green test suite that was not
measured. Five of the six runs ended with `tests-green` as *not measured* rather
than a pass — including `express`, where the tool had nothing measured at all
and said so instead of inventing one. That is the behaviour the tool promises:
it never passes what it did not run.

**The other half did not.** On three of the five repositories the tool named
**blocking items that were not real** — exports "untested" because the test
files were never found, and a "derived store" that does not exist. Those are
false alarms rather than false greens, which is the less dangerous of the two,
but a criterion that names a problem that is not there teaches the reader to
distrust the whole report.

Both halves are the tool's own standard: a false green is a defect, and a false
alarm is a defect too. This page is the first entry of the false-alarm gallery
the roadmap asks for.

## After the fixes (v0.1.2)

All three defects are fixed, and the same script reproduces the difference:

| Repository | Before | After |
| --- | --- | --- |
| chalk/chalk | 0 test files found · 2 blocking · tests not measured | **7 test files found** · **tests measured green** · 1 blocking (the false derived store is gone) |
| expressjs/express | 0 test files found · 12 no signal | **6 test files found** · **tests measured green** · 10 no signal |
| sindresorhus/type-fest | 2 blocking (one of them false) | 1 blocking (the false one is gone) |
| pallets/click | 2 blocking (one of them false) | 1 blocking (the false one is gone) |
| pallets/itsdangerous | 1 blocking | unchanged — its coverage report path is still missing (the fourth finding, not filed) |

Two things stayed the same, and both in the honest direction: type-fest's runner
(`tsd`) is still a runner the tool cannot read, and itsdangerous' coverage
report is still missing. Both are reported as *not measured* rather than as a
failure.

The regression assertions for the three fixes live in
`skills/attest/scripts/test_repo_layout.py`.
