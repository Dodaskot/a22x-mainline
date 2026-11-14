"""The archive of a copy of Android's partitions (partition_archive.py), 2026-11-14."""
import os
from pathlib import Path
import resource
import shutil
import signal
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import partition_archive as pa  # noqa: E402


@unittest.skipUnless(shutil.which('zstd'), 'zstd is needed')
class EncryptedWriter(unittest.TestCase):
    def test_a_file_too_large_is_said_as_such_and_not_as_a_broken_pipe(self):
        # 2026-11-14 review: the encryption's own error (EFBIG, ENOSPC) was lost behind zstd's EPIPE.
        folder = Path(tempfile.mkdtemp(dir=os.environ.get('TMPDIR')))
        self.addCleanup(shutil.rmtree, folder, True)
        self.addCleanup(lambda: (setattr(pa, 'PASSWORD', None), setattr(pa, 'ENCRYPT', False)))
        pa.PASSWORD, pa.ENCRYPT = 'secret 77', True
        old_handler = signal.signal(signal.SIGXFSZ, signal.SIG_IGN)
        self.addCleanup(signal.signal, signal.SIGXFSZ, old_handler)
        soft, hard = resource.getrlimit(resource.RLIMIT_FSIZE)
        self.addCleanup(resource.setrlimit, resource.RLIMIT_FSIZE, (soft, hard))
        fd = os.open(folder / 'copy.tar.zst.enc', os.O_WRONLY | os.O_CREAT, 0o600)
        self.addCleanup(os.close, fd)
        resource.setrlimit(resource.RLIMIT_FSIZE, (1 << 20, hard))
        writer = pa.Writer(fd)
        size = 16 << 20
        with self.assertRaises(pa.ArchiveError) as caught:
            writer.begin('boot.img', size)
            block = os.urandom(1 << 20)
            for _ in range(16):
                writer.write(block)
            writer.finish(b'{}')
        self.assertEqual(caught.exception.code, 'FILE_TOO_LARGE')


if __name__ == '__main__':
    unittest.main()
