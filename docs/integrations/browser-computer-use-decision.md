# Browser / Computer Use architecture decision

Status: **LOCKED pending benchmark evidence**

## Invariants

1. Browser / Computer Use is an Agent Runtime capability, not a Ferro-owned subsystem.
2. Agent Runtime owns capability contracts, permissions, policy, durable session identity, evidence and host routing.
3. Browser mechanics are delegated to a mature open-source backend rather than reimplemented as a custom screenshot/input protocol.
4. Ferro and other agents consume the Runtime browser contract.
5. Existing Ferro browser/profile code stays in place only until the replacement passes real acceptance. It is not the target architecture.

## Selected backend

**Browser Harness** is the persistent execution backend.

Why it is selected:

- It attaches to real Chrome through CDP.
- It already supplies browser interaction primitives and daemon lifecycle.
- It can attach explicitly to an existing logged-in Chrome endpoint.
- It does not require Agent Runtime to implement a second screencast, mouse, keyboard or tab-control stack.
- It can remain isolated in its own Python environment while Agent Runtime calls its executable/daemon contract.

## Non-selected roles

- **Browser Use CLI** remains useful as a browser-agent layer, but is not the session/runtime owner. The previous Ferro integration attached Browser Use below Ferro-owned Chromium and therefore did not remove the custom Computer Use path.
- **PlaywrightBrowserProvider** remains the ephemeral test/isolated-job backend. It is not the persistent-login default.
- **Custom JPEG screencast / CDP input bridges** are not Computer Use backends. A future human-takeover UI may expose a real browser, but it must not become a second browser-control implementation.
- **noVNC/VNC** is not the default Computer Use execution layer.

## Acceptance gates

The persistent backend is accepted only with real, not mocked, evidence:

| Gate | Requirement | Current evidence |
| --- | --- | --- |
| Runtime contract | Existing browser contract + Browser Harness implementation regressions pass | PASS: 12 targeted tests on 2026-10-01 |
| Runtime restart | A fresh provider instance reattaches the same durable browser target | PASS |
| ChatGPT | Attach to the existing logged-in Chrome profile; no login control shown after reattach | PASS on 2026-10-01 using CDP 127.0.0.1:9401 |
| X | Reuse existing logged-in X browser through Runtime | PENDING |
| YouTube | Login/persistence + normal navigation through Runtime | PENDING |
| OKX | Login/persistence + normal navigation through Runtime | PENDING |
| Ferro migration | Production Cortex/browser callers use Agent Runtime provider | PENDING |
| Legacy removal | Remove Ferro-owned Computer Use implementation only after all required gates pass | PENDING |

The real ChatGPT acceptance command is kept in `scripts/browser_harness_acceptance.py`.

## Change rule

Do not replace the selected backend because another framework sounds attractive in discussion. Change this decision only when new evidence shows a concrete acceptance failure or another backend beats it on the same benchmark while preserving these invariants.
