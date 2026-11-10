#!/usr/bin/env python3
"""Owned regular-directory acceptance for distro import/switch/rollback.

These fixtures use synthetic ARM64 ELF headers to test admission, not executed
Linux systems. No block device, mount, reboot, shell from a bundle or phone is
opened. End-to-end boot remains a separate physical qualification.
"""
import contextlib
import copy
import errno
import hashlib
import io
import json
import os
from pathlib import Path
import stat
import struct
import sys
import tarfile
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import distro as d
import filesystem as fs


def arm_elf(machine=183):
    data = bytearray(128)
    data[:16] = b'\x7fELF\x02\x01\x01' + bytes(9)
    struct.pack_into('<HHIQQQIHHHHHH', data, 16, 2, machine, 1, 0x400078, 64, 0, 0, 64, 56, 1, 0, 0, 0)
    struct.pack_into('<IIQQQQQQ', data, 64, 1, 5, 0, 0x400000, 0, 128, 128, 4096)
    data[120:124] = b'\x00\x00\x00\x14'
    return bytes(data)


def dynamic_arm_elf(needed=(), soname=None, rpath=None, runpath=None, interpreter=None):
    """Real ELF64 dynamic/program headers, inert payload; never executed."""
    strings, tags = bytearray(b'\0'), []
    for tag, value in ([(1, name) for name in needed] +
                       [(tag, value) for tag, value in ((14, soname), (15, rpath), (29, runpath))
                        if value is not None]):
        tags.append((tag, len(strings)))
        strings.extend(value.encode() + b'\0')
    count = 3 if interpreter else 2
    dynamic_offset = (64 + count * 56 + 15) & ~15
    dynamic_size = (len(tags) + 3) * 16
    strings_offset = dynamic_offset + dynamic_size
    interp = interpreter.encode() + b'\0' if interpreter else b''
    interp_offset = strings_offset + len(strings)
    size = interp_offset + len(interp)
    data = bytearray(size)
    data[:16] = b'\x7fELF\x02\x01\x01' + bytes(9)
    struct.pack_into('<HHIQQQIHHHHHH', data, 16, 3, 183, 1, 0, 64, 0, 0, 64, 56, count, 0, 0, 0)
    struct.pack_into('<IIQQQQQQ', data, 64, 1, 5, 0, 0x400000, 0, size, size, 4096)
    struct.pack_into('<IIQQQQQQ', data, 120, 2, 4, dynamic_offset,
                     0x400000 + dynamic_offset, 0, dynamic_size, dynamic_size, 8)
    if interpreter:
        struct.pack_into('<IIQQQQQQ', data, 176, 3, 4, interp_offset,
                         0x400000 + interp_offset, 0, len(interp), len(interp), 1)
    for i, pair in enumerate([(5, 0x400000 + strings_offset), (10, len(strings))] + tags + [(0, 0)]):
        struct.pack_into('<QQ', data, dynamic_offset + i * 16, *pair)
    data[strings_offset:interp_offset] = strings
    data[interp_offset:] = interp
    return bytes(data)


def make_root(root, label, uid=1000):
    for name in ('usr/bin', 'usr/lib/firmware', 'usr/lib/modules/7.2.0-a22x+',
                 'usr/lib/systemd/system', 'etc/systemd/system/multi-user.target.wants',
                 'var/lib/pkg', 'home', 'dev', 'proc', 'sys', 'run', 'tmp', 'mnt', 'media', 'root'):
        (root / name).mkdir(mode=0o755, parents=True, exist_ok=True)
    for alias, target in (('bin', 'usr/bin'), ('sbin', 'usr/bin'), ('lib', 'usr/lib')):
        (root / alias).symlink_to(target)
    for name in ('systemd', 'sh'):
        path = root / 'usr/bin' / name
        path.write_bytes(arm_elf())
        path.chmod(0o755)
    (root / 'usr/bin/init').symlink_to('systemd')
    (root / 'etc/os-release').write_text('ID=' + label + '\nNAME="' + label + ' ARM64 fixture"\n')
    (root / 'etc/passwd').write_text('root:x:0:0:root:/root:/bin/sh\nuser:x:%d:1000:User:/home/user:/bin/sh\n' % uid)
    (root / 'etc/group').write_text('root:x:0:\nuser:x:1000:\n')
    if os.geteuid() not in (0, 1000):
        with (root / 'etc/passwd').open('a') as stream:
            stream.write('fixture-owner:x:%d:%d:Fixture owner:/fixture-owner:/bin/sh\n' % (os.geteuid(), os.getegid()))
        with (root / 'etc/group').open('a') as stream:
            stream.write('fixture-owner:x:%d:\n' % os.getegid())
    (root / 'etc/fstab').write_text('PARTLABEL=userdata / ext4 defaults 0 1\n')
    (root / 'var/lib/pkg/state').write_text(label)
    (root / 'root/preferences').write_text('root settings for ' + label)
    (root / 'usr/lib/firmware/a22x.bin').write_bytes(b'firmware-fixture\0')
    (root / 'usr/lib/modules/7.2.0-a22x+/modules.builtin').write_text('all built in for fixture\n')
    (root / 'usr/lib/systemd/system/a22x-fixture.service').write_text('[Service]\nExecStart=/bin/sh -c true\n')
    (root / 'etc/systemd/system/multi-user.target.wants/a22x-fixture.service').symlink_to(
        '/usr/lib/systemd/system/a22x-fixture.service')
    (root / 'tmp').chmod(0o1777)


def archive(root, target):
    with tarfile.open(target, 'w:gz', format=tarfile.PAX_FORMAT) as out:
        for path in sorted(root.rglob('*')):
            out.add(path, arcname=path.relative_to(root).as_posix(), recursive=False)


class StopForTest(Exception):
    pass


class Fixture:
    def __init__(self, parent):
        self.path = Path(parent)
        self.live = self.path / 'userdata'
        self.sd = self.path / 'sd'
        self.new = self.path / 'new-root'
        self.live.mkdir()
        self.sd.mkdir()
        self.new.mkdir()
        make_root(self.live, 'old')
        make_root(self.new, 'new')
        (self.live / 'home/user').mkdir()
        (self.live / 'home/user/дані.txt').write_text('Особисті дані, не змінювати\n')
        if os.geteuid() == 0:
            try:
                os.chown(self.live / 'home/user', 1000, 1000)
                os.chown(self.live / 'home/user/дані.txt', 1000, 1000)
            except OSError as exc:
                if exc.errno not in (errno.EINVAL, errno.EPERM):
                    raise
        (self.live / 'home/user/link').symlink_to('дані.txt')
        (self.live / 'lost+found').mkdir()
        self.context_json = {'schema': 'a22x.filesystem.fixture.v1', 'volumes': [
            {'id': 'userdata', 'path': 'userdata', 'fs_type': 'ext4', 'mounted': True, 'read_only': False},
            {'id': 'sdcard-p1', 'path': 'sd', 'fs_type': 'ext4', 'mounted': True, 'read_only': True}]}
        (self.path / 'filesystem-fixture.json').write_text(json.dumps(self.context_json))
        self.context = fs.FixtureContext(self.path)
        self.profile = d.capture_profile(self.context, ['/usr/lib/modules/7.2.0-a22x+'],
            ['/usr/lib/firmware'], ['/usr/lib/systemd/system/a22x-fixture.service',
             '/etc/systemd/system/multi-user.target.wants/a22x-fixture.service'], 'systemd')['profile']
        (self.path / 'distro-platform.json').write_bytes(d.canonical(self.profile))
        self.tar = self.sd / 'rootfs.tar.gz'
        self.bundle_path = self.sd / 'bundle.json'
        self.repack()
        self.home_inode = (self.live / 'home').stat().st_ino
        self.data_inode = (self.live / 'home/user/дані.txt').stat().st_ino

    def repack(self):
        archive(self.new, self.tar)
        self.bundle = {'schema': 'a22x.distro.bundle.v1', 'id': 'new-linux', 'name': 'New Linux fixture',
            'architecture': 'aarch64', 'boot_profile': d.PROFILE_ID, 'boot_sha256': d.BOOT_SHA256,
            'platform_profile_sha256': d.digest(self.profile),
            'rootfs': {'file': self.tar.name, 'compression': 'gzip',
                       'sha256': hashlib.sha256(self.tar.read_bytes()).hexdigest()}}
        self.bundle_path.write_text(json.dumps(self.bundle))

    def imported(self):
        plan = d.import_plan(self.context, 'sdcard-p1', '/bundle.json')['plan']
        result = d.install(self.context, plan, True)
        assert result['active'] == 'original' and not result['switched']
        return d.switch_plan(self.context, 'new-linux')['plan']

    def assert_home(self, test):
        test.assertEqual((self.live / 'home').stat().st_ino, self.home_inode)
        test.assertEqual((self.live / 'home/user/дані.txt').stat().st_ino, self.data_inode)
        test.assertEqual((self.live / 'home/user/дані.txt').read_text(), 'Особисті дані, не змінювати\n')

    def pending(self):
        return json.loads((self.live / d.MANAGER / 'transaction.json').read_text())


class TestElfClosure(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='a22x-elf-test-')
        self.addCleanup(self.tmp.cleanup)
        self.f = Fixture(self.tmp.name)

    def put(self, path, **kwargs):
        target = self.f.live / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(dynamic_arm_elf(**kwargs))
        target.chmod(0o755)

    def closure(self, path='usr/bin/systemd'):
        with fs.PinnedVolume(self.f.context, 'userdata') as pin, d.Tree(pin) as tree:
            return d.elf_dependencies(tree, path)

    def missing(self, executable, library):
        with self.assertRaises(d.DistroError) as caught:
            self.closure()
        self.assertEqual(caught.exception.code, 'MISSING_RUNTIME_LIBRARY')
        self.assertEqual(caught.exception.details, {'executable': executable, 'library': library})

    def test_current_systemd_private_sibling_layout(self):
        # The real installed systemd names core before shared; core has no
        # RPATH/RUNPATH. Init maps both before visiting core's DT_NEEDED list.
        core, shared = 'libsystemd-core-262-1.so', 'libsystemd-shared-262-1.so'
        self.put('usr/bin/systemd', needed=(core, shared, 'libgcc_s.so.1', 'libc.so.6', 'ld-linux-aarch64.so.1'),
                 runpath='/usr/lib/systemd', interpreter='/lib/ld-linux-aarch64.so.1')
        self.put('usr/lib/systemd/' + core, needed=(shared, 'libc.so.6'), soname=core)
        self.put('usr/lib/systemd/' + shared, needed=('libc.so.6',), soname=shared)
        for name in ('libgcc_s.so.1', 'libc.so.6', 'ld-linux-aarch64.so.1'):
            self.put('usr/lib/' + name, soname=name)
        result = self.closure()
        self.assertEqual(len(result), 6)
        self.assertIn('usr/lib/systemd/' + shared, result)
        with fs.PinnedVolume(self.f.context, 'userdata') as pin, d.Tree(pin) as tree:
            self.assertFalse(d.validate_root(tree, self.f.profile)['hardware_boot_tested'])
        (self.f.live / 'usr/lib/systemd' / shared).unlink()
        self.missing('usr/bin/systemd', shared)

    def test_runpath_is_not_inherited_by_child(self):
        self.put('usr/bin/systemd', needed=('core.so',), runpath='/usr/lib/private')
        self.put('usr/lib/private/core.so', needed=('shared.so',))
        self.put('usr/lib/private/shared.so')
        self.missing('usr/lib/private/core.so', 'shared.so')

    def test_loaded_request_name_without_soname_is_reused(self):
        self.put('usr/bin/systemd', needed=('core.so', 'shared.so'), runpath='/usr/lib/private')
        self.put('usr/lib/private/core.so', needed=('shared.so',))
        self.put('usr/lib/private/shared.so')
        self.assertIn('usr/lib/private/shared.so', self.closure())

    def test_loaded_soname_alias_is_reused(self):
        self.put('usr/bin/systemd', needed=('core.so', 'file.so'), runpath='/usr/lib/private')
        self.put('usr/lib/private/core.so', needed=('shared.so',))
        self.put('usr/lib/private/file.so', soname='shared.so')
        self.assertIn('usr/lib/private/file.so', self.closure())
        self.assertFalse((self.f.live / 'usr/lib/private/shared.so').exists())

    def test_future_grandchild_cannot_satisfy_earlier_request(self):
        for order in (('core.so', 'parent.so'), ('parent.so', 'core.so')):
            with self.subTest(order=order):
                self.put('usr/bin/systemd', needed=order)
                self.put('usr/lib/core.so', needed=('private.so',))
                self.put('usr/lib/parent.so', needed=('private.so',), runpath='/usr/lib/private')
                self.put('usr/lib/private/private.so')
                if order[0] == 'core.so':
                    self.missing('usr/lib/core.so', 'private.so')
                else:
                    self.assertIn('usr/lib/private/private.so', self.closure())

    def test_runpath_search_order_is_left_to_right(self):
        self.put('usr/bin/systemd', needed=('shared.so',), runpath='/opt/first:/opt/second')
        self.put('opt/first/shared.so')
        self.put('opt/second/shared.so', needed=('missing.so',))
        result = self.closure()
        self.assertIn('opt/first/shared.so', result)
        self.assertNotIn('opt/second/shared.so', result)

    def test_legacy_rpath_is_inherited_but_runpath_suppresses_it(self):
        self.put('usr/bin/systemd', needed=('core.so',), rpath='/usr/lib/private')
        self.put('usr/lib/private/core.so', needed=('shared.so',))
        self.put('usr/lib/private/shared.so')
        self.assertIn('usr/lib/private/shared.so', self.closure())
        self.put('usr/lib/private/core.so', needed=('shared.so',), runpath='/usr/lib/unrelated')
        self.missing('usr/lib/private/core.so', 'shared.so')

    def test_runpath_overrides_same_object_rpath(self):
        self.put('usr/bin/systemd', needed=('shared.so',), rpath='/opt/old', runpath='/opt/new')
        self.put('opt/old/shared.so', needed=('missing.so',))
        self.put('opt/new/shared.so')
        self.assertIn('opt/new/shared.so', self.closure())

    def test_loaded_sibling_still_validates_its_own_dependencies(self):
        self.put('usr/bin/systemd', needed=('core.so', 'shared.so'), runpath='/usr/lib/private')
        self.put('usr/lib/private/core.so', needed=('shared.so',))
        self.put('usr/lib/private/shared.so', needed=('missing.so',))
        self.missing('usr/lib/private/shared.so', 'missing.so')

    def test_dynamic_needed_without_string_table_is_rejected(self):
        self.put('usr/bin/systemd', needed=('missing.so',))
        path = self.f.live / 'usr/bin/systemd'
        data = bytearray(path.read_bytes())
        offset = struct.unpack_from('<Q', data, 128)[0]
        struct.pack_into('<Q', data, offset, 21)  # Replace DT_STRTAB with DT_DEBUG.
        path.write_bytes(data)
        with self.assertRaises(d.DistroError) as caught:
            self.closure()
        self.assertEqual(caught.exception.code, 'INVALID_ELF')


class TestDistro(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='a22x-distro-test-')
        self.addCleanup(self.tmp.cleanup)
        self.f = Fixture(self.tmp.name)

    def error(self, code, action):
        with self.assertRaises(fs.FilesystemError) as caught:
            action()
        self.assertEqual(caught.exception.code, code, str(caught.exception))

    def test_import_switch_return_preserves_home_and_each_system(self):
        plan = self.f.imported()
        status = d.status(self.f.context)
        self.assertEqual(status['active'], 'original')
        self.assertEqual(len(status['slots']), 2)
        self.assertEqual((self.f.live / 'var/lib/pkg/state').read_text(), 'old')
        result = d.switch(self.f.context, plan, True)
        self.assertEqual(result['active'], 'new-linux')
        self.assertEqual((self.f.live / 'var/lib/pkg/state').read_text(), 'new')
        (self.f.live / 'var/lib/pkg/new-setting').write_text('new OS state')
        self.f.assert_home(self)
        back = d.switch_plan(self.f.context, 'original')['plan']
        d.switch(self.f.context, back, True)
        self.assertEqual((self.f.live / 'var/lib/pkg/state').read_text(), 'old')
        self.assertFalse((self.f.live / 'var/lib/pkg/new-setting').exists())
        inactive = self.f.live / d.MANAGER / 'slots/new-linux/root'
        self.assertEqual((inactive / 'var/lib/pkg/new-setting').read_text(), 'new OS state')
        self.f.assert_home(self)
        self.assertTrue(d.boot_check(self.f.context)['normal_boot_allowed'])
        self.assertFalse(d.sentinel_path(self.f.context).exists())

    def test_all_forward_cut_points_resume(self):
        # Each rename is tested before, after (before its completion record), and
        # after the durable checkpoint. Every case gets a fresh owned fixture.
        for boundary in ('before-rename', 'after-rename', 'after-checkpoint'):
            for cut in range(28):
                with tempfile.TemporaryDirectory(prefix='a22x-distro-cut-') as tmp:
                    f = Fixture(tmp)
                    plan = f.imported()
                    count = len(plan['binding']['live_entries']) + len(plan['binding']['target_entries'])
                    if cut >= count:
                        break
                    def stop(kind, step):
                        if kind == boundary and step == cut:
                            raise StopForTest()
                    with self.assertRaises(StopForTest):
                        d.switch(f.context, plan, True, stop)
                    tx = f.pending()
                    self.assertTrue(d.status(f.context)['reboot_blocked'])
                    self.error('SWITCH_PENDING', lambda: d.boot_check(f.context))
                    d.recover(f.context, 'resume', tx['id'], True)
                    self.assertEqual((f.live / 'var/lib/pkg/state').read_text(), 'new')
                    f.assert_home(self)

    def test_all_forward_cut_points_rollback(self):
        for boundary in ('before-rename', 'after-rename', 'after-checkpoint'):
            for cut in range(28):
                with tempfile.TemporaryDirectory(prefix='a22x-distro-rollback-') as tmp:
                    f = Fixture(tmp)
                    plan = f.imported()
                    count = len(plan['binding']['live_entries']) + len(plan['binding']['target_entries'])
                    if cut >= count:
                        break
                    def stop(kind, step):
                        if kind == boundary and step == cut:
                            raise StopForTest()
                    with self.assertRaises(StopForTest):
                        d.switch(f.context, plan, True, stop)
                    tx = f.pending()
                    d.recover(f.context, 'rollback', tx['id'], True)
                    self.assertEqual((f.live / 'var/lib/pkg/state').read_text(), 'old')
                    f.assert_home(self)

    def test_rollback_interrupted_then_resume_or_rollback(self):
        for next_direction in ('resume', 'rollback'):
            for boundary in ('before-rollback', 'after-rollback'):
                with tempfile.TemporaryDirectory(prefix='a22x-distro-reverse-') as tmp:
                    f = Fixture(tmp)
                    plan = f.imported()
                    def stop_forward(kind, step):
                        if kind == 'after-checkpoint' and step == 20:
                            raise StopForTest()
                    with self.assertRaises(StopForTest):
                        d.switch(f.context, plan, True, stop_forward)
                    tx = f.pending()
                    def stop_reverse(kind, step):
                        if kind == boundary and step == 18:
                            raise StopForTest()
                    with self.assertRaises(StopForTest):
                        d.recover(f.context, 'rollback', tx['id'], True, stop_reverse)
                    d.recover(f.context, next_direction, tx['id'], True)
                    self.assertEqual((f.live / 'var/lib/pkg/state').read_text(),
                                     'new' if next_direction == 'resume' else 'old')
                    f.assert_home(self)

    def test_final_commit_cuts_resume_or_rollback(self):
        for boundary in ('before-state-commit', 'after-state-commit'):
            for direction in ('resume', 'rollback'):
                with tempfile.TemporaryDirectory(prefix='a22x-distro-final-') as tmp:
                    f = Fixture(tmp)
                    plan = f.imported()
                    def stop(kind, step):
                        if kind == boundary:
                            raise StopForTest()
                    with self.assertRaises(StopForTest):
                        d.switch(f.context, plan, True, stop)
                    tx = f.pending()
                    d.recover(f.context, direction, tx['id'], True)
                    self.assertEqual((f.live / 'var/lib/pkg/state').read_text(), 'new' if direction == 'resume' else 'old')
                    f.assert_home(self)

    def test_history_commit_cut_guard_reconciled_by_status(self):
        plan = self.f.imported()
        def stop(kind, step):
            if kind == 'after-history-commit':
                raise StopForTest()
        with self.assertRaises(StopForTest):
            d.switch(self.f.context, plan, True, stop)
        self.assertTrue(d.sentinel_path(self.f.context).exists())
        self.assertFalse((self.f.live / d.MANAGER / 'transaction.json').exists())
        result = d.status(self.f.context)
        self.assertFalse(result['reboot_blocked'])
        self.assertEqual(result['active'], 'new-linux')
        self.assertFalse(d.sentinel_path(self.f.context).exists())
        self.f.assert_home(self)

    def test_unknown_guard_is_not_silently_cleared(self):
        fs.atomic_state(d.sentinel_path(self.f.context), {'schema': 'a22x.distro.transaction.v1',
                                                        'transaction_id': 'switch-unknown'})
        self.error('SENTINEL_UNRESOLVED', lambda: d.boot_check(self.f.context))
        self.assertTrue(d.sentinel_path(self.f.context).exists())

    def test_incomplete_setup_preserved_without_blocking_switch(self):
        orphan = self.f.live / '.a22x-systems-setup-1234567890abcdef'
        orphan.mkdir()
        (orphan / 'do-not-delete').write_text('review me')
        self.assertIn(orphan.name, d.status(self.f.context)['incomplete_setups'])
        plan = self.f.imported()
        d.switch(self.f.context, plan, True)
        self.assertEqual((orphan / 'do-not-delete').read_text(), 'review me')
        self.f.assert_home(self)

    def test_cross_home_hardlink_refused(self):
        os.link(self.f.live / 'home/user/дані.txt', self.f.live / 'etc/shared-alias')
        plan = d.import_plan(self.f.context, 'sdcard-p1', '/bundle.json')['plan']
        self.error('HOME_CROSS_ROOT_HARDLINK', lambda: d.install(self.f.context, plan, True))
        self.f.assert_home(self)

    def test_hardlinks_fully_inside_home_are_preserved(self):
        os.link(self.f.live / 'home/user/дані.txt', self.f.live / 'home/user/alias')
        plan = self.f.imported()
        d.switch(self.f.context, plan, True)
        self.assertEqual((self.f.live / 'home/user/alias').stat().st_ino, self.f.data_inode)
        self.f.assert_home(self)

    def test_named_acl_owner_is_validated(self):
        # Cloud sandbox may expose only one mapped UID, so inject the native ACL
        # xattr bytes into this unit test. The actual host ACL test is separate.
        acl = struct.pack('<IHHI', 2, 2, 4, 2001)
        original = d.os.getxattr
        def get_acl(fd, name, *args, **kwargs):
            if name == 'system.posix_acl_access' and os.fstat(fd).st_ino == self.f.data_inode:
                return acl
            return original(fd, name, *args, **kwargs)
        plan = d.import_plan(self.f.context, 'sdcard-p1', '/bundle.json')['plan']
        with mock.patch.object(d.os, 'getxattr', get_acl):
            self.error('HOME_UNKNOWN_UID', lambda: d.install(self.f.context, plan, True))

    def test_native_named_acl_owner_is_validated(self):
        raw = struct.pack('<I', 2) + b''.join(struct.pack('<HHI', *entry) for entry in
            ((1, 6, 0xffffffff), (2, 4, 2001), (4, 4, 0xffffffff), (16, 4, 0xffffffff), (32, 0, 0xffffffff)))
        try:
            os.setxattr(self.f.live / 'home/user/дані.txt', 'system.posix_acl_access', raw)
        except OSError as exc:
            if exc.errno in (errno.EINVAL, errno.EPERM, errno.EOPNOTSUPP, errno.ENOTSUP):
                self.skipTest('This sandbox filesystem/UID namespace cannot store the native named ACL')
            raise
        plan = d.import_plan(self.f.context, 'sdcard-p1', '/bundle.json')['plan']
        self.error('HOME_UNKNOWN_UID', lambda: d.install(self.f.context, plan, True))

    def test_system_init_cannot_depend_on_shared_home_template(self):
        (self.f.new / 'home/systemd').write_bytes(arm_elf())
        (self.f.new / 'home/systemd').chmod(0o755)
        (self.f.new / 'usr/bin/init').unlink()
        (self.f.new / 'usr/bin/init').symlink_to('/home/systemd')
        self.f.repack()
        plan = d.import_plan(self.f.context, 'sdcard-p1', '/bundle.json')['plan']
        self.error('SHARED_RUNTIME_DEPENDENCY', lambda: d.install(self.f.context, plan, True))

    def test_saved_plan_summary_and_exact_file_binding(self):
        result = d.import_plan(self.f.context, 'sdcard-p1', '/bundle.json')
        out = self.f.path / 'plan.json'
        summary = d.save_plan_result(self.f.context, result, out)
        self.assertNotIn('binding', summary['plan'])
        self.assertEqual(stat.S_IMODE(out.stat().st_mode), 0o600)
        plan = d.load_plan_file(out, summary['plan']['seal'], summary['plan_file_sha256'])
        self.assertEqual(plan, result['plan'])
        out.write_bytes(out.read_bytes() + b' ')
        self.error('PLAN_FILE_CHANGED', lambda: d.load_plan_file(out, summary['plan']['seal'], summary['plan_file_sha256']))

    def test_saved_plan_cannot_overwrite_or_use_volume(self):
        result = d.import_plan(self.f.context, 'sdcard-p1', '/bundle.json')
        out = self.f.path / 'plan.json'
        out.write_text('existing')
        with self.assertRaises(FileExistsError):
            d.save_plan_result(self.f.context, result, out)
        self.assertEqual(out.read_text(), 'existing')
        self.error('INVALID_PLAN_OUTPUT', lambda: d.save_plan_result(self.f.context, result, self.f.live / 'plan.json'))

    def test_guard_sbin_removed_first_and_committed_last(self):
        plan = self.f.imported()
        total = len(plan['binding']['live_entries']) + len(plan['binding']['target_entries'])
        seen = []
        def observe(kind, step):
            if kind == 'after-checkpoint':
                seen.append(step)
                if step < total - 1:
                    self.assertFalse(os.path.lexists(self.f.live / 'sbin'))
                else:
                    self.assertTrue((self.f.live / 'sbin/init').exists())
        d.switch(self.f.context, plan, True, observe)
        self.assertEqual(seen, list(range(total)))

    def test_home_uid_mismatch_refused_before_commit(self):
        p = self.f.new / 'etc/passwd'
        p.write_text(p.read_text().replace('user:x:1000:', 'user:x:1001:'))
        self.f.repack()
        plan = d.import_plan(self.f.context, 'sdcard-p1', '/bundle.json')['plan']
        self.error('HOME_ACCOUNT_MISMATCH', lambda: d.install(self.f.context, plan, True))
        self.assertEqual((self.f.live / 'var/lib/pkg/state').read_text(), 'old')
        self.assertFalse((self.f.live / d.MANAGER / 'slots/new-linux').exists())
        self.f.assert_home(self)

    def test_wrong_platform_file_rejected(self):
        (self.f.new / 'usr/lib/firmware/a22x.bin').write_bytes(b'wrong firmware')
        self.f.repack()
        plan = d.import_plan(self.f.context, 'sdcard-p1', '/bundle.json')['plan']
        self.error('PLATFORM_FILE_MISMATCH', lambda: d.install(self.f.context, plan, True))
        self.assertEqual((self.f.live / 'var/lib/pkg/state').read_text(), 'old')

    def test_wrong_architecture_refused(self):
        (self.f.new / 'usr/bin/systemd').write_bytes(arm_elf(62))
        self.f.repack()
        plan = d.import_plan(self.f.context, 'sdcard-p1', '/bundle.json')['plan']
        self.error('WRONG_ARCHITECTURE', lambda: d.install(self.f.context, plan, True))

    def test_missing_dynamic_interpreter_refused(self):
        path = self.f.new / 'usr/bin/systemd'
        data = bytearray(256)
        data[:128] = arm_elf()
        struct.pack_into('<H', data, 56, 2)
        text = b'/lib/missing-loader.so\0'
        struct.pack_into('<IIQQQQQQ', data, 120, 3, 4, 200, 0, 0, len(text), len(text), 1)
        data[200:200+len(text)] = text
        path.write_bytes(data)
        self.f.repack()
        plan = d.import_plan(self.f.context, 'sdcard-p1', '/bundle.json')['plan']
        with self.assertRaises(FileNotFoundError):
            d.install(self.f.context, plan, True)

    def test_foreign_required_mount_refused(self):
        (self.f.new / 'etc/fstab').write_text('/dev/vda1 / ext4 defaults 0 1\n')
        self.f.repack()
        plan = d.import_plan(self.f.context, 'sdcard-p1', '/bundle.json')['plan']
        self.error('ROOT_FSTAB_MISMATCH', lambda: d.install(self.f.context, plan, True))

    def test_shared_home_mount_refused(self):
        (self.f.new / 'etc/fstab').write_text('UUID=other /home ext4 defaults 0 2\n')
        self.f.repack()
        plan = d.import_plan(self.f.context, 'sdcard-p1', '/bundle.json')['plan']
        self.error('SEPARATE_HOME_FORBIDDEN', lambda: d.install(self.f.context, plan, True))

    def test_fstab_canonical_targets_cannot_mask_data_or_system(self):
        cases = [('/home/', 'SEPARATE_HOME_FORBIDDEN'), ('/home//user', 'SEPARATE_HOME_FORBIDDEN'),
                 ('/home/user', 'SEPARATE_HOME_FORBIDDEN'), ('/home/../etc', 'CRITICAL_MOUNT_FORBIDDEN'),
                 ('/usr', 'CRITICAL_MOUNT_FORBIDDEN'), ('/\\150ome', 'SEPARATE_HOME_FORBIDDEN'),
                 ('/' + d.MANAGER, 'CRITICAL_MOUNT_FORBIDDEN'), ('/dev', 'VIRTUAL_MOUNT_FORBIDDEN')]
        with fs.PinnedVolume(self.f.context, 'userdata') as pin, d.Tree(pin) as tree:
            for target, code in cases:
                (self.f.live / 'etc/fstab').write_text('tmpfs ' + target + ' tmpfs defaults,nofail 0 0\n')
                self.error(code, lambda: d.validate_fstab(tree, None))

    def test_fstab_known_runtime_and_optional_media_mounts(self):
        (self.f.live / 'etc/fstab').write_text('PARTLABEL=userdata / ext4 defaults 0 1\n'
            'tmpfs /run tmpfs mode=0755 0 0\nproc /proc proc defaults 0 0\n'
            'devpts /dev/pts devpts defaults 0 0\nUUID=external /mnt/sd ext4 nofail 0 2\n')
        with fs.PinnedVolume(self.f.context, 'userdata') as pin, d.Tree(pin) as tree:
            self.assertEqual(d.validate_fstab(tree, None)['entries'], 5)

    def test_archive_changed_after_plan(self):
        plan = d.import_plan(self.f.context, 'sdcard-p1', '/bundle.json')['plan']
        data = bytearray(self.f.tar.read_bytes())
        data[-1] ^= 1
        self.f.tar.write_bytes(data)
        with self.assertRaises((fs.FilesystemError, tarfile.TarError, EOFError)):
            d.install(self.f.context, plan, True)
        self.assertFalse((self.f.live / d.MANAGER).exists())

    def test_plan_seal_and_expiry(self):
        plan = self.f.imported()
        changed = copy.deepcopy(plan)
        changed['slot'] = 'original'
        self.error('RECORD_CHANGED', lambda: d.switch(self.f.context, changed, True))
        expired = dict(plan)
        expired.pop('seal')
        expired['expires_utc'] = 0
        self.error('PLAN_EXPIRED', lambda: d.switch(self.f.context, d.seal(expired), True))

    def test_stale_home_metadata_after_plan(self):
        plan = self.f.imported()
        (self.f.live / 'home/new-file').write_text('new user state')
        self.error('PLAN_STALE', lambda: d.switch(self.f.context, plan, True))
        self.f.assert_home(self)

    def test_unknown_path_inserted_during_transaction_is_not_overwritten(self):
        plan = self.f.imported()
        def stop(kind, step):
            if kind == 'after-checkpoint' and step == 0:
                raise StopForTest()
        with self.assertRaises(StopForTest):
            d.switch(self.f.context, plan, True, stop)
        (self.f.live / 'sbin').mkdir()
        (self.f.live / 'sbin/foreign.txt').write_text('foreign')
        tx = self.f.pending()
        self.error('TRANSACTION_PATH_CHANGED', lambda: d.recover(self.f.context, 'rollback', tx['id'], True))
        self.assertEqual((self.f.live / 'sbin/foreign.txt').read_text(), 'foreign')
        self.assertTrue(d.sentinel_path(self.f.context).exists())

    def test_confirmation_required(self):
        plan = d.import_plan(self.f.context, 'sdcard-p1', '/bundle.json')['plan']
        self.error('CONFIRMATION_REQUIRED', lambda: d.install(self.f.context, plan, False))

    def test_source_userdata_refused(self):
        self.error('BUNDLE_ON_USERDATA', lambda: d.import_plan(self.f.context, 'userdata', '/bundle.json'))

    def test_no_auto_mount_status(self):
        self.f.context.unmount('userdata')
        result = d.status(self.f.context)
        self.assertFalse(result['mounted'])
        self.assertEqual(result['transaction_state'], 'unknown-unmounted')
        self.assertFalse(self.f.context.volume('userdata')['mounted'])

    def test_boot_check_temporarily_mounts_ro_then_restores(self):
        self.f.context.unmount('userdata')
        result = d.boot_check(self.f.context)
        self.assertTrue(result['normal_boot_allowed'])
        self.assertTrue(result['temporary_read_only_mount'])
        self.assertFalse(self.f.context.volume('userdata')['mounted'])

    def test_boot_check_missing_init_restores_unmounted_state(self):
        (self.f.live / 'usr/bin/init').unlink()
        self.f.context.unmount('userdata')
        self.error('LINUX_INIT_MISSING', lambda: d.boot_check(self.f.context))
        self.assertFalse(self.f.context.volume('userdata')['mounted'])

    def test_boot_check_preserves_existing_rw_or_ro_mount(self):
        for mode in ('rw', 'ro'):
            self.f.context.mount('userdata', mode, True)
            before = self.f.context.volume('userdata')
            d.boot_check(self.f.context)
            after = self.f.context.volume('userdata')
            self.assertEqual(before, after)

    def test_existing_slot_never_overwritten(self):
        self.f.imported()
        self.error('SLOT_EXISTS', lambda: d.import_plan(self.f.context, 'sdcard-p1', '/bundle.json'))

    def test_missing_profile_does_not_touch_userdata(self):
        (self.f.path / 'distro-platform.json').unlink()
        self.error('PLATFORM_PROFILE_MISSING', lambda: d.import_plan(self.f.context, 'sdcard-p1', '/bundle.json'))
        self.assertFalse((self.f.live / d.MANAGER).exists())


class TestArchiveBoundary(unittest.TestCase):
    def check(self, items, code):
        with tempfile.TemporaryDirectory(prefix='a22x-archive-test-') as tmp:
            path = Path(tmp) / 'bad.tar'
            with tarfile.open(path, 'w', format=tarfile.PAX_FORMAT) as out:
                for name, typ, target, data in items:
                    item = tarfile.TarInfo(name)
                    item.type, item.linkname = typ, target
                    item.size = len(data) if typ == tarfile.REGTYPE else 0
                    out.addfile(item, io.BytesIO(data) if typ == tarfile.REGTYPE else None)
            fd = os.open(path, os.O_RDONLY)
            try:
                with self.assertRaises(fs.FilesystemError) as caught:
                    d.scan_archive(fd, 'none', hashlib.sha256(path.read_bytes()).hexdigest())
                self.assertEqual(caught.exception.code, code)
            finally:
                os.close(fd)

    def test_parent_escape(self):
        self.check([('../outside', tarfile.REGTYPE, '', b'bad')], 'INVALID_PATH')

    def test_absolute_entry(self):
        self.check([('/etc/passwd', tarfile.REGTYPE, '', b'bad')], 'INVALID_PATH')

    def test_duplicate_file(self):
        self.check([('x', tarfile.REGTYPE, '', b'a'), ('x', tarfile.REGTYPE, '', b'b')], 'DUPLICATE_OR_LARGE_ARCHIVE')

    def test_symlink_escape(self):
        self.check([('etc/link', tarfile.SYMTYPE, '../../outside', b'')], 'UNSAFE_LINK')

    def test_symlink_parent_both_orders(self):
        entries = [('etc', tarfile.SYMTYPE, 'usr/etc', b''), ('etc/data', tarfile.REGTYPE, '', b'bad')]
        for order in (entries, list(reversed(entries))):
            self.check(order, 'ARCHIVE_LINK_PARENT')

    def test_hardlink_escape(self):
        self.check([('x', tarfile.LNKTYPE, '../outside', b'')], 'INVALID_PATH')

    def test_hardlink_missing_target(self):
        self.check([('x', tarfile.LNKTYPE, 'missing', b'')], 'UNSAFE_HARDLINK')

    def test_runtime_dev_payload(self):
        self.check([('dev/null', tarfile.REGTYPE, '', b'')], 'LIVE_RUNTIME_CONTENT')

    def test_special_file(self):
        self.check([('etc/fifo', tarfile.FIFOTYPE, '', b'')], 'ARCHIVE_SPECIAL_FILE')

    def test_manager_overwrite(self):
        self.check([(d.MANAGER + '/state.json', tarfile.REGTYPE, '', b'bad')], 'RESERVED_ARCHIVE_PATH')

    def test_pax_allocation_bound(self):
        item = tarfile.TarInfo('pax')
        item.type = tarfile.XHDTYPE
        item.size = d.MAX_PAX + 1
        with self.assertRaises(fs.FilesystemError) as caught:
            d.BoundedTarInfo.frombuf(item.tobuf(), 'utf-8', 'surrogateescape')
        self.assertEqual(caught.exception.code, 'ARCHIVE_METADATA_TOO_LARGE')

    def test_pax_resolved_file_size_limit(self):
        with tempfile.TemporaryFile() as stream:
            with tarfile.open(fileobj=stream, mode='w', format=tarfile.PAX_FORMAT) as out:
                item = tarfile.TarInfo('x')
                item.pax_headers = {'size': '2048'}
                out.addfile(item)
            stream.seek(0)
            expected = hashlib.sha256(stream.read()).hexdigest()
            with mock.patch.object(d, 'MAX_FILE', 1024):
                with self.assertRaises(fs.FilesystemError) as caught:
                    d.scan_archive(stream.fileno(), 'none', expected)
            self.assertEqual(caught.exception.code, 'ARCHIVE_MEMBER_TOO_LARGE')

    def test_aggregate_pax_budget(self):
        with tempfile.TemporaryFile() as stream:
            with tarfile.open(fileobj=stream, mode='w', format=tarfile.PAX_FORMAT) as out:
                for i in range(10):
                    item = tarfile.TarInfo('x%d' % i)
                    item.pax_headers = {'unknown': 'x' * 100}
                    out.addfile(item)
            stream.seek(0)
            expected = hashlib.sha256(stream.read()).hexdigest()
            with mock.patch.object(d, 'MAX_ARCHIVE_METADATA', 2000):
                with self.assertRaises(fs.FilesystemError) as caught:
                    d.scan_archive(stream.fileno(), 'none', expected)
            self.assertEqual(caught.exception.code, 'ARCHIVE_METADATA_TOO_LARGE')

    def test_inherited_pax_member_budget(self):
        with tempfile.TemporaryFile() as stream:
            with tarfile.open(fileobj=stream, mode='w', format=tarfile.PAX_FORMAT,
                              pax_headers={'unknown': 'x' * 100}) as out:
                out.addfile(tarfile.TarInfo('x'))
            stream.seek(0)
            expected = hashlib.sha256(stream.read()).hexdigest()
            with mock.patch.object(d, 'MAX_PAX', 128):
                with self.assertRaises(fs.FilesystemError) as caught:
                    d.scan_archive(stream.fileno(), 'none', expected)
            self.assertEqual(caught.exception.code, 'ARCHIVE_METADATA_TOO_LARGE')


if __name__ == '__main__':
    unittest.main()
