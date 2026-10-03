"""Bounded, credential-free memory evidence for recurring host stalls."""
from collections import defaultdict
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import os

ROOT = Path.home() / ".local/state/agent-runtime/host-health"
ROOT.mkdir(parents=True, exist_ok=True)
os.chmod(ROOT, 0o700)
row = {"at": datetime.now(timezone.utc).isoformat(),
       "boot_id": Path("/proc/sys/kernel/random/boot_id").read_text().strip(),
       "load": Path("/proc/loadavg").read_text().strip()}
row["memory_kb"] = {m[1]: int(m[2]) for m in re.finditer(
    r"^(MemTotal|MemAvailable|SwapTotal|SwapFree|AnonPages|SReclaimable|SUnreclaim):\s+(\d+)",
    Path("/proc/meminfo").read_text(), re.M)}
row["pressure"] = {name: Path("/proc/pressure", name).read_text().strip()
                   for name in ["memory", "io", "cpu"]}
groups = defaultdict(lambda: {"rss_kb": 0, "swap_kb": 0, "processes": 0})
for proc in Path("/proc").glob("[0-9]*"):
    try:
        status = (proc / "status").read_text()
        rss = re.search(r"VmRSS:\s+(\d+)", status)
        if rss is None:
            continue
        swap = re.search(r"VmSwap:\s+(\d+)", status)
        group = (proc / "cgroup").read_text().strip().split("::", 1)[-1]
        groups[group]["rss_kb"] += int(rss[1])
        groups[group]["swap_kb"] += int(swap[1]) if swap else 0
        groups[group]["processes"] += 1
    except (FileNotFoundError, ProcessLookupError):
        continue  # Process exited during this snapshot.
    except PermissionError:
        row["restricted_processes"] = row.get("restricted_processes", 0) + 1
for group, values in groups.items():
    for name in ["memory.current", "memory.swap.current", "memory.events"]:
        file = Path("/sys/fs/cgroup" + group) / name
        if file.exists():
            values[name] = file.read_text().strip()
row["groups"] = dict(groups)
file = ROOT / "memory.jsonl"
if file.exists() and file.stat().st_size >= 8 * 1024 * 1024:
    os.replace(file, ROOT / "memory.previous.jsonl")
with file.open("a") as out:
    os.chmod(file, 0o600)
    out.write(json.dumps(row, separators=(",", ":")) + "\n")
    out.flush()
    os.fsync(out.fileno())
print(json.dumps({"at": row["at"], "memory_kb": row["memory_kb"], "groups": len(groups)}))
