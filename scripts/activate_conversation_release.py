"""Atomically activate a prepared release, rejecting a concurrent deployment."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import uuid


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release", type=Path, required=True)
    parser.add_argument("--expected-current", type=Path, required=True)
    parser.add_argument("--commit", required=True)
    args = parser.parse_args()
    current = Path.home() / "agent-runtime" / "current"
    release = args.release.resolve(strict=True)
    actual_commit = subprocess.check_output(
        ["git", "-C", str(release), "rev-parse", "HEAD"], text=True,
    ).strip()
    if actual_commit != args.commit:
        raise RuntimeError("Prepared release commit does not match")
    previous = current.resolve(strict=True)
    if not current.is_symlink() or previous != args.expected_current.resolve(strict=True):
        raise RuntimeError("Current release changed; refusing to replace a concurrent deployment")
    temporary = current.with_name("current-conversation-" + uuid.uuid4().hex)
    temporary.symlink_to(release, target_is_directory=True)
    try:
        os.replace(temporary, current)
    finally:
        temporary.unlink(missing_ok=True)
    receipt = {"commit": actual_commit, "previous_release": str(previous),
               "current_release": str(current.resolve(strict=True))}
    (release / "conversation-deployment.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt))


if __name__ == "__main__":
    main()
