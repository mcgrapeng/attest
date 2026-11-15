# Pull request

<!-- Keep this short. What a reviewer needs is the claim and the evidence. -->

## What changed

<!-- One or two sentences. If it changes a criterion, say which one and what
     "measured" now means for it. -->

## Evidence

<!-- Which gates did you run, and what did they say? Paste the numbers. -->

- [ ] Regression suite: `for t in skills/attest/scripts/test_*.py; do python3 "$t"; done`
- [ ] Golden fixtures: `bash fixtures/run_fixtures.sh`
- [ ] Behaviour evals: `python3 evals/run_evals.py`
- [ ] README consistency: `python3 skills/attest/scripts/test_readme_sync.py`
- [ ] Security scan (needs `skillspector`): `bash .github/check-skillspector.sh`

## Why it is right

<!-- If this fixes a false green or a false alarm, say which one and show the
     before/after. If it adds a criterion, say what "not measured" looks like
     for it — a criterion that cannot report "not measured" is not done. -->

## Notes for the reviewer

<!-- Anything you deliberately left out, or a decision you want challenged. -->
