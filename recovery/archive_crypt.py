#!/usr/bin/python3
# SPDX-License-Identifier: MIT
"""Backups protected with a password (2026-11-13).

A copy on a card holds what its owner would not hand to whoever finds the card: password hashes,
SSH keys, Wi-Fi keys, the phone's identity partitions. With a password, the archive is encrypted:

    b'Salted__' salt(8) | AES-256-CTR( the archive as it would be without a password ) |
    HMAC-SHA-256(32) over all before it | check(8) | plain length u64 LE | b'a22xenc1'

The key and the counter come from PBKDF2-HMAC-SHA-256 (600 000 rounds) of the password and the
salt, exactly as `openssl enc -aes-256-ctr -pbkdf2 -iter 600000 -md sha256` derives them, so the
archive opens without this recovery, on any computer:

    head -c -56 NAME.tar.zst.enc | openssl enc -d -aes-256-ctr -pbkdf2 -iter 600000 -md sha256 | tar --zstd -x

The HMAC (its key derived from the cipher key) says whether one byte was changed; the check value
says at once whether the password is the right one. The cipher runs in the kernel (AF_ALG, with
the CPU's AES instructions); Python only moves the bytes. Counter mode lets the end of an archive
be read without the rest: the manifest kept there.
"""
import contextlib
import hashlib
import hmac
import os
import socket
import struct
import threading

HEAD = b'Salted__'
MAGIC = b'a22xenc1'
SUFFIX = '.enc'
ROUNDS = 600000
TRAILER = 56
CHUNK = 1 << 16
PASSWORD_LIMIT = 256


class CryptError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def read_password(path):
    """The password from the file the interface wrote into its private RAM folder; None without one."""
    if not path:
        return None
    with open(path, 'rb') as f:
        raw = f.read(PASSWORD_LIMIT * 4 + 2)
    text = raw.decode('utf-8').rstrip('\n')
    if not text:
        return None
    if len(text) > PASSWORD_LIMIT:
        raise CryptError('INVALID_PASSWORD', 'The password is too long')
    return text


class Key:
    def __init__(self, password, salt):
        material = hashlib.pbkdf2_hmac('sha256', password.encode('utf-8'), salt, ROUNDS, 48)
        self.salt, self.key, self.iv = salt, material[:32], int.from_bytes(material[32:], 'big')
        self.mac = hmac.new(self.key, b'a22x archive mac', hashlib.sha256).digest()
        self.check = hmac.new(self.mac, b'a22x password check', hashlib.sha256).digest()[:8]
        self.alg = socket.socket(socket.AF_ALG, socket.SOCK_SEQPACKET, 0)
        self.alg.bind(('skcipher', 'ctr(aes)'))
        self.alg.setsockopt(socket.SOL_ALG, socket.ALG_SET_KEY, self.key)

    def close(self):
        self.alg.close()

    def crypt(self, data, offset):
        """DATA as it lies OFFSET bytes into the stream (a multiple of 16): CTR is its own inverse."""
        out, view, at = [], memoryview(data), 0
        while at < len(view):
            part = view[at:at + CHUNK]
            counter = ((self.iv + (offset + at) // 16) % (1 << 128)).to_bytes(16, 'big')
            op, _ = self.alg.accept()
            try:
                sent = op.sendmsg_afalg([part], op=socket.ALG_OP_ENCRYPT, iv=counter)
                got = b''
                while len(got) < sent:
                    more = op.recv(sent - len(got))
                    if not more:
                        raise CryptError('CRYPTO_FAILED', 'The kernel cipher returned too little')
                    got += more
            finally:
                op.close()
            if sent % 16 and at + sent < len(view):
                raise CryptError('CRYPTO_FAILED', 'The kernel cipher took a partial block')
            out.append(got)
            at += sent
        return b''.join(out)


def write_all(fd, data):
    view = memoryview(data)
    while view:
        view = view[os.write(fd, view):]


class Encryptor:
    """The plain archive goes in through write(); finish() adds the trailer."""

    def __init__(self, fd, password):
        self.fd, self.key = fd, Key(password, os.urandom(8))
        self.mac = hmac.new(self.key.mac, digestmod=hashlib.sha256)
        self.done, self.rest = 0, b''
        self.put(HEAD + self.key.salt)

    def put(self, data):
        self.mac.update(data)
        write_all(self.fd, data)

    def write(self, data):
        data = self.rest + bytes(data)
        whole = len(data) - len(data) % 16
        self.rest = data[whole:]
        if whole:
            self.put(self.key.crypt(data[:whole], self.done))
            self.done += whole

    def finish(self):
        if self.rest:
            self.put(self.key.crypt(self.rest, self.done))
            self.done += len(self.rest)
            self.rest = b''
        write_all(self.fd, self.mac.digest() + self.key.check + struct.pack('<Q', self.done) + MAGIC)
        os.fsync(self.fd)
        self.key.close()


def is_encrypted(fd):
    size = os.fstat(fd).st_size
    return size >= 16 + TRAILER and os.pread(fd, 8, 0) == HEAD and os.pread(fd, 8, size - 8) == MAGIC


def plain_size(fd):
    size = os.fstat(fd).st_size
    length = struct.unpack('<Q', os.pread(fd, 8, size - 16))[0]
    if length != size - 16 - TRAILER:
        raise CryptError('BACKUP_DAMAGED', 'The encrypted archive is cut short or was changed')
    return length


def open_key(fd, password):
    """The key of the archive at FD for PASSWORD; says so when there is none or it is another one."""
    if not password:
        raise CryptError('PASSWORD_REQUIRED', 'This backup is protected with a password')
    plain_size(fd)
    key = Key(password, os.pread(fd, 8, 8))
    if not hmac.compare_digest(key.check, os.pread(fd, 8, os.fstat(fd).st_size - 24)):
        key.close()
        raise CryptError('WRONG_PASSWORD', 'The password does not open this backup')
    return key


def verify(fd, key, progress=None):
    """Every byte against the HMAC, before anything of the archive is used."""
    size = os.fstat(fd).st_size
    end, at, mac = size - TRAILER, 0, hmac.new(key.mac, digestmod=hashlib.sha256)
    while at < end:
        data = os.pread(fd, min(1 << 20, end - at), at)
        if not data:
            break
        mac.update(data)
        at += len(data)
        if progress:
            progress(at, end)
    if at != end or not hmac.compare_digest(mac.digest(), os.pread(fd, 32, end)):
        raise CryptError('BACKUP_DAMAGED', 'The encrypted archive was changed or is damaged')


def read_tail(fd, key, length):
    """The last LENGTH bytes of the plain archive."""
    total = plain_size(fd)
    length = min(length, total)
    start = total - length
    aligned = start - start % 16
    data = os.pread(fd, total - aligned, 16 + aligned)
    return key.crypt(data, aligned)[start - aligned:]


@contextlib.contextmanager
def sink(fd, password):
    """Where the plain archive is written: FD itself without a password, else a pipe whose other end
    is encrypted into FD. Leaving the block without an error completes the encrypted archive."""
    if not password:
        yield fd
        return
    encryptor = Encryptor(fd, password)
    reader, writer = os.pipe()
    failure = []

    def pump():
        try:
            while True:
                data = os.read(reader, 1 << 20)
                if not data:
                    break
                encryptor.write(data)
        except BaseException as error:  # noqa: BLE001 - handed to the thread that waits
            # The pipe is closed at once, so the writer stops at its next write instead of reading
            # and compressing tens of gigabytes for nothing (2026-11-13 review: a full card).
            failure.append(error)
        finally:
            os.close(reader)

    thread = threading.Thread(target=pump, daemon=True)
    thread.start()
    try:
        yield writer
    except BaseException as error:
        os.close(writer)
        thread.join()
        encryptor.key.close()
        # What failed first is said: the writer's broken pipe only follows from it (2026-11-14 review:
        # a full card or FAT32's 4 GiB came out as a write error without a cause).
        if failure:
            raise failure[0] from error
        raise
    os.close(writer)
    thread.join()
    if failure:
        encryptor.key.close()
        raise failure[0]
    encryptor.finish()


@contextlib.contextmanager
def source(fd, password):
    """The plain archive to read: FD itself when it is not encrypted, else a pipe fed with what is
    decrypted. The HMAC is checked as the bytes pass; a mismatch is raised on leaving the block.
    FD's own position follows the reading, for a progress display."""
    if not is_encrypted(fd):
        os.lseek(fd, 0, os.SEEK_SET)
        yield fd
        return
    key = open_key(fd, password)
    total = plain_size(fd)
    reader, writer = os.pipe()
    state = {'ok': False, 'error': None}

    def pump():
        mac, at = hmac.new(key.mac, digestmod=hashlib.sha256), 0
        mac.update(os.pread(fd, 16, 0))
        try:
            os.lseek(fd, 16, os.SEEK_SET)
            while at < total:
                data = os.read(fd, min(1 << 20, total - at))
                if not data:
                    break
                keep = len(data) - len(data) % 16 if at + len(data) < total else len(data)
                if keep != len(data):
                    os.lseek(fd, keep - len(data), os.SEEK_CUR)
                    data = data[:keep]
                    if not data:
                        continue
                mac.update(data)
                write_all(writer, key.crypt(data, at))
                at += len(data)
            state['ok'] = at == total and hmac.compare_digest(mac.digest(), os.pread(fd, 32, 16 + total))
        except BrokenPipeError:
            state['error'] = 'closed'  # the reader had enough; nothing is claimed about the rest
        except BaseException as error:  # noqa: BLE001
            state['error'] = error
        finally:
            os.close(writer)

    thread = threading.Thread(target=pump, daemon=True)
    thread.start()
    try:
        yield reader
    finally:
        os.close(reader)
        thread.join()
        key.close()
    if isinstance(state['error'], BaseException):
        raise state['error']
    if state['error'] is None and not state['ok']:
        raise CryptError('BACKUP_DAMAGED', 'The encrypted archive was changed or is damaged')
