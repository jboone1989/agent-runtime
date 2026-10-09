# Engineering governance pilot (Agent Runtime)

This is the **first incremental pilot** of [Agent Engineering Kit (AEK)](../../.importlinter) boundary enforcement. The kit's central public GitHub repository is not yet published. This PR intentionally installs a native Import Linter config rather than copying an unpublished package or replacing existing CI.

## Initial contracts

- `agent_runtime.learning` must not depend (directly or transitively) on `agent_runtime.dashboard`.
- `agent_runtime.memory` must not depend (directly or transitively) on `agent_runtime.dashboard`.

They express a concrete rule: domain components must not depend on the presentation dashboard.

Import Linter 2.15 scans the project sources in the new `architecture-boundaries` GitHub Actions job. The existing repository `ci` and `test` workflows remain responsible for behavioral tests.

## Baseline and staged migration

A read-only trial scan on the VM09 working tree on 2026-10-09 inspected 321 Python files and 885 import dependencies. Both declared contracts were kept.

The candidate contract `communication -> learning` was **not** added, because the scanner reported a transitive path:

```text
communication.server -> service -> runtime -> learning.evidence
```

This is a research lead, **not** permission to add exceptions or refactor the application in this pilot. The VM09 worktree has uncommitted changes, so results must be verified anew by GitHub CI against this PR branch.

## Running locally

```bash
python -m pip install "import-linter==2.15"
PYTHONPATH=src lint-imports --config .importlinter
```

To test the guard, deliberately introduce a forbidden import in a throwaway checkout; verify it fails and removing it restores success. Do not intentionally break the production working tree.

## Review and completion criteria

1. The new `architecture` job passes on the PR and also detects a known test violation in a disposable fixture.
2. Existing regression and platform boundary jobs pass (no duplicate suites required).
3. Any change to `.importlinter`, CI, or a future `.agent-engineering/policy.json` receives explicit architecture review.
4. Do not loosen rules solely to make checks pass.
5. Do not merge this pilot until CI actually runs; installing the config is not proof of correctness.
6. Preserve the VM09 untracked `AGENTS.md` browser-session rules when reconciling future deployments.

## Road to the shared kit

After a separate public AEK repository and release pass their own adapter CI, replace this temporary native configuration with the reusable, versioned AEK project guard. The **project** will still own the rules and behavioral tests; the **toolkit** will own checking mechanics. Avoid keeping both configs as competing sources of truth.
