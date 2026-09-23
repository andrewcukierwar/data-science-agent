# Offline remediation validation gate

Run `uv run python scripts/run_remediation_validation.py` from the repository
root. The command removes provider credentials from its child environment,
checks Docker and refreshes the local sandbox image, runs the existing
deterministic pytest fixtures by validation family, then runs the complete
`not live` suite, Ruff checks, and `git diff --check`.

The command does not duplicate analytical calculations. Family checks select
the repository's existing P0/P1 regression tests, including the fixtures added
for cohort grain, scope, coverage, reconciliation, statistical inference,
result binding, Critic repair, SQL cancellation, and task/evaluator boundaries.

If `.runs/phase2-task10-20260820-v8/` is present, the gate reads retained
ledgers, reports, calculation artifacts, and the frozen manifest. It checks
representatives from all ten scenario families and compares a fresh offline
rescore using the manifest's legacy evaluator versions with the retained v8
rescore. It does not execute saved SQL, write into v8, or apply evaluator 1.3 to
historical scores. Missing retained evidence is reported as `not_available`.
Historical classifications record where a demonstrated error occurred; they
are not revised benchmark scores or claims that v8 would pass after remediation.

Results are written to:

- `.runs/remediation-validation/remediation-validation.json`
- `.runs/remediation-validation/remediation-validation.md`

The overall gate requires every deterministic family, Docker integration, the
complete offline suite, code-quality checks, v8 evaluator compatibility, and
frozen-artifact immutability to pass. No paid benchmark matrix is part of this
command.
