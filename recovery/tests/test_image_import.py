#!/usr/bin/env python3
"""On-device image install, exercised on regular directories. Needs a user namespace:
unshare --map-auto --map-root-user python3 -B -m unittest tests.test_image_import"""
import hashlib
import io
import json
import os
import sys
import struct
import shutil
import subprocess
import tarfile
import tempfile
import types
import unittest
from unittest import mock
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import distro as d
import image_import as m
from test_distro import Fixture, arm_elf


class ImageFixture(Fixture):
    def __init__(self, parent):
        super().__init__(parent)
        path = self.new / 'usr/lib/systemd/systemd'
        path.write_bytes(arm_elf())
        path.chmod(0o755)
        (self.new / 'etc/hostname').write_text('old\n')
        (self.new / 'usr/lib/firmware/foreign.bin').write_bytes(b'x')
        # A glibc system with libxcrypt, as the distributions in the catalog: it reads yescrypt.
        (self.new / 'usr/lib/libcrypt.so.2').write_bytes(b'')
        conn = self.live / 'opt/a22x-conn'
        conn.mkdir(parents=True)
        for name in ('wmt_drv.ko', 'bt_addr', 'kernel-release'):
            (conn / name).write_text(name)
        (self.live / 'etc/shadow').write_text('user:$y$hash:20000:0:99999:7:::\n')
        systemd = self.live / 'etc/systemd/system'
        (systemd / 'a22x-conn.service').write_text('[Service]\n')
        (systemd / 'multi-user.target.wants/a22x-conn.service').symlink_to('/etc/systemd/system/a22x-conn.service')
        (self.live / 'etc/modules-load.d').mkdir()
        nm = self.live / 'etc/NetworkManager/conf.d'
        nm.mkdir(parents=True)
        (nm / '90-a22x-wifi.conf').write_text('[keyfile]\n')
        saved = self.live / 'etc/NetworkManager/system-connections'
        saved.mkdir()
        (saved / 'home.nmconnection').write_text('[wifi]\nssid=Дім\n[wifi-security]\npsk=secret\n')
        helpers = self.path / 'helpers'
        helpers.mkdir()
        for name in ('a22x-wmt', 'busybox', 'conn-start.sh', 'a22x-firstboot.sh'):
            (helpers / name).write_text('#!/bin/sh\n')
        os.environ['A22X_PLATFORM_HELPERS'] = str(helpers)
        self.image = self.sd / 'distro-1.0.tar.gz'
        with tarfile.open(self.image, 'w:gz', format=tarfile.PAX_FORMAT) as out:
            for item in sorted(self.new.rglob('*')):
                out.add(item, arcname=item.relative_to(self.new).as_posix(), recursive=False)


class TestCompressedPartitionImages(unittest.TestCase):
    """2026-11-07: a dd copy of userdata (53 GB, 17 GB used, 3.5 GB zstd) needed its full size unpacked."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='a22x-sparse-')
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def image(self, name, blocks, free):
        raw = bytearray(32 << 20)
        struct.pack_into('<I', raw, 1028, blocks)
        struct.pack_into('<I', raw, 1036, free)
        struct.pack_into('<I', raw, 1048, 2)
        struct.pack_into('<H', raw, 1080, 0xEF53)
        raw[12 << 20:13 << 20] = os.urandom(1 << 20)
        path = self.dir / name
        path.write_bytes(raw)
        subprocess.run([m.ZSTD, '-q', '-1', '--rm', str(path)], check=True)
        return Path(str(path) + '.zst'), bytes(raw)

    def test_plan_counts_what_the_filesystem_holds(self):
        packed, _ = self.image('userdata.img', 13035520, 8523829)
        info = m.describe(packed)
        self.assertEqual(info['format'], 'compressed-image')
        self.assertEqual(info['unpacked_bytes'], 13035520 * 4096)
        self.assertEqual(info['used_bytes'], (13035520 - 8523829) * 4096)

    def test_unknown_contents_keep_the_old_estimate(self):
        path = self.dir / 'noise.img'
        path.write_bytes(os.urandom(1 << 20))
        subprocess.run([m.ZSTD, '-q', '--rm', str(path)], check=True)
        self.assertNotIn('used_bytes', m.describe(self.dir / 'noise.img.zst'))

    def test_empty_space_is_not_written(self):
        packed, raw = self.image('part.img', 8192, 4096)
        stream, process = m.open_stream(packed, 'zstd')
        with open(self.dir / 'out.img', 'wb') as out:
            self.assertEqual(m.write_sparse(stream, out), len(raw))
        self.assertEqual(process.wait(), 0)
        stream.close()
        out = self.dir / 'out.img'
        self.assertEqual(out.read_bytes(), raw)
        self.assertLess(out.stat().st_blocks * 512, 4 << 20)

    def test_a_full_volume_stops_the_copy(self):
        packed, _ = self.image('big.img', 8192, 0)
        stream, process = m.open_stream(packed, 'zstd')
        full = os.statvfs_result((4096, 4096, 100, 10, 10, 10, 10, 10, 0, 255))
        with open(self.dir / 'out.img', 'wb') as out, mock.patch.object(m.os, 'fstatvfs', return_value=full):
            with self.assertRaises(m.ImageError) as caught:
                m.write_sparse(stream, out)
        process.kill()
        process.wait()
        stream.close()
        self.assertEqual(caught.exception.code, 'INSUFFICIENT_SPACE')


def optional_bundle(fixture, work):
    """What the recovery keeps for systems without them: this phone's sound profile and mixer state."""
    src = Path(work) / 'optional-src'
    ucm = src / 'usr/share/alsa/ucm2/MediaTek/mt6833/mt6359-sia8109'
    ucm.mkdir(parents=True)
    (ucm / 'mt6833-mt6359.conf').write_text('Syntax 4\n')
    (src / 'usr/share/alsa/ucm2/conf.d/mt6833_mt6359').mkdir(parents=True)
    (src / 'usr/share/alsa/ucm2/conf.d/mt6833_mt6359/mt6833-mt6359.conf').symlink_to(
        '../../MediaTek/mt6833/mt6359-sia8109/mt6833-mt6359.conf')
    (src / 'var/lib/alsa').mkdir(parents=True)
    (src / 'var/lib/alsa/asound.state').write_text('state.mt6833mt6359 {}\n')
    with tarfile.open(fixture.path / 'platform-optional.tar.xz', 'w:xz') as out:
        for item in sorted(src.rglob('*')):
            out.add(item, arcname=item.relative_to(src).as_posix(), recursive=False)


class TestPlatformLibLink(unittest.TestCase):
    def test_the_image_lib_link_takes_the_platform_owner(self):
        """2026-11-10: postmarketOS ships /lib -> usr/lib as root:1000; the adapter keeps the
        image's link and gives it the owner the platform profile names (the final check failed
        with PLATFORM_METADATA_MISMATCH)."""
        import a22x_adapter
        with tempfile.TemporaryDirectory(prefix='a22x-lib-') as tmp:
            root = Path(tmp) / 'root'
            (root / 'usr/lib').mkdir(parents=True)
            (root / 'lib').symlink_to('usr/lib')
            bundle = Path(tmp) / 'platform.tar'
            with tarfile.open(bundle, 'w') as out:
                link = tarfile.TarInfo('lib')
                link.type, link.linkname, link.uid, link.gid = tarfile.SYMTYPE, 'usr/lib', 0, 0
                out.addfile(link)
            calls = []
            real = os.chown
            def record(path, uid, gid, *, follow_symlinks=True):
                calls.append((Path(path), uid, gid, follow_symlinks))
                return real(path, os.getuid(), os.getgid(), follow_symlinks=follow_symlinks)
            with mock.patch.object(a22x_adapter.os, 'chown', record):
                a22x_adapter.install_platform(root, mock.Mock(platform=str(bundle)))
            self.assertIn((root / 'lib', 0, 0, False), calls)
            self.assertEqual(os.readlink(root / 'lib'), 'usr/lib')
            # The import takes the platform files from a directory (the running system or the
            # unpacked bundle) and never copies "lib": the image's link takes that link's owner.
            source = Path(tmp) / 'source'
            (source / 'usr/lib').mkdir(parents=True)
            (source / 'lib').symlink_to('usr/lib')
            calls.clear()
            with mock.patch.object(a22x_adapter.os, 'chown', record):
                a22x_adapter.install_platform(root, mock.Mock(platform=str(source), platform_paths=[], platform_optional=()))
            owner = os.lstat(source / 'lib')
            self.assertIn((root / 'lib', owner.st_uid, owner.st_gid, False), calls)


class TestImageInstall(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='a22x-image-')
        self.addCleanup(self.tmp.cleanup)
        self.f = ImageFixture(self.tmp.name)

    def plan(self, **kw):
        options = d.image_options(kw.get('desktop', 'console'), kw.get('account', 'keep'), kw.get('username'),
                                  kw.get('password'), 0, None, shells=kw.get('shells', ()), greeter=kw.get('greeter', 'auto'))
        return d.image_plan(self.f.context, 'sdcard-p1', '/distro-1.0.tar.gz', options)['plan']

    def root(self, slot):
        return self.f.live / d.MANAGER / 'slots' / slot / 'root'

    def test_install_and_switch(self):
        plan = self.plan()
        self.assertEqual(plan['slot'], 'distro-1-0')
        result = d.install(self.f.context, plan, True)
        self.assertFalse(result['switched'])
        root = self.root('distro-1-0')
        self.assertFalse((root / 'usr/lib/firmware/foreign.bin').exists())
        self.assertTrue((root / 'opt/a22x-conn/wmt_drv.ko').exists())
        self.assertTrue((root / 'usr/bin/a22x-reboot').exists())
        self.assertIn('$y$hash', (root / 'etc/shadow').read_text())
        self.assertEqual((root / 'etc/fstab').read_text()[0], '#')
        # The host is named after the distribution's ID, not the slot id (2026-11-10).
        ident = dict(line.split('=', 1) for line in (root / 'etc/os-release').read_text().splitlines() if '=' in line)
        self.assertEqual((root / 'etc/hostname').read_text(), ident['ID'].strip('"').lower() + '-a22x\n')
        self.assertFalse((root / 'etc/a22x/firstboot.conf').exists())
        self.assertFalse(list((self.f.live / d.MANAGER / 'staging').iterdir()))
        self.f.assert_home(self)
        d.switch(self.f.context, d.switch_plan(self.f.context, 'distro-1-0')['plan'], True)
        self.assertTrue((self.f.live / 'usr/bin/a22x-reboot').exists())
        self.f.assert_home(self)

    def platform_bundle(self):
        """What the recovery ships: the live platform files exactly as the profile names them."""
        names = [e['path'] for e in self.f.profile['entries']] + list(d.PLATFORM_EXTRAS)
        with tarfile.open(self.f.path / 'platform-files.tar.xz', 'w:xz') as out:
            for name in names:
                if (self.f.live / name).exists() or (self.f.live / name).is_symlink():
                    out.add(self.f.live / name, arcname=name, recursive=False)
            for path in sorted((self.f.live / 'opt/a22x-conn').iterdir()):
                out.add(path, arcname='opt/a22x-conn/' + path.name, recursive=False)

    def test_sound_files_of_the_installed_system_come_first(self):
        optional_bundle(self.f, self.tmp.name)
        (self.f.live / 'var/lib/alsa').mkdir(parents=True, exist_ok=True)
        (self.f.live / 'var/lib/alsa/asound.state').write_text('live-state\n')
        d.install(self.f.context, self.plan(), True)
        root = self.root('distro-1-0')
        self.assertEqual((root / 'var/lib/alsa/asound.state').read_text(), 'live-state\n')
        # What the installed system lacks comes from the recovery.
        self.assertTrue((root / 'usr/share/alsa/ucm2/conf.d/mt6833_mt6359/mt6833-mt6359.conf').is_symlink())
        self.assertFalse(list((self.f.live / d.MANAGER / 'staging').iterdir()))

    def test_restored_old_system_takes_platform_files_from_the_recovery(self):
        """2026-11-08: Arch restored from an older copy kept old firmware; import stopped on PLATFORM_FILE_MISMATCH."""
        self.platform_bundle()
        original = (self.f.live / 'usr/lib/firmware/a22x.bin').read_bytes()
        (self.f.live / 'usr/lib/firmware/a22x.bin').write_bytes(b'older-firmware\0')
        d.install(self.f.context, self.plan(), True)
        root = self.root('distro-1-0')
        self.assertEqual((root / 'usr/lib/firmware/a22x.bin').read_bytes(), original)
        self.assertEqual((self.f.live / 'usr/lib/firmware/a22x.bin').read_bytes(), b'older-firmware\0')
        self.assertIn('$y$hash', (root / 'etc/shadow').read_text())
        self.assertFalse(list((self.f.live / d.MANAGER / 'staging').iterdir()))
        # The new system can be started, and the old one, as it was, can come back.
        d.switch(self.f.context, d.switch_plan(self.f.context, 'distro-1-0')['plan'], True)
        self.assertEqual((self.f.live / 'usr/lib/firmware/a22x.bin').read_bytes(), original)
        d.switch(self.f.context, d.switch_plan(self.f.context, 'original')['plan'], True)
        self.assertEqual((self.f.live / 'usr/lib/firmware/a22x.bin').read_bytes(), b'older-firmware\0')
        self.f.assert_home(self)

    def test_without_a_matching_copy_the_old_refusal_stays(self):
        (self.f.live / 'usr/lib/firmware/a22x.bin').write_bytes(b'older-firmware\0')
        with self.assertRaises(d.fs.FilesystemError) as caught:
            d.install(self.f.context, self.plan(), True)
        self.assertEqual(caught.exception.code, 'PLATFORM_FILE_MISMATCH')
        self.platform_bundle()
        bundle = self.f.path / 'platform-files.tar.xz'
        with tarfile.open(bundle, 'w:xz') as out:
            out.add(self.f.live / 'usr/lib/firmware', arcname='usr/lib/firmware')
        with self.assertRaises(d.fs.FilesystemError) as caught:
            d.install(self.f.context, self.plan(), True)
        self.assertEqual(caught.exception.code, 'PLATFORM_BUNDLE_INVALID')

    def test_progress_names_each_phase_and_counts_bytes(self):
        seen = []
        d.PROGRESS_EVENTS = True
        self.addCleanup(setattr, d, 'PROGRESS_EVENTS', False)
        with mock.patch.object(d, 'print', create=True, side_effect=lambda line, **kw: seen.append(json.loads(line))):
            d.install(self.f.context, self.plan(), True)
        phases = [e['phase'] for e in seen]
        for phase in ('image-unpack', 'image-adapt', 'image-check', 'image-finish'):
            self.assertIn(phase, phases)
        self.assertLess(phases.index('image-unpack'), phases.index('image-adapt'))
        self.assertTrue(all(e['partition'] == 'distro-1.0.tar.gz' and e['bytes_done'] <= max(e['bytes_total'], e['bytes_done']) for e in seen))

    def test_desktop_and_new_account_reach_first_boot(self):
        plan = self.plan(desktop='phosh', account='new', username='olena', password="a'b c")
        (Path(os.environ['A22X_PLATFORM_HELPERS']) / 'wpa_supplicant').write_bytes(b'static wpa_supplicant')
        d.install(self.f.context, plan, True)
        conf = (self.root('distro-1-0') / 'etc/a22x/firstboot.conf').read_text()
        self.assertIn('DESKTOP=phosh', conf)
        self.assertIn('NEW_USER=olena', conf)
        self.assertIn("'a'\"'\"'b c'", conf)
        self.assertEqual((self.root('distro-1-0') / 'etc/a22x/firstboot.conf').stat().st_mode & 0o777, 0o600)
        self.assertIn('EXTRA_SHELLS=\'\'', conf)
        self.assertIn('GREETER=auto', conf)

    def test_the_reason_of_a_failed_setup_comes_from_its_log(self):
        """2026-11-10: apt found no room, and the page said a download had failed."""
        root = Path(self.tmp.name) / 'slotroot'
        (root / 'var/log').mkdir(parents=True)
        self.assertIsNone(d.setup_failure(root))
        (root / 'var/log/a22x-firstboot.log').write_text('a22x-firstboot: start\nE: You don\'t have enough free space in /var/cache/apt/archives/.\n')
        self.assertEqual(d.setup_failure(root), 'There was not enough free space on userdata for the packages.')
        (root / 'var/log/a22x-firstboot.log').write_text('Err:1 http://deb.debian.org trixie InRelease\n  Temporary failure resolving deb.debian.org\n')
        self.assertEqual(d.setup_failure(root), 'The package servers could not be reached.')
        (root / 'var/log/a22x-firstboot.log').write_text('everything else\n')
        self.assertIsNone(d.setup_failure(root))

    def test_the_plan_counts_the_room_of_shells_and_apps(self):
        """2026-11-10: Debian 13 with Phosh and GNOME needed far more than the image alone."""
        (Path(os.environ['A22X_PLATFORM_HELPERS']) / 'wpa_supplicant').write_bytes(b'static wpa_supplicant')
        roomy = types.SimpleNamespace(f_bavail=1 << 30, f_blocks=1 << 30, f_frsize=4096)
        with mock.patch.object(d, 'COUNT_PACKAGES', True), mock.patch.object(d.os, 'fstatvfs', lambda fd: roomy):
            bare = d.image_plan(self.f.context, 'sdcard-p1', '/distro-1.0.tar.gz',
                                d.image_options('console', 'keep', None, None, 0, None))['plan']['required_bytes']
            full = d.image_plan(self.f.context, 'sdcard-p1', '/distro-1.0.tar.gz',
                                d.image_options('phosh', 'keep', None, None, 0, None, apps=['ssh', 'calculator'],
                                                shells=['gnome']))['plan']['required_bytes']
        self.assertEqual(full - bare, d.SHELL_BYTES['phosh'] + d.SHELL_BYTES['gnome'] + 2 * d.APP_BYTES)
        tight = types.SimpleNamespace(f_bavail=(bare + d.SHELL_BYTES['phosh']) // 4096, f_blocks=1 << 30, f_frsize=4096)
        with mock.patch.object(d, 'COUNT_PACKAGES', True), mock.patch.object(d.os, 'fstatvfs', lambda fd: tight):
            with self.assertRaises(d.fs.FilesystemError) as caught:
                d.image_plan(self.f.context, 'sdcard-p1', '/distro-1.0.tar.gz',
                             d.image_options('phosh', 'keep', None, None, 0, None, shells=['gnome']))
        self.assertEqual(caught.exception.code, 'INSUFFICIENT_SPACE')  # refused before anything is unpacked

    def test_a_prepared_password_hash_reaches_first_boot(self):
        """2026-11-10: a plan from the Android app has the account's SHA-512 hash, not its password."""
        (Path(os.environ['A22X_PLATFORM_HELPERS']) / 'wpa_supplicant').write_bytes(b'static wpa_supplicant')
        hashed = '$6$a22xsalt$' + 'C' * 86
        options = d.image_options('phosh', 'new', 'olena', None, 0, None, password_hash=hashed)
        self.assertNotIn('password', {k for k, v in options.items() if v})
        d.install(self.f.context, d.image_plan(self.f.context, 'sdcard-p1', '/distro-1.0.tar.gz', options)['plan'], True)
        conf = (self.root('distro-1-0') / 'etc/a22x/firstboot.conf').read_text()
        self.assertIn("NEW_PASSWORD=''", conf)
        self.assertIn("NEW_PASSWORD_HASH='%s'" % hashed, conf)
        for wrong in ('$1$md5$x', 'plain-246810', '$6$salt$short'):
            with self.assertRaises(d.fs.FilesystemError) as caught:
                d.image_options('phosh', 'new', 'olena', None, 0, None, password_hash=wrong)
            self.assertEqual(caught.exception.code, 'INVALID_PASSWORD')
        with self.assertRaises(d.fs.FilesystemError):
            d.image_options('phosh', 'new', 'olena', '246810', 0, None, password_hash=hashed)  # one or the other

    def test_an_added_system_knows_the_shared_account(self):
        """2026-11-10, Ubuntu added beside Fedora: the setup script saw no A22X_USER, and automatic
        sign-in found the account only because it was called "user"."""
        (Path(os.environ['A22X_PLATFORM_HELPERS']) / 'wpa_supplicant').write_bytes(b'static wpa_supplicant')
        for name in ('etc/passwd', 'etc/group'):
            path = self.f.live / name
            path.write_text(path.read_text().replace('user:x:', 'mira:x:').replace('/home/user', '/home/mira'))
        d.install(self.f.context, self.plan(desktop='phosh'), True)
        conf = (self.root('distro-1-0') / 'etc/a22x/firstboot.conf').read_text()
        self.assertIn("NEW_USER=''", conf)
        self.assertIn('PHONE_USER=mira', conf)
        second = d.install(self.f.context, self.plan(desktop='phosh', account='new', username='olena', password='x'),
                           True)['installed_slot']
        conf = (self.root(second) / 'etc/a22x/firstboot.conf').read_text()
        self.assertIn('PHONE_USER=olena', conf)

    def test_the_new_root_is_open_to_every_user_during_the_setup(self):
        """2026-11-09: pacman's download user alpm could not reach "/" of a root made 0700."""
        seen = []
        d.PROVISION_RUNNER = lambda root, on_line: seen.append(oct(root.stat().st_mode & 0o777)) or 3
        try:
            (Path(os.environ['A22X_PLATFORM_HELPERS']) / 'wpa_supplicant').write_bytes(b'static wpa_supplicant')
            d.install(self.f.context, self.plan(desktop='phosh', account='new', username='olena', password='x'), True)
        finally:
            d.PROVISION_RUNNER = None
        self.assertEqual(seen, ['0o755'])

    def test_more_shells_sign_in_and_build_scripts_reach_the_new_system(self):
        helpers = Path(os.environ['A22X_PLATFORM_HELPERS'])
        (helpers / 'wpa_supplicant').write_bytes(b'static wpa_supplicant')
        (helpers / 'build/keys').mkdir(parents=True)
        (helpers / 'build/a22x-build-phoc.sh').write_text('#!/bin/sh\n')
        (helpers / 'build/keys/wlroots.gpg').write_bytes(b'\x99\x02key')
        (self.f.context.root / 'settings.json').write_text(json.dumps({'language': 'uk', 'utc_offset': 60}))
        options = d.image_options('phosh', 'new', 'olena', 'x', 0, None, shells=['plasma'], greeter='phrog',
                                  system_locale='pl_PL', timezone='Europe/Warsaw')
        plan = d.image_plan(self.f.context, 'sdcard-p1', '/distro-1.0.tar.gz', options)['plan']
        result = d.install(self.f.context, plan, True)
        root = self.root('distro-1-0')
        conf = (root / 'etc/a22x/firstboot.conf').read_text()
        self.assertIn('EXTRA_SHELLS=plasma', conf)
        self.assertIn('GREETER=phrog', conf)
        self.assertIn('A22X_LANGUAGE=uk', conf)
        self.assertIn('A22X_UTC_OFFSET=60', conf)
        self.assertIn('A22X_LOCALE=pl_PL', conf)
        self.assertIn('A22X_TIMEZONE=Europe/Warsaw', conf)
        self.assertEqual((result['shells'], result['greeter']), (['plasma'], 'phrog'))
        script = root / 'opt/a22x-conn/build/a22x-build-phoc.sh'
        self.assertEqual(script.stat().st_mode & 0o777, 0o755)
        self.assertEqual((root / 'opt/a22x-conn/build/keys/wlroots.gpg').read_bytes(), b'\x99\x02key')
        self.assertEqual((root / 'opt/a22x-conn/build/keys/wlroots.gpg').stat().st_mode & 0o777, 0o644)

    def test_the_recovery_clock_zone_is_the_default_of_a_new_system(self):
        # 2026-11-09: a recovery clock set to a city passes that zone on unless another is chosen.
        (Path(os.environ['A22X_PLATFORM_HELPERS']) / 'wpa_supplicant').write_bytes(b'static wpa_supplicant')
        (self.f.context.root / 'settings.json').write_text(json.dumps({'language': 'de', 'utc_offset': 60,
                                                                       'timezone': 'Europe/Kyiv'}))
        options = d.image_options('phosh', 'new', 'olena', 'x', 0, None)
        plan = d.image_plan(self.f.context, 'sdcard-p1', '/distro-1.0.tar.gz', options)['plan']
        d.install(self.f.context, plan, True)
        conf = (self.root('distro-1-0') / 'etc/a22x/firstboot.conf').read_text()
        self.assertIn('A22X_TIMEZONE=Europe/Kyiv', conf)
        self.assertIn('A22X_LANGUAGE=de', conf)
        (self.f.context.root / 'settings.json').write_text(json.dumps({'timezone': '../../etc/passwd'}))
        self.assertEqual(d.recovery_locale(self.f.context), ('', 0, ''))

    def test_default_phone_user_makes_way_for_shared_accounts(self):
        with tarfile.open(self.f.image, 'w:gz', format=tarfile.PAX_FORMAT) as out:
            for name, text in (('etc/passwd', 'root:x:0:0:root:/root:/bin/sh\nmobian:x:1000:1000::/home/mobian:/bin/sh\n'),
                               ('etc/group', 'root:x:0:\nmobian:x:1000:\nsudo:x:27:mobian\n'),
                               ('etc/shadow', 'root:*:1::::::\nmobian:$y$old:1::::::\n')):
                (self.f.new / name).write_text(text)
            (self.f.new / 'etc/shadow').chmod(0o640)
            for item in sorted(self.f.new.rglob('*')):
                out.add(item, arcname=item.relative_to(self.f.new).as_posix(), recursive=False)
        d.install(self.f.context, self.plan(), True)
        root = self.root('distro-1-0')
        self.assertNotIn('mobian', (root / 'etc/passwd').read_text())
        self.assertNotIn('mobian', (root / 'etc/group').read_text())
        self.assertNotIn('mobian', (root / 'etc/shadow').read_text())
        self.assertIn('user:x:1000', (root / 'etc/passwd').read_text())
        self.assertEqual((root / 'etc/shadow').stat().st_mode & 0o777, 0o640)

    def test_an_image_user_of_the_same_name_takes_the_shared_numbers(self):
        """2026-11-10, postmarketOS: its own "user" is UID and group 10000. The shared account
        "user" (1000) takes over the name; its group follows, or the files of /home have no
        group in the new system and the import stops at HOME_GROUP_MISMATCH."""
        with tarfile.open(self.f.image, 'w:gz', format=tarfile.PAX_FORMAT) as out:
            for name, text in (('etc/passwd', 'root:x:0:0:root:/root:/bin/sh\nuser:x:10000:10000::/home/user:/bin/ash\n'),
                               ('etc/group', 'root:x:0:\nuser:x:10000:\nwheel:x:10:user\n'),
                               ('etc/shadow', 'root:*:1::::::\nuser:$y$old:1::::::\n')):
                (self.f.new / name).write_text(text)
            (self.f.new / 'etc/shadow').chmod(0o640)
            for item in sorted(self.f.new.rglob('*')):
                out.add(item, arcname=item.relative_to(self.f.new).as_posix(), recursive=False)
        d.install(self.f.context, self.plan(), True)
        root = self.root('distro-1-0')
        self.assertIn('user:x:1000:1000:', (root / 'etc/passwd').read_text())
        self.assertIn('user:x:1000:', (root / 'etc/group').read_text())
        self.assertNotIn(':10000:', (root / 'etc/group').read_text())
        self.f.assert_home(self)

    def test_postmarketos_starts_its_shell_as_the_shared_user(self):
        """2026-11-10: postmarketOS's generator runs plasma-mobile.service as /etc/default_user,
        else as UID 10000; beside the shared accounts the phone's user is 1000."""
        gen = self.f.new / 'usr/lib/systemd/system-generators'
        gen.mkdir(parents=True, exist_ok=True)
        (gen / 'system-generators-plasma-mobile-user-override').write_text(
            '#!/bin/sh\nuser_file="/etc/default_user"\n')
        with tarfile.open(self.f.image, 'w:gz', format=tarfile.PAX_FORMAT) as out:
            for item in sorted(self.f.new.rglob('*')):
                out.add(item, arcname=item.relative_to(self.f.new).as_posix(), recursive=False)
        d.install(self.f.context, self.plan(), True)
        self.assertEqual((self.root('distro-1-0') / 'etc/default_user').read_text(), 'user\n')

    def test_a_password_the_system_cannot_read_is_asked_again(self):
        """2026-11-10, postmarketOS (musl, no libxcrypt): the shared account came with its yescrypt
        hash and nobody could sign in. The import stops with a reason, and goes through when the
        password is given again for that account."""
        with tarfile.open(self.f.image, 'w:gz', format=tarfile.PAX_FORMAT) as out:
            for item in sorted(self.f.new.rglob('*')):
                if item.name != 'libcrypt.so.2':
                    out.add(item, arcname=item.relative_to(self.f.new).as_posix(), recursive=False)
        with self.assertRaises(d.fs.FilesystemError) as caught:
            d.install(self.f.context, self.plan(), True)
        self.assertEqual(caught.exception.code, 'PASSWORD_FORMAT_UNSUPPORTED')
        self.assertFalse(list((self.f.live / d.MANAGER / 'staging').iterdir()))
        result = d.install(self.f.context, self.plan(account='new', username='user', password='246810'), True)
        root = self.root(result['installed_slot'])
        self.assertIn('NEW_USER=user', (root / 'etc/a22x/firstboot.conf').read_text())

    def test_remove_slot(self):
        d.install(self.f.context, self.plan(), True)
        self.assertTrue(self.root('distro-1-0').exists())
        with self.assertRaises(d.DistroError) as caught:
            d.remove_slot(self.f.context, 'original', True)
        self.assertEqual(caught.exception.code, 'RESERVED_SLOT')
        self.assertEqual(d.remove_slot(self.f.context, 'distro-1-0', True)['removed_slot'], 'distro-1-0')
        self.assertFalse(self.root('distro-1-0').exists())
        self.f.assert_home(self)

    def test_active_slot_is_not_removed(self):
        d.install(self.f.context, self.plan(), True)
        d.switch(self.f.context, d.switch_plan(self.f.context, 'distro-1-0')['plan'], True)
        with self.assertRaises(d.DistroError) as caught:
            d.remove_slot(self.f.context, 'distro-1-0', True)
        self.assertEqual(caught.exception.code, 'SLOT_ACTIVE')

    def test_second_install_gets_a_new_slot(self):
        d.install(self.f.context, self.plan(), True)
        self.assertEqual(self.plan()['slot'], 'distro-1-0-2')

    def test_not_an_image(self):
        (self.f.sd / 'note.txt').write_text('hello' * 500)
        options = d.image_options('console', 'keep', None, None, 0, None)
        with self.assertRaises(d.DistroError) as caught:
            d.image_plan(self.f.context, 'sdcard-p1', '/note.txt', options)
        self.assertEqual(caught.exception.code, 'UNKNOWN_IMAGE')

    def test_failed_image_leaves_no_staging(self):
        with tarfile.open(self.f.image, 'w:gz') as out:
            info = tarfile.TarInfo('readme')
            out.addfile(info, io.BytesIO())
        with self.assertRaises(d.DistroError) as caught:
            d.install(self.f.context, self.plan(), True)
        self.assertEqual(caught.exception.code, 'NO_ROOT_FILESYSTEM')
        self.assertFalse(list((self.f.live / d.MANAGER / 'staging').iterdir()))
        self.assertTrue((self.f.live / 'var/lib/pkg/state').exists())

    def test_archive_cannot_write_through_a_link(self):
        outside = Path(self.tmp.name) / 'outside'
        outside.mkdir()
        target = Path(self.tmp.name) / 'evil.tar'
        with tarfile.open(target, 'w') as out:
            link = tarfile.TarInfo('escape')
            link.type, link.linkname = tarfile.SYMTYPE, str(outside)
            out.addfile(link)
            data = tarfile.TarInfo('escape/owned')
            data.size = 1
            out.addfile(data, io.BytesIO(b'x'))
        root = Path(self.tmp.name) / 'root'
        root.mkdir()
        with self.assertRaises(m.ImageError):
            m.extract_tar(target, None, root)
        self.assertFalse((outside / 'owned').exists())


class TestCleanInstall(unittest.TestCase):
    """2026-11-08: after userdata is formatted, one system is installed with the account, Wi-Fi,
    apps and setup script chosen in recovery, and it is the active system right away."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='a22x-clean-')
        self.addCleanup(self.tmp.cleanup)
        self.f = ImageFixture(self.tmp.name)
        TestImageInstall.platform_bundle(self)
        for child in self.f.live.iterdir():
            if child.name != 'lost+found':
                shutil.rmtree(child) if child.is_dir() and not child.is_symlink() else child.unlink()
        self.helpers = Path(os.environ['A22X_PLATFORM_HELPERS'])

    def repack(self):
        with tarfile.open(self.f.image, 'w:gz', format=tarfile.PAX_FORMAT) as out:
            for item in sorted(self.f.new.rglob('*')):
                out.add(item, arcname=item.relative_to(self.f.new).as_posix(), recursive=False)

    def plan(self, **kw):
        options = d.image_options(kw.get('desktop', 'console'), kw.get('account', 'new'), kw.get('username', 'user'),
                                  kw.get('password', '246810'), 0, None, kw.get('wifi'), kw.get('apps', ()), kw.get('script'))
        return d.image_plan(self.f.context, 'sdcard-p1', '/distro-1.0.tar.gz', options)['plan']

    def error(self, code, call, *args):
        with self.assertRaises(d.fs.FilesystemError) as caught:
            call(*args)
        self.assertEqual(caught.exception.code, code)

    def test_status_reports_the_empty_userdata(self):
        status = d.status(self.f.context)
        self.assertFalse(status['initialized'])
        self.assertTrue(status['empty_userdata'])
        (self.f.live / 'note.txt').write_text('x')
        self.assertFalse(d.status(self.f.context)['empty_userdata'])

    def test_files_without_a_system_are_not_replaced(self):
        (self.f.live / 'note.txt').write_text('x')
        self.error('USERDATA_NOT_EMPTY', self.plan)

    def test_a_new_account_and_a_network_are_required(self):
        self.error('ACCOUNT_REQUIRED', lambda: self.plan(account='keep', username=None, password=None))
        self.error('WIFI_REQUIRED', lambda: self.plan(desktop='phosh'))
        self.error('WIFI_REQUIRED', lambda: self.plan(apps=['browser']))

    def test_the_installed_system_is_active(self):
        plan = self.plan()
        self.assertTrue(plan['clean_install'])
        result = d.install(self.f.context, plan, True)
        self.assertTrue(result['switched'] and result['clean_install'])
        self.assertEqual(result['active'], 'distro-1-0')
        self.assertTrue((self.f.live / 'usr/bin/systemd').exists())
        self.assertTrue((self.f.live / 'usr/bin/a22x-reboot').exists())
        self.assertEqual(sorted(p.name for p in self.f.live.iterdir() if p.name.startswith('.a22x')), [d.MANAGER])
        status = d.status(self.f.context)
        self.assertEqual(status['active'], 'distro-1-0')
        self.assertEqual([s['id'] for s in status['slots']], ['distro-1-0'])
        self.assertEqual(list((self.f.live / d.MANAGER / 'slots/distro-1-0/root').iterdir()), [])
        self.assertEqual(d.boot_check(self.f.context)['active'], 'distro-1-0')
        conf = (self.f.live / 'etc/a22x/firstboot.conf').read_text()
        self.assertIn('NEW_USER=user', conf)
        hook = self.f.live / 'etc/NetworkManager/dispatcher.d/90-a22x-firstboot'
        self.assertEqual(hook.stat().st_mode & 0o777, 0o755)
        self.assertIn('a22x-firstboot.service', hook.read_text())
        self.assertIn('PRIMARY_USER=1', conf)
        self.assertIn('NEW_PASSWORD=246810', conf)

    def test_the_first_system_is_reached_again_after_a_switch(self):
        """2026-11-09, on the phone: Ubuntu (clean install), then Manjaro and Arch imported beside
        and switched to; the way back to Ubuntu stopped at "[Errno 2] No such file or directory:
        'home'". The slot of a system that was live keeps no /home (it stays shared at the root),
        and the switch checked that slot like a fresh image."""
        d.install(self.f.context, self.plan(), True)
        options = d.image_options('console', 'keep', None, None, 0, None)
        second = d.install(self.f.context, d.image_plan(self.f.context, 'sdcard-p1', '/distro-1.0.tar.gz', options)['plan'],
                           True)['installed_slot']
        self.assertNotEqual(second, 'distro-1-0')
        for target in (second, 'distro-1-0'):
            result = d.switch(self.f.context, d.switch_plan(self.f.context, target)['plan'], True)
            self.assertEqual(result['active'], target)
            self.assertTrue((self.f.live / 'home').is_dir())
        self.assertFalse((self.f.live / d.MANAGER / 'slots/distro-1-0/root/home').exists())

    def test_sound_comes_from_the_recovery(self):
        """2026-11-08: a clean Fedora install had no UCM profile or mixer state, and PipeWire
        played to the virtual Loopback card: no sound."""
        optional_bundle(self.f, self.tmp.name)
        d.install(self.f.context, self.plan(), True)
        conf = self.f.live / 'usr/share/alsa/ucm2/conf.d/mt6833_mt6359/mt6833-mt6359.conf'
        self.assertTrue(conf.is_symlink())
        self.assertEqual(conf.read_text(), 'Syntax 4\n')
        self.assertIn('mt6833mt6359', (self.f.live / 'var/lib/alsa/asound.state').read_text())
        rule = (self.f.live / 'etc/wireplumber/wireplumber.conf.d/50-a22x-loopback.conf').read_text()
        self.assertIn('device.disabled = true', rule)
        self.assertIn('snd_aloop', (self.f.live / 'etc/wireplumber/main.lua.d/51-a22x-loopback.lua').read_text())
        # Android partitions stay off desktops and file managers (2026-11-09, Xfce on Ubuntu).
        hide = (self.f.live / 'etc/udev/rules.d/80-a22x-hide-internal.rules').read_text()
        self.assertIn('KERNEL=="mmcblk0*", ENV{UDISKS_IGNORE}="1"', hide)
        # Image first-start scripts that grow the root, add default accounts or reset the language
        # stay off (Manjaro's pinephone-post-install made "kde" with password 123456, 2026-11-09).
        for unit in ('pinephone-post-install.service', 'resize.service', 'systemd-firstboot.service'):
            masked = self.f.live / 'etc/systemd/system' / unit
            self.assertTrue(masked.is_symlink() and str(masked.readlink()) == '/dev/null', unit)
        self.assertFalse(list((self.f.live / d.MANAGER / 'staging').iterdir()))

    def test_new_directories_are_readable_by_user_services(self):
        """2026-11-08: under the recovery's umask 077 the adapter made /etc/systemd/user.conf.d
        and /etc/wireplumber/wireplumber.conf.d root-only; the user manager and WirePlumber
        could not read them."""
        old = os.umask(0o077)
        self.addCleanup(os.umask, old)
        d.install(self.f.context, self.plan(), True)
        for name in ('etc/systemd/user.conf.d', 'etc/wireplumber', 'etc/wireplumber/wireplumber.conf.d'):
            self.assertEqual((self.f.live / name).stat().st_mode & 0o777, 0o755, name)
        self.assertEqual((self.f.live / 'etc/a22x').stat().st_mode & 0o777, 0o700)

    def test_bad_optional_bundle_stops_the_install(self):
        with tarfile.open(self.f.path / 'platform-optional.tar.xz', 'w:xz') as out:
            info = tarfile.TarInfo('usr/../../escape')
            out.addfile(info, io.BytesIO(b''))
        self.error('PLATFORM_BUNDLE_INVALID', d.install, self.f.context, self.plan(), True)

    def test_erasing_userdata_first_makes_a_clean_install(self):
        """2026-11-08: the installer page erases userdata when the installation starts."""
        (self.f.live / 'note.txt').write_text('old data')
        self.error('USERDATA_NOT_EMPTY', self.plan)
        options = d.image_options('console', 'new', 'user', '246810', 0, None)
        plan = d.image_plan(self.f.context, 'sdcard-p1', '/distro-1.0.tar.gz', options, wipe=True)['plan']
        self.assertTrue(plan['clean_install'] and plan['erase_userdata'])
        self.assertTrue((self.f.live / 'note.txt').exists())  # Nothing is erased by planning.
        result = d.install(self.f.context, plan, True)
        self.assertTrue(result['clean_install'])
        self.assertFalse((self.f.live / 'note.txt').exists())
        self.assertTrue((self.f.live / 'usr/bin/systemd').exists())

    def test_a_userdata_of_android_is_formatted_by_a_clean_install(self):
        """2026-11-11: with Android on userdata nothing can be mounted. A new system is planned on the
        partition as it will be, and the format waits for the confirmed start of the installation."""
        spec = self.f.context_json
        spec['volumes'][0].update(fs_type='f2fs', fscrypt_feature=True, mounted=False)
        (self.f.path / 'filesystem-fixture.json').write_text(json.dumps(spec))
        (self.f.live / 'android.bin').write_text('what Android kept here')
        status = d.status(self.f.context)
        self.assertTrue(status['foreign'] and not status['mounted'], status)
        self.assertIn('formatted', status['next_action'])
        options = d.image_options('console', 'new', 'user', '246810', 0, None)
        # Beside what is there nothing can be installed: there is no Linux to keep.
        self.error('LINUX_USERDATA_REQUIRED', d.image_plan, self.f.context, 'sdcard-p1', '/distro-1.0.tar.gz', options)
        plan = d.image_plan(self.f.context, 'sdcard-p1', '/distro-1.0.tar.gz', options, wipe=True)['plan']
        self.assertTrue(plan['clean_install'] and plan['erase_userdata'] and plan['format_userdata'], plan)
        self.assertIn('Format userdata', plan['confirmation'])
        self.assertGreater(plan['available_bytes'], plan['required_bytes'])
        self.assertTrue((self.f.live / 'android.bin').exists())  # Nothing is formatted by planning.
        self.assertFalse(self.f.context.volume('userdata')['mounted'])
        result = d.install(self.f.context, plan, True)
        self.assertTrue(result['clean_install'])
        volume = self.f.context.volume('userdata')
        self.assertEqual((volume['fs_type'], volume['mounted'], volume['read_only']), ('ext4', True, False))
        self.assertFalse((self.f.live / 'android.bin').exists())
        self.assertTrue((self.f.live / 'usr/bin/systemd').exists())
        self.assertFalse(d.status(self.f.context)['foreign'])

    def test_status_says_whether_boot_holds_the_linux_kernel(self):
        """2026-11-11: with Android's kernel on BOOT a new system could not start; the page of New
        system says so before anything is chosen, from this field of the status."""
        self.assertTrue(d.status(self.f.context)['boot_linux'])
        spec = dict(self.f.context_json, android_boot=True)
        (self.f.path / 'filesystem-fixture.json').write_text(json.dumps(spec))
        self.assertFalse(d.status(self.f.context)['boot_linux'])

    def test_a_linux_userdata_is_never_taken_for_android(self):
        """A plan beside the installed system and a clean one both keep working on plain ext4."""
        self.assertFalse(d.status(self.f.context)['foreign'])
        options = d.image_options('console', 'new', 'user', '246810', 0, None)
        plan = d.image_plan(self.f.context, 'sdcard-p1', '/distro-1.0.tar.gz', options, wipe=True)['plan']
        self.assertFalse(plan['format_userdata'])
        self.assertIn('Erase userdata', plan['confirmation'])

    def test_builds_made_on_the_phone_outlive_the_erased_userdata(self):
        """2026-11-09: phoc and Phrog builds go over an erased userdata to the next pacman system;
        an archive reaching outside its build's paths is not carried."""
        cache = self.f.live / 'var/cache/a22x-build'
        cache.mkdir(parents=True)

        def pack(name, members):
            with tarfile.open(cache / name, 'w') as out:
                for path, data in members:
                    info = tarfile.TarInfo(path)
                    info.size = len(data)
                    out.addfile(info, io.BytesIO(data))
        pack('phoc.tar', [('opt/a22x-phoc/built-for', b'libwlroots-0.20.so 0.20.2\n')])
        (cache / 'phoc.key').write_text('libwlroots-0.20.so 0.20.2\n')
        pack('phrog.tar', [('etc/shadow', b'root::0:::::\n')])
        (cache / 'phrog.key').write_text('phrog 0.53.0 phosh 0.57.0\n')
        (self.f.new / 'usr/bin/pacman').write_text('#!/bin/sh\n')
        self.repack()
        options = d.image_options('console', 'new', 'user', '246810', 0, None)
        plan = d.image_plan(self.f.context, 'sdcard-p1', '/distro-1.0.tar.gz', options, wipe=True)['plan']
        d.install(self.f.context, plan, True)
        given = self.f.live / 'var/cache/a22x-build'
        self.assertEqual((given / 'phoc.key').read_text(), 'libwlroots-0.20.so 0.20.2\n')
        self.assertTrue((given / 'phoc.tar').is_file())
        self.assertFalse((given / 'phrog.tar').exists())
        self.assertTrue((self.f.context.root / 'build-cache/phoc.tar').is_file())
        # A system without pacman gets nothing; the store keeps the build for a later one.
        self.assertEqual(d.give_builds(self.f.context, self.f.path / 'no-pacman'), [])
        self.assertTrue((self.f.context.root / 'build-cache/phoc.tar').is_file())

    def serve(self, folder):
        import functools
        import http.server
        import threading
        class Quiet(http.server.SimpleHTTPRequestHandler):
            def log_message(self, *args):
                pass
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), functools.partial(Quiet, directory=str(folder)))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return 'http://127.0.0.1:%d/' % server.server_address[1]

    def catalog(self, sha256=None):
        data = self.f.image.read_bytes()
        base = self.serve(self.f.image.parent)
        entry = {'id': 'fixture-linux', 'name': 'Fixture Linux', 'version': '1.0', 'family': 'fixture', 'url': base + self.f.image.name,
                 'sha256': sha256 or hashlib.sha256(data).hexdigest(), 'size': len(data), 'file': self.f.image.name,
                 'format': 'tar', 'compression': 'gzip', 'factor': 4, 'minimal': True}
        (self.f.path / 'catalog.json').write_text(json.dumps({'images': [entry]}))

    def test_a_catalog_image_is_downloaded_checked_and_installed(self):
        self.catalog()
        listed = d.catalog(self.f.context)
        self.assertEqual([i['id'] for i in listed['images']], ['fixture-linux'])
        self.assertTrue(listed['images'][0]['reachable'] and 'url' not in listed['images'][0])
        options = d.image_options('console', 'new', 'user', '246810', 0, None)
        plan = d.image_plan(self.f.context, None, None, options, remote=d.catalog_entry(self.f.context, 'fixture-linux'))['plan']
        self.assertEqual(plan['download_bytes'], self.f.image.stat().st_size)
        result = d.install(self.f.context, plan, True)
        self.assertTrue(result['switched'] and result['clean_install'])
        self.assertTrue((self.f.live / 'usr/bin/systemd').exists())
        self.assertFalse((self.f.live / d.DOWNLOAD_DIR).exists())

    def test_a_download_that_differs_from_the_catalog_stops(self):
        self.catalog(sha256='0' * 64)
        options = d.image_options('console', 'new', 'user', '246810', 0, None)
        plan = d.image_plan(self.f.context, None, None, options, remote=d.catalog_entry(self.f.context, 'fixture-linux'))['plan']
        self.error('DOWNLOAD_CHECKSUM_MISMATCH', d.install, self.f.context, plan, True)
        self.assertFalse((self.f.live / d.DOWNLOAD_DIR).exists())
        self.assertEqual(sorted(p.name for p in self.f.live.iterdir()), ['lost+found'])
        self.error('CATALOG_IMAGE_UNKNOWN', d.catalog_entry, self.f.context, 'nothing')

    def server_release(self, folder, name, data):
        """One release folder of the fixture server: an image and its SHA256SUMS."""
        folder.mkdir(parents=True, exist_ok=True)
        (folder / name).write_bytes(data)
        (folder / 'SHA256SUMS').write_text('%s *%s\n' % (hashlib.sha256(data).hexdigest(), name))

    def followed_catalog(self):
        """2026-11-08: a catalog entry pinned to a release the server will replace."""
        srv = Path(self.tmp.name) / 'srv'
        data = self.f.image.read_bytes()
        self.server_release(srv / '20261101-1', 'fixture-20261101-1.tar.gz', data)
        base = self.serve(srv)
        entry = {'id': 'fixture-linux', 'name': 'Fixture Linux', 'version': '1', 'family': 'fixture',
                 'url': base + '20261101-1/fixture-20261101-1.tar.gz', 'file': 'fixture-20261101-1.tar.gz',
                 'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data), 'format': 'tar', 'compression': 'gzip', 'factor': 4,
                 'source': {'index': base, 'dir': r'\d{8}-\d+/', 'file': r'fixture-\d{8}-\d+\.tar\.gz', 'sums': 'SHA256SUMS'}}
        (self.f.path / 'catalog.json').write_text(json.dumps({'pinned': 1000, 'images': [entry]}))
        return srv, base, data

    def test_the_catalog_follows_its_server(self):
        srv, base, data = self.followed_catalog()
        listed = d.catalog(self.f.context, refresh=True)
        self.assertEqual(listed['refresh'], {'checked': 1, 'updated': 0, 'unavailable': 0, 'failed': 0, 'problems': {}})
        self.assertEqual(d.catalog(self.f.context, refresh=True)['refresh']['checked'], 0)  # Checked recently.
        # A newer release appears: the next check pins it.
        newer = data + b'\0' * 512
        self.server_release(srv / '20261103-1', 'fixture-20261103-1.tar.gz', newer)
        listed = d.catalog(self.f.context, refresh=True, force=True)
        self.assertEqual(listed['refresh']['updated'], 1)
        self.assertEqual(listed['images'][0]['file'], 'fixture-20261103-1.tar.gz')
        entry = d.catalog_entry(self.f.context, 'fixture-linux')
        self.assertEqual((entry['url'], entry['size'], entry['sha256']),
                         (base + '20261103-1/fixture-20261103-1.tar.gz', len(newer), hashlib.sha256(newer).hexdigest()))
        self.assertTrue((self.f.path / 'catalog-state.json').is_file())
        # A newest folder without the image yet: the release before it is taken.
        (srv / '20261105-1').mkdir()
        (srv / '20261105-1' / 'SHA256SUMS').write_text('')
        self.assertEqual(d.catalog(self.f.context, refresh=True, force=True)['images'][0]['file'], 'fixture-20261103-1.tar.gz')
        # The server lists none: the image is shown as gone and cannot be planned.
        for folder in ('20261101-1', '20261103-1', '20261105-1'):
            shutil.rmtree(srv / folder)
        listed = d.catalog(self.f.context, refresh=True, force=True)
        self.assertEqual(listed['refresh']['unavailable'], 1)
        self.assertTrue(listed['images'][0]['unavailable'] and not listed['images'][0]['reachable'])
        self.error('CATALOG_IMAGE_GONE', d.catalog_entry, self.f.context, 'fixture-linux')
        # It comes back.
        self.server_release(srv / '20261107-1', 'fixture-20261107-1.tar.gz', data)
        listed = d.catalog(self.f.context, refresh=True, force=True)
        self.assertEqual((listed['refresh']['updated'], listed['images'][0]['file'], listed['images'][0]['reachable']),
                         (1, 'fixture-20261107-1.tar.gz', True))

    def signing_fixture(self):
        """The fixture key in the fixture's keys folder, and its signatures beside the data."""
        data = Path(__file__).resolve().parent / 'data' / 'openpgp'
        (self.f.path / 'keys').mkdir(exist_ok=True)
        shutil.copy(data / 'fixture.asc', self.f.path / 'keys' / 'fixture.asc')
        return data

    def test_a_signed_checksum_list_is_checked(self):
        """2026-11-08: Ubuntu and Fedora sign their checksum lists; the pinned key decides."""
        srv, base, data = self.followed_catalog()
        catalog = json.loads((self.f.path / 'catalog.json').read_text())
        catalog['images'][0]['source'].update({'key': 'fixture.asc', 'sums_sig': '{sums}.gpg'})
        (self.f.path / 'catalog.json').write_text(json.dumps(catalog))
        self.signing_fixture()
        # No signature beside the list: not trusted, and not taken for a gone image either.
        listed = d.catalog(self.f.context, refresh=True, force=True)
        self.assertEqual((listed['refresh']['failed'], listed['refresh']['unavailable']), (1, 0))
        self.assertTrue(listed['images'][0]['signed'])
        import subprocess as sp
        gnupg = Path(self.tmp.name) / 'gnupg'
        gnupg.mkdir(mode=0o700)
        env = dict(os.environ, GNUPGHOME=str(gnupg))
        if sp.run(['gpg', '--batch', '--quiet', '--pinentry-mode', 'loopback', '--passphrase', '', '--quick-gen-key',
                   'Signer <signer@example.invalid>', 'rsa2048', 'sign', 'never'], env=env, capture_output=True).returncode:
            self.skipTest('no gpg here')
        sums = srv / '20261101-1' / 'SHA256SUMS'
        sign = lambda: sp.run(['gpg', '--batch', '--yes', '--quiet', '--pinentry-mode', 'loopback', '--passphrase', '', '--detach-sign',
                               '-o', str(sums) + '.gpg', str(sums)], env=env, check=True)
        sign()
        # Signed by a key that is not pinned: refused.
        self.assertEqual(d.catalog(self.f.context, refresh=True, force=True)['refresh']['failed'], 1)
        (self.f.path / 'keys' / 'fixture.asc').write_bytes(sp.run(['gpg', '--batch', '--export', '--armor', 'signer@example.invalid'],
                                                                  env=env, capture_output=True, check=True).stdout)
        self.assertEqual(d.catalog(self.f.context, refresh=True, force=True)['refresh']['checked'], 1)
        # The list changes after signing: refused again.
        sums.write_text(sums.read_text().replace(sums.read_text()[:4], 'ffff'))
        self.assertEqual(d.catalog(self.f.context, refresh=True, force=True)['refresh']['failed'], 1)

    def test_a_weak_network_download_resumes_while_it_progresses(self):
        """Every reply breaks off after 1000 bytes: six breaks are more than the four tries in a row
        without progress, yet each one adds data, so the download resumes to the end."""
        import http.server
        import threading
        data = os.urandom(6500)
        class Dropping(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_GET(self):
                start = int(self.headers.get('Range', 'bytes=0-')[6:].rstrip('-') or 0)
                part = data[start:start + 1000]
                self.send_response(206 if start else 200)
                self.send_header('Content-Length', str(len(data) - start))
                self.end_headers()
                self.wfile.write(part)  # The connection closes before the announced length.
        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Dropping)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        target = Path(self.tmp.name) / 'weak-download'
        sleeps = []
        with mock.patch.object(d.time, 'sleep', sleeps.append):
            d.fetch(self.f.context, 'http://127.0.0.1:%d/image' % server.server_address[1], target, len(data),
                    hashlib.sha256(data).hexdigest(), lambda *args: None)
        self.assertEqual(target.read_bytes(), data)
        self.assertGreater(len(sleeps), 4)

    def test_a_signed_image_is_downloaded_and_verified(self):
        """Arch Linux ARM: the image has its own signature, checked after the download."""
        data = self.signing_fixture()
        srv = Path(self.tmp.name) / 'srv-signed'
        srv.mkdir()
        # The signed fixture data stands for the image; a tarball is not needed to check the download.
        shutil.copy(data / 'data.bin', srv / 'image.tar.gz')
        shutil.copy(data / 'data.bin.sig', srv / 'image.tar.gz.sig')
        base = self.serve(srv)
        source = {'kind': 'signed-file', 'url': base + 'image.tar.gz', 'signature': base + 'image.tar.gz.sig',
                  'file': 'image.tar.gz', 'key': 'fixture.asc'}
        found = d.resolve_source(self.f.context, source)
        self.assertEqual((found['size'], found['signature_sha256']), (4096, hashlib.sha256((data / 'data.bin.sig').read_bytes()).hexdigest()))
        target = Path(self.tmp.name) / 'download'
        progress = lambda *args: None
        signature = {'url': found['signature_url'], 'sha256': found['signature_sha256'], 'key': 'fixture.asc'}
        d.fetch(self.f.context, found['url'], target, found['size'], '', progress, signature=signature)
        self.assertEqual(target.read_bytes(), (data / 'data.bin').read_bytes())
        # A changed image fails its signature; a changed signature is a changed image.
        changed = bytearray((data / 'data.bin').read_bytes())
        changed[0] ^= 1
        (srv / 'image.tar.gz').write_bytes(bytes(changed))
        self.error('DOWNLOAD_SIGNATURE_BAD', d.fetch, self.f.context, found['url'], target, found['size'], '', progress, 4, signature)
        self.error('DOWNLOAD_SIGNATURE_CHANGED', d.fetch, self.f.context, found['url'], target, found['size'], '', progress, 4,
                   dict(signature, sha256='0' * 64))
        # An image signed by another key is not planned.
        (self.f.path / 'keys' / 'fixture.asc').write_bytes((Path(d.__file__).parent / 'keys' / 'ubuntu-cloud.asc').read_bytes())
        self.error('CATALOG_SIGNATURE_BAD', d.resolve_source, self.f.context, source)

    def test_a_github_release_is_followed(self):
        """Manjaro ARM: the newest release asset with its .sha256 from the GitHub API."""
        srv = Path(self.tmp.name) / 'srv-github'
        (srv / 'dl').mkdir(parents=True)
        (srv / 'dl' / 'Distro-minimal-20261012.img.xz').write_bytes(b'image')
        (srv / 'dl' / 'Distro-minimal-20261012.img.xz.sha256').write_text(hashlib.sha256(b'image').hexdigest() + '  Distro-minimal-20261012.img.xz\n')
        base = self.serve(srv)
        asset = lambda name, size: {'name': name, 'size': size, 'browser_download_url': base + 'dl/' + name}
        releases = [{'tag_name': 'draft', 'draft': True, 'prerelease': False, 'assets': []},
                    {'tag_name': '20261012', 'draft': False, 'prerelease': True,
                     'assets': [asset('Distro-minimal-20261012.img.xz', 5), asset('Distro-minimal-20261012.img.xz.sha256', 99)]}]
        (srv / 'repos' / 'team' / 'images').mkdir(parents=True)
        (srv / 'repos' / 'team' / 'images' / 'releases').write_text(json.dumps(releases))
        source = {'kind': 'github', 'repo': 'team/images', 'file': r'Distro-minimal-\d+\.img\.xz', 'prerelease': True, 'api': base.rstrip('/')}
        found = d.resolve_source(self.f.context, source)
        self.assertEqual((found['file'], found['size'], found['sha256'], found['version']),
                         ('Distro-minimal-20261012.img.xz', 5, hashlib.sha256(b'image').hexdigest(), '20261012'))
        self.error('CATALOG_SOURCE_EMPTY', d.resolve_source, self.f.context, dict(source, prerelease=False))

    def test_a_server_out_of_reach_keeps_the_pins(self):
        srv, base, data = self.followed_catalog()
        shutil.rmtree(srv)
        (self.f.path / 'catalog.json').write_text((self.f.path / 'catalog.json').read_text().replace(base, 'http://127.0.0.1:9/'))
        listed = d.catalog(self.f.context, refresh=True, force=True)
        problems = listed['refresh'].pop('problems')
        self.assertEqual(listed['refresh'], {'checked': 0, 'updated': 0, 'unavailable': 0, 'failed': 1})
        self.assertTrue(problems['fixture-linux'].startswith('URLError'), problems)  # Said why, after one more try.
        self.assertEqual(listed['images'][0]['file'], 'fixture-20261101-1.tar.gz')
        self.assertFalse(listed['images'][0]['unavailable'])

    def test_lists_come_over_https_only(self):
        class Device:
            mode = 'device'
        self.error('CATALOG_INSECURE', d.remote_text, Device, 'http://example.org/SHA256SUMS')
        self.error('CATALOG_INVALID', d.resolve_source, Device, {'index': 'http://example.org/', 'file': 'x', 'sums': 'y'})

    def test_checksum_lists_of_both_kinds_are_read(self):
        a, b = 'ab' * 32, 'cd' * 64
        text = '%s  debian.qcow2\n%s *ubuntu.tar.xz\n# x: 1 bytes\nSHA256 (Fedora.qcow2) = %s\n' % (b, a, a)
        self.assertEqual(d.parse_sums(text, 'debian.qcow2'), b)
        self.assertEqual(d.parse_sums(text, 'ubuntu.tar.xz'), a)
        self.assertEqual(d.parse_sums(text, 'Fedora.qcow2'), a)
        self.assertIsNone(d.parse_sums(text, 'qcow2'))
        self.assertIsNone(d.parse_sums('%s  other.img\n' % a, 'debian.qcow2'))

    def test_a_download_replaced_on_the_server_updates_the_catalog(self):
        srv, base, data = self.followed_catalog()
        options = d.image_options('console', 'new', 'user', '246810', 0, None)
        plan = d.image_plan(self.f.context, None, None, options, remote=d.catalog_entry(self.f.context, 'fixture-linux'))['plan']
        shutil.rmtree(srv / '20261101-1')
        self.server_release(srv / '20261103-1', 'fixture-20261103-1.tar.gz', data)
        self.error('CATALOG_UPDATED', d.install, self.f.context, plan, True)
        self.assertEqual(d.catalog_entry(self.f.context, 'fixture-linux')['file'], 'fixture-20261103-1.tar.gz')
        self.assertFalse((self.f.live / d.DOWNLOAD_DIR).exists())
        # Planned again, it installs.
        plan = d.image_plan(self.f.context, None, None, options, remote=d.catalog_entry(self.f.context, 'fixture-linux'))['plan']
        self.assertTrue(d.install(self.f.context, plan, True)['clean_install'])

    def test_a_ready_system_is_set_up_in_recovery(self):
        """2026-11-08: the first-start script runs in the new root from recovery; when it ends
        well, the configuration is gone and the system starts ready."""
        seen, events = [], []
        def runner(root, on_line):
            seen.append(root)
            for line in ('a22x-firstboot: start: desktop=phosh apps=browser', 'apt noise',
                         'a22x-firstboot: installing the desktop and apps; this takes 10-30 minutes',
                         'a22x-firstboot: installing apps: browser', 'a22x-firstboot: finished'):
                on_line(line + '\n')
            (root / 'etc/a22x/firstboot.conf').unlink()
            return 0
        self.patch_runner(runner)
        result = d.install(self.f.context, self.plan(desktop='phosh', wifi={'ssid': 'Test', 'password': 'fixture-psk'}), True)
        self.assertEqual((result['provisioned'], result['provision']), (True, 'recovery'))
        self.assertEqual(len(seen), 1)
        self.assertIn('a22x-firstboot: finished', (self.f.path / 'provision.log').read_text())
        self.assertFalse((self.f.live / 'etc/a22x/firstboot.conf').exists())
        self.assertTrue((self.f.live / 'opt/a22x-conn/a22x-firstboot.sh').exists())

    def test_a_failed_setup_in_recovery_is_left_to_the_first_start(self):
        def runner(root, on_line):
            on_line('a22x-firstboot: package install failed; the system finishes this at its first start\n')
            return 3
        self.patch_runner(runner)
        result = d.install(self.f.context, self.plan(desktop='phosh', wifi={'ssid': 'Test', 'password': 'fixture-psk'}), True)
        self.assertEqual((result['provisioned'], result['provision'], result['provision_status']), (False, 'first-start', 3))
        self.assertTrue((self.f.live / 'etc/a22x/firstboot.conf').exists())

    def test_a_chroot_that_cannot_start_is_left_to_the_first_start(self):
        def runner(root, on_line):
            raise subprocess.CalledProcessError(32, ['mount'])
        self.patch_runner(runner)
        result = d.install(self.f.context, self.plan(desktop='phosh', wifi={'ssid': 'Test', 'password': 'fixture-psk'}), True)
        self.assertEqual((result['provisioned'], result['provision']), (False, 'first-start'))
        self.assertTrue((self.f.live / 'etc/a22x/firstboot.conf').exists())

    def test_without_a_runner_the_first_start_does_it(self):
        (self.helpers / 'wpa_supplicant').write_bytes(b'static wpa_supplicant')
        result = d.install(self.f.context, self.plan(desktop='phosh', wifi={'ssid': 'Test', 'password': 'fixture-psk'}), True)
        self.assertEqual((result['provisioned'], result['provision']), (False, 'first-start'))

    def patch_runner(self, runner):
        (self.helpers / 'wpa_supplicant').write_bytes(b'static wpa_supplicant')
        old = d.PROVISION_RUNNER
        d.PROVISION_RUNNER = runner
        self.addCleanup(setattr, d, 'PROVISION_RUNNER', old)

    def test_provision_steps_follow_the_script(self):
        events = []
        progress = lambda phase, done=0, total=0: events.append((phase, done, total))
        for line in ('a22x-firstboot: start: desktop=sxmo shells=none sign-in=auto apps=none', 'Get:1 http://deb.debian.org trixie',
                     'a22x-firstboot: installing the desktop and apps; this takes 10-30 minutes',
                     "a22x-firstboot: building phoc's wlroots fix for Phosh; this takes 5-10 minutes",
                     'a22x-firstboot: running the setup script (system debian, packages apt)', 'a22x-firstboot: finished'):
            d.provision_step(line, progress)
        self.assertEqual(events, [('image-provision', 0, 5), ('image-provision', 1, 5), ('image-provision', 3, 5),
                                  ('image-provision', 4, 5), ('image-provision', 5, 5)])

    def test_more_shells_and_the_sign_in_screen(self):
        options = d.image_options('phosh', 'new', 'user', '246810', 0, None, shells=['plasma', 'phosh', 'sxmo'], greeter='phrog')
        self.assertEqual((options['shells'], options['greeter']), (['plasma', 'sxmo'], 'phrog'))
        plain = d.image_options('phosh', 'new', 'user', '246810', 0, None)
        self.assertNotIn('shells', plain)
        self.assertNotIn('greeter', plain)
        options = lambda desktop, **kw: d.image_options(desktop, 'new', 'user', 'x', 0, None, **kw)
        chosen = options('phosh', system_locale='uk_UA', timezone='America/Argentina/Buenos_Aires')
        self.assertEqual((chosen['locale'], chosen['timezone']), ('uk_UA', 'America/Argentina/Buenos_Aires'))
        self.error('INVALID_LOCALE', lambda: options('phosh', system_locale='xx_YY'))
        for bad in ('../etc/passwd', 'Europe', '/Europe/Kyiv', 'Europe/Kyiv;rm', 'E' * 70 + '/x'):
            self.error('INVALID_TIMEZONE', lambda: options('phosh', timezone=bad))
        self.error('INVALID_SHELL', lambda: options('phosh', shells=['kde']))
        self.error('INVALID_GREETER', lambda: options('phosh', greeter='xdm'))
        self.error('SHELLS_NEED_DESKTOP', lambda: options('console', shells=['phosh']))
        self.error('GREETER_NEEDS_DESKTOP', lambda: options('image', greeter='gdm'))

    def test_a_network_joined_from_the_saved_list_keeps_its_key(self):
        """2026-11-08: recovery joined "Test" from its saved list; the new system got it as open."""
        (self.f.path / 'wifi-saved.json').write_text(json.dumps({'networks': [
            {'ssid_hex': 'Test'.encode().hex(), 'psk': 'ab' * 32}, {'ssid_hex': 'Cafe'.encode().hex(), 'psk': ''}]}))
        self.assertEqual(d.saved_key(self.f.context, 'Test'), 'ab' * 32)
        self.assertEqual(d.saved_key(self.f.context, 'Cafe'), '')
        self.error('WIFI_NOT_SAVED', d.saved_key, self.f.context, 'Other')
        options = d.image_options('phosh', 'new', 'user', '246810', 0, None, {'ssid': 'Test', 'password': d.saved_key(self.f.context, 'Test')})
        self.assertEqual(options['wifi'], {'ssid': 'Test', 'password': 'ab' * 32})
        self.assertIn('psk=' + 'ab' * 32, d.wpa_network('Test', options['wifi']['password']))

    def test_the_recovery_itself_is_never_a_chroot_to_clean(self):
        self.assertEqual(list(d.chroot_processes(Path('/'))), [])
        self.assertEqual(list(d.chroot_processes(Path('/tmp/..'))), [])

    def test_mounts_never_follow_an_image_link(self):
        root = Path(self.tmp.name) / 'linked'
        for name in ('proc', 'sys', 'run', 'etc'):
            (root / name).mkdir(parents=True)
        (root / 'dev').symlink_to('/')
        self.assertIsNone(d.inside(root, 'dev'))
        with self.assertRaisesRegex(OSError, '/dev'):
            d.chroot_script(root, lambda line: None)
        self.assertEqual(os.readlink(root / 'dev'), '/')

    def test_the_chroot_is_undone_completely(self):
        """The real chroot_script in a mount, PID and network namespace of this test: proc, a
        read-only sys, a /dev without block devices, /run, the resolver; all undone after."""
        busybox = shutil.which('busybox')
        if not busybox or 'statically linked' not in subprocess.run(['file', '-L', busybox], capture_output=True, text=True).stdout:
            self.skipTest('no static busybox for the test root')
        if subprocess.run(['unshare', '--mount', '--pid', '--fork', '--net', '--mount-proc', 'true'],
                          capture_output=True).returncode:
            self.skipTest('no mount namespaces here')
        root = Path(self.tmp.name) / 'chroot'
        for name in ('proc', 'sys', 'dev', 'run', 'tmp', 'etc/a22x', 'usr/bin', 'usr/sbin', 'opt/a22x-conn'):
            (root / name).mkdir(parents=True, exist_ok=True)
        (root / 'bin').symlink_to('usr/bin')
        shutil.copy(busybox, root / 'usr/bin/busybox')
        for applet in ('sh', 'ls', 'cat', 'rm', 'touch', 'sleep', 'test'):
            (root / 'usr/bin' / applet).symlink_to('busybox')
        (root / 'etc/resolv.conf').symlink_to('../run/systemd/resolve/stub-resolv.conf')
        (root / 'etc/a22x/firstboot.conf').write_text('DESKTOP=phosh\n')
        (root / 'opt/a22x-conn/a22x-firstboot.sh').write_text(
            '#!/bin/sh\n'
            'echo "a22x-firstboot: start: desktop=phosh apps=none"\n'
            'test -c /dev/null && test -c /dev/urandom && test -c /dev/pts/ptmx || exit 7\n'
            'ls /dev > /tmp/devices\n'
            'test -n "$(cat /proc/self/stat)" || exit 8\n'
            'touch /sys/x 2>/dev/null && exit 9\n'
            'test -f /etc/resolv.conf && test ! -L /etc/resolv.conf || exit 10\n'
            'test -x /usr/sbin/policy-rc.d || exit 11\n'
            '# apt downloads as the user _apt: the resolver file must be readable by everyone.\n'
            'test "$(stat -c %a /etc/resolv.conf)" = 644 || exit 13\n'
            'test "$(stat -c %a /usr/sbin/policy-rc.d)" = 755 || exit 14\n'
            'test "$A22X_CHROOT" = 1 || exit 12\n'
            'sleep 300 &\n'
            'mkdir /etc/created && echo x > /etc/created/file\n'
            'rm /etc/a22x/firstboot.conf\n'
            'echo "a22x-firstboot: finished"\n')
        helper = Path(self.tmp.name) / 'run_chroot.py'
        helper.write_text(
            'import json, os, subprocess, sys\n'
            'from pathlib import Path\n'
            'sys.path.insert(0, %r)\n'
            'import distro as d\n'
            'source = Path(sys.argv[1])\n'
            'base = source.parent / "noexec-volume"\n'
            'base.mkdir()\n'
            '# The recovery mounts userdata noexec,nosuid,nodev; the root lives on such a mount here.\n'
            'subprocess.run(["mount", "-t", "tmpfs", "-o", "noexec,nosuid,nodev,mode=0755", "userdata", str(base)], check=True)\n'
            'subprocess.run(["cp", "-a", str(source), str(base / "root")], check=True)\n'
            'root = base / "root"\n'
            'lines = []\n'
            'status = d.chroot_script(root, lines.append, timeout=60)\n'
            'left = [l.split()[4] for l in open("/proc/self/mountinfo") if l.split()[4].startswith(str(root))]\n'
            'files = {n: (root / n).read_text() if (root / n).is_file() else None for n in ("tmp/devices",)}\n'
            'print(json.dumps({"status": status, "lines": lines, "left": left, "alive": list(d.chroot_processes(root)),\n'
            '                  "devices": files["tmp/devices"], "conf": (root / "etc/a22x/firstboot.conf").exists(),\n'
            '                  "resolv": os.readlink(root / "etc/resolv.conf"), "policy": (root / "usr/sbin/policy-rc.d").exists(),\n'
            '                  "dev": os.listdir(root / "dev"), "modes": [oct((root / p).stat().st_mode & 0o777) for p in ("etc/created", "etc/created/file")]}))\n'
            % str(Path(d.__file__).resolve().parent))
        run = subprocess.run(['unshare', '--mount', '--pid', '--fork', '--net', '--mount-proc', sys.executable, '-B', str(helper), str(root)],
                             capture_output=True, text=True, timeout=120)
        self.assertEqual(run.returncode, 0, run.stderr[-2000:])
        report = json.loads(run.stdout.strip().splitlines()[-1])
        self.assertEqual(report['status'], 0, report)
        self.assertEqual((report['left'], report['alive']), ([], []))
        self.assertEqual(report['lines'][-1].strip(), 'a22x-firstboot: finished')
        self.assertEqual(sorted(report['devices'].split()), sorted(['null', 'zero', 'full', 'random', 'urandom', 'tty', 'pts', 'shm',
                                                                    'ptmx', 'fd', 'stdin', 'stdout', 'stderr']))
        self.assertFalse(report['conf'])
        self.assertEqual(report['resolv'], '../run/systemd/resolve/stub-resolv.conf')
        self.assertFalse(report['policy'])
        self.assertEqual(report['dev'], [])
        self.assertEqual(report['modes'], ['0o755', '0o644'])  # Not the recovery's umask 077.

    def test_new_systems_skip_the_charger_screen_and_settings_switch_it(self):
        """BOOT's /init reads etc/a22x/charger-mode: off starts Linux instead of the charge-only screen."""
        d.install(self.f.context, self.plan(), True)
        mode = self.f.live / 'etc/a22x/charger-mode'
        self.assertEqual((mode.read_text(), oct(mode.stat().st_mode & 0o777)), ('off\n', '0o644'))
        self.assertEqual(d.charger_mode(self.f.context), {'charger_mode': 'off'})
        self.assertEqual(d.charger_mode(self.f.context, 'on'), {'charger_mode': 'on'})
        self.assertEqual(mode.read_text(), 'on\n')
        self.assertEqual(d.charger_mode(self.f.context, 'off')['charger_mode'], 'off')
        mode.unlink()
        self.assertEqual(d.charger_mode(self.f.context)['charger_mode'], 'on')  # No file: the old behaviour.
        self.error('INVALID_CHARGER_MODE', d.charger_mode, self.f.context, 'maybe')

    def test_later_systems_install_beside_it(self):
        d.install(self.f.context, self.plan(), True)
        options = d.image_options('console', 'keep', None, None, 0, None)
        later = d.image_plan(self.f.context, 'sdcard-p1', '/distro-1.0.tar.gz', options)['plan']
        self.assertFalse(later['clean_install'])
        self.assertFalse(d.install(self.f.context, later, True)['switched'])
        self.assertEqual(len(d.status(self.f.context)['slots']), 2)

    def test_the_image_default_account_becomes_the_typed_one(self):
        new = self.f.new
        (new / 'etc/passwd').write_text('root:x:0:0:root:/root:/bin/sh\nmobian:x:1000:1000:Mobian,,,:/home/mobian:/bin/bash\n')
        (new / 'var/lib/AccountsService/users').mkdir(parents=True)
        (new / 'var/lib/AccountsService/users/mobian').write_text('[User]\nSession=phosh\n')
        (new / 'etc/group').write_text('root:x:0:\nsudo:x:27:mobian\nmobian:x:1000:\nfeedbackd:x:990:mobian\n')
        (new / 'etc/gshadow').write_text('root:*::\nsudo:*::mobian\nmobian:!::\n')
        (new / 'etc/shadow').write_text('root:*:1::::::\nmobian:$y$default:1::::::\n')
        (new / 'etc/subuid').write_text('mobian:100000:65536\n')
        (new / 'home/mobian').mkdir(parents=True)
        (new / 'home/mobian/.profile').write_text('# image profile\n')
        (new / 'etc/greetd').mkdir(parents=True)
        (new / 'etc/greetd/config.toml').write_text('[initial_session]\ncommand = "phosh"\nuser = "mobian"\n\n[x]\n')
        self.repack()
        result = d.install(self.f.context, self.plan(username='user'), True)
        self.assertEqual(result['renamed_account'], 'mobian')
        live = self.f.live
        # The full name was the old login; login screens showed "Mobian" for the typed account (2026-11-09).
        self.assertIn('user:x:1000:1000:user,,,:/home/user:/bin/bash', (live / 'etc/passwd').read_text())
        self.assertEqual((live / 'var/lib/AccountsService/users/user').read_text(), '[User]\nSession=phosh\n')
        self.assertNotIn('mobian', (live / 'etc/passwd').read_text() + (live / 'etc/group').read_text())
        self.assertIn('sudo:x:27:user', (live / 'etc/group').read_text())
        self.assertIn('feedbackd:x:990:user', (live / 'etc/group').read_text())
        self.assertIn('user:x:1000:', (live / 'etc/group').read_text())
        self.assertIn('sudo:*::user', (live / 'etc/gshadow').read_text())
        self.assertTrue((live / 'etc/shadow').read_text().startswith('root:*'))
        self.assertIn('user:$y$default', (live / 'etc/shadow').read_text())
        self.assertEqual((live / 'etc/subuid').read_text(), 'user:100000:65536\n')
        self.assertEqual((live / 'home/user/.profile').read_text(), '# image profile\n')
        self.assertEqual((live / 'etc/greetd/config.toml').read_text(),
                         '[initial_session]\ncommand = "phosh"\nuser = "user"\n\n[x]\n')

    def test_the_typed_network_is_saved_for_every_start(self):
        (self.helpers / 'wpa_supplicant').write_bytes(b'static wpa_supplicant')
        wifi = {'ssid': 'Дім 5G', 'password': 'pass;word\\1'}
        result = d.install(self.f.context, self.plan(desktop='phosh', wifi=wifi, apps=['telegram', 'browser']), True)
        self.assertTrue(result['wifi_for_first_boot'])
        self.assertEqual(result['apps'], ['browser', 'telegram'])
        saved = list((self.f.live / 'etc/NetworkManager/system-connections').glob('a22x-*.nmconnection'))
        self.assertEqual(len(saved), 1)
        self.assertEqual(saved[0].stat().st_mode & 0o777, 0o600)
        text = saved[0].read_text()
        self.assertIn('ssid=Дім 5G\n', text)
        self.assertIn('psk=pass;word\\\\1\n', text)
        self.assertIn('autoconnect=true', text)
        key = hashlib.pbkdf2_hmac('sha1', wifi['password'].encode(), wifi['ssid'].encode(), 4096, 32).hex()
        self.assertEqual((self.f.live / 'etc/a22x/wpa.conf').read_text(),
                         'network={\n ssid=%s\n scan_ssid=1\n psk=%s\n}\n' % (wifi['ssid'].encode().hex(), key))
        self.assertEqual((self.f.live / 'opt/a22x-conn/wifi/wpa_supplicant').read_bytes(), b'static wpa_supplicant')
        conf = (self.f.live / 'etc/a22x/firstboot.conf').read_text()
        self.assertIn("APPS='browser telegram'", conf)
        # nmcli joins this network itself when NetworkManager has not (2026-11-08).
        self.assertIn("WIFI_SSID='Дім 5G'", conf)
        self.assertIn("WIFI_PASSWORD='pass;word\\1'", conf)

    def test_an_image_without_a_wifi_program_stops_early(self):
        wifi = {'ssid': 'home', 'password': ''}
        self.error('NO_WIFI_TOOLS', d.install, self.f.context, self.plan(desktop='phosh', wifi=wifi), True)
        self.assertEqual([p.name for p in self.f.live.iterdir()], ['lost+found'])
        # The image's own program is enough.
        (self.f.new / 'usr/sbin').mkdir(parents=True)
        (self.f.new / 'usr/sbin/wpa_supplicant').write_bytes(arm_elf())
        self.repack()
        self.assertTrue(d.install(self.f.context, self.plan(desktop='phosh', wifi=wifi), True)['wifi_for_first_boot'])
        self.assertFalse((self.f.live / 'opt/a22x-conn/wifi').exists())
        self.assertIn('key_mgmt=NONE', (self.f.live / 'etc/a22x/wpa.conf').read_text())
        self.assertNotIn('wifi-security', next((self.f.live / 'etc/NetworkManager/system-connections').glob('a22x-*')).read_text())

    def test_the_setup_script_is_copied_and_checked(self):
        script = self.f.sd / 'setup.sh'
        script.write_text('#!/bin/sh\necho hello\n')
        choice = {'volume': 'sdcard-p1', 'token': None, 'path': '/setup.sh'}
        plan = self.plan(script=choice)
        self.assertEqual(plan['binding']['script']['sha256'], hashlib.sha256(script.read_bytes()).hexdigest())
        script.write_text('#!/bin/sh\necho changed\n')
        with self.assertRaises(d.fs.FilesystemError) as caught:
            d.install(self.f.context, plan, True)
        self.assertIn(caught.exception.code, ('PLAN_STALE', 'SCRIPT_CHANGED'))
        self.assertEqual([p.name for p in self.f.live.iterdir()], ['lost+found'])
        d.install(self.f.context, self.plan(script=choice), True)
        copied = self.f.live / 'etc/a22x/setup-script'
        self.assertEqual(copied.read_text(), '#!/bin/sh\necho changed\n')
        self.assertEqual(copied.stat().st_mode & 0o777, 0o700)

    def test_a_binary_or_large_script_is_refused(self):
        (self.f.sd / 'bin.sh').write_bytes(b'#!/bin/sh\n\0')
        self.error('SCRIPT_NOT_TEXT', lambda: self.plan(script={'volume': 'sdcard-p1', 'token': None, 'path': '/bin.sh'}))
        (self.f.sd / 'big.sh').write_bytes(b'#' * (d.SCRIPT_LIMIT + 1))
        self.error('SCRIPT_TOO_LARGE', lambda: self.plan(script={'volume': 'sdcard-p1', 'token': None, 'path': '/big.sh'}))

    def test_an_interrupted_move_puts_everything_back(self):
        original, calls = d.Tree.rename, []

        def flaky(tree, source, destination):
            calls.append(source)
            if len(calls) == 4:
                raise OSError(5, 'injected')
            return original(tree, source, destination)
        with mock.patch.object(d.Tree, 'rename', flaky):
            with self.assertRaises(OSError):
                d.install(self.f.context, self.plan(), True)
        self.assertEqual([p.name for p in self.f.live.iterdir()], ['lost+found'])
        self.assertTrue(d.install(self.f.context, self.plan(), True)['switched'])


def make_qcow2(path, clusters, version=3, compress=(), zero=()):
    """A small qcow2 with 64 KiB clusters: header, L1, one L2, then data; None leaves a hole."""
    import zlib as z
    cb, cs = 16, 1 << 16
    size = len(clusters) * cs
    header = bytearray(cs)
    struct.pack_into('>4sIQIIQIIQQIIQ', header, 0, b'QFI\xfb', version, 0, 0, cb, size, 0, 1, cs, 0, 0, 0, 0)
    if version == 3:
        struct.pack_into('>QQQII', header, 72, 0, 0, 0, 4, 104)
    l1 = bytearray(cs)
    struct.pack_into('>Q', l1, 0, (2 * cs) | (1 << 63))
    l2 = bytearray(cs)
    data = bytearray()
    host = 3 * cs
    span = 62 - (cb - 8)
    for index, content in enumerate(clusters):
        if index in zero:
            struct.pack_into('>Q', l2, 8 * index, 1)
        elif content is None:
            continue
        elif index in compress:
            packer = z.compressobj(9, z.DEFLATED, -12)
            packed = packer.compress(content) + packer.flush()
            sectors = (len(packed) + 511) // 512
            struct.pack_into('>Q', l2, 8 * index, (1 << 62) | ((sectors - 1) << span) | host)
            data += packed + bytes(sectors * 512 - len(packed))
            host += sectors * 512
        else:
            struct.pack_into('>Q', l2, 8 * index, host | (1 << 63))
            data += content
            host += cs
    Path(path).write_bytes(bytes(header) + bytes(l1) + bytes(l2) + bytes(data))
    return size


def make_vhd(path, blocks, block=65536):
    """A dynamic VHD: footer copy, dynamic header, allocation table, blocks, footer."""
    size = len(blocks) * block
    footer = bytearray(512)
    struct.pack_into('>8sIIQ', footer, 0, b'conectix', 2, 0x10000, 512)
    struct.pack_into('>QQ', footer, 40, size, size)
    struct.pack_into('>I', footer, 60, 3)
    header = bytearray(1024)
    struct.pack_into('>8sQQIII', header, 0, b'cxsparse', 0xFFFFFFFFFFFFFFFF, 1536, 0x10000, len(blocks), block)
    table = bytearray(512)
    body = bytearray()
    sector = 4
    bitmap = 512
    for index, content in enumerate(blocks):
        if content is None:
            struct.pack_into('>I', table, 4 * index, 0xFFFFFFFF)
            continue
        struct.pack_into('>I', table, 4 * index, sector)
        body += b'\xff' * bitmap + content
        sector += (bitmap + block) // 512
    Path(path).write_bytes(bytes(footer) + bytes(header) + bytes(table) + bytes(body) + bytes(footer))
    return size


class TestImageStore(unittest.TestCase):
    """2026-11-10: images saved on the phone (userdata/.a22x-images) install without an SD card,
    stay through a switch and a clean installation, and a clean installation from the catalog
    downloads before it erases anything."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='a22x-store-')
        self.addCleanup(self.tmp.cleanup)
        self.f = ImageFixture(self.tmp.name)
        TestImageInstall.platform_bundle(self)
        self.store = self.f.live / d.STORE_DIR

    serve = TestCleanInstall.serve
    catalog = TestCleanInstall.catalog

    def error(self, code, call, *args):
        with self.assertRaises(d.fs.FilesystemError) as caught:
            call(*args)
        self.assertEqual(caught.exception.code, code)

    def save(self):
        self.store.mkdir(exist_ok=True)
        shutil.copyfile(self.f.image, self.store / self.f.image.name)
        return '/' + d.STORE_DIR + '/' + self.f.image.name

    def test_download_list_and_delete(self):
        self.catalog()
        self.assertEqual(d.images(self.f.context)['images'], [])
        saved = d.images_download(self.f.context, 'fixture-linux', True)
        self.assertEqual(saved['saved'], self.f.image.name)
        self.assertEqual((self.store / self.f.image.name).read_bytes(), self.f.image.read_bytes())
        item = d.images(self.f.context)['images'][0]
        self.assertEqual((item['file'], item['catalog_id'], item['name'], item['checked']),
                         (self.f.image.name, 'fixture-linux', 'Fixture Linux', True))
        self.assertEqual([p.name for p in self.store.iterdir() if p.name.startswith('.')], [])
        self.error('INVALID_ARGUMENT', d.images_delete, self.f.context, '../etc', True)
        self.error('IMAGE_NOT_FOUND', d.images_delete, self.f.context, 'missing.tar.gz', True)
        self.assertEqual(d.images_delete(self.f.context, self.f.image.name, True)['images'], [])
        self.assertEqual(list(self.store.iterdir()), [])

    def test_a_sideloaded_file_lands_in_the_store(self):
        # adb sideload (2026-11-10): the store is created when needed and must have room for the file.
        with d.sideload_store(self.f.context, 1000) as folder:
            self.assertEqual(folder.resolve(), self.store.resolve())
            (folder / 'sideload-20261110-000000.zip').write_bytes(b'PK\x03\x04' + b'0' * 996)
        self.assertEqual([item['file'] for item in d.images(self.f.context)['images']], ['sideload-20261110-000000.zip'])
        self.error('INSUFFICIENT_SPACE', lambda: d.sideload_store(self.f.context, 1 << 60).__enter__())

    def test_an_android_image_of_the_catalog_is_saved_but_never_installed_as_linux(self):
        """2026-11-10: GSIs in the catalog: listed with their version, type and whether they fit
        this phone's vendor; downloaded into the store, checked; the Linux installer refuses them."""
        import zipfile
        gsi = self.f.path / 'served' / 'aosp_arm64-exp-TEST.zip'
        gsi.parent.mkdir()
        with zipfile.ZipFile(gsi, 'w') as z:
            z.writestr('system.img', bytes(8192))
            z.writestr('vbmeta.img', b'AVB0' + bytes(60))
        base = self.serve(gsi.parent)
        data = gsi.read_bytes()
        entry = lambda android, levels: {
            'id': 'gsi-%s-aosp' % android, 'category': 'gsi', 'name': 'Android ' + android, 'version': 'TEST', 'android': android,
            'variant': 'aosp', 'region': 'Global', 'family': 'android', 'url': base + gsi.name, 'file': gsi.name, 'size': len(data),
            'sha256': hashlib.sha256(data).hexdigest(), 'system_bytes': 8192, 'vendor_levels': levels}
        (self.f.path / 'catalog.json').write_text(json.dumps({'images': [], 'android': [entry('13', [4, 5, 6, 7]), entry('16', [6, 7, 8])]}))
        listed = d.catalog(self.f.context)['android']
        self.assertEqual([(i['id'], i['fits'], i['variant'], i['region']) for i in listed],
                         [('gsi-13-aosp', True, 'aosp', 'Global'), ('gsi-16-aosp', False, 'aosp', 'Global')])
        self.assertTrue(listed[1]['fit_note'] and 'url' not in listed[0])
        saved = d.images_download(self.f.context, 'gsi-13-aosp', confirmed=True)
        self.assertEqual(saved['saved'], gsi.name)
        item = next(i for i in d.images(self.f.context)['images'] if i['file'] == gsi.name)
        self.assertEqual((item['family'], item['checked']), ('android', True))
        self.assertEqual((self.store / gsi.name).read_bytes(), data)
        self.error('CATALOG_NOT_LINUX', d.linux_entry, self.f.context, 'gsi-13-aosp')

    def android_phone(self, card=True, mounted=True, cache=False):
        """The same phone with Android on userdata: encrypted, not a place for files."""
        volumes = [{'id': 'userdata', 'path': 'userdata', 'fs_type': 'f2fs', 'fscrypt_feature': True, 'mounted': False, 'read_only': True}]
        if card:
            volumes.append({'id': 'sdcard-p1', 'path': 'sd', 'fs_type': 'ext4', 'mounted': mounted, 'read_only': True})
        if cache:
            (self.f.path / 'cache').mkdir(exist_ok=True)
            volumes.append({'id': 'cache', 'path': 'cache', 'fs_type': 'ext4', 'mounted': False, 'read_only': True})
        (self.f.path / 'filesystem-fixture.json').write_text(json.dumps({'schema': 'a22x.filesystem.fixture.v1', 'volumes': volumes}))
        state = self.f.path / '.filesystem-state.json'
        if state.exists():
            state.unlink()
        self.f.context = d.fs.FixtureContext(self.f.path)
        return self.f.context

    def android_catalog(self):
        import zipfile
        gsi = self.f.path / 'served' / 'aosp_arm64-exp-TEST.zip'
        gsi.parent.mkdir(exist_ok=True)
        with zipfile.ZipFile(gsi, 'w') as z:
            z.writestr('system.img', bytes(8192))
        data = gsi.read_bytes()
        base = self.serve(gsi.parent)
        linux = self.f.image.read_bytes()
        shutil.copyfile(self.f.image, gsi.parent / self.f.image.name)
        (self.f.path / 'catalog.json').write_text(json.dumps({
            'images': [{'id': 'fixture-linux', 'name': 'Fixture Linux', 'version': '1.0', 'family': 'fixture', 'url': base + self.f.image.name,
                        'sha256': hashlib.sha256(linux).hexdigest(), 'size': len(linux), 'file': self.f.image.name,
                        'format': 'tar', 'compression': 'gzip', 'factor': 4, 'minimal': True}],
            'android': [{'id': 'gsi-13-aosp', 'category': 'gsi', 'name': 'Android 13', 'version': 'TEST', 'android': '13', 'variant': 'aosp',
                         'region': 'Global', 'family': 'android', 'url': base + gsi.name, 'file': gsi.name, 'size': len(data),
                         'sha256': hashlib.sha256(data).hexdigest(), 'system_bytes': 8192, 'vendor_levels': [4, 5, 6, 7]}]}))
        return gsi, data

    def test_with_android_on_userdata_an_android_image_is_saved_on_the_card(self):
        """2026-11-11: a phone that runs Android had no place for a download from the catalog, since
        the store was only on a Linux userdata. There the store is the same folder on a card."""
        gsi, data = self.android_catalog()
        context = self.android_phone()
        token = context.volume('sdcard-p1')['volume_token']
        listed = d.images(context)
        self.assertEqual((listed['images'], listed['store_volume'], listed['store_token'], listed['store']),
                         ([], 'sdcard-p1', token, '/' + d.STORE_DIR))
        self.assertGreater(listed['free_bytes'], 0)
        self.assertFalse((self.f.sd / d.STORE_DIR).exists())
        saved = d.images_download(context, 'gsi-13-aosp', confirmed=True)
        self.assertEqual((saved['saved'], saved['store_volume'], saved['store_token']), (gsi.name, 'sdcard-p1', token))
        self.assertEqual((self.f.sd / d.STORE_DIR / gsi.name).read_bytes(), data)
        self.assertEqual([p.name for p in (self.f.sd / d.STORE_DIR).iterdir() if p.name.startswith('.part-')], [])
        self.assertFalse((self.f.live / d.STORE_DIR).exists())
        # The card was read-only before the download and is read-only again: the image on it can be installed.
        card = context.volume('sdcard-p1')
        self.assertTrue(card['mounted'] and card['read_only'])
        item = d.images(context)['images'][0]
        self.assertEqual((item['file'], item['path'], item['family'], item['checked']),
                         (gsi.name, '/' + d.STORE_DIR + '/' + gsi.name, 'android', True))
        # A Linux image keeps its place on the userdata it is installed to: none goes to the card.
        with self.assertRaises(d.fs.FilesystemError):
            d.images_download(context, 'fixture-linux', confirmed=True)
        self.assertEqual(sorted(p.name for p in (self.f.sd / d.STORE_DIR).iterdir()), [gsi.name, gsi.name + d.STORE_META])
        self.error('IMAGE_NOT_FOUND', d.images_delete, context, 'missing.zip', True)
        gone = d.images_delete(context, gsi.name, True)
        self.assertEqual((gone['images'], gone['store_volume']), ([], 'sdcard-p1'))
        self.assertEqual(list((self.f.sd / d.STORE_DIR).iterdir()), [])
        self.assertTrue(context.volume('sdcard-p1')['read_only'])

    def test_a_card_that_was_not_mounted_is_left_so_unless_it_holds_an_image(self):
        gsi, _data = self.android_catalog()
        context = self.android_phone(mounted=False)
        self.assertEqual(d.images(context)['store_volume'], 'sdcard-p1')
        self.assertFalse(context.volume('sdcard-p1')['mounted'])
        d.images_download(context, 'gsi-13-aosp', confirmed=True)
        card = context.volume('sdcard-p1')
        self.assertTrue(card['mounted'] and card['read_only'])
        self.assertEqual([i['file'] for i in d.images(context)['images']], [gsi.name])
        # Deleting finds the card mounted read-only and leaves it so.
        d.images_delete(context, gsi.name, True)
        card = context.volume('sdcard-p1')
        self.assertTrue(card['mounted'] and card['read_only'])

    def test_without_a_card_the_page_is_told_to_ask_for_one(self):
        self.android_catalog()
        context = self.android_phone(card=False)
        listed = d.images(context)
        self.assertEqual((listed['images'], listed['store_volume'], listed['free_bytes']), ([], '', 0))
        self.assertIn('SD card', listed['store_reason'])
        self.error('STORE_MISSING', d.images_download, context, 'gsi-13-aosp', True)
        self.error('IMAGE_NOT_FOUND', d.images_delete, context, 'aosp_arm64-exp-TEST.zip', True)

    def test_with_android_on_userdata_a_sideloaded_file_goes_to_the_card_or_to_cache(self):
        """2026-11-11: adb sideload refused every file on a phone that runs Android, since its only
        place was the store on a Linux userdata. A card's store takes it, else cache when it fits;
        the volume is read-only afterwards, so the file can be reviewed from it at once."""
        context = self.android_phone(cache=True)
        with d.sideload_place(context, 1000) as place:
            self.assertEqual((place['volume'], place['dir']), ('sdcard-p1', '/' + d.STORE_DIR))
            self.assertEqual(place['token'], context.volume('sdcard-p1')['volume_token'])
            (place['folder'] / 'sideload-20261111-000000.zip').write_bytes(b'PK\x03\x04' + bytes(996))
        self.assertTrue((self.f.sd / d.STORE_DIR / 'sideload-20261111-000000.zip').is_file())
        card = context.volume('sdcard-p1')
        self.assertTrue(card['mounted'] and card['read_only'])
        self.assertFalse(context.volume('cache')['mounted'])
        # No card: cache takes what fits, in a folder of its own, and is read-only afterwards.
        context = self.android_phone(card=False, cache=True)
        with d.sideload_place(context, 1000) as place:
            self.assertEqual((place['volume'], place['dir']), ('cache', '/sideload'))
            (place['folder'] / 'sideload-20261111-000001.zip').write_bytes(b'PK\x03\x04' + bytes(996))
        self.assertTrue((self.f.path / 'cache/sideload/sideload-20261111-000001.zip').is_file())
        volume = context.volume('cache')
        self.assertTrue(volume['mounted'] and volume['read_only'])
        # What fits neither is refused, and cache stays as it was left.
        self.error('INSUFFICIENT_SPACE', lambda: d.sideload_place(context, 1 << 50).__enter__())
        self.assertTrue(context.volume('cache')['read_only'])
        context = self.android_phone(card=True, cache=True)
        self.error('INSUFFICIENT_SPACE', lambda: d.sideload_place(context, 1 << 50).__enter__())
        # A Linux userdata keeps its own store, as before.
        self.f.context = d.fs.FixtureContext(self.f.path)
        (self.f.path / 'filesystem-fixture.json').write_text(json.dumps(self.f.context_json))
        state = self.f.path / '.filesystem-state.json'
        if state.exists():
            state.unlink()
        context = self.f.context = d.fs.FixtureContext(self.f.path)
        with d.sideload_place(context, 1000) as place:
            self.assertEqual((place['volume'], place['dir']), ('userdata', '/' + d.STORE_DIR))

    def test_a_sideload_reopens_a_read_only_userdata(self):
        """2026-11-10, on the phone: userdata left read-only by the image picker refused the
        transfer. It is writable for the transfer and read-only again after it."""
        context = self.f.context
        context.mount('userdata', 'ro', True, context.volume('userdata')['volume_token'])
        self.assertTrue(context.volume('userdata')['read_only'])
        with d.sideload_store(context, 1000) as folder:
            self.assertFalse(context.volume('userdata')['read_only'])
            (folder / 'sideload-20261110-000001-recovery.img').write_bytes(b'ANDROID!' + bytes(992))
        self.assertTrue(context.volume('userdata')['mounted'] and context.volume('userdata')['read_only'])
        self.assertIn('sideload-20261110-000001-recovery.img', [p.name for p in self.store.iterdir()])

    def test_a_saved_image_installs_beside_the_system(self):
        path = self.save()
        options = d.image_options('console', 'keep', None, None, 0, None)
        plan = d.image_plan(self.f.context, 'userdata', path, options)['plan']
        self.assertFalse(plan['clean_install'])
        result = d.install(self.f.context, plan, True)
        self.assertTrue((self.f.live / d.MANAGER / 'slots' / result['installed_slot'] / 'root/usr/bin/systemd').exists())
        self.assertTrue((self.store / self.f.image.name).exists())
        # Other files on userdata stay refused: only the store is a source there.
        self.error('BUNDLE_ON_USERDATA', d.image_plan, self.f.context, 'userdata', '/etc/os-release', options)

    def test_a_saved_image_installs_as_the_only_system_and_stays(self):
        path = self.save()
        options = d.image_options('console', 'new', 'user', '246810', 0, None)
        plan = d.image_plan(self.f.context, 'userdata', path, options, wipe=True)['plan']
        self.assertTrue(plan['clean_install'] and plan['erase_userdata'])
        result = d.install(self.f.context, plan, True)
        self.assertTrue(result['switched'] and result['clean_install'])
        self.assertTrue((self.store / self.f.image.name).exists())
        self.assertFalse((self.f.live / 'var/lib/pkg/state').read_text() == 'live')
        self.assertEqual(sorted(p.name for p in self.f.live.iterdir() if p.name.startswith('.a22x')), [d.STORE_DIR, d.MANAGER])

    def test_a_switch_leaves_the_store_in_place(self):
        self.save()
        options = d.image_options('console', 'keep', None, None, 0, None)
        slot = d.install(self.f.context, d.image_plan(self.f.context, 'sdcard-p1', '/distro-1.0.tar.gz', options)['plan'], True)['installed_slot']
        d.switch(self.f.context, d.switch_plan(self.f.context, slot)['plan'], True)
        self.assertTrue((self.store / self.f.image.name).exists())
        self.assertFalse((self.f.live / d.MANAGER / 'slots' / 'original' / 'root' / d.STORE_DIR).exists())

    def test_a_clean_catalog_install_downloads_before_erasing(self):
        self.catalog(sha256='0' * 64)
        options = d.image_options('console', 'new', 'user', '246810', 0, None)
        plan = d.image_plan(self.f.context, None, None, options, wipe=True, remote=d.catalog_entry(self.f.context, 'fixture-linux'))['plan']
        self.error('DOWNLOAD_CHECKSUM_MISMATCH', d.install, self.f.context, plan, True)
        # Nothing was erased: the system and /home are as they were.
        self.assertTrue((self.f.live / 'usr/bin/systemd').exists())
        self.f.assert_home(self)
        self.assertEqual([p for p in self.store.iterdir()] if self.store.exists() else [], [])
        self.catalog()
        plan = d.image_plan(self.f.context, None, None, options, wipe=True, remote=d.catalog_entry(self.f.context, 'fixture-linux'))['plan']
        self.assertTrue(d.install(self.f.context, plan, True)['clean_install'])
        self.assertEqual(list(self.store.iterdir()), [])
        self.assertFalse((self.f.live / d.DOWNLOAD_DIR).exists())

    def test_saved_images_survive_a_clean_install_without_room_to_download_first(self):
        self.save()
        self.catalog()
        options = d.image_options('console', 'new', 'user', '246810', 0, None)
        plan = d.image_plan(self.f.context, None, None, options, wipe=True, remote=d.catalog_entry(self.f.context, 'fixture-linux'))['plan']
        with mock.patch.object(d, 'download_before_erase', return_value=None):
            self.assertTrue(d.install(self.f.context, plan, True)['clean_install'])
        self.assertTrue((self.store / self.f.image.name).exists())


class TestVirtualDisks(unittest.TestCase):
    """2026-11-08: qcow2, dynamic VHD and Vagrant boxes unpack to the raw disk they describe."""
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='a22x-vdisk-')
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def unpack(self, source, convert):
        out_path = self.dir / 'out.raw'
        with open(out_path, 'wb') as out:
            convert(source, out)
        return out_path.read_bytes()

    def test_qcow2_clusters(self):
        cs = 1 << 16
        a, b = bytes([65]) * cs, (b'compressed cluster ' * 4000)[:cs]
        for version in (2, 3):
            image = self.dir / ('v%d.qcow2' % version)
            clusters = [a, None, b, a] if version == 2 else [a, None, b, a, bytes(cs)]
            size = make_qcow2(image, clusters, version, compress={2}, zero={4} if version == 3 else ())
            self.assertEqual(m.disk_format(image), 'qcow2')
            self.assertEqual(m.describe(image)['format'], 'qcow2')
            raw = self.unpack(image, m.qcow2_to_raw)
            self.assertEqual(len(raw), size)
            self.assertEqual(raw, a + bytes(cs) + b + a + (bytes(cs) if version == 3 else b''))

    def test_qcow2_with_a_backing_file_is_refused(self):
        image = self.dir / 'child.qcow2'
        make_qcow2(image, [bytes(1 << 16)])
        data = bytearray(image.read_bytes())
        struct.pack_into('>Q', data, 8, 4096)
        image.write_bytes(bytes(data))
        with self.assertRaises(m.ImageError) as caught:
            self.unpack(image, m.qcow2_to_raw)
        self.assertEqual(caught.exception.code, 'IMAGE_UNSUPPORTED')

    def test_dynamic_vhd_blocks(self):
        block = 65536
        c, d = bytes([67]) * block, bytes([68]) * block
        image = self.dir / 'disk.vhd'
        size = make_vhd(image, [c, None, d], block)
        self.assertEqual(m.disk_format(image), 'vhd')
        raw = self.unpack(image, m.vhd_to_raw)
        self.assertEqual(raw, c + bytes(block) + d)
        self.assertEqual(len(raw), size)

    def test_a_vagrant_box_unpacks_its_disk(self):
        image = self.dir / 'box.img'
        make_qcow2(image, [bytes([69]) * (1 << 16)])
        box = self.dir / 'fedora.vagrant.libvirt.box'
        with tarfile.open(box, 'w:gz') as out:
            out.add(image, 'box.img')
            meta = self.dir / 'metadata.json'
            meta.write_text('{"provider": "libvirt", "format": "qcow2"}')
            out.add(meta, 'metadata.json')
        work, root, seen = self.dir / 'work', self.dir / 'root', []
        work.mkdir()
        root.mkdir()
        original = m.locate

        def fake(path, unused_work, target, depth, progress):
            if m.disk_format(path) == 'qcow2':
                return original(path, unused_work, target, depth, progress)
            seen.append(Path(path).read_bytes()[:4])
            (target / 'usr').mkdir()
            (target / 'etc').mkdir()
            (target / 'etc/os-release').write_text('ID=fedora\n')
            return True
        with mock.patch.object(m, 'locate', side_effect=fake):
            m.extract(box, work, root)
        self.assertEqual(seen, [bytes([69]) * 4])
        self.assertFalse(list(work.iterdir()))


class TestDamagedImage(unittest.TestCase):
    def test_a_damaged_xz_file_is_named(self):
        """2026-11-08: an image cut short on the stick ended the import with a bare LZMAError."""
        import lzma
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            packed = lzma.compress(os.urandom(1 << 20) + bytes(4 << 20))
            image = tmp / 'system.img.xz'
            image.write_bytes(packed[:len(packed) // 2] + b'garbage' * 1000 + packed[len(packed) // 2:])
            (tmp / 'work').mkdir()
            (tmp / 'root').mkdir()
            with self.assertRaises(m.ImageError) as caught:
                m.extract(image, tmp / 'work', tmp / 'root')
            self.assertEqual(caught.exception.code, 'IMAGE_CORRUPT')
            image.write_bytes(packed[:len(packed) // 3])
            with self.assertRaises(m.ImageError) as caught:
                m.extract(image, tmp / 'work', tmp / 'root')
            self.assertEqual(caught.exception.code, 'IMAGE_CORRUPT')


class TestBtrfsSubvolumes(unittest.TestCase):
    def test_subvolumes_of_the_fstab_come_along(self):
        """2026-11-08: Fedora 45 Cloud keeps /var in its own subvolume; the new root had no /var/log."""
        import contextlib
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            top, target, work = tmp / 'top', tmp / 'target', tmp / 'work'
            (top / 'root/usr').mkdir(parents=True)
            (top / 'root/etc').mkdir()
            (top / 'root/var').mkdir()
            (top / 'root/etc/os-release').write_text('ID=fedora\n')
            (top / 'root/etc/fstab').write_text(
                'UUID=1 / btrfs subvol=root,compress=zstd:1 0 0\n'
                'UUID=1 /var btrfs subvol=var,compress=zstd:1 0 0\n'
                'UUID=1 /home btrfs subvol=/home 0 0\n'
                'UUID=1 /etc btrfs subvol=etc 0 0\n'
                'UUID=1 /var/../etc btrfs subvol=bad 0 0\n'
                'UUID=2 /boot/efi vfat umask=0077 0 2\n')
            (top / 'var/log').mkdir(parents=True)
            (top / 'var/lib/dnf').mkdir(parents=True)
            (top / 'var/log/boot.log').write_text('kept\n')
            (top / 'home/user').mkdir(parents=True)
            (top / 'etc').mkdir()
            (top / 'etc/passwd').write_text('not the system one\n')
            target.mkdir()
            work.mkdir()

            @contextlib.contextmanager
            def mounted(*unused):
                yield top
            with mock.patch.object(m, 'kernel_reads', return_value=True), \
                    mock.patch.object(m, 'Loop', side_effect=lambda *a: mounted()):
                self.assertTrue(m.from_filesystem(tmp / 'x.img', 0, 'btrfs', work, target, 0, None))
            self.assertEqual((target / 'var/log/boot.log').read_text(), 'kept\n')
            self.assertTrue((target / 'var/lib/dnf').is_dir())
            self.assertTrue((target / 'home/user').is_dir())
            self.assertFalse((target / 'etc/passwd').exists())  # /etc is not a place for a subvolume.

    def test_the_system_is_found_in_a_subvolume(self):
        """2026-11-08: Fedora keeps its system in the btrfs subvolume "root", Manjaro in "@"."""
        import contextlib
        for name in ('root', '@'):
            with tempfile.TemporaryDirectory() as tmp:
                tmp = Path(tmp)
                top, target, work = tmp / 'top', tmp / 'target', tmp / 'work'
                (top / name / 'usr').mkdir(parents=True)
                (top / name / 'etc').mkdir()
                (top / name / 'etc/os-release').write_text('ID=fedora\n')
                (top / 'home').mkdir()
                target.mkdir()
                work.mkdir()

                @contextlib.contextmanager
                def mounted(*unused):
                    yield top
                with mock.patch.object(m, 'kernel_reads', return_value=True), \
                        mock.patch.object(m, 'Loop', side_effect=lambda *a: mounted()):
                    self.assertTrue(m.from_filesystem(tmp / 'x.img', 0, 'btrfs', work, target, 0, None))
                self.assertEqual((target / 'etc/os-release').read_text(), 'ID=fedora\n')
                self.assertFalse((target / 'home').exists())


class TestUnreadableFilesystem(unittest.TestCase):
    def test_btrfs_without_kernel_support_is_named(self):
        """2026-11-08: the recovery kernel reads ext4, f2fs, erofs and squashfs only."""
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(m, 'kernel_reads', return_value=False):
            with self.assertRaises(m.ImageError) as caught:
                m.from_filesystem(Path(tmp) / 'x.img', 0, 'btrfs', Path(tmp), Path(tmp), 0, None)
        self.assertEqual(caught.exception.code, 'FILESYSTEM_UNSUPPORTED')
        self.assertIn('btrfs', str(caught.exception))


class TestWifiText(unittest.TestCase):
    def test_wpa_key_matches_the_standard_vector(self):
        # IEEE 802.11i-2004, H.4: passphrase "password", SSID "IEEE".
        self.assertIn('psk=f42c6fc52df0ebef9ebb4b90b38a5f902e83fe1b135a70e23aed762e9710a12e', d.wpa_network('IEEE', 'password'))
        self.assertIn('scan_ssid=1', d.wpa_network('IEEE', 'password'))

    def test_unusual_names_are_written_as_bytes(self):
        text = d.nm_connection({'ssid': ' a;b', 'password': ''})
        self.assertIn('ssid=32;97;59;98;\n', text)
        self.assertNotIn('[wifi-security]', text)

    def test_choices_are_checked(self):
        for wifi in ({'ssid': '', 'password': ''}, {'ssid': 'x' * 33, 'password': ''},
                     {'ssid': 'home', 'password': 'short'}, {'ssid': 'a\nb', 'password': ''}):
            with self.assertRaises(d.fs.FilesystemError):
                d.image_options('console', 'keep', None, None, 0, None, wifi)
        with self.assertRaises(d.fs.FilesystemError):
            d.image_options('console', 'keep', None, None, 0, None, None, ['games'])
        self.assertEqual(d.image_options('console', 'keep', None, None, 0, None, None, ['ssh', 'browser'])['apps'],
                         ['browser', 'ssh'])


class TestFormat(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='a22x-format-')
        self.addCleanup(self.tmp.cleanup)
        self.f = Fixture(self.tmp.name)

    def error(self, code, *args):
        with self.assertRaises(d.fs.FilesystemError) as caught:
            self.f.context.format(*args)
        self.assertEqual(caught.exception.code, code)

    def test_other_internal_partitions_are_refused(self):
        with self.assertRaises(d.fs.FilesystemError) as caught:
            d.fs.format_admitted({'id': 'boot', 'mounted': False}, 'ext4', False)
        self.assertEqual(caught.exception.code, 'FORMAT_FORBIDDEN')
        d.fs.format_admitted({'id': 'userdata', 'mounted': False}, 'f2fs', False)

    def test_mounted_volume_is_refused(self):
        self.error('VOLUME_MOUNTED', 'sdcard-p1', 'ext4', True)

    def test_format_needs_confirmation(self):
        with self.assertRaises(d.fs.FilesystemError):
            self.f.context.format('sdcard-p1', 'ext4', False)

    def test_unmounted_card_is_formatted(self):
        self.f.context.unmount('sdcard-p1')
        self.assertEqual(self.f.context.format('sdcard-p1', 'vfat', True)['formatted'], 'vfat')


class TestLibraryNames(unittest.TestCase):
    def test_short_names_follow_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            lib, target = Path(tmp) / 'lib', Path(tmp) / 'out'
            lib.mkdir()
            target.mkdir()
            (lib / 'libssl.so.4.0.3').write_text('x')
            (lib / 'libssl.so.4').symlink_to('libssl.so.4.0.3')
            (lib / 'libssl.so').symlink_to('libssl.so.4')
            (lib / 'libother.so.1').symlink_to('libother.so.1.2')
            (target / 'libssl.so.4.0.3').write_text('x')
            d.library_names(lib, target)
            self.assertTrue((target / 'libssl.so.4').exists())
            self.assertTrue((target / 'libssl.so').exists())
            self.assertFalse((target / 'libother.so.1').exists())


class TestArchiveWithImage(unittest.TestCase):
    def test_rootfs_image_inside_a_tar(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / 'x.boot.img').write_bytes(b'a' * 10)
            (tmp / 'x.rootfs.img').write_bytes(b'b' * 100)
            archive = tmp / 'p.tar'
            with tarfile.open(archive, 'w') as out:
                out.add(tmp / 'x.boot.img', 'x.boot.img')
                out.add(tmp / 'x.rootfs.img', 'x.rootfs.img')
            work, root, seen = tmp / 'work', tmp / 'root', []
            work.mkdir()
            root.mkdir()

            def fake(path, unused_work, target, depth, progress):
                seen.append(Path(path).read_bytes())
                (target / 'usr').mkdir()
                (target / 'etc').mkdir()
                (target / 'etc/os-release').write_text('ID=x\n')
                return True
            original, m.locate = m.locate, fake
            try:
                m.extract(archive, work, root)
            finally:
                m.locate = original
            self.assertEqual(seen, [b'b' * 100])
            self.assertFalse(list(root.glob('*.img')))


class TestFormats(unittest.TestCase):
    def test_mbr_and_gpt_absent(self):
        with tempfile.NamedTemporaryFile() as f:
            f.write(bytes(4096))
            f.flush()
            self.assertEqual(m.partitions(f.name), [])
            with self.assertRaises(m.ImageError):
                m.describe(f.name)


if __name__ == '__main__':
    unittest.main()


fs = d.fs


class LinuxBootTaken(unittest.TestCase):
    """2026-11-11, the owner: "I tried to install Mobian Phosh myself, flashing BOOT before it: make
    this simpler". With Android's kernel on BOOT a new system takes its Linux BOOT with it. First the
    release this recovery was made for, kept on cache or downloaded; only then another one that the
    phone keeps (on the owner's phone a BOOT kept since kernel 107 was taken, and the systems
    installed with it did not start as they should). The plan is bound to the image and the
    installation writes it at its end."""
    RELEASE = {'sha256': 'd' * 64, 'release': '7.2.0-a22x+'}
    OLD = {'sha256': 'c' * 64, 'release': '7.2.0-a22x+'}

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='a22x-linux-boot-')
        self.addCleanup(self.tmp.cleanup)
        self.f = ImageFixture(self.tmp.name)

    def spec(self, **more):
        spec = dict(self.f.context_json, **more)
        (self.f.path / 'filesystem-fixture.json').write_text(json.dumps(spec))

    def code(self, **kwargs):
        with self.assertRaises(fs.FilesystemError) as raised:
            d.boot_for_linux(self.f.context, **kwargs)
        return raised.exception.code

    def test_the_boot_there_is_used_when_it_is_linux(self):
        boot = d.boot_for_linux(self.f.context)
        self.assertNotIn('write', boot)
        status = d.status(self.f.context)
        self.assertEqual((status['boot_source'], status['boot_outdated']), ('', False))

    def test_without_any_linux_boot_the_plan_says_what_to_do(self):
        self.spec(android_boot=True)
        self.assertEqual(self.code(), 'BOOT_PROFILE_MISMATCH')
        self.assertEqual(d.status(self.f.context)['boot_source'], '')

    def test_the_release_kept_on_cache_needs_no_network(self):
        self.spec(android_boot=True, offline=True, linux_boot_release=self.RELEASE, linux_boot_cache=[self.OLD, self.RELEASE])
        boot = d.boot_for_linux(self.f.context)
        self.assertEqual((boot['boot_sha256'], boot['write'], boot['kernel_release']), ('d' * 64, True, '7.2.0-a22x+'))
        self.assertEqual(d.status(self.f.context)['boot_source'], 'cache')

    def test_the_release_comes_before_an_older_boot_the_phone_keeps(self):
        """What went wrong on the phone: the old one was newest on cache, and it was taken."""
        self.spec(android_boot=True, linux_boot_release=self.RELEASE, linux_boot_cache=[self.OLD])
        self.assertEqual(d.boot_for_linux(self.f.context)['boot_sha256'], 'd' * 64)
        self.assertEqual(d.status(self.f.context)['boot_source'], 'download')
        # Without the network the older one is better than none.
        self.spec(android_boot=True, offline=True, linux_boot_release=self.RELEASE, linux_boot_cache=[self.OLD])
        self.assertEqual(d.boot_for_linux(self.f.context)['boot_sha256'], 'c' * 64)
        self.assertEqual(d.status(self.f.context)['boot_source'], 'cache')

    def test_the_published_boot_needs_the_network_at_planning(self):
        self.spec(android_boot=True, offline=True, linux_boot_release=self.RELEASE)
        self.assertEqual(self.code(), 'LINUX_BOOT_NEEDS_NETWORK')
        self.spec(android_boot=True, linux_boot_release=self.RELEASE)
        self.assertEqual(d.boot_for_linux(self.f.context)['boot_sha256'], 'd' * 64)
        self.assertEqual(d.status(self.f.context)['boot_source'], 'download')

    def test_a_clean_installation_replaces_an_older_linux_boot(self):
        """BOOT holds a Linux kernel for this phone, but not the release: a clean installation writes
        the release; a system added beside the others leaves BOOT as it is."""
        self.spec(linux_boot_release=self.RELEASE, boot_sha256='c' * 64)
        self.assertNotIn('write', d.boot_for_linux(self.f.context))
        fresh = d.boot_for_linux(self.f.context, fresh=True)
        self.assertEqual((fresh['boot_sha256'], fresh['write']), ('d' * 64, True))
        status = d.status(self.f.context)
        self.assertEqual((status['boot_linux'], status['boot_outdated'], status['boot_source']), (True, True, 'download'))
        # The release is there already: nothing to write. Offline with nothing kept: BOOT stays.
        self.spec(linux_boot_release=self.RELEASE, boot_sha256='d' * 64)
        self.assertNotIn('write', d.boot_for_linux(self.f.context, fresh=True))
        self.assertFalse(d.status(self.f.context)['boot_outdated'])
        self.spec(linux_boot_release=self.RELEASE, boot_sha256='c' * 64, offline=True)
        self.assertNotIn('write', d.boot_for_linux(self.f.context, fresh=True))
        self.assertFalse(d.status(self.f.context)['boot_outdated'])

    def test_the_boot_is_fetched_first_and_written_last(self):
        self.spec(android_boot=True, linux_boot_release=self.RELEASE)
        steps = []
        plan = {'binding': {'boot': d.boot_for_linux(self.f.context)}, 'name': 'test'}
        with mock.patch.object(d, 'verify_plan'), \
                mock.patch.object(d, 'linux_boot_fetched', side_effect=lambda *a: steps.append('fetch') or '/kept.img') as fetched, \
                mock.patch.object(d, 'install_image_system', side_effect=lambda *a: steps.append('system') or {'installed_slot': 'x'}), \
                mock.patch.object(d, 'linux_boot_written', side_effect=lambda *a: steps.append('boot') or {'sha256': 'd' * 64, 'kept_boot': '/old.img'}):
            result = d.install_image(self.f.context, plan, True)
        self.assertEqual(steps, ['fetch', 'system', 'boot'])
        self.assertEqual((result['installed_slot'], result['boot_written'], result['kept_boot']), ('x', 'd' * 64, '/old.img'))
        self.assertEqual(fetched.call_args[0][1], plan['binding']['boot'])
        # A system that fails leaves BOOT as it was: nothing is written.
        steps.clear()
        with mock.patch.object(d, 'verify_plan'), mock.patch.object(d, 'linux_boot_fetched', return_value='/kept.img'), \
                mock.patch.object(d, 'install_image_system', side_effect=fs.FilesystemError('IMAGE_NOT_INSTALLABLE', 'no')), \
                mock.patch.object(d, 'linux_boot_written', side_effect=lambda *a: steps.append('boot')):
            with self.assertRaises(fs.FilesystemError):
                d.install_image(self.f.context, plan, True)
        self.assertEqual(steps, [])

    def test_the_fixture_writes_its_boot(self):
        self.spec(android_boot=True, linux_boot_cache=[self.OLD])
        boot = d.boot_for_linux(self.f.context)
        self.assertEqual(d.linux_boot_fetched(self.f.context, boot, lambda *a: None), '')
        d.linux_boot_written(self.f.context, '', boot, lambda *a: None)
        self.assertTrue(d.status(self.f.context)['boot_linux'])
        # A plan for an image that is neither kept nor offered any more is stale.
        self.spec(android_boot=True, linux_boot_release={'sha256': 'e' * 64, 'release': '7.2.0-a22x+'})
        with self.assertRaises(fs.FilesystemError) as raised:
            d.linux_boot_fetched(self.f.context, boot, lambda *a: None)
        self.assertEqual(raised.exception.code, 'PLAN_STALE')
