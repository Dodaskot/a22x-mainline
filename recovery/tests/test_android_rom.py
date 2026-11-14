"""Android ROM packages read as data (android_rom.py, 2026-11-10): synthetic payloads, image ZIPs,
block-based OTAs and GSIs. Nothing from a package is run, and the boot chain is refused."""
import bz2
import hashlib
import io
import json
import lzma
import os
from pathlib import Path
import shutil
import struct
import sys
import tempfile
import unittest
from unittest import mock
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import android_rom as rom  # noqa: E402

BLOCK = 4096


def key(number, wire):
    return encode_varint(number << 3 | wire)


def encode_varint(value):
    out = bytearray()
    while True:
        byte = value & 0x7f
        value >>= 7
        out.append(byte | (0x80 if value else 0))
        if not value:
            return bytes(out)


def field(number, value):
    if isinstance(value, int):
        return key(number, 0) + encode_varint(value)
    return key(number, 2) + encode_varint(len(value)) + value


def extent(start, blocks):
    return field(1, start) + field(2, blocks)


def operation(kind, offset, data, start, blocks, digest=True):
    body = field(1, kind) + field(2, offset) + field(3, len(data)) + field(6, extent(start, blocks))
    if digest and data:
        body += field(8, hashlib.sha256(data).digest())
    return body


def payload(partitions, groups=(), incremental=(), partial=False):
    """A version 2 payload.bin: PARTITIONS is [(name, content, how)], HOW 'replace', 'xz', 'bz2' or 'zero'."""
    blobs, parts = bytearray(), []
    for name, content, how in partitions:
        blocks = len(content) // BLOCK
        if how == 'zero':
            ops = operation(rom.ZERO, 0, b'', 0, blocks, digest=False)
        else:
            data = {'replace': content, 'xz': lzma.compress(content), 'bz2': bz2.compress(content)}[how]
            kind = {'replace': rom.REPLACE, 'xz': rom.REPLACE_XZ, 'bz2': rom.REPLACE_BZ}[how]
            ops = operation(kind, len(blobs), data, 0, blocks)
            blobs += data
        info = field(1, len(content)) + field(2, hashlib.sha256(content).digest())
        part = field(1, name.encode()) + field(7, info) + field(8, ops)
        if name in incremental:
            part += field(6, field(1, len(content)))
        parts.append(part)
    manifest = field(3, BLOCK) + b''.join(field(13, p) for p in parts)
    if groups:
        dynamic = b''.join(field(1, field(1, g.encode()) + field(2, size) + b''.join(field(3, n.encode()) for n in names))
                           for g, size, names in groups)
        manifest += field(15, dynamic)  # dynamic_partition_metadata (update_metadata.proto)
    if partial:
        manifest += field(16, 1)  # partial_update
    head = b'CrAU' + struct.pack('>QQI', 2, len(manifest), 0)
    return head + manifest + bytes(blobs)


class Packages(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='a22x-rom-', dir=os.environ.get('TMPDIR')))
        self.addCleanup(shutil.rmtree, self.root, True)

    def zip(self, name, files, stored=('payload.bin',)):
        path = self.root / name
        with zipfile.ZipFile(path, 'w') as z:
            for member, data in files.items():
                z.writestr(member, data, compress_type=zipfile.ZIP_STORED if member in stored else zipfile.ZIP_DEFLATED)
        return path

    def test_a_full_payload_is_read_and_extracted(self):
        boot = os.urandom(3 * BLOCK)
        system = (b'android system ' * 3000)[:8 * BLOCK]  # whole blocks, as payloads always are
        vendor = bytes(2 * BLOCK)
        data = payload([('boot', boot, 'replace'), ('system', system, 'xz'), ('vendor', vendor, 'zero'),
                        ('lk', os.urandom(BLOCK), 'bz2'), ('md1img', os.urandom(BLOCK), 'replace'), ('recovery', os.urandom(BLOCK), 'replace')],
                       groups=[('main', 6900 << 20, ['system', 'vendor'])])
        path = self.zip('rom.zip', {'payload.bin': data, 'META-INF/com/android/metadata': 'pre-device=a22x\npost-sdk-level=34\nota-type=AB\n',
                                    'META-INF/com/google/android/update-binary': '#!/sbin/sh\nexit 1\n'})
        info = rom.inspect(path)
        self.assertEqual((info['format'], info['device_ok']), ('payload', True))
        targets = {p['name']: p['target'] for p in info['partitions']}
        self.assertEqual(targets, {'boot': 'physical', 'system': 'logical', 'vendor': 'logical', 'lk': 'refused',
                                   'md1img': 'refused', 'recovery': 'refused'})
        self.assertEqual(info['logical_bytes'], len(system) + len(vendor))
        self.assertEqual(info['groups'][0]['name'], 'main')
        self.assertTrue(any('update-binary' in w for w in info['warnings']))
        for name, content in (('boot', boot), ('system', system), ('vendor', vendor)):
            out = self.root / (name + '.img')
            with io.StringIO() as stdout:
                code = self.run_main(['extract', str(path), '--partition', name, '--out', str(out)])
            self.assertEqual(code, 0)
            self.assertEqual(out.read_bytes(), content)
        refused = self.run_main(['extract', str(path), '--partition', 'lk', '--out', str(self.root / 'lk.img')])
        self.assertEqual(refused, 2)
        self.assertFalse((self.root / 'lk.img').exists())

    def test_a_partial_update_payload_is_read(self):
        """2026-11-12 review: partial_update (field 16) was taken for the partition groups, and its
        number 1 failed the reading with a TypeError and no answer."""
        data = payload([('boot', os.urandom(BLOCK), 'replace')], groups=[('main', 1 << 30, ['system'])], partial=True)
        path = self.zip('partial.zip', {'payload.bin': data, 'META-INF/com/android/metadata': 'pre-device=a22x\n'})
        info = rom.inspect(path)
        self.assertEqual((info['format'], info['groups'][0]['name']), ('payload', 'main'))

    def run_main(self, argv):
        import contextlib
        stream = io.StringIO()
        with contextlib.redirect_stdout(stream):
            code = rom.main(argv)
        self.last = json.loads(stream.getvalue())
        return code

    def test_another_phone_and_an_incremental_update_are_named(self):
        system = os.urandom(2 * BLOCK)
        path = self.zip('other.zip', {'payload.bin': payload([('system', system, 'replace')], incremental=('system',)),
                                      'META-INF/com/android/metadata': 'pre-device=a52q\n'})
        info = rom.inspect(path)
        self.assertFalse(info['device_ok'])
        self.assertIn('The package is made for another phone', info['warnings'])
        self.assertEqual(info['partitions'][0]['target'], 'refused')
        self.assertIn('incremental', info['partitions'][0]['reason'])

    def test_a_damaged_payload_is_refused(self):
        system = os.urandom(2 * BLOCK)
        data = bytearray(payload([('system', system, 'replace')]))
        data[-10] ^= 0xff
        path = self.zip('bad.zip', {'payload.bin': bytes(data)})
        self.assertEqual(self.run_main(['extract', str(path), '--partition', 'system', '--out', str(self.root / 's.img')]), 2)
        self.assertEqual(self.last['error']['code'], 'PAYLOAD_DAMAGED')
        compressed = self.zip('deflated.zip', {'payload.bin': payload([('system', system, 'replace')])}, stored=())
        self.assertEqual(self.run_main(['inspect', str(compressed)]), 2)
        self.assertEqual(self.last['error']['code'], 'PAYLOAD_COMPRESSED')

    def test_an_images_zip_a_block_ota_and_a_gsi(self):
        images = self.zip('images.zip', {'boot.img': os.urandom(8192), 'system.img': os.urandom(16384),
                                         'preloader_a22x.img': b'x' * 100, 'vbmeta.img': b'AVB0' + bytes(60)})
        info = rom.inspect(images)
        self.assertEqual(info['format'], 'images')
        self.assertEqual({p['name']: p['target'] for p in info['partitions']},
                         {'boot': 'physical', 'system': 'logical', 'preloader_a22x': 'refused', 'vbmeta': 'physical'})
        transfer = '4\n3\n0\n0\nerase 2,0,3\nnew 2,0,3\n'
        block = self.zip('block.zip', {'system.transfer.list': transfer, 'system.new.dat': bytes(3 * BLOCK),
                                       'vendor.transfer.list': transfer, 'vendor.new.dat.br': b'brotli',
                                       'dynamic_partitions_op_list': 'remove_all_groups\nadd_group main 7235174400\nadd system main\n',
                                       'boot.img': os.urandom(4096)})
        info = rom.inspect(block)
        self.assertEqual(info['format'], 'block')
        parts = {p['name']: p for p in info['partitions']}
        self.assertEqual((parts['system']['target'], parts['system']['size']), ('logical', 3 * BLOCK))
        self.assertEqual(parts['vendor']['target'], 'refused')
        self.assertIn('Brotli', parts['vendor']['reason'])
        self.assertEqual(parts['boot']['target'], 'physical')
        self.assertEqual(info['groups'], [{'name': 'main', 'size': 7235174400, 'partitions': []}])
        gsi = self.root / 'system-arm64-ab.img.xz'
        gsi.write_bytes(lzma.compress(bytes(4096)))
        info = rom.inspect(gsi)
        self.assertEqual((info['format'], info['compression'], info['partitions'][0]['name']), ('gsi', 'xz', 'system'))
        zipped = self.zip('gsi.zip', {'system-squeak-arm64-ab.img': os.urandom(4096)})
        self.assertEqual(rom.inspect(zipped)['format'], 'gsi')
        junk = self.zip('photos.zip', {'a.jpg': b'jpeg'})
        self.assertEqual(self.run_main(['inspect', str(junk)]), 2)
        self.assertEqual(self.last['error']['code'], 'UNKNOWN_PACKAGE')

    def test_the_refused_list_keeps_the_boot_chain_and_the_modem(self):
        for name in ('preloader', 'preloader_a22x', 'lk', 'lk2', 'tee1', 'tee2', 'gz1', 'sspm_1', 'scp1', 'spmfw', 'mcupm_1',
                     'dpm_1', 'md1img', 'nvram', 'nvdata', 'protect1', 'seccfg', 'efuse', 'sec1', 'up_param', 'param',
                     'recovery', 'userdata', 'cache', 'prism', 'optics', 'metadata'):
            self.assertEqual(rom.classify(name, ())[0], 'refused', name)
        for name in ('boot', 'dtbo', 'vbmeta', 'vbmeta_system', 'vbmeta_vendor'):
            self.assertEqual(rom.classify(name, ()), ('physical', None))
        self.assertEqual(rom.classify('system', ('system',)), ('logical', None))
        self.assertEqual(rom.classify('system_ext', ('system',))[0], 'logical')
        self.assertEqual(rom.classify('mystery', ())[0], 'refused')


class BlockImages(unittest.TestCase):
    """2026-11-13: a full block-based OTA (a One UI port) written by the recovery itself."""
    def package(self, new, listing, name='odm.new.dat'):
        path = Path(tempfile.mkdtemp(prefix='a22x-block-', dir=os.environ.get('TMPDIR'))) / 'rom.zip'
        self.addCleanup(shutil.rmtree, path.parent, True)
        with zipfile.ZipFile(path, 'w') as z:
            z.writestr('odm.transfer.list', listing)
            z.writestr(name, new)
        return zipfile.ZipFile(path)

    def test_new_and_zero_ranges_land_where_the_list_says_and_are_read_back(self):
        new = os.urandom(3 * BLOCK)
        listing = '4\n6\n0\n0\nerase 2,0,8\nnew 4,4,5,0,2\nzero 2,2,4\n'
        with tempfile.TemporaryFile() as device:
            device.write(b'\xaa' * 8 * BLOCK)
            device.flush()
            done = rom.write_block_image(self.package(new, listing), 'odm', device.fileno(), 8 * BLOCK)
            device.seek(0)
            got = device.read()
        self.assertEqual(done, {'blocks': 3, 'sha256': hashlib.sha256(new).hexdigest()})
        self.assertEqual(got[4 * BLOCK:5 * BLOCK], new[:BLOCK])
        self.assertEqual(got[:2 * BLOCK], new[BLOCK:])
        self.assertEqual(got[2 * BLOCK:4 * BLOCK], bytes(2 * BLOCK))
        self.assertEqual(got[5 * BLOCK:], b'\xaa' * 3 * BLOCK)

    def test_what_does_not_fit_or_is_incremental_or_short_is_refused(self):
        with tempfile.TemporaryFile() as device:
            device.write(bytes(4 * BLOCK))
            device.flush()
            for new, listing, code in ((bytes(BLOCK), '4\n9\n0\n0\nnew 2,8,9\n', 'ROM_PART_TOO_LARGE'),
                                       (bytes(BLOCK), '4\n2\n0\n0\nmove 2,0,1 2,1,2\n', 'TRANSFER_LIST_INCREMENTAL'),
                                       (bytes(BLOCK), '4\n2\n0\n0\nnew 2,0,2\n', 'ROM_DATA_SHORT'),
                                       (bytes(3 * BLOCK), '4\n2\n0\n0\nnew 2,0,2\n', 'ROM_DATA_LONG')):
                with self.assertRaises(rom.RomError) as caught:
                    rom.write_block_image(self.package(new, listing), 'odm', device.fileno(), 4 * BLOCK)
                self.assertEqual(caught.exception.code, code)

    def test_a_brotli_image_goes_through_the_decoder(self):
        decoder = os.environ.get('A22X_TEST_BROTLI_DEC')
        encoder = shutil.which('brotli')
        if not decoder or not encoder:
            self.skipTest('no brotli encoder and decoder on this computer')
        import subprocess
        new = (b'kkrt ' * 4096)[:2 * BLOCK]
        packed = subprocess.run([encoder, '-c'], input=new, capture_output=True, check=True).stdout
        with tempfile.TemporaryFile() as device, mock.patch.object(rom, 'BROTLI', decoder):
            device.write(bytes(2 * BLOCK))
            device.flush()
            done = rom.write_block_image(self.package(packed, '4\n2\n0\n0\nnew 2,0,2\n', 'odm.new.dat.br'), 'odm', device.fileno(), 2 * BLOCK)
            device.seek(0)
            self.assertEqual(device.read(), new)
        self.assertEqual(done['blocks'], 2)


if __name__ == '__main__':
    unittest.main()
