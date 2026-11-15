# Security Policy

## Reporting a vulnerability

Please **do not open a public issue** for a security problem.

- Use GitHub's private vulnerability reporting:
  <https://github.com/mcgrapeng/attest/security/advisories/new> (Report a
  vulnerability → New draft advisory). This opens a private thread with the
  maintainer; no details are published until the maintainer decides to publish.
- If that link is unavailable, email `mcgrapengzhang@gmail.com` with the same
  information and ask for a private reply.

Please include: what you found, how to reproduce it, the affected file and
version, and what an attacker could do with it. A proof of concept is welcome;
a working exploit against other people's repositories is not needed.

You can expect an acknowledgement within a few days and a fix or an explanation
of why not within two weeks.

## What is in scope

`attest` runs code that the **target repository** defines — its `test`,
`coverage`, `reconcile` and `eval:baseline:check` entries, or the matching
Makefile / tox targets. That is by design, and it is the part most worth
scrutinising:

- **A target repository could make `attest` execute arbitrary code.** That is
  the same trust level as running that repository's own test suite. If you find
  a path where `attest` executes something the target repository never
  declared, that is a vulnerability.
- **Secrets leaking into a child process.** The child environment is
  allowlisted (`PATH`, `HOME`, `LANG`, `TZ`, `TMPDIR`, `SHELL`, `USER`,
  `LOGNAME`, `TERM`, `CI`, `LC_*`, `NODE_*`, `PYTHON*`, plus the few
  `POSTGRES_URL` / `DATABASE_URL` / `MEMORY_*` / `REQ_PLAN_*` variables the
  tool needs). Anything outside that list must not reach the child — in
  particular `AWS_*`, `GCP_*`, `AZURE_*`, `NPM_TOKEN` and `NODE_AUTH_TOKEN`.
- **The tool writing into the target repository** outside the paths it was
  given. `req_plan.py scaffold` writes test files; every write must stay under
  `--out-dir` after normalisation.
- **The tool making a claim it did not measure.** Reporting a criterion as
  *measured* when it was inferred is a correctness bug in the same family as a
  false green — report it even though nothing was exploited.

## What is out of scope

- Running `attest` against a repository you do not trust, and then reporting
  what that repository's own scripts did. That repository's scripts run with
  your privileges; `attest` does not add privileges.
- Denial of service through an expensive measurement (`--mutation-check`
  copies the workspace and runs the test suite repeatedly). This is documented
  and off by default.
- Vulnerabilities in the target repository's dependencies. Report those to the
  dependency's own maintainers.

## Scope of the code

The skill lives in `skills/attest/`. The scripts under
`skills/attest/scripts/` are the runtime; everything else in the repository is
tooling for developing the skill.
