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
reads as "the scanner broke". `bash .github/skillspector-baseline.py --out
/tmp/ss-baseline.yaml` translates the reviewed rule-level allowlist into the
baseline SkillSpector expects, and with that baseline the scan exits zero and
the Security Scan step can report:

```bash
python3 .github/skillspector-baseline.py --out /tmp/ss-baseline.yaml
skillevaluator security-scan skills/attest --skillspector-baseline /tmp/ss-baseline.yaml
```

Two of its findings are known and accepted:

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
`_openclaw_read_chars`, not with a byte count. (The other advisory,
`OW-REF-BUDGET`, says `readiness.py` / `req_plan.py` / `decision_points.py`
exceed OpenClaw's 64 KB single-file read cap. Splitting those three core
scripts is a multi-thousand-line refactor for an advisory that only means
"the model pages more" — not worth it.)

### Optional: live A/B (Tier 3)

Tier 3 runs a real agent with and without the skill. The cases come from this
repository's own behaviour evals — `evals/make_tier3_dataset.py` translates
them into the dataset shape and stages the fixtures the way the sandbox
expects them:

```bash
python3 package.py --out /tmp/dist && unzip -q /tmp/dist/attest.skill -d /tmp/se_t3/skills
python3 evals/make_tier3_dataset.py --out /tmp/se_t3/skills/attest
skillevaluator tier3 validate /tmp/se_t3/skills/attest --strict
skillevaluator tier3 evaluate /tmp/se_t3/skills/attest -a codex --max-cases 1
```

Tier 2 (duplicate detection) needs an embeddings endpoint; a local
OpenAI-compatible one is enough:

```bash
export SKILL_EVAL_LLM_PROVIDER=openai-compatible
export SKILL_EVAL_LLM_BASE_URL=http://127.0.0.1:11434/v1   # ollama
export SKILL_EVAL_LLM_API_KEY=ollama
export SKILL_EVAL_EMBEDDING_MODEL=qwen3-embedding:0.6b
skillevaluator tier2 skills/attest
```

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
