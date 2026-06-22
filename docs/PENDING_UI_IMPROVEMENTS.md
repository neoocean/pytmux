# Pending UI Improvements

## Token Usage Panel

### 1. Remove sort functionality from Options menu ✅ DONE (2026-06-22)
- **Location**: Token usage (est) panel, Options menu
- **Change**: Remove the "Sort" button/feature to simplify the Options menu
- **Status**: Implemented. Removed the `정렬`(Sort) toggle entirely — the `[o]`
  key / `tab_order` tab / click handler / `_order` field are gone, and the
  `#tksub` "Options" sub-option row (Sort was its only content) was removed.
  The period view is now always the time-ordered hierarchical tree; the scope
  header no longer shows the "by time" indicator. `[o]`/`h`/`d`/`w`/`m` are
  reserved no-ops (don't close the popup). Screenshots `24-token-log` /
  `37-token-log-hour` regenerated. Tests updated (892 passed). `usagelog.agg_view`
  left general (still supports token order; just unused by the screen).

### 2. Combine header lines into single row
- **Location**: Token usage (est) panel header
- **Change**: Merge these two header lines into a single compact row:
  - Line 1: "5h 5% · wk 31% · by time · ~Σ153.2M"
  - Line 2: "Haiku  Sonnet  Opus  ?"
- **Status**: Pending implementation
- **Task**: #2

---

*Last updated: 2026-06-22*
