# P1 validation record

Branch: `feat/generic-capabilities-p1`
Base: `fc09c9178fa871176f3e0e49fdef2e9100ae9a50`

Validated before PR:

- Focused filesystem/browser/database/scheduler suite: 37 passed.
- Real watchdog smoke on the authorized VM observed filesystem events after provider start.
- Real SQLAlchemy/SQLite smoke returned allowlisted data with the Runtime read-only guard enabled.
- Playwright Python package and Chromium binary installed successfully, but the VM browser launch still required additional OS libraries. The attempted `playwright install-deps chromium` step could not run because the Remote Desktop Commander connection returned HTTP 401 at that point; this is recorded as an environment verification gap, not as a successful browser launch.
- CI is configured to install `filesystem-events`, `browser`, `database`, and `scheduler` extras on clean regression runners. Normal CI verifies the Playwright Python integration but does not download browser binaries.

The PR must not claim a real browser-launch smoke until that deployment dependency check is completed on an authorized machine.
