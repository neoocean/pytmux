# Memory index

- [git mirror lags p4 depot](git-mirror-lags-p4-depot.md) — depot is source of truth; don't reset to git HEAD when submitting, it can clobber unmirrored depot work
- [p4 submit flaky locks](p4-submit-flaky-locks.md) — playground client crons (dropbox sync, checkpoint) periodically lock p4; submits block for minutes, serialize calls
- [p4 local/depot desync](p4-local-depot-desync-sync-before-edit.md) — sync file before editing for a new CL; verify non-empty diff before submit
