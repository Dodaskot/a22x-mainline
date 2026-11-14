"""Files and folders into NAME.tar.zst and out again (file_archive.py, 2026-11-13)."""
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import file_archive as fa  # noqa: E402
import filesystem as fs  # noqa: E402
import partition_archive as pa  # noqa: E402


@unittest.skipUnless(shutil.which('zstd'), 'zstd is needed')
class FileArchive(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='a22x-archive-', dir=os.environ.get('TMPDIR')))
        self.addCleanup(shutil.rmtree, self.root, True)
        self.data, self.sd = self.root / 'volumes/data', self.root / 'volumes/sd'
        self.data.mkdir(parents=True)
        self.sd.mkdir()
        (self.root / 'filesystem-fixture.json').write_text(json.dumps({'schema': 'a22x.filesystem.fixture.v1', 'volumes': [
            {'id': 'userdata', 'path': 'volumes/data', 'fs_type': 'ext4', 'mounted': True, 'read_only': False},
            {'id': 'sdcard-p1', 'path': 'volumes/sd', 'fs_type': 'ext4', 'mounted': True, 'read_only': False}]}))
        self.ctx = fs.FixtureContext(self.root)
        home = self.data / 'home'
        (home / 'Документи' / 'вкладене').mkdir(parents=True)
        (home / 'Документи' / 'вкладене' / 'нотатка.txt').write_text('привіт\n')
        (home / 'script.sh').write_text('#!/bin/sh\necho hi\n')
        (home / 'script.sh').chmod(0o750)
        (home / 'big.bin').write_bytes(os.urandom(3 << 20))
        (home / 'link').symlink_to('/etc/passwd')
        (home / 'left-out.txt').write_text('no')
        self.home = home
        self.events = []

    def progress(self, done, total, label):
        self.events.append((done, total))

    def token(self, ident):
        return self.ctx.volume(ident)['volume_token']

    def pack(self, everything, names, destination='/A22xArchives/home.tar.zst'):
        return fa.pack(self.ctx, 'userdata', self.token('userdata'), '/home', everything, names, 'sdcard-p1',
                       self.token('sdcard-p1'), destination, True, self.progress)

    def test_chosen_entries_go_into_one_archive_and_come_out_beside_it(self):
        done = self.pack(False, ['Документи', 'script.sh', 'big.bin', 'link'])
        archive = self.sd / 'A22xArchives/home.tar.zst'
        self.assertEqual(done['archive']['path'], '/A22xArchives/home.tar.zst')
        self.assertEqual(done['bytes'], (3 << 20) + len('привіт\n'.encode()) + len('#!/bin/sh\necho hi\n'))
        self.assertTrue(archive.is_file() and not list((self.sd / 'A22xArchives').glob('.part-*')))
        self.assertTrue(self.events and self.events[-1][0] == done['bytes'])
        # The manifest after the stream, read without unpacking.
        about = fa.manifest_of(self.ctx, 'sdcard-p1', None, '/A22xArchives/home.tar.zst')['manifest']
        self.assertEqual((about['schema'], about['entries'], about['source']['folder']), ('a22x.files.v1', 6, '/home'))
        self.assertEqual(about['top'], sorted(['Документи', 'big.bin', 'link', 'script.sh']))
        # Any computer's tar reads it.
        if subprocess.run(['tar', '--zstd', '--help'], capture_output=True).returncode == 0:
            names = subprocess.run(['tar', '--zstd', '-tf', str(archive)], capture_output=True, text=True, check=True).stdout.split()
            self.assertIn('Документи/вкладене/нотатка.txt', names)
            self.assertNotIn('left-out.txt', names)
        # Out again, beside it, in a folder named after it.
        out = fa.unpack(self.ctx, 'sdcard-p1', self.token('sdcard-p1'), '/A22xArchives/home.tar.zst', True, self.progress)
        here = self.sd / 'A22xArchives/home'
        self.assertEqual((out['folder'], out['entries']), ('/A22xArchives/home', 6))
        self.assertEqual((here / 'Документи/вкладене/нотатка.txt').read_text(), 'привіт\n')
        self.assertEqual((here / 'big.bin').read_bytes(), (self.home / 'big.bin').read_bytes())
        self.assertEqual(stat.S_IMODE((here / 'script.sh').stat().st_mode), 0o750)
        self.assertTrue((here / 'link').is_symlink() and os.readlink(here / 'link') == '/etc/passwd')
        self.assertFalse((here / 'left-out.txt').exists())
        # Never into a folder that is already there, never over an archive that is.
        with self.assertRaises(fs.FilesystemError) as caught:
            fa.unpack(self.ctx, 'sdcard-p1', self.token('sdcard-p1'), '/A22xArchives/home.tar.zst', True, self.progress)
        self.assertEqual(caught.exception.code, 'DESTINATION_EXISTS')
        with self.assertRaises(fs.FilesystemError) as caught:
            self.pack(False, ['script.sh'])
        self.assertEqual(caught.exception.code, 'DESTINATION_EXISTS')

    def test_everything_but_the_ones_left_out(self):
        self.pack(True, ['left-out.txt', 'big.bin'], '/all.tar.zst')
        fa.unpack(self.ctx, 'sdcard-p1', None, '/all.tar.zst', True, self.progress)
        self.assertEqual(sorted(p.name for p in (self.sd / 'all').iterdir()), sorted(['Документи', 'link', 'script.sh']))

    def test_refusals(self):
        with self.assertRaises(fs.FilesystemError) as caught:
            self.pack(False, ['gone.txt'])
        self.assertEqual(caught.exception.code, 'SELECTION_CHANGED')
        with self.assertRaises(fs.FilesystemError) as caught:
            self.pack(False, ['script.sh'], '/x.zip')
        self.assertEqual(caught.exception.code, 'INVALID_DESTINATION')
        with self.assertRaises(fs.FilesystemError) as caught:
            fa.pack(self.ctx, 'userdata', None, '/home', False, ['script.sh'], 'sdcard-p1', None, '/a.tar.zst', False, self.progress)
        self.assertEqual(caught.exception.code, 'CONFIRMATION_REQUIRED')

    def test_an_archive_that_would_write_outside_its_folder_is_refused(self):
        raw = io.BytesIO()
        with tarfile.open(fileobj=raw, mode='w') as tar:
            info = tarfile.TarInfo('../escaped.txt')
            info.size = 2
            tar.addfile(info, io.BytesIO(b'no'))
        (self.sd / 'evil.tar').write_bytes(raw.getvalue())
        with self.assertRaises(fs.FilesystemError) as caught:
            fa.unpack(self.ctx, 'sdcard-p1', None, '/evil.tar', True, self.progress)
        self.assertEqual(caught.exception.code, 'ARCHIVE_UNSAFE')
        self.assertFalse((self.sd / 'escaped.txt').exists())
        # A damaged zstd archive says so.
        self.pack(False, ['big.bin'], '/damaged.tar.zst')
        blob = bytearray((self.sd / 'damaged.tar.zst').read_bytes())
        blob[len(blob) // 3] ^= 0xff
        (self.sd / 'damaged.tar.zst').write_bytes(bytes(blob))
        with self.assertRaises(fs.FilesystemError) as caught:
            fa.unpack(self.ctx, 'sdcard-p1', None, '/damaged.tar.zst', True, self.progress)
        self.assertEqual(caught.exception.code, 'ARCHIVE_DAMAGED')

    def test_links_and_devices_that_would_reach_outside_are_refused(self):
        # 2026-11-13 review: a hard link to a file outside, then a file of the same name, wrote
        # through the link; a device node was made as it came.
        victim = self.sd / 'victim.txt'
        victim.write_text('keep')
        for name, members in (('link.tar', [('x', tarfile.LNKTYPE, '../victim.txt', b''), ('x', tarfile.REGTYPE, '', b'OVERWRITTEN')]),):
            raw = io.BytesIO()
            with tarfile.open(fileobj=raw, mode='w') as tar:
                for member, kind, link, data in members:
                    info = tarfile.TarInfo(member)
                    info.type, info.linkname, info.size = kind, link, len(data)
                    info.devmajor, info.devminor = 179, 0
                    tar.addfile(info, io.BytesIO(data) if data else None)
            (self.sd / name).write_bytes(raw.getvalue())
            with self.assertRaises(fs.FilesystemError) as caught:
                fa.unpack(self.ctx, 'sdcard-p1', None, '/' + name, True, self.progress)
            self.assertEqual(caught.exception.code, 'ARCHIVE_UNSAFE')
        self.assertEqual(victim.read_text(), 'keep')
        # 2026-11-14 review: a device node (a container's whiteout in a home) is passed over and
        # counted, and the rest is unpacked; before, the whole unpacking stopped.
        raw = io.BytesIO()
        with tarfile.open(fileobj=raw, mode='w') as tar:
            info = tarfile.TarInfo('disk')
            info.type, info.devmajor, info.devminor = tarfile.CHRTYPE, 0, 0
            tar.addfile(info)
            info = tarfile.TarInfo('kept.txt')
            info.size = 2
            tar.addfile(info, io.BytesIO(b'ok'))
        (self.sd / 'dev.tar').write_bytes(raw.getvalue())
        out = fa.unpack(self.ctx, 'sdcard-p1', None, '/dev.tar', True, self.progress)
        self.assertEqual((out['entries'], out['skipped']), (2, 1))
        self.assertEqual(sorted(p.name for p in (self.sd / 'dev').iterdir()), ['kept.txt'])
        # A hard link inside the archive's own folder is kept.
        raw = io.BytesIO()
        with tarfile.open(fileobj=raw, mode='w') as tar:
            info = tarfile.TarInfo('a.txt')
            info.size = 2
            tar.addfile(info, io.BytesIO(b'hi'))
            link = tarfile.TarInfo('b.txt')
            link.type, link.linkname = tarfile.LNKTYPE, 'a.txt'
            tar.addfile(link)
        (self.sd / 'pair.tar').write_bytes(raw.getvalue())
        fa.unpack(self.ctx, 'sdcard-p1', None, '/pair.tar', True, self.progress)
        self.assertEqual((self.sd / 'pair/b.txt').read_text(), 'hi')

    def test_a_part_of_linux_data_stays_in_its_own_folder(self):
        # 2026-11-13 review: a "home" entry such as home/../etc/x landed in the system around it.
        root = self.root / 'system'
        (root / 'home').mkdir(parents=True)
        (root / 'etc').mkdir()
        (root / 'etc/shadow').write_text('root:x')
        check = fa.safe_filter(root / 'home')
        for name, kind, link in (('home/../etc/x', tarfile.REGTYPE, ''), ('home/u/s', tarfile.LNKTYPE, 'etc/shadow')):
            info = tarfile.TarInfo(name)
            info.type, info.linkname = kind, link
            with self.assertRaises(tarfile.FilterError):
                check(info, str(root))
        fine = tarfile.TarInfo('home/u/notes.txt')
        self.assertEqual(check(fine, str(root)).name, 'home/u/notes.txt')
        # 2026-11-14 review: a link named like the folder itself ('home' -> '.') would have made the
        # system's root the place of every later 'home/...'; a file of that name is refused too.
        for kind in (tarfile.SYMTYPE, tarfile.REGTYPE):
            info = tarfile.TarInfo('home')
            info.type, info.linkname = kind, '.' if kind == tarfile.SYMTYPE else ''
            with self.assertRaises(tarfile.FilterError):
                check(info, str(root))
        folder = tarfile.TarInfo('home')
        folder.type = tarfile.DIRTYPE
        self.assertEqual(check(folder, str(root)).name, 'home')
        # 2026-11-14 review: a link is judged where it is made; one that is there already, leading
        # out of /home, is replaced by tarfile, not written through: restoring it is fine.
        (root / 'home/u').mkdir()
        (root / 'srv/shared').mkdir(parents=True)
        (root / 'home/u/shared').symlink_to('../../srv/shared')
        again = tarfile.TarInfo('home/u/shared')
        again.type, again.linkname = tarfile.SYMTYPE, '../../srv/shared'
        self.assertEqual(check(again, str(root)).name, 'home/u/shared')
        # A file of that name would be written through the link: still refused.
        through = tarfile.TarInfo('home/u/shared')
        with self.assertRaises(tarfile.FilterError):
            check(through, str(root))

    def test_a_folder_on_fat_may_come_back_with_another_inode(self):
        # 2026-11-14 review: FAT and exFAT give a folder a new inode number when it comes back into the
        # cache; the folder had not changed. Elsewhere a changed folder is still refused, by its name
        # in the archive.
        entries = []
        fa.walk(os.open(self.home, os.O_RDONLY | os.O_DIRECTORY), 'Документи', os.stat(self.home).st_dev, 'docs', entries)
        where = next(e[0] for e in entries if e[1] == 'вкладене')
        where.ident = (where.ident[0], where.ident[1] + 1)
        opener = fa.Opener()
        self.addCleanup(opener.close)
        self.addCleanup(os.close, where.base)
        with self.assertRaises(fs.FilesystemError) as caught:
            opener.fd(where)
        self.assertEqual((caught.exception.code, caught.exception.details['file']), ('FILE_CHANGED', 'docs'))
        opener.close()
        from unittest import mock
        with mock.patch.object(fa, 'stable_inodes', return_value=False):
            self.assertGreaterEqual(opener.fd(where), 0)
        mountinfo = self.root / 'mountinfo'
        mountinfo.write_text('36 25 8:1 / /mnt/a rw - vfat /dev/sda1 rw\n37 25 8:2 / /mnt/b rw - ext4 /dev/sda2 rw\n')
        self.assertFalse(fa.stable_inodes(os.makedev(8, 1), str(mountinfo)))
        self.assertTrue(fa.stable_inodes(os.makedev(8, 2), str(mountinfo)))

    def test_a_file_on_fat_may_come_back_with_another_inode_too(self):
        # 2026-11-14 review: the FAT rule held for folders only; a file that had not changed was refused.
        import io
        from unittest import mock
        entries = []
        fa.walk(os.open(self.home, os.O_RDONLY | os.O_DIRECTORY), 'script.sh', os.stat(self.home).st_dev, 'script.sh', entries)
        self.addCleanup(lambda: [os.close(e[0]) for e in entries if e[1] is None])
        where, name, info, arcname = next(e for e in entries if e[1] is not None)
        moved = os.stat_result((info.st_mode, info.st_ino + 1) + tuple(info)[2:])
        files = [(where, name, moved, arcname)]
        out = io.BytesIO()
        read, write = os.pipe()
        with self.assertRaises(fs.FilesystemError) as caught:
            fa.write_archive(write, files, info.st_size, self.progress)
        self.assertEqual(caught.exception.code, 'FILE_CHANGED')
        with mock.patch.object(fa, 'stable_inodes', return_value=False):
            fa.write_archive(write, files, info.st_size, self.progress)
        os.close(write)
        with os.fdopen(read, 'rb') as stream:
            out.write(stream.read())
        self.assertGreater(len(out.getvalue()), 0)

    def test_device_nodes_left_out_are_counted(self):
        # 2026-11-14 review: device nodes (a container layer's whiteouts, char 0:0) left the archive
        # without a word. A device node cannot be made here, so the walked entry is given its mode.
        import stat as st
        entries = []
        fa.walk(os.open(self.home, os.O_RDONLY | os.O_DIRECTORY), 'script.sh', os.stat(self.home).st_dev, 'script.sh', entries)
        self.addCleanup(lambda: [os.close(e[0]) for e in entries if e[1] is None])
        where, name, info, _ = next(e for e in entries if e[1] is not None)
        node = os.stat_result((st.S_IFCHR | 0o600,) + tuple(info)[1:])
        left_out = []
        read, write = os.pipe()
        self.addCleanup(os.close, read)
        try:
            fa.write_archive(write, [(where, name, info, 'script.sh'), (where, name, node, 'layer/.wh.gone')], info.st_size,
                             self.progress, left_out=left_out)
        finally:
            os.close(write)
        self.assertEqual(left_out, ['layer/.wh.gone'])
        self.assertEqual(self.pack(False, ['script.sh'])['skipped'], 0)

    def test_a_failed_write_says_its_own_cause(self):
        # 2026-11-14 review: every cause behind a broken pipe was said as a full volume.
        import errno
        too_large = os.strerror(errno.EFBIG)
        self.assertEqual(fa.write_error(errno.EPIPE, 'zstd: error 70 : Write error : cannot write block : ' + too_large).code,
                         'FILE_TOO_LARGE')
        self.assertEqual(fa.write_error(errno.EPIPE, 'Write error : ' + os.strerror(errno.ENOSPC)).code, 'INSUFFICIENT_SPACE')
        broken = fa.write_error(errno.EPIPE, 'Write error : ' + os.strerror(errno.EIO))
        self.assertEqual((broken.code, broken.details['said']), ('ARCHIVE_WRITE_FAILED', 'Write error : ' + os.strerror(errno.EIO)))
        self.assertEqual(fa.write_error(errno.EPIPE).code, 'ARCHIVE_WRITE_FAILED')
        for number, code in ((errno.EIO, 'ARCHIVE_WRITE_FAILED'), (errno.EROFS, 'ARCHIVE_WRITE_FAILED'),
                             (errno.ENOSPC, 'INSUFFICIENT_SPACE'), (errno.EFBIG, 'FILE_TOO_LARGE')):
            with self.assertRaises(fs.FilesystemError) as caught:
                fa.raise_cause(OSError(number, os.strerror(number)), RuntimeError('pipe'))
            self.assertEqual(caught.exception.code, code)
        fa.raise_cause(None, RuntimeError('pipe'))
        # A failure zstd met at its final flush, after the stream was whole (2026-11-14 review).
        self.assertEqual(fa.said_error('zstd: error 70 : Write error : ' + os.strerror(errno.ENOSPC)).code, 'INSUFFICIENT_SPACE')
        self.assertEqual(fa.said_error('zstd: error 70 : Write error : ' + too_large).code, 'FILE_TOO_LARGE')
        self.assertEqual(fa.said_error('zstd: error 1 : something else').details['said'], 'zstd: error 1 : something else')

    def test_many_folders_need_few_descriptors(self):
        # 2026-11-13 review: every folder walked stayed open; 1100 folders under a limit of 1024 failed.
        import resource
        deep = self.home / 'many'
        for i in range(1100):
            (deep / ('d%04d' % i)).mkdir(parents=True)
        (deep / 'd0007' / 'f.txt').write_text('seven')
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        resource.setrlimit(resource.RLIMIT_NOFILE, (256, hard))
        self.addCleanup(resource.setrlimit, resource.RLIMIT_NOFILE, (soft, hard))
        before = len(os.listdir('/proc/self/fd'))
        done = self.pack(False, ['many'], '/many.tar.zst')
        self.assertEqual(done['entries'], 1102)
        self.assertEqual(len(os.listdir('/proc/self/fd')), before)
        fa.unpack(self.ctx, 'sdcard-p1', None, '/many.tar.zst', True, self.progress)
        self.assertEqual((self.sd / 'many/many/d0007/f.txt').read_text(), 'seven')

    def test_an_archive_protected_with_a_password(self):
        import archive_crypt as ac
        self.addCleanup(lambda: (setattr(pa, 'PASSWORD', None), setattr(pa, 'ENCRYPT', False)))
        pa.PASSWORD, pa.ENCRYPT = 'geheim', True
        done = self.pack(False, ['Документи', 'script.sh'], '/secret.tar.zst')
        self.assertEqual(done['archive']['path'], '/secret.tar.zst.enc')
        self.assertTrue((self.sd / 'secret.tar.zst.enc').read_bytes().startswith(b'Salted__'))
        self.assertEqual(fa.manifest_of(self.ctx, 'sdcard-p1', None, '/secret.tar.zst.enc')['manifest']['entries'], 4)
        pa.PASSWORD, pa.ENCRYPT = None, False
        with self.assertRaises(ac.CryptError) as caught:
            fa.unpack(self.ctx, 'sdcard-p1', None, '/secret.tar.zst.enc', True, self.progress)
        self.assertEqual(caught.exception.code, 'PASSWORD_REQUIRED')
        self.assertFalse((self.sd / 'secret').exists())
        pa.PASSWORD = 'geheim'
        out = fa.unpack(self.ctx, 'sdcard-p1', None, '/secret.tar.zst.enc', True, self.progress)
        self.assertEqual((out['folder'], (self.sd / 'secret/Документи/вкладене/нотатка.txt').read_text()), ('/secret', 'привіт\n'))

    def test_the_command_line(self):
        selection = self.root / 'selection.json'
        selection.write_text(json.dumps({'all': False, 'names': ['script.sh']}))
        run = subprocess.run([sys.executable, str(ROOT / 'file_archive.py'), '--fixture', str(self.root), 'pack', '--volume', 'userdata',
                              '--path', '/home', '--selection-file', str(selection), '--destination-volume', 'sdcard-p1',
                              '--destination', '/one.tar.zst', '--confirm'], capture_output=True, text=True)
        result = json.loads(run.stdout)
        self.assertTrue(result['ok'], result)
        self.assertIn('"phase":"Packing"', run.stderr)
        run = subprocess.run([sys.executable, str(ROOT / 'file_archive.py'), '--fixture', str(self.root), 'unpack', '--volume', 'sdcard-p1',
                              '--path', '/one.tar.zst', '--confirm'], capture_output=True, text=True)
        self.assertTrue(json.loads(run.stdout)['ok'], run.stdout)
        self.assertTrue((self.sd / 'one/script.sh').is_file())


if __name__ == '__main__':
    unittest.main()
