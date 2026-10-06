"""Tests for the server-wide solve cap (server_semaphore.py). The flock path only
exists on POSIX, which is what prod and CI run; Windows falls back to a plain
in-process semaphore and skips the flock-specific tests."""

import multiprocessing
import threading

import pytest

from server_semaphore import ServerSemaphore, fcntl, server_semaphore

posix_only = pytest.mark.skipif(fcntl is None, reason="flock is POSIX only")


def _hold_one_permit(lock_dir, acquired, release):
    sem = ServerSemaphore(2, lock_dir, "slot")
    assert sem.acquire()
    acquired.set()
    release.wait(timeout=10)
    sem.release()


def _die_holding_a_permit(lock_dir):
    sem = ServerSemaphore(1, lock_dir, "slot")
    assert sem.acquire()
    # exit without releasing, like a worker killed mid-solve


@posix_only
def test_permits_are_shared_across_processes(tmp_path):
    ctx = multiprocessing.get_context("spawn")
    acquired, release = ctx.Event(), ctx.Event()
    other = ctx.Process(target=_hold_one_permit, args=(str(tmp_path), acquired, release))
    other.start()
    try:
        assert acquired.wait(timeout=10)
        # Each worker builds its own instance, so this one only sees the other's flock.
        sem = ServerSemaphore(2, str(tmp_path), "slot")
        assert sem.acquire()
        assert not sem.acquire()
        sem.release()
    finally:
        release.set()
        other.join(timeout=10)


@posix_only
def test_a_dead_holder_frees_its_permit(tmp_path):
    ctx = multiprocessing.get_context("spawn")
    proc = ctx.Process(target=_die_holding_a_permit, args=(str(tmp_path),))
    proc.start()
    proc.join(timeout=10)
    sem = ServerSemaphore(1, str(tmp_path), "slot")
    assert sem.acquire()
    sem.release()


@posix_only
def test_release_from_another_thread(tmp_path):
    sem = ServerSemaphore(1, str(tmp_path), "slot")
    assert sem.acquire()
    t = threading.Thread(target=sem.release)
    t.start()
    t.join(timeout=5)
    assert sem.acquire()
    sem.release()
    with pytest.raises(ValueError):
        sem.release()


def test_cap_holds_within_one_process(tmp_path):
    sem = server_semaphore(2, str(tmp_path), "slot")
    assert sem.acquire(blocking=False)
    assert sem.acquire(blocking=False)
    assert not sem.acquire(blocking=False)
    sem.release()
    assert sem.acquire(blocking=False)
    sem.release()
    sem.release()
