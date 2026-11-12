"""ZIPs that install themselves (zip_install.py, 2026-11-11): TWRP's Install. The installer of the
ZIP runs with only the partitions it may write in reach; the BOOT there is kept first, and the
result names what changed. Here the installer is a shell script run as a plain process against the
fixture's partition files; the room of its own (new spaces, a private /dev) is the phone's."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import struct
import sys
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import filesystem as fs  # noqa: E402
import saved_boot  # noqa: E402
import zip_install as zi  # noqa: E402
from test_android_gsi import stock_boot  # noqa: E402
from test_flash import boot_image  # noqa: E402
from test_keep_recovery import signed  # noqa: E402

MIB = 1 << 20
SCRIPT = '''#!/sbin/sh
OUTFD=$2
ui_print() { echo "ui_print $1" >&$OUTFD; echo "ui_print" >&$OUTFD; }
ui_print "Hello from the ZIP"
echo "progress 0.5 1" >&$OUTFD
ls "$A22X_ZIP_ROOT/dev/block/by-name" > "$A22X_ZIP_ROOT/../seen"
%s
ui_print "Done"
exit %d
'''


class ZipInstall(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='a22x-zip-', dir=os.environ.get('TMPDIR')))
        self.addCleanup(shutil.rmtree, self.root, True)
        for name in ('targets', 'cache/a22x-recovery'):
            (self.root / name).mkdir(parents=True)
        (self.root / 'install-fixture.json').write_text(json.dumps({
            'schema': 'a22x.install.fixture.v1', 'device': {'model': 'SM-A226B', 'identity': 'fixture:test'},
            'targets': {'boot': {'path': 'targets/boot', 'size_bytes': 4 * MIB}, 'dtbo': {'path': 'targets/dtbo', 'size_bytes': MIB},
                        'recovery': {'path': 'targets/recovery', 'size_bytes': MIB}, 'userdata': {'path': 'targets/userdata', 'size_bytes': MIB}}}))
        (self.root / 'filesystem-fixture.json').write_text(json.dumps({'schema': 'a22x.filesystem.fixture.v1', 'volumes': [
            {'id': 'cache', 'path': 'cache', 'fs_type': 'ext4', 'mounted': True, 'read_only': True}]}))
        self.old = signed(stock_boot())
        (self.root / 'targets/boot').write_bytes(self.old)
        for name in ('dtbo', 'recovery', 'userdata'):
            (self.root / 'targets' / name).write_bytes(name.encode() * (MIB // len(name)) + bytes(MIB % len(name)))
        self.new = signed(boot_image())
        (self.root / 'new-boot.img').write_bytes(self.new)

    def archive(self, name, body='', code=0, installer=True, extra=None):
        path = self.root / 'cache' / name
        with zipfile.ZipFile(path, 'w') as z:
            if installer:
                z.writestr(zi.INSTALLER, SCRIPT % (body, code))
                z.writestr('META-INF/com/google/android/updater-script', '#MAGISK\n')
            for member, data in (extra or {}).items():
                z.writestr(member, data)
        return '/' + name

    def helper(self, command, path, *more):
        token = fs.FixtureContext(self.root).volume('cache')['volume_token']
        stdout, stderr = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            code = zi.main(['--fixture', str(self.root), command, '--volume', 'cache', '--volume-token', token, '--path', path, *more])
        return code, json.loads(stdout.getvalue().splitlines()[-1]), stderr.getvalue()

    def test_the_review_says_what_the_zip_is_and_refuses_what_has_no_installer(self):
        path = self.archive('module.zip', extra={'module.prop': 'id=demo\nname=Demo module\nversion=v1.2\n'})
        code, about, _ = self.helper('inspect', path)
        self.assertEqual(code, 0, about)
        self.assertEqual((about['installer'], about['what'], about['title']), ('script', 'module', 'Demo module v1.2'))
        self.assertEqual(about['sha256'], hashlib.sha256((self.root / 'cache/module.zip').read_bytes()).hexdigest())
        self.assertEqual(about['partitions'], ['boot', 'dtbo'])  # never the recovery or Android's data
        self.assertTrue(any('module' in line for line in about['warnings']))
        code, refused, _ = self.helper('inspect', self.archive('images.zip', installer=False, extra={'system.img': b'x'}))
        self.assertEqual((code, refused['error']['code']), (2, 'ZIP_NO_INSTALLER'))
        (self.root / 'cache/broken.zip').write_bytes(b'PK\x03\x04 not a zip')
        code, refused, _ = self.helper('inspect', '/broken.zip')
        self.assertEqual((code, refused['error']['code']), (2, 'ZIP_DAMAGED'))

    def test_the_installer_runs_with_the_allowed_partitions_and_the_old_boot_is_kept(self):
        path = self.archive('kernel.zip', 'dd if="$A22X_ZIP_ROOT/../new-boot.img" of="$A22X_ZIP_ROOT/dev/block/by-name/boot" conv=notrunc 2>/dev/null')
        about = self.helper('inspect', path)[1]
        code, refused, _ = self.helper('run', path, '--expected-sha256', about['sha256'])
        self.assertEqual((code, refused['error']['code']), (2, 'CONFIRMATION_REQUIRED'))
        code, refused, _ = self.helper('run', path, '--expected-sha256', '0' * 64, '--confirm')
        self.assertEqual((code, refused['error']['code']), (2, 'ZIP_CHANGED'))
        self.assertEqual((self.root / 'targets/boot').read_bytes(), self.old)
        code, done, progress = self.helper('run', path, '--expected-sha256', about['sha256'], '--confirm')
        self.assertEqual(code, 0, done)
        self.assertEqual((done['exit'], done['changed'], done['log']), (0, ['boot'], ['Hello from the ZIP', 'Done']))
        self.assertIn('Hello from the ZIP', progress)
        # Only what an installer may write was in its reach.
        self.assertEqual((self.root / 'seen').read_text().split(), ['boot', 'dtbo'])
        self.assertEqual((self.root / 'cache' / done['kept_boot'].lstrip('/')).read_bytes(), self.old)
        self.assertEqual(saved_boot.describe_boot((self.root / 'targets/boot').read_bytes())['kind'], 'linux')
        self.assertEqual((self.root / 'targets/recovery').read_bytes()[:8], b'recovery')

    def test_an_installer_that_fails_is_reported_with_its_last_lines(self):
        path = self.archive('bad.zip', 'ui_print "! Unsupported device"', code=3)
        about = self.helper('inspect', path)[1]
        code, failed, _ = self.helper('run', path, '--expected-sha256', about['sha256'], '--confirm')
        self.assertEqual((code, failed['error']['code']), (2, 'ZIP_INSTALLER_FAILED'))
        self.assertEqual((failed['error']['details']['exit'], failed['error']['details']['changed']), (3, []))
        self.assertIn('! Unsupported device', failed['error']['details']['log'])
        self.assertEqual((self.root / 'targets/boot').read_bytes(), self.old)


def area_lookup(area, name):
    """bionic's own way through a property area (prop_area::find_property), to read back what
    property_area wrote: None when the name has no value there."""
    used, _serial, magic, version = struct.unpack_from('<4I', area, 0)
    assert (magic, version) == (0x504f5250, 0xfc6ed0ab) and 128 + used <= len(area)
    data = area[128:]
    current = 0
    for part in name.encode().split(b'.'):
        if not part:
            return None
        at = struct.unpack_from('<I', data, current + 16)[0]
        while True:
            if not at:
                return None
            length = struct.unpack_from('<I', data, at)[0]
            other = data[at + 20:at + 20 + length]
            assert data[at + 20 + length] == 0 and at % 4 == 0
            if (len(part), part) == (length, other):
                break
            at = struct.unpack_from('<I', data, at + (8 if (len(part), part) < (length, other) else 12))[0]
        current = at
    info = struct.unpack_from('<I', data, current + 4)[0]
    if not info:
        return None
    serial = struct.unpack_from('<I', data, info)[0]
    assert not serial & 1 and info % 4 == 0 and data[info + 96:].split(b'\0', 1)[0] == name.encode()
    return data[info + 4:info + 4 + (serial >> 24)].decode()


class PropertyArea(unittest.TestCase):
    """2026-11-11: a program installer (edify) asks Android's property area, not a getprop script.
    The area is read back here the way bionic walks it; a real updater reads it on the phone."""

    def test_every_value_is_found_the_way_bionic_looks_it_up(self):
        values = dict(zi.PROPS)
        values.update({'ro': 'top', 'ro.build.version.sdk': '33', 'ro.build.version.release': '13', 'a': '1', 'b.c.d.e.f': 'deep',
                       'ro.build.fingerprint': 'samsung/a22xnsxx/a22x:13/TP1A.220624.014/A226BXXSBDZC3:user/release-keys', 'empty': '',
                       'persist.sys.usb.config': 'mtp,adb', 'ro.zz': 'z', 'ro.aa': 'a', 'ro.product.devicf': 'near'})
        area = zi.property_area(values)
        self.assertEqual(len(area) % 4096, 0)
        self.assertGreaterEqual(len(area), 128 << 10)
        for name, value in values.items():
            self.assertEqual(area_lookup(area, name), value, name)
        for name in ('ro.product', 'ro.product.devic', 'ro.product.device.x', 'nothing', 'b.c', 'ro.product.devicee'):
            self.assertIsNone(area_lookup(area, name), name)

    def test_what_has_no_place_in_the_area_is_left_out_and_the_first_value_stands(self):
        area = zi.property_area({'ro.ok': 'yes', 'bad..name': '1', '.lead': '1', 'trail.': '1', 'with space': '1', 'long': 'v' * 92,
                                 'fits': 'v' * 91, 'ro.nul': 'a\0b'})
        self.assertEqual((area_lookup(area, 'ro.ok'), area_lookup(area, 'fits')), ('yes', 'v' * 91))
        for name in ('long', 'ro.nul', 'with space', 'bad', 'trail'):
            self.assertIsNone(area_lookup(area, name), name)
        found = {}
        zi.property_lines('# a comment\nro.a=1\nro.a=2\n\nnot a line\nro.b = spaced \n=nameless\nro.c=x=y\n', found)
        self.assertEqual(found, {'ro.a': '1', 'ro.b': 'spaced', 'ro.c': 'x=y'})
        many = {'ro.p%04d' % n: 'value-%d' % n for n in range(3000)}
        area = zi.property_area(many)
        self.assertGreater(len(area), 128 << 10)
        self.assertEqual((area_lookup(area, 'ro.p0000'), area_lookup(area, 'ro.p1500'), area_lookup(area, 'ro.p2999')),
                         ('value-0', 'value-1500', 'value-2999'))

    def test_a_file_is_read_inside_its_folder_and_never_through_a_link(self):
        root = Path(tempfile.mkdtemp(prefix='a22x-props-', dir=os.environ.get('TMPDIR')))
        self.addCleanup(shutil.rmtree, root, True)
        (root / 'image/system').mkdir(parents=True)
        (root / 'image/system/build.prop').write_text('ro.build.version.sdk=33\n')
        (root / 'secret').write_text('ro.secret=1\n')
        os.symlink('../secret', root / 'image/link.prop')
        os.symlink('..', root / 'image/out')
        os.mkfifo(root / 'image/pipe.prop')
        (root / 'image/huge.prop').write_bytes(b'x' * (600 << 10))
        self.assertEqual(zi.read_inside(str(root / 'image'), 'system/build.prop'), 'ro.build.version.sdk=33\n')
        for relative in ('link.prop', 'out/secret', 'missing.prop', 'system', 'pipe.prop', 'huge.prop'):
            self.assertIsNone(zi.read_inside(str(root / 'image'), relative), relative)


class DeviceMapper(unittest.TestCase):
    """2026-11-11: an installer that maps super's partitions itself (a ROM for dynamic partitions)
    waits for the nodes ueventd makes. Mapper makes them in the room, for mappings on super only."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='a22x-mapper-', dir=os.environ.get('TMPDIR')))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.sys = self.root / 'sys-block'
        self.block = self.root / 'room/dev/block'
        (self.block / 'mapper/by-uuid').mkdir(parents=True)
        self.old = zi.SYS_BLOCK
        zi.SYS_BLOCK = self.sys
        self.addCleanup(setattr, zi, 'SYS_BLOCK', self.old)
        self.nodes = []
        real = os.mknod
        self.addCleanup(setattr, os, 'mknod', real)
        os.mknod = lambda path, mode, device: (self.nodes.append((Path(path).name, os.major(device), os.minor(device))), Path(path).write_bytes(b''))[1]

    def device(self, kernel, name, uuid, devno, slaves, written=0):
        folder = self.sys / kernel
        (folder / 'dm').mkdir(parents=True, exist_ok=True)
        (folder / 'dm/name').write_text(name + '\n')
        (folder / 'dm/uuid').write_text(uuid + '\n')
        (folder / 'dev').write_text(devno + '\n')
        (folder / 'stat').write_text('  5 0 40 0 9 0 %d 0 0 0 0\n' % written)
        shutil.rmtree(folder / 'slaves', ignore_errors=True)
        for index, slave in enumerate(slaves):
            (folder / 'slaves' / ('s%d' % index)).mkdir(parents=True)
            (folder / 'slaves' / ('s%d' % index) / 'dev').write_text(slave + '\n')

    def test_a_mapping_on_super_gets_its_node_and_links_and_any_other_gets_none(self):
        self.device('dm-0', 'old', 'old-uuid', '253:0', ['179:44'])
        mapper = zi.Mapper(self.block, zi.mapped_devices(), '179:44')
        self.device('dm-1', 'system', '', '253:1', [])            # made, its table not loaded yet
        mapper.tend()
        self.assertEqual(self.nodes, [])
        self.device('dm-1', 'system', '4b1c6c7e-0a3f-4b1e-9a55-0c1d2e3f4a5b', '253:1', ['179:44'])
        self.device('dm-2', 'data', 'aaaa', '253:2', ['179:54'])   # userdata, not super
        self.device('dm-3', 'both', 'bbbb', '253:3', ['179:44', '179:30'])
        self.device('dm-4', 'bad/../name', 'cccc', '253:4', ['179:44'])
        (self.block / 'mapper/vendor').write_bytes(b'')            # the recovery's own node of that name
        self.device('dm-5', 'vendor', 'dddd', '253:5', ['179:44'])
        mapper.tend()
        mapper.tend()
        self.assertEqual(sorted(self.nodes), [('dm-1', 253, 1), ('dm-5', 253, 5)])
        self.assertEqual(os.readlink(self.block / 'mapper/system'), '/dev/block/dm-1')
        self.assertEqual(os.readlink(self.block / 'mapper/by-uuid/4b1c6c7e-0a3f-4b1e-9a55-0c1d2e3f4a5b'), '/dev/block/dm-1')
        self.assertEqual(os.readlink(self.block / 'mapper/by-uuid/dddd'), '/dev/block/dm-5')
        self.assertFalse((self.block / 'mapper/vendor').is_symlink())
        self.assertEqual(sorted(p.name for p in self.block.iterdir()), ['dm-1', 'dm-5', 'mapper'])
        self.assertEqual(mapper.seen, {'dm-1': True, 'dm-2': False, 'dm-3': False, 'dm-4': False, 'dm-5': True})

    def test_what_was_written_is_told_from_the_kernels_count_and_the_request_is_the_kernels_struct(self):
        self.device('dm-0', 'old', 'x', '253:0', ['179:44'], written=99)
        before = set(zi.mapped_devices())
        self.device('dm-1', 'system', 'y', '253:1', ['179:44'], written=4096)
        self.device('dm-2', 'vendor', 'z', '253:2', ['179:44'], written=0)
        asked = []
        real = zi.remove_mapping
        self.addCleanup(setattr, zi, 'remove_mapping', real)
        zi.remove_mapping = lambda name: (asked.append(name), shutil.rmtree(self.sys / {'system': 'dm-1', 'vendor': 'dm-2'}[name]))[0] is None
        written, left = zi.unmap_after(before)
        self.assertEqual((written, left, asked), (['system'], [], ['system', 'vendor']))
        self.assertEqual(sorted(zi.mapped_devices()), ['dm-0'])
        self.assertEqual((zi.DM_IOCTL.size, zi.DM_DEV_REMOVE), (312, 0xc138fd04))
        request = zi.DM_IOCTL.pack(4, 0, 0, zi.DM_IOCTL.size, 0, 0, 0, 0, 0, 0, 0, b'system', b'', b'')
        self.assertEqual((request[:12], request[12:16], request[48:55]), (struct.pack('<3I', 4, 0, 0), struct.pack('<I', 312), b'system\0'))


if __name__ == '__main__':
    unittest.main()
