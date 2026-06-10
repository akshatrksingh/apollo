# changelog.md

Intentional deviations from the design docs only. Not a git log. Claude Code appends a row here when the human approves a change to the plan (schema, signatures, task design, stage order). Each entry must cite which doc/section it changes and why.

| date | stage | change | reason | approved by |
|---|---|---|---|---|
| 2026-06-10 | 5 | post-only expected score corrected 0.33 -> 0.67 in tasks_spec.md exploit table (Task 3 row "post to C002 but never touch any task") and dev_stages.md Stage 5 test goal | clean single C002 post consumes the allowed message budget, so it is not collateral damage under the path-agnostic AllowedChanges schema; symmetric to update-no-post (each fails exactly one assertion) | Akshat |
