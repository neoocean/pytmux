---
name: p4-submit-flaky-locks
description: p4 submits on the playground client periodically block for minutes; cron jobs (dropbox sync, p4 admin checkpoint) hold the workspace/global lock
metadata:
  type: reference
---

On this machine the `playground` Perforce client is shared with recurring background
jobs that hold locks and block p4 operations (even read-only `p4 changes`):

- `p4 -c playground sync //woojinkim/inbox/dropbox/...` — a Dropbox-mirror cron that
  re-runs every few minutes and locks the workspace for 2–8 min each time.
- `p4 admin checkpoint` (via a cron that does `p4 login && p4 admin checkpoint`) —
  can pile up stuck at the `p4 login` step and jam the server; a real checkpoint
  holds the global DB lock.

Symptoms: my `p4 edit`/`p4 submit` hang for minutes; concurrent invocations pile up
into zombie p4 processes that deadlock. Once during this work the server became
unresponsive to even `p4 changes` until the cron processes timed out / the user
stopped the dropbox sync.

How to cope:
- Don't fire many p4 commands concurrently — they contend and deadlock. Serialize.
- Before a submit, it's fine to wait for a gap: `pgrep -f "sync //woojinkim/inbox/dropbox"`
  empty = lock likely free.
- Do NOT kill `p4 admin checkpoint` / other clients' jobs — the auto-mode classifier
  blocks it and killing a real checkpoint can corrupt the DB. Killing your OWN stuck
  `p4 edit/change/changes` is fine.
- See [[p4-local-depot-desync-sync-before-edit]] for the desync that follows.
