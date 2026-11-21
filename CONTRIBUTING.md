# Contributing to attest

Thanks for looking. attest is small on purpose: one Python tool, one skill
contract, no third-party dependencies.

## Setup

```bash
git clone https://github.com/mcgrapeng/attest.git && cd attest
python3 install.py
```

Python 3.11+ is the only requirement. `node` is needed for the JavaScript
fixtures, `pytest` + `pytest-cov` for the Python one, and `skillspector` for
the security scan.

## Repository layout

The skill lives in `skills/attest/` — `SKILL.md`, `scripts/` and `assets/`.
Everything else at the repository root is development tooling for the skill
(`fixtures/`, `evals/`, `docs/`, `.github/`) and the packaging entry points
(`install.py`, `package.py`).

The skill is in a subdirectory on purpose: a `SKILL.md` at the repository root
is invisible to the skills.sh index and to the Anthropic marketplace template,
so the tool would exist but never be found.

## The gates

Every change has to pass all of them. They are the same commands CI runs:

```bash
for t in skills/attest/scripts/test_*.py; do python3 "$t"; done   # regression suite
bash fixtures/run_fixtures.sh                        # golden fixtures
python3 evals/run_evals.py                           # behaviour evals
bash .github/check-skillspector.sh                   # security scan
python3 skills/attest/scripts/check_env_inheritance.py             # environment containment
python3 skills/attest/scripts/selfcheck.py src --repo fixtures/ts-colocated   # reverse verification
python3 skills/attest/scripts/test_readme_sync.py                  # README consistency
python3 package.py --check                          # packaging smoke
```

`test_readme_sync.py` is the one people forget: it checks that the English and
Chinese READMEs have the same sections, that the numbers quoted in both match
the repository, that every path they mention exists, and that the sample
report is the tool's verbatim output. If you change a criterion count or a
command, run it before you commit.

### Optional: the skill contract's own score

`skillevaluator tier1 skills/attest --no-llm` (needs `pip install skillevaluator`)
scores `SKILL.md` itself: structure, wording, and the harnesses it must load
in. It is not in CI because its Security Scan step cannot complete on any
skill — the scanner exits non-zero when it finds something, which that step
reads as "the scanner broke". Two of its findings are known and accepted:

- `Unexpected 'LICENSE' in skill root` — the LICENSE has to ship inside the
  package (`package.py` requires it at that path), and the validator's
  allowed-root list only knows `README.md`. `SKILLEVALUATOR_SCHEMA_ALLOWED_DIRS`
  extends the allowed *directories* only, so this one cannot be silenced. Do
  not "fix" it by moving the file.
- `No troubleshooting section documented` and `No mention of error handling` —
  false positives: the section exists as `## 出错了怎么办` (the validator only
  looks for the English heading), and the LLM rubric scores it 9/10.

Its `Harness Compatibility` check is the one that matters here: it fails when
`SKILL.md` gets too long for a harness that counts CJK characters as four.
Keep it under 15,000 weighted characters — measure with the validator's own
`_openclaw_read_chars`, not with a byte count.

## What a pull request looks like

- One behavioural change per commit, with the regression assertion in the same
  commit. A fix without an assertion will be sent back.
- Comments and identifiers in English; user-facing strings in the tool's output
  are Chinese, because the tool prints Chinese. The README is English by
  default with a Chinese counterpart at `README.zh-CN.md`.
- If a change alters what the tool reports, update `skills/attest/SKILL.md` in the same
  change — the skill contract and the implementation are read together.

## What is being built next

[docs/roadmap.md](docs/roadmap.md) lists the phases and where outside help is
worth the most. Issues labelled *good first issue* are the smallest useful
steps; a pull request that fixes something nobody filed is welcome too.

The highest-value contributions right now, in order:

| If you can… | The most useful contribution is… |
| --- | --- |
| Write Rust or Go | Port the readiness checker's module recognition (Phase 2 of the roadmap) |
| Maintain a public repository | Let us run attest on it and publish the report |
| Use an agent we do not support | Port the skill contract to that agent's plugin format |
| Review the criteria | Argue with one — a criterion that cannot be argued with is a bug |
| Write documentation | A language that is not English or Chinese yet |
| Nothing above | Report a case where the tool was wrong, in either direction |

The tool's own [self-test report](docs/self-test-report.md) is the model to
follow: run it against real repositories, publish the verdicts **including the
ones that make the tool look wrong**, and file each one as an issue with the
evidence attached.

## Reporting a problem

Open an issue with: the repo you ran it on, the module path, the command, and
the output. If the tool claimed something it should not have, that is the
interesting kind of report — attach the exact wording so it can become a
fixture.

Security problems do not go in an issue — see [SECURITY.md](SECURITY.md).
Everyone participating is expected to follow the
[Code of Conduct](CODE_OF_CONDUCT.md).
