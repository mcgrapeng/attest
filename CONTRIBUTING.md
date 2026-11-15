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
```

`test_readme_sync.py` is the one people forget: it checks that the English and
Chinese READMEs have the same sections, that the numbers quoted in both match
the repository, that every path they mention exists, and that the sample
report is the tool's verbatim output. If you change a criterion count or a
command, run it before you commit.

## What a pull request looks like

- One behavioural change per commit, with the regression assertion in the same
  commit. A fix without an assertion will be sent back.
- Comments and identifiers in English; user-facing strings in the tool's output
  are Chinese, because the tool prints Chinese. The README is English by
  default with a Chinese counterpart at `README.zh-CN.md`.
- If a change alters what the tool reports, update `skills/attest/SKILL.md` in the same
  change — the skill contract and the implementation are read together.

## Reporting a problem

Open an issue with: the repo you ran it on, the module path, the command, and
the output. If the tool claimed something it should not have, that is the
interesting kind of report — attach the exact wording so it can become a
fixture.

Security problems do not go in an issue — see [SECURITY.md](SECURITY.md).
Everyone participating is expected to follow the
[Code of Conduct](CODE_OF_CONDUCT.md).

## What is being built next

[docs/roadmap.md](docs/roadmap.md) lists the phases and where outside help is
worth the most. Issues labelled *good first issue* are the smallest useful
steps; a pull request that fixes something nobody filed is welcome too.
