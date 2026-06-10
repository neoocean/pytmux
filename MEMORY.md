# pytmux — working notes for contributors / agents

## Perforce: NEVER use the default changelist

This workspace lives on a **shared Perforce `office` client** that concurrent
sessions also edit. Files left in the **default changelist** can be swept into an
unrelated session's `p4 submit`.

> On 2026-06-09 a `p4 edit` of 5 source files sat in the default CL and a parallel
> session's submit pulled them into its unrelated change **57695** ("슬랙 공유 M15
> 기획서…") — the pytmux fix shipped under a misleading description nobody intended.

**Always submit through a dedicated, numbered changelist:**

1. Create a named CL first (BOM-safe — use a shell that writes UTF-8 **without** a
   BOM; PowerShell here-strings / `Out-File` add a UTF-16 BOM that breaks
   `p4 change -i`):
   ```
   p4 change -o | <set Description> | p4 change -i      # → "Change N created"
   ```
2. Open files directly into it — never a bare `p4 edit`/`p4 add` (those use default):
   ```
   p4 edit -c N <files>
   p4 add  -c N <files>
   p4 reopen -c N <files>      # if anything is already in default, move it
   ```
3. Submit only that CL, then verify:
   ```
   p4 submit -c N
   p4 opened                   # should be empty afterwards
   p4 describe -s N
   ```

This is a Perforce-only tree — there is no git mirror here; submit with `p4` only.
