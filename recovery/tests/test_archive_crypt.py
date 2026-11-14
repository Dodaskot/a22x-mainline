"""Archives protected with a password (archive_crypt.py, 2026-11-13)."""
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import archive_crypt as ac  # noqa: E402


def kernel_cipher():
    try:
        s = socket.socket(socket.AF_ALG, socket.SOCK_SEQPACKET, 0)
        s.bind(('skcipher', 'ctr(aes)'))
        s.close()
        return True
    except OSError:
        return False


@unittest.skipUnless(kernel_cipher(), 'the kernel offers no ctr(aes) to this user')
class Crypt(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='a22x-crypt-', dir=os.environ.get('TMPDIR')))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.plain = os.urandom(3 * (1 << 20) + 12345)
        self.path = self.root / 'x.tar.zst.enc'
        self.rounds = ac.ROUNDS
        ac.ROUNDS = 1000  # the tests need no half second per key
        self.addCleanup(setattr, ac, 'ROUNDS', self.rounds)

    def write(self, password='пароль 1'):
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_TRUNC, 0o600)
        with ac.sink(fd, password) as out:
            for at in range(0, len(self.plain), 70001):  # pieces that are no multiple of 16
                ac.write_all(out, self.plain[at:at + 70001])
        return fd

    def test_round_trip_tail_and_refusals(self):
        fd = self.write()
        self.addCleanup(os.close, fd)
        self.assertTrue(ac.is_encrypted(fd))
        self.assertEqual(os.fstat(fd).st_size, len(self.plain) + 16 + ac.TRAILER)
        self.assertNotIn(self.plain[:64], self.path.read_bytes())
        with ac.source(fd, 'пароль 1') as stream:
            got = b''
            while True:
                data = os.read(stream, 1 << 20)
                if not data:
                    break
                got += data
        self.assertEqual(got, self.plain)
        key = ac.open_key(fd, 'пароль 1')
        self.addCleanup(key.close)
        self.assertEqual(ac.read_tail(fd, key, 1001), self.plain[-1001:])
        self.assertEqual(ac.read_tail(fd, key, 1 << 30), self.plain)
        ac.verify(fd, key)
        for password, code in ((None, 'PASSWORD_REQUIRED'), ('', 'PASSWORD_REQUIRED'), ('other', 'WRONG_PASSWORD')):
            with self.assertRaises(ac.CryptError) as caught:
                ac.open_key(fd, password)
            self.assertEqual(caught.exception.code, code)
        # One byte changed in the middle: the check says so, and so does reading it through.
        os.pwrite(fd, bytes([os.pread(fd, 1, 100000)[0] ^ 1]), 100000)
        with self.assertRaises(ac.CryptError) as caught:
            ac.verify(fd, key)
        self.assertEqual(caught.exception.code, 'BACKUP_DAMAGED')
        with self.assertRaises(ac.CryptError) as caught:
            with ac.source(fd, 'пароль 1') as stream:
                while os.read(stream, 1 << 20):
                    pass
        self.assertEqual(caught.exception.code, 'BACKUP_DAMAGED')
        # Cut short: not taken for an encrypted archive's end.
        os.ftruncate(fd, os.fstat(fd).st_size - 100)
        self.assertFalse(ac.is_encrypted(fd))

    def test_a_full_card_stops_the_writer_at_once(self):
        # 2026-11-13 review: after a failed write the rest was still read and compressed.
        import resource
        import signal
        old_handler = signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
        soft, hard = resource.getrlimit(resource.RLIMIT_FSIZE)
        path = Path(tempfile.mkdtemp(dir=os.environ.get('TMPDIR'))) / 'full.enc'
        fd = os.open(path, os.O_WRONLY | os.O_CREAT, 0o600)
        written = 0
        try:
            resource.setrlimit(resource.RLIMIT_FSIZE, (1 << 20, hard))
            with self.assertRaises(OSError) as caught:
                with ac.sink(fd, 'pw') as writer:
                    block = bytes(1 << 16)
                    while written < 64 << 20:
                        written += os.write(writer, block)
        finally:
            resource.setrlimit(resource.RLIMIT_FSIZE, (soft, hard))
            signal.signal(signal.SIGXFSZ, old_handler)
            os.close(fd)
            shutil.rmtree(path.parent)
        self.assertLess(written, 8 << 20)
        # 2026-11-14 review: what failed first is said, not the writer's broken pipe.
        import errno
        self.assertEqual(caught.exception.errno, errno.EFBIG)

    def test_without_a_password_nothing_changes(self):
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT | os.O_TRUNC, 0o600)
        self.addCleanup(os.close, fd)
        with ac.sink(fd, None) as out:
            self.assertEqual(out, fd)
            os.write(out, b'plain')
        self.assertFalse(ac.is_encrypted(fd))
        with ac.source(fd, None) as stream:
            self.assertEqual(os.read(stream, 10), b'plain')

    @unittest.skipUnless(shutil.which('openssl'), 'openssl is needed')
    def test_openssl_opens_it(self):
        ac.ROUNDS = self.rounds  # the real number, as the documented command has it
        os.close(self.write('s3cret pass'))
        body = self.path.read_bytes()[:-ac.TRAILER]
        done = subprocess.run(['openssl', 'enc', '-d', '-aes-256-ctr', '-pbkdf2', '-iter', '600000', '-md', 'sha256',
                               '-pass', 'pass:s3cret pass'], input=body, capture_output=True, check=True)
        self.assertEqual(done.stdout, self.plain)

    def test_password_file(self):
        path = self.root / 'pw'
        path.write_text('héslo\n')
        self.assertEqual(ac.read_password(path), 'héslo')
        path.write_text('')
        self.assertIsNone(ac.read_password(path))
        self.assertIsNone(ac.read_password(None))


if __name__ == '__main__':
    unittest.main()
