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
- Shared browser: MemoryMax 1792 MiB, swap max 512 MiB; MemoryHigh disabled.
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
