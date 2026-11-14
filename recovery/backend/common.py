"""Small shared primitives. No device operations happen on import."""
from __future__ import annotations

import ctypes
import errno
import hashlib
import json
import os
import re
import stat
import time
from pathlib import Path
from typing import Any

CHUNK = 1024 * 1024
RESERVE = 16 * 1024 * 1024
LABELS = frozenset({"boot", "recovery", "metadata", "userdata"})
RETAINED_LABELS = frozenset({"super", "vbmeta", "tee1", "tzar", "dtbo"})
PROFILES = {
    "linux": ("boot", "userdata"),
    "android": ("boot", "metadata", "userdata"),
    "recovery": ("recovery",),
}
PROFILE_NAMES = {
    "linux": "Linux boot and root filesystem",
    "android": "Android boot and encrypted data; retained firmware required",
    "recovery": "Recovery image only",
}


class RecoveryError(Exception):
    def __init__(self, code: str, message: str, **details: Any):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details


def fail(code: str, message: str, **details: Any):
    raise RecoveryError(code, message, **details)


def canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      allow_nan=False).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()


def utc() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            fail("INVALID_JSON", "Duplicate JSON key", key=key)
        result[key] = value
    return result


def open_regular_fd(path: Path, *, write=False) -> int:
    """Pin the object with O_PATH, prove regular-file type, then open that FD.

    A swapped pathname, FIFO or device node cannot turn a fixture restore into
    a block write. /proc/self/fd here refers only to our own validated handle.
    """
    handle = os.open(path, os.O_PATH | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        original = os.fstat(handle)
        if not stat.S_ISREG(original.st_mode):
            fail("REGULAR_FILE_REQUIRED", "A regular file is required", path=str(path))
        fd = os.open(f"/proc/self/fd/{handle}", (os.O_RDWR if write else os.O_RDONLY) | os.O_CLOEXEC)
        actual = os.fstat(fd)
        if (actual.st_dev, actual.st_ino) != (original.st_dev, original.st_ino):
            os.close(fd)
            fail("FILE_IDENTITY_CHANGED", "File descriptor identity changed")
        return fd
    finally:
        os.close(handle)


def read_json_with_hash(path: Path, maximum: int = 1024 * 1024):
    """Parse and hash the same bounded bytes from one pinned regular file."""
    fd = open_regular_fd(path)
    try:
        before = stamp(fd)
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_size > maximum:
            fail("INVALID_JSON", "JSON input must be a bounded regular file", path=str(path))
        with os.fdopen(fd, "rb", closefd=False) as stream:
            raw = stream.read(maximum + 1)
        if len(raw) > maximum:
            fail("INVALID_JSON", "JSON input is too large", path=str(path))
        if len(raw) != st.st_size or stamp(fd) != before:
            fail("JSON_CHANGED", "JSON input changed while being read", path=str(path))
        value = json.loads(raw, object_pairs_hook=strict_object)
        return value, hashlib.sha256(raw).hexdigest()
    except (UnicodeError, json.JSONDecodeError) as exc:
        fail("INVALID_JSON", str(exc), path=str(path))
    finally:
        os.close(fd)


def read_json(path: Path, maximum: int = 1024 * 1024):
    return read_json_with_hash(path, maximum)[0]


def no_symlinks(path: Path, *, must_exist: bool = True) -> Path:
    path = Path(os.path.abspath(path))
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current /= part
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            if must_exist:
                fail("PATH_MISSING", "Required path does not exist", path=str(current))
            continue
        if stat.S_ISLNK(mode):
            fail("SYMLINK_REJECTED", "Symlinks are not allowed for archive/fixture paths",
                 path=str(current))
    return path


def beneath(path: Path, root: Path, *, must_exist: bool = True) -> Path:
    path = no_symlinks(path, must_exist=must_exist)
    if path != root and root not in path.parents:
        fail("PATH_OUTSIDE_ROOT", "Path is outside the admitted root", path=str(path))
    return path


def validate_name(name: str):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", name or ""):
        fail("INVALID_NAME", "Use 1–64 ASCII letters, digits, dots, underscores or hyphens")


def require_hash(value, field="sha256"):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        fail("INVALID_MANIFEST", "Expected a SHA-256 value", field=field)


def stamp(fd: int) -> dict:
    st = os.fstat(fd)
    return {"device": st.st_dev, "inode": st.st_ino, "rdev": st.st_rdev,
            "size": st.st_size, "mtime_ns": st.st_mtime_ns, "ctime_ns": st.st_ctime_ns}


def same_archive(one, other):
    """Whether two stamps of an archive file name the same unchanged file, read again later. exFAT
    numbers an inode only while it stays cached, so a long read in between gave a closed archive a
    new inode and a valid plan PLAN_STALE (2026-11-13 review); the content is checked against the
    manifest anyway, and size and times still change with any write."""
    if not isinstance(one, dict) or not isinstance(other, dict):
        return one == other
    return {k: v for k, v in one.items() if k != "inode"} == {k: v for k, v in other.items() if k != "inode"}


def directory_identity(st, filesystem):
    """What names a directory between two runs: exFAT numbers an inode only while it stays cached."""
    return {"device": st.st_dev, "inode": None if filesystem == "exfat" else st.st_ino}


def fsync_directory(path: Path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_json_new(path: Path, data: Any):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    try:
        with os.fdopen(fd, "wb", closefd=False) as stream:
            stream.write(canonical(data) + b"\n")
            stream.flush()
        os.fsync(fd)
    finally:
        os.close(fd)
    fsync_directory(path.parent)


def rename_noreplace(source: Path, destination: Path):
    """Publish a whole backup directory without replacing an existing one."""
    libc = ctypes.CDLL(None, use_errno=True)
    function = getattr(libc, "renameat2", None)
    if function is None:
        fail("ATOMIC_PUBLISH_UNAVAILABLE", "renameat2(RENAME_NOREPLACE) is required")
    function.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p,
                         ctypes.c_uint]
    function.restype = ctypes.c_int
    result = function(-100, os.fsencode(source), -100, os.fsencode(destination), 1)
    if result:
        code = ctypes.get_errno()
        if code == errno.EEXIST:
            fail("BACKUP_EXISTS", "A backup with this name already exists",
                 path=str(destination))
        raise OSError(code, os.strerror(code), str(destination))
    fsync_directory(destination.parent)


def hash_file(path: Path, *, regular_only=False) -> tuple[str, int]:
    fd = open_regular_fd(path) if regular_only else os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    hasher = hashlib.sha256()
    count = 0
    try:
        with os.fdopen(fd, "rb", closefd=False) as stream:
            while True:
                block = stream.read(CHUNK)
                if not block:
                    break
                count += len(block)
                hasher.update(block)
    finally:
        os.close(fd)
    return hasher.hexdigest(), count
