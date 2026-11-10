"""ImageReader byte ranges and LZ4 frames, as Odin tar archives keep their images (2026-11-09)."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import filesystem as fs  # noqa: E402
import install as i  # noqa: E402

LZ4 = shutil.which('lz4')


class Ranges(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix='a22x-ranges-', dir=os.environ.get('TMPDIR')))
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.payload = (b'A22x ' * 50000)[:200000] + os.urandom(65536)

    def file(self, data):
        path = self.dir / ('f%d' % len(list(self.dir.iterdir())))
        path.write_bytes(data)
        fd = os.open(path, os.O_RDONLY)
        self.addCleanup(os.close, fd)
        return fd

    def read_all(self, reader):
        out = bytearray()
        try:
            while True:
                block = reader.read(i.CHUNK)
                if not block:
                    break
                out.extend(block)
            reader.finish()
        finally:
            reader.close()
        return bytes(out)

    def code(self, call):
        with self.assertRaises(fs.FilesystemError) as caught:
            call()
        return caught.exception.code

    def test_raw_range_reads_exactly_the_member(self):
        fd = self.file(b'H' * 512 + self.payload + b'T' * 700)
        reader = i.ImageReader(fd, 'none', offset=512, length=len(self.payload))
        self.assertEqual(self.read_all(reader), self.payload)
        self.assertEqual(reader.encoded_hash.hexdigest(), hashlib.sha256(self.payload).hexdigest())
        self.assertEqual(self.code(lambda: i.ImageReader(fd, 'none', offset=512, length=len(self.payload) + 701)), 'SHORT_IMAGE')

    @unittest.skipUnless(LZ4, 'lz4 is not installed on this host')
    def test_lz4_frame_inside_an_archive(self):
        frame = subprocess.run([LZ4, '-c', '-9'], input=self.payload, stdout=subprocess.PIPE, check=True).stdout
        fd = self.file(b'H' * 1024 + frame + b'\0' * 512)
        os.environ['A22X_LZ4'] = LZ4
        i.LZ4 = LZ4
        reader = i.ImageReader(fd, 'lz4', offset=1024, length=len(frame))
        self.assertEqual(self.read_all(reader), self.payload)
        self.assertEqual((reader.encoded_bytes, reader.input_bytes), (len(frame), len(self.payload)))
        self.assertEqual(reader.encoded_hash.hexdigest(), hashlib.sha256(frame).hexdigest())
        self.assertEqual(reader.input_hash.hexdigest(), hashlib.sha256(self.payload).hexdigest())
        # The inspected image names the same bytes as a raw copy would.
        inspected = i.inspect_image(fd, 'lz4', 'raw', len(self.payload), offset=1024, length=len(frame))
        self.assertEqual(inspected['target_sha256'], hashlib.sha256(self.payload).hexdigest())

    @unittest.skipUnless(LZ4, 'lz4 is not installed on this host')
    def test_lz4_damage_trailing_bytes_and_expansion_bound(self):
        i.LZ4 = LZ4
        frame = subprocess.run([LZ4, '-c'], input=self.payload, stdout=subprocess.PIPE, check=True).stdout
        damaged = bytearray(frame)
        damaged[len(damaged) // 2] ^= 0xFF
        fd = self.file(bytes(damaged))
        self.assertIn(self.code(lambda: self.read_all(i.ImageReader(fd, 'lz4'))), ('INVALID_LZ4', 'IMAGE_EXPANSION_LIMIT'))
        fd = self.file(frame + b'not lz4 at all')
        self.assertEqual(self.code(lambda: self.read_all(i.ImageReader(fd, 'lz4'))), 'INVALID_LZ4')
        fd = self.file(frame)
        self.assertEqual(self.code(lambda: self.read_all(i.ImageReader(fd, 'lz4', maximum=len(frame) + 10))),
                         'IMAGE_EXPANSION_LIMIT')

    def test_unknown_compression_is_refused(self):
        fd = self.file(self.payload)
        self.assertEqual(self.code(lambda: i.ImageReader(fd, 'xz')), 'UNSUPPORTED_COMPRESSION')


if __name__ == '__main__':
    unittest.main()
