"""The data of a Linux system into an archive and into another system (linux_data.py, 2026-11-13)."""
import json
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
import linux_data as ld  # noqa: E402

STUB = '''#!/bin/sh
# A stand-in for the system's programs: says what it was asked, answers from files of the test.
me=$(basename "$0")
echo "$me $*" >> "$A22X_STUB_LOG"
case $me in
    getent) grep -q "^$2:" "$A22X_ROOT/etc/$1" ;;
    id) case $1 in -u) echo 0 ;; *) grep "^$2:" "$A22X_ROOT/etc/passwd" | cut -d: -f3; grep -q "^$2:" "$A22X_ROOT/etc/passwd" ;; esac ;;
    useradd) for last; do :; done; echo "$last:x:1001:1001::/home/$last:/bin/sh" >> "$A22X_ROOT/etc/passwd" ;;
    apt-cache) [ "$2" = firefox-esr ] || [ "$2" = htop ] && echo "Package: $2" ;;
    dpkg) grep -qx "$2" "$A22X_STUB_LOG.installed" 2>/dev/null && echo "Status: install ok installed" ;;
    apt-get) [ "$1" = install ] && { shift; for p; do case $p in -*) ;; *) echo "$p" >> "$A22X_STUB_LOG.installed" ;; esac; done; } ;;
esac
exit $?
'''


@unittest.skipUnless(shutil.which('zstd'), 'zstd is needed')
class LinuxData(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='a22x-linux-data-', dir=os.environ.get('TMPDIR')))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.data, self.sd = self.root / 'data', self.root / 'sd'
        self.sd.mkdir()
        self.make_arch(self.data)
        (self.root / 'filesystem-fixture.json').write_text(json.dumps({'schema': 'a22x.filesystem.fixture.v1', 'volumes': [
            {'id': 'userdata', 'path': 'data', 'fs_type': 'ext4', 'mounted': False, 'read_only': True},
            {'id': 'sdcard-p1', 'path': 'sd', 'fs_type': 'ext4', 'mounted': True, 'read_only': True}]}))
        self.ctx = fs.FixtureContext(self.root)
        stubs = self.root / 'stubs'
        stubs.mkdir()
        for name in ('getent', 'id', 'useradd', 'usermod', 'chown', 'apt-cache', 'apt-get', 'dpkg'):
            (stubs / name).write_text(STUB)
            (stubs / name).chmod(0o755)
        os.environ.update(A22X_LINUX_DATA_PATH=str(stubs), A22X_STUB_LOG=str(self.root / 'calls.log'))
        self.addCleanup(lambda: [os.environ.pop(k, None) for k in ('A22X_LINUX_DATA_PATH', 'A22X_STUB_LOG')])

    def make_arch(self, top):
        for folder in ('etc/NetworkManager/system-connections', 'etc/ssh', 'home/alex/.cache/junk', 'home/alex/Документи', 'root',
                       'var/lib/pacman/local/firefox-1-1', 'var/lib/pacman/local/htop-3-1', 'var/lib/pacman/local/libfoo-1-1',
                       'var/lib/pacman/local/zsh-5-1', 'var/lib/pacman/local/linux-aarch64-1-1', 'var/tmp', 'lib/modules/7.2.0'):
            (top / folder).mkdir(parents=True)
        (top / 'etc/os-release').write_text('NAME="Arch Linux ARM"\nID=archarm\nID_LIKE=arch\nPRETTY_NAME="Arch Linux ARM"\n')
        (top / 'etc/passwd').write_text('root:x:0:0::/root:/bin/bash\nalex:x:1000:1000::/home/alex:/bin/zsh\nnobody:x:65534:65534::/:/sbin/nologin\n')
        (top / 'etc/shadow').write_text('root:$6$r$R:1::::::\nalex:$6$salt$HASH:1::::::\n')
        (top / 'etc/group').write_text('wheel:x:998:alex\nvideo:x:985:alex\nalex:x:1000:\n')
        (top / 'etc/hostname').write_text('a22x\n')
        (top / 'etc/locale.conf').write_text('LANG=uk_UA.UTF-8\n')
        (top / 'etc/localtime').symlink_to('/usr/share/zoneinfo/Europe/Berlin')
        (top / 'etc/NetworkManager/system-connections/Home.nmconnection').write_text('[wifi]\nssid=Home\n')
        (top / 'etc/ssh/ssh_host_ed25519_key').write_text('KEY')
        (top / 'etc/ssh/sshd_config').write_text('system file')
        (top / 'etc/fstab').write_text('this phone only')
        (top / 'home/alex/Документи/нотатка.txt').write_text('моє\n')
        (top / 'home/alex/.zshrc').write_text('alias ll=ls\n')
        (top / 'home/alex/.cache/junk/big').write_bytes(b'x' * 4096)
        (top / 'root/.bash_history').write_text('ls\n')
        for name, reason in (('firefox', None), ('htop', '0'), ('libfoo', '1'), ('zsh', None), ('linux-aarch64', None)):
            (top / ('var/lib/pacman/local/%s-%s-1/desc' % (name, '3' if name == 'htop' else '5' if name == 'zsh' else '1'))).write_text(
                '%NAME%\n' + name + '\n\n' + ('%REASON%\n' + reason + '\n\n' if reason else ''))

    def progress(self, *unused):
        pass

    def test_inspect_backup_and_restore_into_another_distribution(self):
        seen = ld.inspect(self.ctx)
        self.assertTrue(seen['linux'], seen)
        self.assertEqual((seen['os']['family'], seen['accounts'], seen['parts']['packages'], seen['parts']['accounts']), ('arch', ['alex'], 3, 1))
        self.assertEqual([d['volume'] for d in seen['destinations']], ['sdcard-p1'])
        self.assertFalse(self.ctx.volume('userdata')['mounted'])  # left as it was found
        done = ld.backup(self.ctx, ['home', 'accounts', 'settings', 'packages'], 'sdcard-p1', None, True, self.progress)
        archive = self.sd / done['archive']['path'].lstrip('/')
        self.assertTrue(archive.is_file() and done['packages'] == 3 and done['accounts'] == 1)
        names = subprocess.run(['tar', '--zstd', '-tf', str(archive)], capture_output=True, text=True, check=True).stdout.split('\n')
        self.assertIn('home/alex/Документи/нотатка.txt', names)
        self.assertIn('settings/etc/NetworkManager/system-connections/Home.nmconnection', names)
        self.assertIn('settings/etc/ssh/ssh_host_ed25519_key', names)
        self.assertIn('restore.sh', names)
        for absent in ('home/alex/.cache/junk/big', 'settings/etc/fstab', 'settings/etc/ssh/sshd_config'):
            self.assertNotIn(absent, names)
        # A card mounted only to be looked at is let go again.
        config = json.loads((self.root / 'filesystem-fixture.json').read_text())
        config['volumes'][1]['mounted'] = False
        (self.root / 'filesystem-fixture.json').write_text(json.dumps(config))
        (self.root / '.filesystem-state.json').unlink(missing_ok=True)  # the backup had mounted it for writing
        looked = ld.inspect(fs.FixtureContext(self.root))
        self.assertEqual(len(looked['archives']), 1)
        self.assertEqual([d['volume'] for d in looked['destinations']], ['sdcard-p1'])
        self.assertFalse(fs.FixtureContext(self.root).volume('sdcard-p1')['mounted'])
        config['volumes'][1]['mounted'] = True
        (self.root / 'filesystem-fixture.json').write_text(json.dumps(config))
        (self.root / '.filesystem-state.json').unlink(missing_ok=True)
        listed = ld.inspect(self.ctx)['archives']
        self.assertEqual((len(listed), listed[0]['parts'], listed[0]['os']['family'], listed[0]['accounts']),
                         (1, list(ld.PARTS), 'arch', ['alex']))
        # The phone is erased and another distribution installed: Debian, no account of that name.
        shutil.rmtree(self.data)
        for folder in ('etc/NetworkManager', 'etc/ssh', 'home', 'root', 'var/tmp', 'bin'):
            (self.data / folder).mkdir(parents=True)
        (self.data / 'etc/os-release').write_text('ID=debian\nPRETTY_NAME="Debian GNU/Linux 13"\n')
        (self.data / 'etc/passwd').write_text('root:x:0:0::/root:/bin/bash\n')
        (self.data / 'etc/group').write_text('sudo:x:27:\nvideo:x:44:\n')
        (self.data / 'etc/hostname').write_text('debian\n')
        (self.data / 'bin/bash').write_text('#!')
        (self.data / 'bin/bash').chmod(0o755)
        back = ld.restore(fs.FixtureContext(self.root), 'sdcard-p1', None, listed[0]['path'], list(ld.PARTS), True, self.progress)
        self.assertEqual((back['accounts_created'], back['into']['id'], back['owners_changed']), (['alex'], 'debian', 1))
        self.assertEqual((back['packages_installed'], back['packages_missing'], back['missing']), (2, 1, ['zsh']))
        self.assertEqual((self.data / 'home/alex/Документи/нотатка.txt').read_text(), 'моє\n')
        self.assertEqual((self.data / 'root/.bash_history').read_text(), 'ls\n')
        self.assertEqual((self.data / 'etc/hostname').read_text(), 'a22x\n')
        self.assertEqual(os.readlink(self.data / 'etc/localtime'), '/usr/share/zoneinfo/Europe/Berlin')
        self.assertTrue((self.data / 'etc/NetworkManager/system-connections/Home.nmconnection').is_file())
        self.assertEqual((self.data / 'etc/ssh/ssh_host_ed25519_key').read_text(), 'KEY')
        self.assertFalse((self.data / 'var/tmp/a22x-restore').exists())
        calls = (self.root / 'calls.log').read_text()
        self.assertIn('useradd -m -u 1000 -s /bin/bash alex', calls)   # zsh is not there: bash
        self.assertIn('usermod -p $6$salt$HASH alex', calls)
        self.assertIn('usermod -aG sudo alex', calls)
        self.assertIn('usermod -aG video alex', calls)
        self.assertIn('apt-get install -y -q --no-install-recommends --no-remove firefox-esr htop', calls)  # firefox by Debian's name
        # Restored once more: the account is there now, and its password is left alone.
        (self.root / 'calls.log').write_text('')
        again = ld.restore(fs.FixtureContext(self.root), 'sdcard-p1', None, listed[0]['path'], ['accounts'], True, self.progress)
        self.assertEqual((again['accounts_created'], again['accounts_kept']), ([], ['alex']))
        self.assertNotIn('usermod -p', (self.root / 'calls.log').read_text())

    def test_parts_alone_and_refusals(self):
        done = ld.backup(self.ctx, ['settings'], 'sdcard-p1', None, True, self.progress)
        names = subprocess.run(['tar', '--zstd', '-tf', str(self.sd / done['archive']['path'].lstrip('/'))], capture_output=True, text=True).stdout
        self.assertNotIn('home/', names)
        self.assertNotIn('accounts.tsv', names)
        back = ld.restore(self.ctx, 'sdcard-p1', None, done['archive']['path'], ['home', 'settings'], True, self.progress)
        self.assertEqual(back['parts'], ['settings'])
        for call, code in ((lambda: ld.backup(self.ctx, [], 'sdcard-p1', None, True, self.progress), 'NOTHING_SELECTED'),
                           (lambda: ld.backup(self.ctx, ['home'], 'sdcard-p1', None, False, self.progress), 'CONFIRMATION_REQUIRED'),
                           (lambda: ld.restore(self.ctx, 'sdcard-p1', None, done['archive']['path'], ['packages'], True, self.progress), 'NOTHING_SELECTED')):
            with self.assertRaises(fs.FilesystemError) as caught:
                call()
            self.assertEqual(caught.exception.code, code)
        (self.sd / 'A22xDataBackups/other.tar.zst').write_bytes(b'not one')
        with self.assertRaises(fs.FilesystemError) as caught:
            ld.restore(self.ctx, 'sdcard-p1', None, '/A22xDataBackups/other.tar.zst', ['home'], True, self.progress)
        self.assertEqual(caught.exception.code, 'NOT_A_DATA_BACKUP')
        # Android on userdata: nothing to back up, the archives are still listed.
        config = json.loads((self.root / 'filesystem-fixture.json').read_text())
        config['volumes'][0]['fs_type'] = 'f2fs'
        (self.root / 'filesystem-fixture.json').write_text(json.dumps(config))
        seen = ld.inspect(fs.FixtureContext(self.root))
        self.assertEqual((seen['linux'], seen['reason'], len(seen['archives'])), (False, 'NO_LINUX', 1))

    def test_a_backup_protected_with_a_password(self):
        """2026-11-13: encrypted when a password is set; listed closed without it; restored with it;
        and restore.sh alone opens it on another device with openssl."""
        import archive_crypt as ac
        import partition_archive as pa
        self.addCleanup(lambda: (setattr(pa, 'PASSWORD', None), setattr(pa, 'ENCRYPT', False), setattr(ac, 'ROUNDS', ac.ROUNDS)))
        pa.PASSWORD, pa.ENCRYPT = 'мій пароль', True
        done = ld.backup(self.ctx, ['home', 'accounts'], 'sdcard-p1', None, True, self.progress)
        self.assertTrue(done['encrypted'] and done['archive']['path'].endswith('.tar.zst.enc'), done)
        archive = self.sd / done['archive']['path'].lstrip('/')
        self.assertTrue(archive.read_bytes().startswith(b'Salted__'))
        self.assertNotIn('HASH'.encode(), archive.read_bytes())
        pa.PASSWORD, pa.ENCRYPT = None, False
        row = ld.inspect(self.ctx)['archives'][0]
        self.assertEqual((row['locked'], row['encrypted'], row['parts'], row['os']['name']), (True, True, [], 'archarm'))
        with self.assertRaises(pa.ArchiveError) as caught:
            ld.restore(self.ctx, 'sdcard-p1', None, row['path'], ['home'], True, self.progress)
        self.assertEqual(caught.exception.code, 'PASSWORD_REQUIRED')
        pa.PASSWORD = 'not it'
        self.assertTrue(ld.inspect(self.ctx)['archives'][0]['wrong_password'])
        pa.PASSWORD = 'мій пароль'
        row = ld.inspect(self.ctx)['archives'][0]
        self.assertEqual((row['locked'], row['encrypted'], row['parts'], row['accounts']), (False, True, ['home', 'accounts'], ['alex']))
        (self.data / 'home/alex/Документи/нотатка.txt').write_text('changed')
        back = ld.restore(self.ctx, 'sdcard-p1', None, row['path'], ['home'], True, self.progress)
        self.assertEqual(back['parts'], ['home'])
        self.assertEqual((self.data / 'home/alex/Документи/нотатка.txt').read_text(), 'моє\n')
        # One byte changed: refused before a file of it reaches the system.
        blob = bytearray(archive.read_bytes())
        blob[len(blob) // 2] ^= 1
        archive.write_bytes(bytes(blob))
        (self.data / 'home/alex/Документи/нотатка.txt').write_text('changed')
        with self.assertRaises(ac.CryptError) as caught:
            ld.restore(self.ctx, 'sdcard-p1', None, row['path'], ['home'], True, self.progress)
        self.assertEqual(caught.exception.code, 'BACKUP_DAMAGED')
        self.assertEqual((self.data / 'home/alex/Документи/нотатка.txt').read_text(), 'changed')
        blob[len(blob) // 2] ^= 1
        archive.write_bytes(bytes(blob))
        if shutil.which('openssl'):
            # Another device: restore.sh from the repository, the archive and its password.
            other = self.root / 'other'
            (other / 'etc').mkdir(parents=True)
            (other / 'etc/passwd').write_text('root:x:0:0::/root:/bin/sh\n')
            (other / 'etc/group').write_text('')
            env = dict(os.environ, A22X_ROOT=str(other), A22X_FAKE_ROOT='1', A22X_PASSWORD='мій пароль', TMPDIR=str(self.root),
                       PATH=os.environ['A22X_LINUX_DATA_PATH'] + ':' + os.environ['PATH'])
            run = subprocess.run(['sh', str(ROOT / 'linux_data_restore.sh'), str(archive), '--parts', 'home'], env=env, capture_output=True, text=True)
            self.assertIn('a22x: done', run.stdout, run.stdout + run.stderr)
            self.assertEqual((other / 'home/alex/Документи/нотатка.txt').read_text(), 'моє\n')
            env['A22X_PASSWORD'] = 'wrong'
            run = subprocess.run(['sh', str(ROOT / 'linux_data_restore.sh'), str(archive)], env=env, capture_output=True, text=True)
            self.assertEqual(run.returncode, 1)
            self.assertIn('password does not open', run.stdout)

    def test_debian_package_list(self):
        top = self.root / 'deb'
        (top / 'var/lib/dpkg').mkdir(parents=True)
        (top / 'var/lib/apt').mkdir(parents=True)
        (top / 'var/lib/dpkg/status').write_text(
            'Package: htop\nStatus: install ok installed\nPriority: optional\n\nPackage: libc6\nStatus: install ok installed\nPriority: required\n\n'
            'Package: libnl\nStatus: install ok installed\nPriority: optional\n\nPackage: gone\nStatus: deinstall ok config-files\nPriority: optional\n')
        (top / 'var/lib/apt/extended_states').write_text('Package: libnl\nArchitecture: arm64\nAuto-Installed: 1\n')
        fd = os.open(top, os.O_RDONLY)
        self.addCleanup(os.close, fd)
        self.assertEqual(ld.packages_of(fd, 'debian'), ['htop'])
        self.assertIsNone(ld.packages_of(fd, 'fedora'))
        self.assertIsNone(ld.packages_of(fd, 'suse'))
        # 2026-11-13: dnf5's own record of why a package is there, and apk's world.
        (top / 'usr/lib/sysimage/libdnf5').mkdir(parents=True)
        (top / 'usr/lib/sysimage/libdnf5/packages.toml').write_text(
            'version = "1.0"\n[packages]\n"htop.aarch64" = {reason = "User"}\n"glibc.aarch64" = {reason = "Dependency"}\n'
            '"linux-firmware.noarch" = {reason = "User"}\n[packages."python3.12-pip.noarch"]\nreason = "User"\n'
            '[packages.vim-enhanced.aarch64]\nreason = "User"\n[packages.libfoo.aarch64]\nreason = "Weak Dependency"\n')
        self.assertEqual(ld.packages_of(fd, 'fedora'), ['htop', 'python3.12-pip', 'vim-enhanced'])
        (top / 'etc/apk').mkdir(parents=True)
        (top / 'etc/apk/world').write_text('alpine-base\nhtop\nfish>=3.0\nfoo@testing\n')
        self.assertEqual(ld.packages_of(fd, 'alpine'), ['alpine-base', 'fish', 'foo', 'htop'])

    def backup_now(self):
        done = ld.backup(self.ctx, ['home', 'accounts', 'settings', 'packages'], 'sdcard-p1', None, True, self.progress)
        return done['archive']['path']

    def unmount_card(self):
        config = json.loads((self.root / 'filesystem-fixture.json').read_text())
        config['volumes'][1]['mounted'] = False
        (self.root / 'filesystem-fixture.json').write_text(json.dumps(config))
        (self.root / '.filesystem-state.json').unlink(missing_ok=True)
        return fs.FixtureContext(self.root)

    def test_a_backup_on_a_card_that_is_only_inserted_restores(self):
        # 2026-11-14 review: the page listed such a backup, and its restore said VOLUME_NOT_MOUNTED.
        path = self.backup_now()
        ctx = self.unmount_card()
        token = ctx.volume('sdcard-p1')['volume_token']
        back = ld.restore(ctx, 'sdcard-p1', token, path, ['home'], True, self.progress)
        self.assertEqual(back['parts'], ['home'])
        self.assertFalse(fs.FixtureContext(self.root).volume('sdcard-p1')['mounted'])  # let go again

    def test_a_failure_after_unpacking_takes_the_staging_folder_with_it(self):
        # 2026-11-14 review: a failure while the recovery's script was put in left the staged
        # password hashes, keys and Wi-Fi in the system.
        path = self.backup_now()
        from unittest import mock
        with mock.patch.object(ld, 'SCRIPT', self.root / 'no-such-script.sh'):
            with self.assertRaises(OSError):
                ld.restore(fs.FixtureContext(self.root), 'sdcard-p1', None, path, ['accounts', 'settings'], True, self.progress)
        self.assertFalse((self.data / 'var/tmp/a22x-restore').exists())

    def test_a_link_named_home_does_not_move_where_homes_go(self):
        # 2026-11-14 review: 'home' -> '.' first, then 'home/etc/shadow' landed in the system's /etc.
        import io
        import tarfile
        raw = io.BytesIO()
        with tarfile.open(fileobj=raw, mode='w') as tar:
            link = tarfile.TarInfo('home')
            link.type, link.linkname = tarfile.SYMTYPE, '.'
            tar.addfile(link)
            evil = tarfile.TarInfo('home/etc/shadow')
            evil.size = 5
            tar.addfile(evil, io.BytesIO(b'owned'))
        packed = subprocess.run(['zstd', '-q', '-c'], input=raw.getvalue(), capture_output=True, check=True).stdout
        archive = self.root / 'evil.tar.zst'
        archive.write_bytes(packed)
        before = (self.data / 'etc/shadow').read_text()
        fd = os.open(archive, os.O_RDONLY)
        self.addCleanup(os.close, fd)
        with self.assertRaises(fs.FilesystemError) as caught:
            ld.unpack_into(fd, str(self.data), ['home'], self.progress)
        self.assertEqual(caught.exception.code, 'ARCHIVE_UNSAFE')
        self.assertEqual((self.data / 'etc/shadow').read_text(), before)
        self.assertTrue((self.data / 'home').is_dir() and not (self.data / 'home').is_symlink())
        self.assertFalse((self.data / 'var/tmp/a22x-restore').exists())

    def test_a_home_too_large_still_shows_its_system_and_caches_are_not_walked(self):
        # 2026-11-14 review: ~/.cache was walked whole before it was left out, and a home too large
        # for one archive made the page say there was no Linux system.
        from unittest import mock
        import file_archive as fa
        for n in range(60):
            (self.data / 'home/alex/.cache/junk' / ('f%02d' % n)).write_text('x')
        with mock.patch.object(fa, 'MAX_ENTRIES', 40):
            seen = ld.inspect(self.ctx)
        self.assertTrue(seen['linux'] and not seen['reason'], seen)
        with mock.patch.object(fa, 'MAX_ENTRIES', 3):
            seen = ld.inspect(self.ctx)
        self.assertEqual((seen['linux'], seen['reason'], seen['parts']['home'], seen['accounts']), (True, 'SELECTION_TOO_LARGE', -1, ['alex']))

    def test_each_part_says_what_it_adds_to_one_archive(self):
        # 2026-11-14 reviews: one limit for the archive (not one for each part), and a home that fits
        # alone is offered; inspect says what each part adds, and the backup checks the same sum.
        from unittest import mock
        import file_archive as fa
        seen = ld.inspect(self.ctx)
        home, settings = seen['entries']['home'], seen['entries']['settings']
        self.assertEqual(seen['max_entries'], fa.MAX_ENTRIES)
        with mock.patch.object(fa, 'MAX_ENTRIES', home + settings - 1):
            seen = ld.inspect(self.ctx)
            self.assertFalse(seen['reason'], seen)  # each fits alone: both offered
            with self.assertRaises(fs.FilesystemError) as caught:
                ld.backup(self.ctx, ['home', 'settings'], 'sdcard-p1', None, True, self.progress)
            self.assertEqual(caught.exception.code, 'SELECTION_TOO_LARGE')
            ld.backup(self.ctx, ['home'], 'sdcard-p1', None, True, self.progress)
        with mock.patch.object(fa, 'MAX_ENTRIES', home + settings):
            ld.backup(self.ctx, ['home', 'settings'], 'sdcard-p1', None, True, self.progress)

    def test_a_failed_upgrade_is_read_as_such(self):
        # 2026-11-14 review: "packages upgrade failed" was read as the line of counts, and
        # int('upgrade') ended the helper with a traceback after everything else was restored.
        found = ld.script_result(['account alex kept', 'packages upgrade failed', 'packages-skipped 2', 'packages 1 0', 'done'])
        self.assertEqual((found['packages_installed'], found['packages_missing'], found['packages_skipped'], found['note']),
                         (1, 0, 2, ld.UPGRADE_FAILED))
        self.assertEqual(ld.script_result(['packages upgrade failed', 'done'])['packages_installed'], 0)
        # Digits that are not ASCII are no counts (2026-11-14 review: '²'.isdigit() and int('²') raises).
        self.assertEqual(ld.script_result(['packages \u00b2 0', 'packages-skipped \u0661', 'done'])['packages_installed'], 0)

    def test_arch_is_upgraded_once_and_never_partly(self):
        # 2026-11-14 review: pacman -Syu for every package meant one upgrade that could not go
        # through kept every package out, each one tried again in vain.
        staged = self.root / 'staged'
        system = self.root / 'arch'
        (system / 'etc').mkdir(parents=True)
        staged.mkdir()
        (system / 'etc/os-release').write_text('ID=archarm\nID_LIKE=arch\n')
        (staged / 'manifest.json').write_text('{}')
        (staged / 'packages.txt').write_text('htop\nfirefox\n')
        (staged / 'source-family').write_text('arch\n')
        (staged / 'aliases.tsv').write_text('firefox|firefox-esr|firefox\n')
        stubs = self.root / 'pacman-stubs'
        stubs.mkdir()
        log = self.root / 'pacman.log'
        (stubs / 'pacman').write_text('#!/bin/sh\necho "pacman $*" >> "%s"\n'
                                      'case "$1" in -Syu) exit $UPGRADE ;; -Qq) grep -qx "$2" "%s.installed" 2>/dev/null ;; '
                                      '-Si) exit 0 ;; -S) shift; for p; do case $p in -*) ;; *) echo "$p" >> "%s.installed" ;; esac; done ;; esac\n'
                                      % (log, log, log))
        (stubs / 'pacman').chmod(0o755)
        # A list already there needs no upgrade at all (2026-11-14 review: it upgraded the system as a
        # side effect); a failed upgrade is said, and nothing is installed onto the older system.
        for upgrade, installs, upgrades, said in (('1', False, 1, 'a22x: packages 0 0'),
                                                  ('0', True, 1, 'a22x: packages 2 0'), ('1', False, 0, 'a22x: packages 2 0')):
            log.unlink(missing_ok=True)
            if upgrades:
                Path(str(log) + '.installed').unlink(missing_ok=True)
            env = dict(os.environ, A22X_ROOT=str(system), A22X_FAKE_ROOT='1', UPGRADE=upgrade, PATH=str(stubs) + ':' + os.environ['PATH'])
            out = subprocess.run(['/bin/sh', str(ROOT / 'linux_data_restore.sh'), '--staged', str(staged), '--home-done',
                                  '--parts', 'packages'], env=env, capture_output=True, text=True)
            calls = log.read_text().splitlines() if log.exists() else []
            self.assertEqual(sum(1 for c in calls if c.startswith('pacman -Syu')), upgrades, calls)
            self.assertEqual(any(c.startswith('pacman -S ') for c in calls), installs, calls)
            self.assertIn(said, out.stdout)
            if upgrade == '1' and upgrades:
                # The failed upgrade is said, and what is missing is named as missing.
                self.assertIn('a22x: packages upgrade failed', out.stdout)
                # Not installed now, though the system has them: counted apart, never as missing.
                self.assertIn('a22x: packages-skipped 2', out.stdout)
                self.assertNotIn('a22x: missing', out.stdout)


if __name__ == '__main__':
    unittest.main()
