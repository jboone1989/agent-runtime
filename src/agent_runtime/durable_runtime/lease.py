"""Host lease: Pi storage requires a single process owner."""
from contextlib import contextmanager
import os
from pathlib import Path

from .models import RuntimeBusyError


@contextmanager
def execution_lease(root: Path):
    root.mkdir(parents=True, exist_ok=True)
    # Keep the file: deleting it can allow two owners to lock different inodes.
    with (root / "pi-owner.lock").open("a+b") as handle:
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            raise RuntimeBusyError("Pi Durable storage already has an owner") from exc
        try:
            yield handle.fileno()
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)
