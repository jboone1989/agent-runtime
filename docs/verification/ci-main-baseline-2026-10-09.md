# CI baseline verification — 2026-10-09

This branch is based on exactly `2f809f3134d4f304d43db34cd52b69a562b85435`, the main commit used as the base for the architecture governance pilot PR #65.

**Purpose:** trigger the repository's unchanged `ci` and `test` GitHub Actions workflows with an intentionally documentation-only change. No application source, test, dependencies, test configuration, or CI definition has been modified by this branch.

Compare the full-suite failures with PR #65 (24 failed, 1241 passed, 4 skipped in each full suite) to establish whether failures pre-date architecture rules.

This file must not be merged; close the draft baseline PR after comparison.
