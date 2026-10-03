# Lightpanda 1.0.0 replacement probe — 2026-10-03

Verdict: **not ready to replace Ferro's current shared Chromium path**.

Real acceptance on VM-0-9-ubuntu used the released Linux x86_64 binary. GitHub
asset SHA-256 was verified: `aa5a4b8ed53d1e38b3c73f5b2647d0a84a82e6744557f45f9a9c85858aa031c3`.

| Check | Result |
|---|---|
| Dynamic JavaScript form: input, click, response read | PASS |
| Existing BrowserHarnessProvider navigation and page evaluation | PASS after explicit Lightpanda context adaptation |
| Cookie import and ChatGPT session authentication | PASS (session endpoint returned 200 with authenticated user) |
| Existing ChatGPTBrowserProvider send/response flow | FAIL before a completed turn: Runtime.evaluate / navigation IPC timed out |
| X timeline loading | PASS |
| Existing Jev X draft fill and readback | FAIL: observed input action left zero verified text characters |
| Second default task tab on same CDP connection | FAIL: TargetAlreadyLoaded, code -31998 |
| Production Chrome remains alive, test Lightpanda stopped | PASS |

## Scope and limitations

No public test post was submitted. ChatGPT produced no verified acceptance answer.
Page loading and successful authentication are not a successful cognition or
publication loop. Default-context adaptation passes 20 focused Harness/session
regression tests; it does not establish production readiness.

The optional `BrowserHarnessRuntime(engine="lightpanda")` selects explicit
default-context handling and one shared Harness CDP daemon, and does not advertise
page screenshots. Chromium remains the default. This adapter and receipt are
experimental branch work; there is no production cutover.

Lightpanda scopes browser/login state to each CDP client connection. The probe
therefore copies relevant first-party cookies into the actual Harness connection,
rather than assuming that importing into a different diagnostic connection
authenticates the Harness. Cookie values remain on the host and are excluded
from the receipt.

A second default target fails even without Browser Harness. The single-target
diagnostic explicitly rebinds the disposable test tab for ChatGPT and X to
distinguish this target limitation from the input/navigation failures. This is
diagnostic only: production task tabs must retain independent bindings.

RSS is for the one Lightpanda process, including diagnostic and Harness sessions:
approximately 388 MiB after the diagnostic ChatGPT page, and 681 MiB after the
additional attempted Harness ChatGPT flow and X page. The Chrome figure in the
receipt covers its parent process only. These are not comparable full workload
benchmarks and do not substantiate a 16x reduction for Ferro.

## Reproduce

Run `scripts/lightpanda_acceptance.py probe` using the deployed Ferro interpreter
with Browser Harness, websockets, Jev, and Runtime dependencies. The script obtains
the existing Chrome endpoint from Runtime's profile state; uses a disposable
Lightpanda server and short temporary Harness directory; reuses cached 1.0.0;
emits progress and a receipt; stops its test server/daemons; exits nonzero when
replacement requirements fail. Latest host receipt:
`~/.local/state/agent-runtime/lightpanda-acceptance/latest.json`.

Current VM09 interpreter:
`/home/ubuntu/content-agent-prod-venv-connectome-ffc4c726-c7d266d4-d2f4fd8c/bin/python`.

## Next useful work

Resolve or redesign the CDP target/session limitation; verify input delivery
through Jev and ChatGPT without losing DOM/session identity after navigation;
then test login and conversation recovery across Harness and browser restarts.
Read-only learning-source retrieval is a narrower candidate, but no such
production route was activated in this experiment.

Upstream references:
- https://lightpanda.io/blog/posts/lightpanda-1-0
- https://lightpanda.io/docs/reference/python
- https://github.com/lightpanda-io/browser/issues/1962
