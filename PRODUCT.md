# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

**Primary:** a developer who wants to add a delivery gate to their own
repository — someone who has been burned by a coverage number ("90% coverage"
while a whole class of defect shipped) and wants something they can point at
in a pull request.

They arrive from a repository they already own, usually with a test suite and
a CI setup, and they are deciding whether this tool is worth adding to their
workflow — not whether to adopt a platform.

## Product Purpose

attest turns a module's delivery preconditions into a machine-checkable,
auditable evidence list. It runs the target repository's own test command, its
own coverage gate, its own reconciliation script, and queries the PostgreSQL
catalog for the actual schema. Where it cannot measure something it says
*not measured* instead of passing it.

Success means: a developer can run one command against a real repository and
get a report whose every line is either evidence they can re-run or an
explicit statement that it could not be measured. Success does **not** mean
the tool decides whether the module can ship.

## Positioning

The mechanism: four evidence tiers — measured / static signal / not measured /
explicitly not judged — and an exit code that distinguishes "measured and
blocking" from "could not measure the key criterion". A neighbouring tool could
copy the vocabulary; it could not copy the refusal to pass what was not
measured.

The claim that a competitor cannot truthfully copy: **attest never prints a
verdict.** The decision stays with whoever owns the delivery. The name avoids
*verify*, *certify* and *sign-off* on purpose, and the report always ends with
a fixed list of what the tool refuses to judge.

## Operating Context

- **Surfaces:** a CLI (`readiness.py`, `req_plan.py`), an agent skill
  (`skills/attest/SKILL.md` — the contract), a GitHub Action
  (`action.yml`), a GitHub README (the landing page, English by default with a
  Chinese counterpart), and per-command docs.
- **Distribution:** `npx skills add mcgrapeng/attest` (skills.sh index and the
  skills CLI), `.claude-plugin/marketplace.json` (Claude Code and Codex
  marketplaces), GitHub releases with a version tag.
- **Runtime:** Python 3.11+, no third-party dependencies; the target
  repository's own toolchain does the actual test and coverage work.
- **Language of the tool's output:** Chinese — criterion names and the verdict
  lines are what the developer reads in their terminal.

## Capabilities and Constraints

- 14 criteria, each of them measured, a static signal, not measured, or
  explicitly not judged. A static signal can never support a pass on its own.
- The threshold belongs to the repository: the repo's own coverage gate is
  driven and judged by its exit code; generic thresholds are a fallback and do
  not block by default.
- "Cannot run" is never "below the bar" — setup errors are judged *not
  measured*, with the missing precondition named.
- The child process environment is allowlisted; credential prefixes
  (`NPM_*`, `PIP_*`, `UV_*`, …) are not passed through and `NODE_AUTH_TOKEN`
  is explicitly denied.
- The skill contract and the implementation are read together: a change in what
  the tool reports updates `SKILL.md` in the same change.
- The English and Chinese READMEs are one page in two languages: same
  sections, same order, same numbers.
- Coverage of languages: TypeScript/JavaScript and Python are recognised;
  Rust and Go are recognised by the plan generator but the readiness checker
  exits with code 2 and says so.

## Brand Commitments

*Inferred from the repository — the brand question was left unanswered, so
these are the maintainer's existing assets recorded as binding, not a new
direction. Confirm or correct before new visual work.*

- **Name:** `attest`, from *attestation* (the same root as SLSA's
  attestations). The rationale is written down in the changelog: the tool
  puts a claim on the record as something it has checked, and refuses the
  vocabulary of approval.
- **Wordmark:** the two-tone wordmark (`assets/attest-wordmark.png` and
  `assets/attest-wordmark-dark.png`), JetBrains Mono ExtraBold, teal — shipped
  as assets, not redrawn per surface.
- **Terminal hero:** the first-screen image is a real run of the tool, rendered
  from its actual output (`assets/hero.png`), not an illustration.
- **Voice:** plain and exact. It states what it measured, what it did not, and
  what it refuses to judge. It does not disparage other tools, and it does not
  claim capabilities it has not measured.

## Evidence on Hand

- **Self-test report** (`docs/self-test-report.md`): five public repositories,
  the verdicts published verbatim, including the four defects the run found in
  the tool itself.
- **Reproducible gates:** the regression suite, five golden fixtures, six
  behaviour evals, a security scan with a rule-level allowlist, and a README
  consistency guard — every number quoted in the README comes from one of
  these commands.
- **Criteria reference** (`docs/criteria.md`): the 14 criteria, the four
  evidence kinds, the language matrix.
- **Absences future work must not fabricate:** no user count, no testimonials,
  no named adopters, no benchmark numbers, no production-readiness claim. The
  repository is pre-1.0 and has no stars to cite.

## Product Principles

1. **Say what was measured, not what was concluded.** The tool reports; the
   owner decides. A criterion that cannot name its evidence does not ship.
2. **Every number is re-runnable.** A figure in the documentation exists
   because a command in the repository produces it, and a guard fails when it
   drifts.
3. **Publish the mistakes.** The tool's own verdicts on real repositories are
   published, including the runs that made it look wrong. A false alarm is a
   defect to be filed, not to be hidden.
4. **The English and Chinese pages are one page.** Neither language is a
   translation afterthought; they ship together and stay structurally
   identical.

## Accessibility & Inclusion

No product-specific requirement has been established yet.
