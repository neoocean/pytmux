---
name: p4-local-depot-desync-sync-before-edit
description: after a p4 submit the local workspace can stay at the old have-revision (desync); sync the file before the next edit or submit needs a resolve
metadata:
  type: feedback
---

When editing the same file across many CLs (e.g. docs/HANDOFF.md, client.py), the
local working copy sometimes stays at the previous have-revision after a submit
(have < head), so the next `p4 submit` fails with "must resolve". An external
linter/cron also appears to reset local files after submits.

**Why:** lock contention + crons ([[p4-submit-flaky-locks]]) interfere with the
normal submit→have-update, leaving local desynced from depot head.

**How to apply:**
- Before editing a file for a new CL, `p4 sync <file>` to head — BUT only when the
  file has no local Edit-tool changes yet (sync overwrites local mods). For docs I
  edit fresh each task, so sync-first is safe; do it.
- After each successful submit, `p4 sync <file>` so the next round starts at head.
- Before submitting, verify the diff is non-empty (`p4 diff <f> | grep -c '^[<>]'`) —
  a 0-diff submit creates/leaves an EMPTY changelist and reverts the (clean) opens,
  silently losing nothing but wasting a CL. The /tmp/ptsubmit.py helper now aborts
  if nothing is opened.
- If "must resolve": `p4 revert <file>` (only if it has no needed local edits),
  `p4 sync <file>`, re-apply edits on head, resubmit.
