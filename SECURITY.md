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

## What the tool runs, and when

`readiness.py` **executes scripts the target repository defines itself** (the
`test`, `coverage`, `reconcile` and `eval:baseline:check` entries in
`package.json`, or the matching Makefile / tox targets). Three things to know:

1. **See what it will run first**. `--dry-run` lists the candidate scripts and
   the exact commands without executing them. In normal mode the tool prints
   that line before it runs anything — it is the audit trail, and it includes
   the repo's own `test` command, which is the largest thing the tool runs (it
   is executed once per test file).

   Script selection has **two** rules, and they differ by ecosystem:

   - **JS repos (`package.json`)** — an **explicit allowlist** of script names
     (exact names or allowlisted prefixes). A script that merely has a keyword
     in its name (for example `reconcile:reset`) is never auto-executed.
   - **Python repos (Makefile / tox.ini)** — matched by **keyword in the target
     name** (`cov` / `reconcile` / `integration` / `itest`), as a whole word:
     `coverage`, `test-coverage` and `py-cov` qualify; `recover` and `cover` do
     not. The coverage gate additionally accepts a Makefile anywhere at or above
     the module directory, so a repo-wide `make coverage` still counts; the
     reconcile and itest lanes additionally require the recipe or the command to
     mention this module.

   The Python rule is the looser one: a target whose name looks like a coverage
   gate will be run. The tool never runs anything the repo has not declared
   itself, and `--dry-run` shows exactly which targets would run — so if you do
   not want a target picked up, do not give it a gate-like name.

2. **Child processes get an allowlisted environment only**: `PATH`, `HOME`,
   `LANG`/`LANGUAGE`, `TZ`, `TMPDIR`/`TEMP`/`TMP`, `SHELL`, `USER`/`LOGNAME`,
   `TERM`, `CI`, `SYSTEMROOT`/`COMSPEC` (Windows), the prefixes `LC_*`,
   `NODE_*`, `PYTHON*`, plus the `POSTGRES_URL` / `DATABASE_URL` /
   `MEMORY_ITEST` / `MEMORY_RECONCILE_OUT_DIR` / `REQ_PLAN_RECONCILE_OUT_DIR`
   variables the tool itself needs.

   **Nothing outside that list is passed through**: your cloud provider keys
   (`AWS_*` / `GCP_*` / `AZURE_*`) and your registry credentials (`NPM_TOKEN`,
   `NODE_AUTH_TOKEN`, `PIP_INDEX_URL` with a password in it,
   `UV_INDEX_SECRET_PASSWORD`) do not reach the target repo's scripts. The
   `NPM_*` / `PNPM_*` / `YARN_*` / `PIP_*` / `UV_*` prefixes are not on the
   allowlist at all, and `NODE_AUTH_TOKEN` is on an explicit **deny** list even
   though `NODE_*` is allowed. If a repo's test command needs npm or pip
   configuration, put it in a config file (`.npmrc`, `pip.conf`) rather than in
   the environment.

   The two connection strings *are* passed through: a repo's own integration
   lane has to reach its database, and that trade-off is deliberate. The levers
   that exist: unset `POSTGRES_URL` / `DATABASE_URL` in the environment and pass
   the connection string with `--database-url` (it is not read from the
   environment, so it never enters the child environment), or set `PGHOST` /
   `PGUSER` / `PGDATABASE` and let `psql` read the password from `.pgpass`.
   `.pgpass` alone is not enough: the schema query reads only the `PG*`
   variables, so without them the schema criterion degrades to "not measured"
   instead of being measured through `.pgpass`. The `psql` password travels in
   the `PGPASSWORD` environment variable, never in a command-line argument.

3. **Trusting a repo's `test` script means handing it your environment**. The
   risk level of that layer equals the risk level of every script it runs. With
   `--self-check-deps` the copy's `node_modules` / `.venv` are **symlinks to the
   real ones**, so a test that writes inside a dependency directory still writes
   to the real one.

`req_plan.py scaffold` writes test files into the repo. Two gates run before
anything is written: a pinned path must still be under `--out-dir` after
normalisation (a pin that escapes it is neither read nor written), and test
case names are quote-escaped per language (the plan's markdown is not turned
into executable code). When it cannot recognise the repo's test conventions it
**refuses to generate** and reports red instead of guessing.

## What scanners flag, and why

`attest` is flagged by automated skill scanners, and it will keep being
flagged. The reason is the tool's purpose: it runs the **target repository's**
own scripts, so a scanner sees subprocess execution and file writes and says so.

The three patterns a scanner will find, and what is actually behind them:

| Flagged | What it really is |
| --- | --- |
| Subprocess execution | `subprocess.run([...])` with an **argument list**, never `shell=True`, never a string command. The argv comes from the target repo's own `package.json` / `Makefile` / `tox.ini` |
| File writes | `req_plan.py scaffold` writes test files, and every write is confined to `--out-dir` after path normalisation. `--self-check` and `--mutation-check` write only into a temporary copy |
| Environment access | The child process gets an **allowlisted** environment only (see above). The whole-`os.environ` inheritance check is a CI gate, not a convention |

`skills.sh` shows the aggregated verdict of several scanners for this skill,
and it is `critical`. That verdict comes from the pattern above, not from a
hidden hole: there is no `shell=True`, no `eval`, no `exec`, no outbound
network call and no credential read anywhere in `skills/attest/scripts/`.

If a scanner reports something that is **not** on this list, that is a real
finding — please report it.

## Scope of the code

The skill lives in `skills/attest/`. The scripts under
`skills/attest/scripts/` are the runtime; everything else in the repository is
tooling for developing the skill.
