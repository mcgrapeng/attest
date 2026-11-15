# The criteria — what attest measures, and what it refuses to measure

`attest` runs 17 criteria against a module. Each one produces **evidence**, and
the evidence is sorted into four kinds. The report never says "ready to ship" —
that decision rests on things no tool can read off the code, and the report
lists them under **本工具不判定**.

The criterion names below are the ones the tool actually prints (it prints
Chinese); `key` is the machine-readable identifier in `--json`.

## The four kinds of evidence

| Kind | Chinese label in the report | What it means |
| --- | --- | --- |
| Measured | 已实测 | Something ran. The command, its exit code and its output are in the report |
| Static signal | 静态信号 | Read out of the source, not run. **Can never support a pass on its own** |
| Not measured | 未实测 | Could not be measured, and the report says which precondition was missing |
| Not judged | 本工具不判定 | Out of scope by design: requirement understanding, product behaviour, performance, security, availability, operational readiness |

The verdict has three tiers, and they are deliberately not "pass/fail":
**阻塞项** (measured and below the bar), **待人工确认** (measured, but a person
has to call it) and **无信号** (not measured). Exit code `0` means no blocking
items, `1` means there are blocking items or a key criterion could not be
measured at all, `2` means the module could not be recognised.

## The 17 criteria

| Criterion | key | Evidence kind | How it is measured |
| --- | --- | --- | --- |
| 测试全绿 (tests green) | `tests-green` | Measured | Runs the module's tests with the **repo's own** test command, listing failures (**assertion failures only**; a setup error means the environment is not ready ⇒ not measured) |
| 核心逻辑测试到位 (core logic is covered) | `coverage` | Measured | **Drives the repo's own coverage gate**; only without a gate do you use `--min-line-coverage` explicitly |
| 测试能发现缺陷 (the tests can find defects) | `test-strength` | Measured | Breaks the source on purpose and runs the tests: red ⇒ killed, still green ⇒ **this suite cannot find this class of defect** (`--mutation-check`; off by default ⇒ not measured) |
| 表结构基本合规 (schema is basically compliant) | `schema` | Measured | Queries the PG catalog: primary key / unique constraint / tenant column / RLS+FORCE / timestamps with a timezone (single-tenant, config and append-only tables exempt) |
| 数据流一致（跨存储对账）(data agrees across stores) | `data-flow` | Measured | **Runs** the module's own reconciliation script and parses the drift; judged **per tenant kind**; field-level comparison is part of this one |
| 数据准确（基线对拍）(data matches the baseline) | `data-accuracy` | Measured | **Runs** the repo's own baseline script and parses the metric differences |
| 钉有区分力 (the pin has discriminating power) | `pin-discriminating` | Measured | **Breaks the source at the pinned point** and runs the pinned test case: red ⇒ the pin kills this class of defect; still green ⇒ **this pin cannot find it** (`--plan` + `--discriminating`; no plan ⇒ not measured) |
| 需求覆盖到位 (requirement is covered) | `requirement` | Static signal | Reads the plan and the test files: every invariant's pin → a test case that really exists (`--plan`); an unreviewed plan downgrades this to advisory |
| 向量集合属性自洽 (vector collection attributes agree) | `vector-schema` | Static signal | Whether one collection's dimension/metric/partition key agree across call sites; the partition key must not be a vector field |
| 测试卫生（不留残留、不靠 sleep）(hygiene) | `hygiene` | Static signal | Fixed waits (a source of flakiness) and database writes without cleanup (a source of leftovers) |
| 导出的逻辑都有测试 (every export is referenced by a test) | `uncovered-exports` | Static signal | Exported functions/classes with zero references in the tests are named |
| 不过度抽象 (not over-abstracted) | `extensibility` | Static signal | Exported symbols with almost no call sites anywhere in the repo |
| 没有重复造轮子 (no reinvented wheels) | `no-wheels` | Static signal | Same-named implementations across the repo |
| 使用主流开源库 (uses mainstream open-source libraries) | `open-source` | Static signal | The module's real dependency list (path aliases and the standard library excluded) |
| 断言的期望值独立 (assertion oracles are independent) | `oracle` | Static signal | Assertions whose expected value comes from the system under test ⇒ advisory |
| 计划与机器推导对齐 (the plan matches the machine-derived decision points) | `plan-alignment` | Static signal | The decision points are **reverse-derived from the source** and compared with the plan's decision-point column in both directions: machine-only, plan-only, and pins that do not reference the module at all (`--plan`) |
| 应有判定点覆盖到位 (the decision points are covered) | `decision-coverage` | Static signal | Every high-risk decision point in the plan has an owning invariant **or** a written exemption, and the landing point (file::case) really exists (`--plan`) |

The evidence kind is assigned **per run**: a criterion that needs a database is
marked *not measured* when there is no connection, never *measured*.

## Language support

| | Plan generation | Coverage measurement | Export-reference criterion | Cross-store reconciliation | Real-backend itest lane |
| --- | --- | --- | --- | --- | --- |
| TypeScript / JavaScript | ✅ | ✅ node / vitest / jest | ✅ | ✅ | ✅ |
| Python | ✅ | ✅ pytest-cov | ✅ module-level `def`/`class` | ✅ | ✅ |
| Rust / Go | partial (reconnaissance is reused) | — module not recognised | — | — | — |

The plan generator handles Rust and Go exports in its reconnaissance, and
`req_plan.py scaffold` has Rust and Go branches. It is the readiness checker
that stops at the language boundary — it exits with code 2 and says that only
TS/JS/Python is implemented. "Recognised but no data" and "not recognised" are
different, and only the second one is true. A report full of ✓ where nothing
was measured is worse than an error.

## How each measurement is decided

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
- **Level L2 never hard-judges.** When the decision points can only be derived by
  the lexical fallback (L2), the three decision-point criteria are downgraded to
  warnings — a missing capability must never report green. The report names the
  level it reached (`推导等级 L2`) so a downgrade is never silent.
