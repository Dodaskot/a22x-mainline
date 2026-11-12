#!/usr/bin/python3
# SPDX-License-Identifier: MIT
"""One compressed archive of partition images (2026-11-11): NAME.tar.zst.

A tar of LABEL.img files and, last, manifest.json, compressed by zstd on every core. After the
zstd stream comes one skippable frame that holds the manifest again and a trailer, so a list of
copies reads a few kilobytes instead of unpacking gigabytes. zstd skips that frame, and so
`tar --zstd -xf NAME.tar.zst` unpacks a copy on any computer.

    zstd( tar: boot.img, dtbo.img, ..., super.img, manifest.json )
    skippable frame 0x184D2A5E: manifest.json | u64 its length | b'a22xtar1'

Nothing of an archive is ever written to a path it names: the members are only read, in the
order they come, by the caller that knows the manifest.
"""
import contextlib
import fcntl
import os
import shutil
import struct
import subprocess
import tarfile
import tempfile
import threading
import time

SUFFIX = '.tar.zst'
SKIPPABLE = 0x184D2A5E
TRAILER = b'a22xtar1'
MAX_MANIFEST = 1 << 16
LEVEL = 3
PIPE_BYTES = 1 << 20
F_SETPIPE_SZ = 1031


class ArchiveError(ValueError):
    """A failure with the code a helper reports."""

    def __init__(self, code, message, said=''):
        super().__init__(message)
        self.code, self.details = code, ({'said': said} if said else {})


def zstd_path():
    found = shutil.which('zstd') or next((p for p in ('/usr/bin/zstd', '/bin/zstd') if os.access(p, os.X_OK)), None)
    if not found:
        raise ArchiveError('ZSTD_UNAVAILABLE', 'This recovery has no zstd to pack or unpack the archive')
    return found


def write_all(fd, data):
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view):]


def said(errors):
    """The last line zstd wrote about its failure."""
    with contextlib.suppress(OSError, ValueError):
        errors.seek(0)
        lines = errors.read(4096).decode('utf-8', 'replace').strip().splitlines()
        return lines[-1][:200] if lines else ''
    return ''


def write_failure(text):
    low = text.lower()
    if 'no space' in low:
        return ArchiveError('NO_SPACE', 'The volume ran out of space while the archive was written')
    if 'too large' in low:
        return ArchiveError('FILE_TOO_LARGE', 'This volume cannot hold a file this large: FAT32 stops at 4 GiB. Use exFAT or ext4, or leave super out')
    return ArchiveError('ARCHIVE_WRITE_FAILED', 'The archive could not be written', text)


def packed_bytes(pieces):
    """How many bytes zstd makes of PIECES at the archive's level: what an estimate of an archive's
    size is counted from."""
    errors = tempfile.TemporaryFile()
    proc = subprocess.Popen([zstd_path(), '-q', '-%d' % LEVEL, '-T0', '-c'], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=errors, bufsize=0)
    count = [0]

    def drain():
        while True:
            data = proc.stdout.read(PIPE_BYTES)
            if not data:
                break
            count[0] += len(data)

    reader = threading.Thread(target=drain, daemon=True)
    reader.start()
    try:
        for data in pieces:
            write_all(proc.stdin.fileno(), data)
        proc.stdin.close()
        reader.join()
        if proc.wait() != 0:
            raise ArchiveError('ARCHIVE_WRITE_FAILED', 'The size of the archive could not be estimated', said(errors))
        return count[0]
    except BaseException:
        with contextlib.suppress(OSError, ValueError):
            proc.stdin.close()
        if proc.poll() is None:
            proc.kill()
        proc.wait()
        reader.join()
        raise
    finally:
        proc.stdout.close()
        errors.close()


class Writer:
    """Members go in one after another; finish() adds the manifest and the footer."""

    def __init__(self, fd, threads=0):
        self.fd, self.left, self.pad = fd, 0, 0
        self.errors = tempfile.TemporaryFile()
        self.proc = subprocess.Popen([zstd_path(), '-q', '-%d' % LEVEL, '-T%d' % threads, '-c'], stdin=subprocess.PIPE,
                                     stdout=fd, stderr=self.errors, bufsize=0)
        self.pipe = self.proc.stdin.fileno()
        with contextlib.suppress(OSError):
            fcntl.fcntl(self.pipe, F_SETPIPE_SZ, PIPE_BYTES)

    def put(self, data):
        try:
            write_all(self.pipe, data)
        except OSError:
            self.abort()
            raise write_failure(said(self.errors)) from None

    def begin(self, name, size):
        if self.left or self.pad:
            raise ArchiveError('ARCHIVE_WRITE_FAILED', 'The member before this one is not complete')
        info = tarfile.TarInfo(name)
        info.size, info.mtime, info.mode = size, int(time.time()), 0o600
        self.put(info.tobuf(tarfile.PAX_FORMAT))
        self.left, self.pad = size, -size % 512

    def write(self, data):
        if len(data) > self.left:
            raise ArchiveError('ARCHIVE_WRITE_FAILED', 'A member got more bytes than its size')
        self.put(data)
        self.left -= len(data)
        if not self.left and self.pad:
            self.put(bytes(self.pad))
            self.pad = 0

    def finish(self, manifest):
        """The manifest as the last member, the end of the tar, and the footer after zstd's stream."""
        self.begin('manifest.json', len(manifest))
        self.write(manifest)
        self.put(bytes(1024))
        self.proc.stdin.close()
        if self.proc.wait() != 0:
            raise write_failure(said(self.errors))
        try:
            # zstd wrote through the same open file, so its end is where the footer goes.
            os.lseek(self.fd, 0, os.SEEK_END)
            payload = manifest + struct.pack('<Q', len(manifest)) + TRAILER
            write_all(self.fd, struct.pack('<II', SKIPPABLE, len(payload)) + payload)
            os.fsync(self.fd)
        except OSError as error:
            raise write_failure(os.strerror(error.errno)) from None
        finally:
            self.errors.close()

    def abort(self):
        with contextlib.suppress(OSError, ValueError):
            self.proc.stdin.close()
        if self.proc.poll() is None:
            self.proc.kill()
        self.proc.wait()


def footer(fd):
    """The manifest kept after the zstd stream: its bytes, or None when the file has no such footer."""
    size = os.fstat(fd).st_size
    if size < 24:
        return None
    length, magic = struct.unpack('<Q8s', os.pread(fd, 16, size - 16))
    if magic != TRAILER or not 0 < length <= MAX_MANIFEST or size < 24 + length:
        return None
    if struct.unpack('<II', os.pread(fd, 8, size - 24 - length)) != (SKIPPABLE, length + 16):
        return None
    data = os.pread(fd, length, size - 16 - length)
    return data if len(data) == length else None


@contextlib.contextmanager
def members(fd):
    """The files of the archive in the order they come: yields an iterator of (name, size, stream),
    where stream.read(n) gives the next bytes of that file. Leaving the block checks that zstd
    unpacked every frame and its checksum; a damaged archive raises BACKUP_DAMAGED."""
    os.lseek(fd, 0, os.SEEK_SET)
    errors = tempfile.TemporaryFile()
    proc = subprocess.Popen([zstd_path(), '-q', '-d', '-c'], stdin=fd, stdout=subprocess.PIPE, stderr=errors, bufsize=PIPE_BYTES)
    with contextlib.suppress(OSError):
        fcntl.fcntl(proc.stdout.fileno(), F_SETPIPE_SZ, PIPE_BYTES)
    finished = False

    def walk():
        nonlocal finished
        try:
            with tarfile.open(fileobj=proc.stdout, mode='r|', bufsize=PIPE_BYTES) as tar:
                for info in tar:
                    if not info.isreg():
                        raise ArchiveError('BACKUP_DAMAGED', 'The archive holds something that is not a plain file')
                    yield info.name, info.size, tar.extractfile(info)
            finished = True
        except (tarfile.TarError, EOFError) as error:
            raise ArchiveError('BACKUP_DAMAGED', 'The archive is damaged', said(errors) or str(error)[:120]) from None

    try:
        yield walk()
        if finished:
            # What follows the tar is nothing; zstd then ends, and says whether every checksum held.
            while proc.stdout.read(PIPE_BYTES):
                pass
    except BaseException:
        proc.kill()
        raise
    finally:
        if not finished and proc.poll() is None:
            proc.kill()
        proc.stdout.close()
        code = proc.wait()
        text = said(errors)
        errors.close()
    if not finished:
        raise ArchiveError('BACKUP_DAMAGED', 'The archive was not read to its end')
    if code != 0:
        raise ArchiveError('BACKUP_DAMAGED', 'The archive is damaged', text)
