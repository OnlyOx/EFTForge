"""A non-blocking counting semaphore shared by every Gunicorn worker process.

Kept out of main.py so the tests can exercise it on CI without a synced tarkov.db.
"""

import os
import threading

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None


class ServerSemaphore:
    """One flock'd slot file per permit. The kernel drops a flock when its holder
    dies, so a worker killed mid-solve can't leak a permit the way an O_EXCL lock
    file would. Slots are interchangeable, so release() frees any one this process
    holds, which lets a caller release from a different thread than it acquired on."""

    def __init__(self, value: int, lock_dir: str, name: str):
        self._paths = [os.path.join(lock_dir, f"{name}_{i}.flock") for i in range(value)]
        self._held: list[int] = []
        self._held_lock = threading.Lock()

    def acquire(self, blocking: bool = False) -> bool:
        assert not blocking, "only non-blocking acquire is supported"
        for path in self._paths:
            # os.open fds are non-inheritable, so spawned solver children never hold the lock.
            fd = os.open(path, os.O_CREAT | os.O_RDWR)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                os.close(fd)
                continue
            with self._held_lock:
                self._held.append(fd)
            return True
        return False

    def release(self) -> None:
        with self._held_lock:
            if not self._held:
                raise ValueError("semaphore released too many times")
            fd = self._held.pop()
        os.close(fd)  # closing the only descriptor drops the flock


def server_semaphore(value: int, lock_dir: str, name: str):
    """Windows (dev and the desktop app) has no flock, but it also only runs one
    process, where a plain semaphore is already server-wide."""
    if fcntl is None:
        return threading.BoundedSemaphore(value)
    return ServerSemaphore(value, lock_dir, name)
