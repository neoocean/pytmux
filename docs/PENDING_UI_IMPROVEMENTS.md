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

### 2. Combine header lines into single row ✅ DONE (verified 2026-07-27)
- **Location**: Token usage (est) panel header
- **Change**: Merge these two header lines into a single compact row:
  - Line 1: "5h 5% · wk 31% · by time · ~Σ153.2M"
  - Line 2: "Haiku  Sonnet  Opus  ?"
- **Status**: Already satisfied — but nothing was holding it, so it is pinned now.
  Line 2 (the model colour legend) was removed on its own when Period/Session bars
  went single-colour (2026-06-22, oracle
  `test_period_and_session_drop_model_color_and_legend`), and line 1 has been the
  only `#tktop` row since — `_limit_summary() + scope` in **both** render paths
  (flat `_refresh` and the period tree). The "by time" indicator disappeared with
  the sort toggle in item 1. New oracle:
  `tests/test_token_log_screen.py::test_top_header_stays_one_line` fails if the
  header splits into multiple lines again.

---

*Last updated: 2026-07-27 (item 2 verified done + pinned by a test; both items closed)*
