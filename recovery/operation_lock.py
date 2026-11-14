"""One cooperative lease for recovery UI, backups and authorized SSH sessions."""
import contextlib
import errno
import fcntl
import math
import os
from pathlib import Path
import stat
import threading
import time

ENV_FD = 'A22X_RECOVERY_LOCK_FD'
DEVICE_LOCK = '/run/lock/a22x-mainline-recovery.lock'
# The leases this process holds now, by lock path. A second flock of the same file through a new
# descriptor would be refused even inside the same process, so a step that needs the lease asks
# held() first and then runs inside the caller's lease (filesystem.py close_logical, 2026-11-12).
# Each entry is (the lock's real path, the holding thread object): a lease is lent only within the
# thread that holds it (2026-11-14 reviews: adbd's threads borrowed each other's lease; a thread's
# ident is reused once it ends, its object never is).
_HELD = []
_GUARD = threading.Lock()  # the check for another holder and the record of this one, as one step


def _key(path):
    """The lock's real path, for the record of leases held only: one file under any spelling
    (2026-11-14 reviews: '..' kept in one spelling missed the other; resolved by text it skipped a
    link). operation_lock itself still refuses a path with a link in it."""
    return os.path.realpath(str(path))
# Set by the interface for a job it starts while its screenshot still holds the lease: until when
# (CLOCK_MONOTONIC seconds) the job waits for it, MAX_WAIT at most (2026-11-14 review: a job started a
# second after a screenshot to an unmounted card failed OPERATION_BUSY).
WAIT_ENV, MAX_WAIT = 'A22X_LOCK_WAIT_UNTIL', 30.0


def _wait_until():
    try:
        until = float(os.environ.get(WAIT_ENV, ''))
    except ValueError:
        return 0.0
    return min(until, time.monotonic() + MAX_WAIT) if math.isfinite(until) else 0.0


def held(path=DEVICE_LOCK):
    """Whether this thread holds the lease for PATH now."""
    return (_key(path), threading.current_thread()) in _HELD


def _held_elsewhere(path):
    """Whether another thread of this process holds the lease for PATH now."""
    key, me = _key(path), threading.current_thread()
    return any(entry == key and thread is not me for entry, thread in _HELD)


@contextlib.contextmanager
def lease(path=DEVICE_LOCK, mode='device'):
    """The lease for PATH: this thread's own when it holds it already (a step run inside its caller's
    lease, as distro.py's saved Wi-Fi key), else a new one (2026-11-14 review: a second flock of the
    same file in one process is refused, and the step failed busy every time). Another thread of the
    process never borrows it."""
    if held(path):
        yield None
        return
    with operation_lock(path, mode) as fd:
        yield fd


def _regular(fd, path):
    opened = os.fstat(fd)
    named = os.stat(path, follow_symlinks=False)
    if (not stat.S_ISREG(opened.st_mode) or not stat.S_ISREG(named.st_mode)
            or opened.st_nlink != 1 or named.st_nlink != 1
            or (opened.st_dev, opened.st_ino) != (named.st_dev, named.st_ino)):
        raise OSError(errno.EINVAL, 'Recovery operation lock changed or is not a regular file')


@contextlib.contextmanager
def operation_lock(path, mode='device'):
    """Yield the held FD. Borrowed inherited leases are never explicitly unlocked.

    FD inheritance coordinates trusted root helpers; it is not a security boundary.
    A helper cannot bypass another session's lock by setting the environment: the
    descriptor must name this exact lock, and flock must still succeed.
    """
    path = Path(path).absolute()
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    for part in (path, *path.parents):
        if part.is_symlink():
            raise OSError(errno.ELOOP, 'Recovery lock path may not contain a symlink')
    inherited = os.environ.get(ENV_FD) if mode == 'device' else None
    fd = None
    try:
        if inherited is not None:
            if not inherited.isdecimal() or int(inherited) < 3:
                raise OSError(errno.EINVAL, 'Invalid inherited recovery lock descriptor')
            fd = os.dup(int(inherited))
        else:
            fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        _regular(fd, path)
        until = _wait_until() if inherited is None else 0.0
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= until:
                    raise
                time.sleep(0.05)
        _regular(fd, path)
        mine = (_key(path), threading.current_thread())
        with _GUARD:
            # The lease is this process's: one thread at a time, checked and recorded as one step
            # (2026-11-14 reviews: a second thread duplicated an inherited lease, and its flock on the
            # same open file succeeded; then both passed the check before either was recorded).
            if inherited is not None and _held_elsewhere(path):
                raise BlockingIOError(errno.EWOULDBLOCK, 'Another thread holds the recovery lease')
            _HELD.append(mine)
        try:
            yield fd
        finally:
            with _GUARD:  # never while another thread walks the record (2026-11-14 review)
                _HELD.remove(mine)
    finally:
        if fd is not None:
            # Closing the duplicate does not release a parent's shared open-file
            # description. Explicit LOCK_UN here would break the SSH lease.
            os.close(fd)
