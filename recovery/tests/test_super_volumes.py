"""Android's partitions in super as read-only volumes in Files (2026-11-12): the device-mapper
requests (dm_linear.py), the volumes filesystem.py makes of super's metadata, opening and closing
them, and installers closing them before super itself is read or written (install.py)."""
import contextlib
import errno
import fcntl
import os
from pathlib import Path
import shutil
import struct
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests'))
import dm_linear as dm
import filesystem as fs
import install
import operation_lock
from test_lp_metadata import make_super, MIB

SUPER_DEVICE = '179:50'


def ext4_superblock(label):
    sb = bytearray(1024)
    sb[24:28] = struct.pack('<I', 2)        # 4 KiB blocks
    sb[32:36] = struct.pack('<I', 32768)    # blocks per group
    sb[56:58] = b'\x53\xef'
    sb[104:120] = bytes(range(16))
    sb[120:136] = label.encode().ljust(16, b'\0')
    return bytes(sb)


def erofs_superblock():
    sb = bytearray(128)
    sb[0:4] = b'\xe2\xe1\xf5\xe0'
    sb[12] = 12
    sb[48:64] = bytes(range(16, 32))
    return bytes(sb)


class DeviceMapperRequests(unittest.TestCase):
    def test_the_layouts_and_requests_are_the_kernels(self):
        self.assertEqual((dm.IOCTL.size, dm.SPEC.size), (312, 40))
        # include/uapi/linux/dm-ioctl.h: _IOWR(0xfd, n, struct dm_ioctl)
        self.assertEqual([hex(r) for r in (dm.DEV_CREATE, dm.DEV_REMOVE, dm.DEV_SUSPEND, dm.TABLE_LOAD, dm.TABLE_STATUS)],
                         ['0xc138fd03', '0xc138fd04', '0xc138fd06', '0xc138fd09', '0xc138fd0c'])
        head = dm.fields(dm.header('a22x-ro-system'))
        self.assertEqual((head['version0'], head['version1'], head['version2'], head['data_size']), (4, 0, 0, 312))
        self.assertEqual(head['name'].rstrip(b'\0'), b'a22x-ro-system')
        for bad in ('', 'a/b', 'x' * 128, 'a\0b'):
            with self.assertRaises(dm.DmError):
                dm.header(bad)

    def test_a_table_is_laid_as_the_kernel_reads_it_and_read_back(self):
        table = dm.linear_table([[2048, 8930848], [11673600, 16]], SUPER_DEVICE)
        self.assertEqual(table, [(0, 8930848, 'linear', '179:50 2048'), (8930848, 16, 'linear', '179:50 11673600')])
        body = dm.table_bytes(table)
        at = 0
        for start, sectors, kind, params in table:
            sector, length, _, following, name = dm.SPEC.unpack_from(body, at)
            self.assertEqual((sector, length, name.rstrip(b'\0').decode()), (start, sectors, kind))
            self.assertEqual(following % 8, 0)
            self.assertEqual(body[at + dm.SPEC.size:at + following].rstrip(b'\0').decode(), params)
            self.assertEqual(body[at + dm.SPEC.size + len(params)], 0)
            at += following
        self.assertEqual(at, len(body))
        # DM_TABLE_STATUS: "next" counts from the first spec; the flags say whether it is read-only.
        status, at = bytearray(dm.RESULT_SIZE), dm.IOCTL.size
        for start, sectors, kind, params in table:
            spec = dm.SPEC.pack(start, sectors, 0, 0, kind.encode()) + params.encode() + b'\0'
            spec += bytes(-len(spec) % 8)
            status[at:at + len(spec)] = spec
            following = at + len(spec) - dm.IOCTL.size
            struct.pack_into('<I', status, at + 20, following)
            at += len(spec)
        dm.IOCTL.pack_into(status, 0, 4, 0, 0, at, dm.IOCTL.size, 2, 1, dm.READONLY_FLAG, 0, 0, 0, b'a22x-ro-system', b'', b'')
        self.assertEqual(dm.parse_status(bytes(status)), (table, True))
        dm.IOCTL.pack_into(status, 0, 4, 0, 0, at, dm.IOCTL.size, 2, 1, 0, 0, 0, 0, b'a22x-ro-system', b'', b'')
        self.assertEqual(dm.parse_status(bytes(status))[1], False)

    def test_create_loads_a_read_only_table_and_cleans_up_a_refusal(self):
        calls = []

        def ioctl(fd, request, data, mutate):
            head = dm.fields(data)
            calls.append((request, head['data_size'], head['data_start'], head['target_count'], head['flags'],
                          head['name'].rstrip(b'\0').decode()))
            if request == dm.TABLE_LOAD and fail_load:
                raise OSError(errno.EINVAL, 'refused')
            if request == dm.DEV_SUSPEND:
                dm.IOCTL.pack_into(data, 0, 4, 0, 0, 312, 312, 1, 0, dm.READONLY_FLAG, 0, 0, (251 << 8) | 3,
                                   b'a22x-ro-vendor', b'', b'')
            return 0
        table = dm.linear_table([[11673600, 2135800]], SUPER_DEVICE)
        with mock.patch.object(dm.fcntl, 'ioctl', side_effect=ioctl), \
                mock.patch.object(dm.os, 'open', return_value=99), mock.patch.object(dm.os, 'close'):
            fail_load = False
            self.assertEqual(dm.create('a22x-ro-vendor', table), '251:3')
            size = 312 + len(dm.table_bytes(table))
            self.assertEqual(calls, [(dm.DEV_CREATE, 312, 0, 0, 0, 'a22x-ro-vendor'),
                                     (dm.TABLE_LOAD, size, 312, 1, dm.READONLY_FLAG, 'a22x-ro-vendor'),
                                     (dm.DEV_SUSPEND, 312, 0, 0, 0, 'a22x-ro-vendor')])  # no DM_SUSPEND_FLAG: resume
            calls.clear()
            fail_load = True
            with self.assertRaises(OSError):
                dm.create('a22x-ro-vendor', table)
            self.assertEqual([c[0] for c in calls], [dm.DEV_CREATE, dm.TABLE_LOAD, dm.DEV_REMOVE])

    def test_devices_are_found_by_name_in_sysfs(self):
        with tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR')) as tmp:
            for n, name in ((0, 'a22x-ro-system'), (4, 'other')):
                (Path(tmp) / ('dm-%d' % n) / 'dm').mkdir(parents=True)
                (Path(tmp) / ('dm-%d' % n) / 'dm/name').write_text(name + '\n')
                (Path(tmp) / ('dm-%d' % n) / 'dev').write_text('251:%d\n' % n)
            (Path(tmp) / 'mmcblk0').mkdir()
            self.assertEqual(dm.devices(Path(tmp)), {'a22x-ro-system': ('251:0', 'dm-0'), 'other': ('251:4', 'dm-4')})


class FakeKernel:
    """Device-mapper disks and mounts, as far as filesystem.py sees them."""
    def __init__(self):
        self.mapped, self.tables, self.mounts, self.commands, self.mount_fails = {}, {}, [], [], False

    def create(self, name, table):
        devno = '251:%d' % len(self.tables)
        self.mapped[name], self.tables[name] = devno, table
        return devno

    def remove(self, name):
        if any(m['devno'] == self.mapped[name] for m in self.mounts):
            raise OSError(errno.EBUSY, 'busy')
        del self.mapped[name]

    def devices(self):
        return {name: (devno, 'dm-' + devno.split(':')[1]) for name, devno in self.mapped.items()}

    def status(self, name):
        return self.tables[name], True

    def command(self, action, arguments):
        self.commands.append([action, *arguments])
        if action == 'mount':
            if self.mount_fails:
                fs.fail('MOUNT_FAILED', 'The kernel refused the filesystem operation')
            devno = next(d for n, d in self.mapped.items() if arguments[-2] == '/dev/dm-' + d.split(':')[1])
            self.mounts.append({'mount_id': 70 + len(self.commands), 'parent_id': 1, 'devno': devno, 'root': '/',
                                'mountpoint': arguments[-1], 'options': arguments[3].split(','), 'fs_type': arguments[1],
                                'source': arguments[-2]})
        else:
            self.mounts = [m for m in self.mounts if m['mountpoint'] != arguments[-1]]


class SuperVolumes(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='a22x-super-', dir=os.environ.get('TMPDIR')))
        self.addCleanup(shutil.rmtree, self.root, True)
        image = self.root / 'super.img'
        make_super(image, parts=(('system', 8 * MIB), ('vendor', 4 * MIB), ('product', 2 * MIB), ('odm', 1 * MIB)))
        fd = os.open(image, os.O_RDONLY)
        try:
            import lp_metadata as lp
            extents = {p['name']: lp.Super(fd).extents_of(p)[0] for p in lp.Super(fd).metadata['partitions']}
        finally:
            os.close(fd)
        with open(image, 'r+b') as f:
            f.seek(extents['system']['start'] * 512 + 1024)
            f.write(ext4_superblock('system'))
            f.seek(extents['vendor']['start'] * 512 + 1024)
            f.write(erofs_superblock())
        # odm reaches past the end of a super that is cut short: it does not lie in super alone.
        self.cut = (extents['odm']['start'] + 8) * 512
        fd = os.open(image, os.O_RDONLY)
        try:
            self.found = fs.logical_partitions(fd, self.cut)
        finally:
            os.close(fd)
        self.base = self.root / 'volumes'
        self.base.mkdir()
        patcher = mock.patch.object(fs, 'MOUNT_BASE', self.base)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.kernel = FakeKernel()
        for name in ('create', 'remove', 'devices', 'status'):
            patcher = mock.patch.object(fs.dm_linear, name, side_effect=getattr(self.kernel, name))
            patcher.start()
            self.addCleanup(patcher.stop)
        for name in ('read_mounts', 'all_namespace_mounts'):
            patcher = mock.patch.object(fs, name, side_effect=lambda: list(self.kernel.mounts))
            patcher.start()
            self.addCleanup(patcher.stop)
        self.context = fs.DeviceContext.__new__(fs.DeviceContext)
        self.context.boot_id, self.context.state_path = 'boot', self.root / 'filesystems.json'
        self.context.container = {'path': '/dev/mmcblk0p50', 'device': SUPER_DEVICE, 'device_identity': 'cid',
                                  'partition_number': 50, 'size_bytes': self.cut, 'volume_token': 'super-token'}
        self.context._read_super = lambda container: self.found
        self.context._command = self.kernel.command
        self.context._mapping_node = lambda devno, volume: '/dev/dm-' + devno.split(':')[1]
        self.context.volumes = lambda: self.context._logical_volumes(
            self.context.container, {'ext4', 'f2fs', 'erofs'}, self.context.state(), fs.read_mounts())

    def rows(self):
        return {v['id']: v for v in self.context.volumes()}

    def test_each_partition_in_super_is_a_read_only_volume(self):
        rows = self.rows()
        self.assertEqual(sorted(rows), ['super-odm', 'super-product', 'super-system', 'super-vendor'])
        system = rows['super-system']
        self.assertEqual((system['label'], system['fs_type'], system['size_bytes'], system['filesystem_label']),
                         ('system', 'ext4', 8 * MIB, 'system'))
        self.assertEqual((system['mountable'], system['write_supported'], system['read_only_reason_code'], system['mounted'],
                          system['device'], system['mapping'], system['external']),
                         (True, False, 'LOGICAL_PARTITION_READ_ONLY', False, '', None, False))
        self.assertEqual((rows['super-vendor']['fs_type'], rows['super-vendor']['mountable']), ('erofs', True))
        self.assertEqual((rows['super-product']['mountable'], rows['super-product']['reason_code']),
                         (False, 'ENCRYPTED_OR_UNSUPPORTED_FILESYSTEM'))
        self.assertEqual((rows['super-odm']['mountable'], rows['super-odm']['reason_code']),
                         (False, 'LOGICAL_PARTITION_LAYOUT_UNSUPPORTED'))
        self.assertEqual(len({v['volume_token'] for v in rows.values()}), 4)

    def test_open_read_and_close_a_partition(self):
        token = self.rows()['super-system']['volume_token']
        with self.assertRaises(fs.FilesystemError) as refused:
            self.context.mount('super-system', 'rw', True, token)
        self.assertEqual(refused.exception.code, 'WRITE_NOT_QUALIFIED')
        self.assertEqual(self.kernel.mapped, {})
        done = self.context.mount('super-system', 'ro', False, token)
        volume = done['volume']
        self.assertEqual(self.kernel.tables['a22x-ro-system'], dm.linear_table(volume['extents'], SUPER_DEVICE))
        self.assertEqual(self.kernel.commands, [['mount', '-t', 'ext4', '-o', 'ro,nodev,nosuid,noexec,noload', '/dev/dm-0',
                                                 str(self.base / 'super-system')]])
        self.assertEqual((volume['mounted'], volume['mount_owner'], volume['device'], volume['path'], volume['volume_token'],
                          volume['read_only_guarded']),
                         (True, 'filesystem', '251:0', '/dev/dm-0', token, True))
        self.assertEqual(self.context.mount('super-system', 'ro', False, token)['volume']['mount_id'], volume['mount_id'])
        done = self.context.unmount('super-system', token)
        self.assertEqual((done['volume']['mounted'], done['volume']['device'], self.kernel.mapped, self.kernel.mounts),
                         (False, '', {}, []))
        self.assertEqual(self.context.state()['volumes'], {})

    def test_a_mount_the_kernel_refuses_leaves_no_mapping(self):
        self.kernel.mount_fails = True
        with self.assertRaises(fs.FilesystemError) as refused:
            self.context.mount('super-vendor', 'ro', False, self.rows()['super-vendor']['volume_token'])
        self.assertEqual((refused.exception.code, self.kernel.mapped), ('MOUNT_FAILED', {}))

    def test_a_mapping_that_changed_or_is_used_elsewhere_is_not_admitted(self):
        token = self.rows()['super-system']['volume_token']
        self.context.mount('super-system', 'ro', False, token)
        self.kernel.tables['a22x-ro-system'] = dm.linear_table([[4096, 16384]], SUPER_DEVICE)
        row = self.rows()['super-system']
        self.assertEqual((row['mountable'], row['reason_code'], row['volume_token']), (False, 'LOGICAL_MAPPING_CHANGED', token))
        self.kernel.tables['a22x-ro-system'] = dm.linear_table(row['extents'], SUPER_DEVICE)
        self.kernel.mounts[0]['mountpoint'] = '/mnt/elsewhere'
        row = self.rows()['super-system']
        self.assertEqual((row['mountable'], row['reason_code'], row['mount_owner']), (False, 'VOLUME_MOUNTED_OUTSIDE_MANAGER', None))
        with self.assertRaises(fs.FilesystemError) as refused:
            self.context.unmount('super-system', token)
        self.assertEqual(refused.exception.code, 'UNMANAGED_MOUNT')
        self.assertIn('a22x-ro-system', self.kernel.mapped)

    def test_closing_unmounts_and_removes_every_mapping_of_super(self):
        rows = self.rows()
        self.context.mount('super-system', 'ro', False, rows['super-system']['volume_token'])
        self.context.mount('super-vendor', 'ro', False, rows['super-vendor']['volume_token'])
        self.context.unmount('super-vendor', rows['super-vendor']['volume_token'])
        self.kernel.create('a22x-ro-vendor', dm.linear_table(rows['super-vendor']['extents'], SUPER_DEVICE))  # left mapped
        self.kernel.create('a22x-ro-gone', dm.linear_table([[4096, 8]], SUPER_DEVICE))  # metadata names it no more
        self.kernel.create('someone-else', [])
        self.assertEqual(sorted(self.context.close_logical()), ['super-gone', 'super-system', 'super-vendor'])
        self.assertEqual((list(self.kernel.mapped), self.kernel.mounts), (['someone-else'], []))
        self.assertEqual(self.context.close_logical(), [])


class InstallersCloseThemFirst(unittest.TestCase):
    """install.py check_offline: what Files opened in super is closed under the lease, and the
    storage graph is read again, before super is opened."""
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='a22x-super-lock-', dir=os.environ.get('TMPDIR')))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.lock = self.root / 'lock'
        self.seen = []
        outer = self

        class Files:
            def close_logical(self):
                outer.seen.append(operation_lock.held(outer.lock))
                return ['super-system']

        class Graph:
            def __init__(self, name):
                self.name = name

            def require_unmounted(self, device, mounts):
                outer.seen.append((self.name, 'unmounted', device))

            def require_no_holders(self, device):
                outer.seen.append((self.name, 'holders', device))

        class Raw:
            def __init__(self, name='fresh'):
                self.graph, self.mounts = Graph(name), []

            def assert_recovery(self):
                outer.seen.append('assert_recovery')

        self.context = install.DeviceContext.__new__(install.DeviceContext)
        self.context.fs, self.context.raw = Files(), Raw('old')
        self.context.lock_path, self.context.mode = self.lock, 'device'
        patcher = mock.patch.object(install, '_backend_storage', return_value=mock.Mock(DeviceContext=Raw))
        patcher.start()
        self.addCleanup(patcher.stop)
        os.environ.pop(operation_lock.ENV_FD, None)

    def test_only_super_closes_them(self):
        self.context.check_offline([{'label': 'boot', 'device': '179:30', 'object': [0, 1, 2]}])
        self.assertEqual(self.seen, [('old', 'unmounted', '179:30'), ('old', 'holders', '179:30')])

    def test_without_a_lease_one_is_taken_for_the_closing(self):
        self.context.check_offline([{'label': 'super', 'device': SUPER_DEVICE, 'object': [0, 1, 3]}])
        self.assertEqual(self.seen, [True, 'assert_recovery', ('fresh', 'unmounted', SUPER_DEVICE), ('fresh', 'holders', SUPER_DEVICE)])
        self.assertFalse(operation_lock.held(self.lock))

    def test_inside_the_callers_lease_it_is_not_taken_again(self):
        with operation_lock.operation_lock(self.lock):
            self.assertTrue(operation_lock.held(self.lock))
            self.context.check_offline([{'label': 'super', 'device': SUPER_DEVICE, 'object': [0, 1, 3]}])
        self.assertEqual(self.seen[:2], [True, 'assert_recovery'])
        self.assertFalse(operation_lock.held(self.lock))

    def test_while_another_operation_runs_nothing_is_closed(self):
        fd = os.open(self.lock, os.O_RDWR | os.O_CREAT, 0o600)  # another holder: not this process's lease
        self.addCleanup(os.close, fd)
        fcntl.flock(fd, fcntl.LOCK_EX)
        self.assertEqual(self.context.close_logical(), [])
        self.assertEqual(self.seen, [])


if __name__ == '__main__':
    unittest.main()
