# Roadmap

attest is one day old. This is what is being built next, in the order it is
being built, and where outside help is worth the most. Every item below is a
thing that can be checked — no "someday we might".

Phase 0 is done or nearly done. Phase 1 is next. Phases 2 and 3 run in
parallel with them, because they need different people.

## Phase 0 — Make it findable

The tool exists; almost nobody can find it. This phase is about distribution.

- [x] **One-command install** — `npx skills add mcgrapeng/attest`, no clone, no
      Python, and the CLI detects which agent you run.
- [x] **Standard skill layout** — the skill lives in `skills/attest/`, which is
      what the skills.sh index and the Anthropic marketplace template both
      expect. A skill at the repository root is invisible to both.
- [x] **Tagged release** — `v0.1.0` and `v0.1.1`, with release notes, so that
      `mcgrapeng/attest@v0.1.1` is a thing other people can pin.
- [x] **Marketplace submissions** — skills.sh indexed (it needs the skill in a
      subdirectory, which is why the layout moved); Anthropic's official skills
      repository carries a Partner Skills entry (PR
      <https://github.com/anthropics/skills/pull/2224>); `.claude-plugin/marketplace.json`
      makes the repository a marketplace for **Claude Code and Codex** — both
      verified by installing the plugin from it. OpenCode has no skill
      marketplace; it reads `.agents/skills/`, which the one-line install
      already populates.
- [ ] **A curl one-liner** for people who are not on npm and do not want the
      Node CLI either.

## Phase 1 — Make it usable in CI

A tool that only runs on someone's laptop does not change a team's habits. It
has to run on every pull request.

- [ ] **A GitHub Action** — `uses: mcgrapeng/attest@v1`, running the criteria
      against the changed module and posting the evidence list as a PR comment.
      The action must not be able to block a merge on its own: the tool reports
      evidence, the repository decides.
- [ ] **A stable machine-readable contract** — the `--json` shape and the exit
      codes, versioned, so a CI job can gate on them.
- [ ] **An MCP server** — for agents that do not load skills, the same four
      commands over stdio.

## Phase 2 — Make it provable against real repositories

The criteria are only as good as the repositories they have been run against.
Public reports are the evidence, and they are also what makes a new user trust
the tool enough to try it.

- [x] **Dogfooding reports** — five public repositories, published verbatim in
      [docs/self-test-report.md](self-test-report.md), including the four
      defects that run found in the tool itself (three filed as issues).
- [ ] **A false-green gallery** — real cases where the tool said *not measured*
      and a human found the defect anyway. Each one becomes a criterion or a
      documented limit.
- [ ] **Rust and Go** — the readiness checker recognises TypeScript, JavaScript
      and Python modules today; for Rust and Go it exits with code 2 and says
      so. Porting the module recognition is the whole job.

## Phase 3 — Make it a standard

The long-term goal is not a popular tool but a shared definition: a set of
criteria that any tool, in any language, can be checked against.

- [ ] **The criteria as a published spec** — language-neutral, with the
      measurement each one requires and the evidence kind it produces.
- [ ] **Reference implementations per language** — so a Rust team can check the
      same criteria without switching to Python.
- [ ] **A comparison suite** — the same repositories run through several tools,
      with the reports side by side.

## How to help

| If you can… | The most useful contribution is… |
| --- | --- |
| Write Rust or Go | Port the readiness checker's module recognition (Phase 2) |
| Maintain a public repository | Let us run attest on it and publish the report |
| Use an agent we do not support | Port the skill contract to that agent's plugin format |
| Review the criteria | Argue with one — a criterion that cannot be argued with is a bug |
| Write documentation | A language that is not English or Chinese yet |
| Nothing above | Report a case where the tool was wrong, in either direction |

Start with [CONTRIBUTING.md](../CONTRIBUTING.md). Issues labelled *good first
issue* are the smallest useful steps; anything else in the issue tracker is
fair game, and a pull request that fixes a problem nobody filed is welcome.

## What we will not do

- **Give a verdict.** The tool produces evidence. Whether the module is ready
  to ship stays with the person who owns the delivery. This is why the name
  avoids *verify*, *certify* and *sign-off*.
- **Rewrite the user's code.** `attest` measures and reports. Fixing what it
  finds is the user's call.
- **Guess.** Where a criterion cannot be measured, it says *not measured*
  instead of passing it silently.
