# VM09 recurring memory failures, 2026-10-03

Host: VM-0-9-ubuntu. Times below are Asia/Shanghai.

## Evidence

The host has 3,747,732 KiB usable RAM and 2,035,708 KiB swap.
Kernel journals record eight global OOM kills since October 1:
October 1 at 12:01, 15:13, 17:20, 21:00; October 2 at 19:43;
October 3 at 05:24, 07:36, 11:05. Chrome was selected every time.
Free swap at these events was only 36–252 KiB. Several October 3
OOM events coincided with systemd-journald watchdog failures.

The last userspace journal records end around October 3 12:01;
kernel multicast firewall records continue to 12:30. The subsequent
boot begins at 13:05. During the stall, TCP port 22 accepted connections
but delivered no SSH banner; both reverse desktop forwards accepted
connections but delivered no HTTP response. No final 12:01 OOM record
was recovered, so that specific outage is consistent with resource
thrashing but its final trigger is not proven.

Five minutes after reboot, swap already held about 1.2 GiB. Current
large consumers include the shared Chrome, Ferro/connectome Python,
the Crawl4AI container's independent crawler, and cron Screeps workers.
At October 3 11:05, the kernel table showed several Chrome processes
holding roughly 0.43–0.99 GiB each and a Python process holding
roughly 0.69 GiB resident plus swapped memory. Summed RSS is not a
precise total because shared mappings can be counted multiple times.

Only three current webpage targets were found: two ChatGPT pages and
one X page. Their JavaScript heaps totaled about 700 MiB before explicit
GC; the cognition page dropped from 485 MB to 233 MB after GC.
This demonstrates reclaimable garbage, not proof of the source of all
growth.

## Corrected defects and containment

Runtime's account scope previously opened a browser-level CDP session
on every operation, including default-profile operations, without
detaching it. Harness switch_tab also creates a new page session each
time; disabling its old Network domain does not detach the old
Runtime/DOM agents. Jev additionally created a page attachment per
transaction. Runtime now reuses the daemon's existing page session,
detaches it when switching to another target, avoids unnecessary
default-profile browser attachments, and closes isolated storage
attachments in cleanup. Jev uses the same existing session.

A missing-directory legacy livingruntime-remote-public.service retried
over 11,300 times during the previous boot. It was disabled and stopped;
the active Remote connector and review connector remain running.

Persisted operational limits:
- Shared browser: MemoryMax 2048 MiB, swap max 512 MiB; MemoryHigh disabled.
- Ferro: MemoryMax 1024 MiB, swap max 256 MiB; MemoryHigh disabled.
- Crawl4AI: memory max 512 MiB; memory plus swap max 640 MiB.
- Screeps: isolated transient unit, memory high/max 384/512 MiB,
  swap max 64 MiB, CPU quota 75%, runtime max 300 seconds.
  Admission requires at least 1400 MiB available memory.
- SSH, journald, both Remote connectors: MemoryLow 64 MiB and
  CPU/IO weight 300.

Live validation rejected the initial soft limits: they caused sustained direct
reclaim even while the host had free RAM. Soft limits were removed from
Chrome and Ferro. The initial Chrome hard budget of 1408 MiB also caused
one local cgroup OOM at 13:28; SSH and Remote remained responsive. The
Chrome budget was then raised to 1792 MiB and its confirmed crashed
conversation target was recreated at the exact journaled URL, preserving
the pending request without submitting it again.

The supervisor now starts with about:blank. Account/task providers restore
their durable task pages, avoiding a duplicate ChatGPT homepage on reboot.
The empty, unbound supervisor-created homepage from this boot was closed,
leaving the X page and persisted ChatGPT conversation. No conversation
journal or browser cookie storage was cleared.

Vultr retained the pre-reboot reverse-forward sshd session, blocking new
16080/16081 listeners. After verifying its PID, peer IP and both owned ports,
that stale session was terminated. A Match rule for root from VM09's IP now
sets ClientAliveInterval 15 and ClientAliveCountMax 3. sshd syntax and
effective configuration were validated before reload. Both real forwarded
noVNC HTTP probes subsequently returned 200, and SSH returned its banner.

Existing swapped pages can remain above a newly lowered swap maximum;
the limit prevents new swap charges rather than immediately removing
existing pages. No swapoff or emergency host reboot was performed.
Hard limits can terminate an individual workload if it exceeds its
budget; its failure remains visible in systemd/cgroup evidence.

A bounded, credential-free sampler records memory availability, swap,
PSI, per-cgroup usage and memory.events every minute. It rotates after
8 MiB, keeping one previous file. These records survive a host reboot
and help identify a final trigger even when the journal stops.

## Validation

65 focused tests passed. Disposable real Chrome acceptance passed
same-site account isolation, runtime restart, browser restart restoring
cookies/local storage/IndexedDB, and thirty repeated transactions using
exactly one page session. Production verification must also check
the shared Chrome count, actual X identity, ChatGPT authentication,
Operator HTTP, Remote access and persisted resource properties.

The session lifecycle defects are confirmed by source and regression
tests. Their exact contribution to each historical stall cannot be
quantified from the old journals. A short healthy observation after
deployment is not a long-term stability guarantee.

## Publication recovery follow-up at 14:13

A second local browser cgroup OOM at 13:51 killed a renderer; service HTTP health
did not prove page-script or publication health. The exact uncertain publication
was found on the correct account timeline and reconciled without input:
https://x.com/caini56006962/status/2106259501913661750 .

The model transport now uses one verified composer edit and records completed
payload hashes for retry recovery. A 39,765-character real request completed with
a sampled browser cgroup peak of 1,451,966,464 bytes and no additional OOM.

Production also exposed Browser Harness's default 64 KiB asyncio request-frame
limit, which rejects larger JSON-encoded requests (including Unicode escaping).
Run scripts/patch_browser_harness_ipc.py with the Browser Harness interpreter
after installing/upgrading that dependency. It guards the known source shape,
keeps the original source backup, applies a bounded 2 MiB limit for Unix/loopback
servers, and verifies an exact 432,014-byte Unicode frame on a private test socket.
No browser process or account storage is modified by the patch.

## Owned rejection recovery and real publication at 14:52

The browser model accepted a 19,365-character Unicode request after the IPC patch;
exact retry reused its receipt, with a sampled browser peak of 1,557,020,672 bytes
and no additional OOM. Production later submitted a 148,386-character X draft
request. The owned request fingerprint matched the last user message, generation
was stopped, and ChatGPT explicitly reported that the message was too long.

The provider now archives only a stable, explicitly rejected owned request to a
private, fsynced journal archive before clearing pending state. It does not
resubmit that request. An unowned alert or an active generation cannot clear a
pending request. The rejection tests and provider suite passed: 48 tests.
At 14:47, production rejection recovery archived that exact request and a short
acceptance call succeeded in the preserved conversation.

Ferro now projects public posting history into drafting context rather than
including internal execution lineage. After deployment, a fresh publication was
confirmed at 14:52 on the expected account:
https://x.com/caini56006962/status/2106275925344284675 .
Verified daily progress advanced from 11 to 12. The browser's oom_kill counter
remained 2, unchanged from the 13:28 and 13:51 local incidents. Runtime release
5c84ad1 and Ferro release 4f7b699 were active for this acceptance. No account
storage, browser conversation, or host reboot was required for this recovery.

## Third local OOM and safe page garbage collection

At 15:07:46 another local browser OOM killed renderer 41642 (814,700 KiB anonymous
RSS). The host and Ferro remained reachable, but three confirmed Runtime task
pages stopped answering JavaScript. The responsive current X publisher was
preserved; the three confirmed crashed task pages were closed and the cognition
page restored to the exact journaled conversation URL without changing the
pending journal or submitting. A stale cognition Harness daemon was reconnected
without stopping Chrome. The accepted request c99a5b2d57d6405688ac0edc602369bc then
recovered its 620-character reply without another submission. Browser oom_kill is
now 3, so the earlier acceptance window must not be read as later stability.

The model transaction now requests page garbage collection before a verifiably
unsent turn and after the complete answer has been durably journaled. It preserves
page, login storage, conversation and request identity. It never runs that cleanup
to interrupt an unowned generation. Cleanup failure cannot lose the saved answer.
The provider suite passed 51 tests, including both safe boundaries, cleanup timeout
and the unowned-generation guard. The hard resource limits remain unchanged.

Task-tab adoption also incorrectly discarded a valid persisted task binding when
its URL path changed within the same website. An X publisher navigates from /home
to its profile during reconciliation, so the next startup could allocate another
/home tab. Adoption now prefers the persisted task tab on the same origin; the
existing browser/account checks and account-scoped tab inventory still apply.
A different website is not reused. Ten repeated adoptions after a path change
allocate no new tab and leave the separate home tab untouched. The provider and
Harness suites passed 67 tests; the account-isolation and session-lifetime suites
passed another 7 tests.

## Recovery validation beginning at 18:42

The earlier three-request GC acceptance did not establish sustained stability.
Further local browser OOMs occurred at 15:59, 16:01, 16:20 and 16:38. Ferro had a
separate local cgroup OOM at 16:25. At 18:47, daily publication was 14/20, with 28
consecutive attempts producing no publication and the bound model renderer
failing read-only JavaScript. No new host boot was observed in this interval.

The browser budget is now persistently 2048 MiB, with swap still 512 MiB and
MemoryHigh disabled. This bounded increase alone was insufficient: another local
browser OOM at 18:53 brought oom_kill to 8. The exact obsolete, unbound X publisher
target from the earlier 15:07 recovery was closed. Its renderer subsequently
exited, releasing roughly 750 MiB of resident plus swapped anonymous memory.
The current publisher and its verified /caini56006962 identity were retained.

The dead model target was restored to the same journaled conversation without a
model submission or journal alteration. Its stale Harness daemon was reconnected
without stopping Chrome. Runtime d9927a9 adds one metadata-guarded owned-renderer
reload on explicit Runtime.evaluate timeout. A fresh production-conversation
probe returned MODEL_CHANNEL_OK with recovered=false, renderer_reloaded=true,
page_refresh_count=51 and page_garbage_collected=true. The related suites passed
77 tests. There were no further observed OOMs through the 19:15 check.

The real pipeline reached evidence selection but showed "Unknown error / Retry".
One observed UI Retry was durably recorded; no new answer appeared, and the
exact request payload was restored to the composer. Runtime 515a267 introduced
failed-generation recovery. The follow-up strengthens its live URL, fingerprint,
generation, assistant identity and unique control checks, recognizes the
earlier recorded retry, waits for a stable error, and requires exact composer
restoration before settling the failed receipt. Archives are private and
fsynced before pending is cleared; an ambiguous failure preserves pending.
No composer Send is used in this recovery. The expanded suites passed 92 tests.

A bounded, read-only network observation saw preparation, sentinel checks and
conversation GETs return HTTP 200. Model POSTs returned HTTP 200 event streams;
one stream was aborted by the client. This does not identify the abort's cause
or prove an upstream service fault. No headers or message contents were emitted.

Publication remains a separate acceptance criterion. Short successful requests
and memory observations must not be reported as completion of the daily goal.
