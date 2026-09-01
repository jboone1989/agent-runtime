# Agent platform migration TODO

This is the execution checklist for making Agent Runtime the shared host for
all stateful agents. A checked item has implementation and automated coverage
in this repository.

## Platform foundation

- [x] Record the single-runtime architecture and authority boundaries.
- [x] Add a strict, versioned `agent.yaml` manifest.
- [x] Persist and restore the hosted-agent manifest with Runtime state.
- [x] Add a reusable starter template and migration guide.
- [x] Add static guards against VirtualBrain coupling and vendor SDK leakage.

## Provider-neutral execution

- [x] Add `AgentBackend`, request/result/event/artifact/usage contracts.
- [x] Separate `ADVISORY` from `EXECUTION`; advisory mode has no tools or writes.
- [x] Select implementations by declared capability instead of vendor name.
- [x] Keep the old `LLMProvider` behind an advisory-only compatibility adapter.
- [x] Move the CLI's old provider path behind `AgentBackend`.
- [ ] Remove the deprecated `llm_provider` constructor/property after downstream
      agents have migrated and one release cycle has elapsed.

## Runtime observation boundary

- [x] Reuse Capability Inventory and the read gateway for observations; do not
      add a second provider registry.
- [x] Add one JSON-safe observation result contract and one provider bundle for
      Git, workspace, clock, environment, system, process, and read-only HTTP.
- [x] Bind roots/hosts and enforce depth, entry, byte, timeout, process-count,
      method, and redirect limits at the Runtime boundary.
- [x] Use Dulwich, psutil, and HTTPX instead of Git CLI compatibility,
      hand-written system inspection, or hand-written HTTP transport.
- [ ] Add watchdog-backed filesystem events when the life loop consumes event
      streams rather than snapshots.
- [ ] Add database reads only with a concrete bound database requirement and
      read-only connection/query policy.

## Context and state

- [x] Add a bounded, versioned Runtime context projection.
- [x] Redact credential-shaped fields recursively.
- [x] Keep provider thread references separate from trusted Runtime memory.
- [x] Expose backend capabilities through Runtime capability inventory.
- [ ] Add per-agent projection profiles after real agent manifests identify
      concrete context requirements.

## Codex specialist backend

- [x] Add an optional adapter for the official `openai-codex` Python SDK.
- [x] Map Runtime write authority to Codex read-only/workspace-write sandboxes.
- [x] Deny unmapped network authority by default.
- [x] Support provider thread references without treating them as identity.
- [x] Test the adapter with a fake SDK; no real side effects in unit tests.
- [x] Translate streamed SDK events and usage into Runtime records:
      the adapter drives `turn.stream()`, records command/file/MCP/search
      items and token usage as backend events, and the execution gateway
      persists them in `OutcomeRecord.effects` (`backend_events`,
      `backend_usage`) across restore.
- [x] Persist a failed provider turn as an auditable outcome: a turn that
      ends with status `failed` returns `AgentResult(status="FAILED")` with
      every pre-failure event and token usage intact (`error` carries the
      message), so the gateway still creates an OutcomeRecord. Only an
      unavailable SDK, a corrupt stream, or a Runtime bug raises.
- [x] Redact, truncate, coalesce, and bound backend evidence at the single
      Runtime persistence boundary (`execution/evidence.py`): credential-shaped
      strings are masked and secret-named keys dropped — including
      env-style `*_TOKEN=`/`SECRET=` assignments, CLI flags
      (`--token VALUE`), auth headers, and URL query secrets — long strings
      truncated, repeated `backend.usage.updated` events collapse to the last
      one, event count is capped (`events_truncated`, `events_dropped`), and
      nested lists/dicts are capped with depth and total-node budgets so one
      oversized payload cannot bypass the bounds before anything reaches
      `OutcomeRecord.effects`.
- [ ] Run an opt-in real SDK smoke test in an authorized disposable workspace
      and record the verified `openai-codex` version constraint in
      `pyproject.toml`; until then the dependency stays intentionally
      unconstrained.
- [ ] Add a Claude Agent SDK adapter only when a real agent needs a capability
      Codex does not provide; do not add it merely for symmetry.

## VirtualBrain

- [x] Keep VirtualBrain composed through frozen Contract v1.
- [x] Keep VirtualBrain as a mind/motivation module, not a second Runtime.
- [x] Keep production packages independent of each other's internal types.
- [x] In the VirtualBrain repository, replace hand-written OpenAI-compatible
      HTTP/retry/JSON transport with an official inference SDK adapter.
- [x] Keep deterministic appraisal and rule-based fallback available without
      any cloud SDK.
- [x] Do not place Codex/Claude tool execution inside appraisal or motivation.
- [x] Keep concrete environment providers in Agent Runtime and cross into
      VirtualBrain only through the JSON observation-result/perception bridge.

## Adoption gate for every future agent

- [x] Provide a starter manifest and definition of done.
- [x] Require restore, denial, provider-loss, and evidence tests in the guide.
- [x] Add this boundary suite to repository CI (`.github/workflows/ci.yml`):
      a dedicated boundary job gates the platform-boundary static guards,
      platform contracts, extension governance, and Contract v1 suites on
      pull requests and pushes to `main`. An org-wide project template
      remains open until the template repository exists.
- [ ] Enforce those jobs as required status checks before merging to `main`.
      Blocked by repository plan: branch protection and ruleset APIs return
      403 for private repositories on GitHub Free, so CI runs today but does
      not yet block merges. Needs GitHub Pro, a public repository, or an org
      template repo; until then, treat green CI as a review-time gate.
- [ ] Migrate each existing downstream agent and delete its duplicated identity,
      goals, memory, provider loop, session, sandbox, and generic tool code.
