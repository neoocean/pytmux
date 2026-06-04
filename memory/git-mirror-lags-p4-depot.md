---
name: git-mirror-lags-p4-depot
description: pytmux git mirror often lags the Perforce depot; never use git HEAD as the depot baseline when splitting/submitting
metadata:
  type: project
---

pytmux is **Perforce-primary with a git mirror** (GitHub `neoocean/pytmux`). The git mirror is maintained per-machine and **frequently lags the p4 depot** — work submitted from another client (e.g. `woojinkim@surface`) lands in the depot but may not be in local git HEAD or `origin/main` for a while.

**Why:** On 2026-06-03, what `git diff` showed as "uncommitted progress" was mostly already-submitted depot work the mirror lacked (CLs 56319 UI, 56320 busy-detection from `@surface`). Resetting a file to git HEAD before re-editing (`git checkout HEAD -- file`) and submitting **reverted the depot's newer @surface UI work** (had to repair with CL 56323).

**How to apply:**
- The **p4 depot is the source of truth**, not git HEAD. Before submitting, compare working tree to the **depot** (`p4 print -q //...file#have` or `p4 diff`), never assume git HEAD == depot.
- When splitting changes into CLs, do NOT `git checkout HEAD -- <file>` to "isolate" a change — it can clobber unmirrored depot revisions. Use hunk-level patches against the depot-synced working tree instead.
- The git mirror may need force-push to catch up (default branch `main` can be rebuilt from an earlier commit dropping reverted commits); a `backup-*` branch is kept as safety. Force-push of `main` needs explicit user authorization (classifier blocks it).
- Watch for **concurrent activity on this machine** — the user submits p4 CLs and does git ops in parallel.

Related: [[pytmux-ship-workflow]]
