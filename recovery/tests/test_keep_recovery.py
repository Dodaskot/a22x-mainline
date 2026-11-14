"""The mark that keeps this recovery (saved_boot.keep_recovery, 2026-11-10). Samsung's Android puts
its own recovery back at every start when BOOT is exactly the stock one (install-recovery.sh checks
the SHA-1 of the whole 32 MiB BOOT); a mark in the BOOT's unused tail, after the AVB vbmeta and
outside every hash, stops that and changes nothing that starts."""
import contextlib
import hashlib
import io
import json
import types
import os
from pathlib import Path
import shutil
import struct
import sys
import tempfile
import unittest
import unittest.mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import flash as fl  # noqa: E402
import install  # noqa: E402
import saved_boot  # noqa: E402
from test_android_gsi import stock_boot  # noqa: E402
from test_flash import boot_image, vbmeta  # noqa: E402

MIB = 1 << 20


def signed(image, size=4 * MIB):
    """IMAGE padded to SIZE with an AVB vbmeta after it and the AVB footer at the end, as Samsung ships BOOT."""
    data = bytearray(size)
    data[:len(image)] = image
    meta = vbmeta(b'')
    at = (len(image) + 4095) & ~4095  # the image as signed: whole pages
    data[at:at + len(meta)] = meta
    data[size - 64:size - 64 + 36] = b'AVBf' + struct.pack('>IIQQQ', 1, 0, at, at, len(meta))
    return bytes(data)


class KeepRecovery(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='a22x-keep-', dir=os.environ.get('TMPDIR')))
        self.addCleanup(shutil.rmtree, self.root, True)
        for name in ('targets', 'cache/a22x-recovery'):
            (self.root / name).mkdir(parents=True)
        (self.root / 'install-fixture.json').write_text(json.dumps({
            'schema': 'a22x.install.fixture.v1', 'device': {'model': 'SM-A226B', 'identity': 'fixture:test'},
            'targets': {'boot': {'path': 'targets/boot', 'size_bytes': 4 * MIB}}}))
        (self.root / 'filesystem-fixture.json').write_text(json.dumps({'schema': 'a22x.filesystem.fixture.v1', 'volumes': [
            {'id': 'cache', 'path': 'cache', 'fs_type': 'ext4', 'mounted': True, 'read_only': True}]}))
        self.stock = signed(stock_boot())
        (self.root / 'cache/stock.img').write_bytes(self.stock)
        (self.root / 'targets/boot').write_bytes(signed(boot_image()))

    def flash(self, name):
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(io.StringIO()):
            import filesystem as fs
            token = fs.FixtureContext(self.root).volume('cache')['volume_token']
            code = fl.main(['--fixture', str(self.root), 'check', '--target', 'boot', '--volume', 'cache', '--volume-token', token,
                            '--path', '/' + name])
            checked = json.loads(stdout.getvalue().splitlines()[-1])
            self.assertEqual(code, 0, checked)
            stdout.truncate(0)
            stdout.seek(0)
            code = fl.main(['--fixture', str(self.root), 'write', '--target', 'boot', '--volume', 'cache', '--volume-token', token,
                            '--path', '/' + name, '--expected-sha256', checked['image']['sha256'],
                            '--expected-identity', checked['image']['identity'], '--confirm'])
            done = json.loads(stdout.getvalue().splitlines()[-1])
        self.assertEqual(code, 0, done)
        return done

    def test_an_android_boot_gets_the_mark_outside_every_hash(self):
        done = self.flash('stock.img')
        self.assertTrue(done['keeps_recovery'])
        boot = (self.root / 'targets/boot').read_bytes()
        spot = len(boot) - saved_boot.KEEP_RECOVERY_FROM_END
        self.assertEqual(boot[spot:spot + len(saved_boot.KEEP_RECOVERY_MARK)], saved_boot.KEEP_RECOVERY_MARK)
        # Everything else is the stock image: what starts and what AVB hashes are the same.
        self.assertEqual(boot[:spot], self.stock[:spot])
        self.assertEqual(boot[spot + len(saved_boot.KEEP_RECOVERY_MARK):], self.stock[spot + len(saved_boot.KEEP_RECOVERY_MARK):])
        self.assertNotEqual(hashlib.sha1(boot).digest(), hashlib.sha1(self.stock).digest())
        self.assertEqual(saved_boot.describe_boot(boot)['kind'], 'android')
        # A second write finds the mark there; the BOOT kept on cache is the image itself, unmarked.
        context = install.FixtureContext(self.root)
        self.assertTrue(saved_boot.keep_recovery(context, context.target('boot'))['already'])
        kept = saved_boot.keep_current_boot(context, lambda *a: None)
        self.assertEqual((self.root / 'cache' / kept['kept'].lstrip('/')).read_bytes(), self.stock)

    def test_cache_keeps_one_copy_of_a_boot_and_every_different_one(self):
        """2026-11-10: cache filled up with the stock BOOT under three names; only true copies go."""
        folder = self.root / 'cache' / saved_boot.SAVED_BOOT
        folder.mkdir(parents=True)
        marked = bytearray(self.stock)
        spot = len(marked) - saved_boot.KEEP_RECOVERY_FROM_END
        marked[spot:spot + len(saved_boot.KEEP_RECOVERY_MARK)] = saved_boot.KEEP_RECOVERY_MARK
        other = signed(boot_image())
        for name, data, saved in (('android-old.img', self.stock, 1), ('stock-A226BXXSBDZC3-x.img', self.stock, 5),
                                  ('android-marked.img', bytes(marked), 3), ('linux-k122.img', other, 2)):
            (folder / name).write_bytes(data)
            (folder / (name + '.json')).write_text(json.dumps({'saved': saved, 'sha256': hashlib.sha256(data).hexdigest(),
                                                               'bytes': len(data)}))
            (folder / (name + '.sha256')).write_text('x\n')
        (self.root / 'targets/boot').write_bytes(bytes(marked))
        context = install.FixtureContext(self.root)
        kept = saved_boot.keep_current_boot(context, lambda *a: None)
        self.assertEqual(kept['kept'], '/' + saved_boot.SAVED_BOOT + '/stock-A226BXXSBDZC3-x.img')
        self.assertEqual(sorted(p.name for p in folder.iterdir()),
                         ['linux-k122.img', 'linux-k122.img.json', 'linux-k122.img.sha256', 'stock-A226BXXSBDZC3-x.img',
                          'stock-A226BXXSBDZC3-x.img.json', 'stock-A226BXXSBDZC3-x.img.sha256'])

    def test_a_description_alone_does_not_remove_the_one_whole_copy(self):
        # 2026-11-14 review: a damaged stock- copy kept its old description (content key); tidying
        # took it for the same BOOT and removed the other, the one whole copy.
        folder = self.root / 'cache' / saved_boot.SAVED_BOOT
        folder.mkdir(parents=True)
        key = saved_boot.content_key(self.stock)
        damaged = bytearray(self.stock)
        damaged[100] ^= 0xff
        for name, data in (('stock-A226BXXSBDZC3-x.img', bytes(damaged)), ('android-y.img', self.stock)):
            (folder / name).write_bytes(data)
            (folder / (name + '.json')).write_text(json.dumps({'saved': 1, 'bytes': len(data), 'content': key}))
        fd = os.open(folder, os.O_RDONLY | os.O_DIRECTORY)
        try:
            kept = saved_boot.tidy_folder(fd)
        finally:
            os.close(fd)
        self.assertTrue((folder / 'android-y.img').exists())
        self.assertIn('android-y.img', kept)

    def test_a_lone_damaged_copy_is_not_taken_for_the_boot(self):
        # 2026-11-14 review: a damaged stock- copy whose description still gave the content key was
        # returned as the copy already there, and the good BOOT was never saved.
        folder = self.root / 'cache' / saved_boot.SAVED_BOOT
        folder.mkdir(parents=True)
        damaged = bytearray(self.stock)
        damaged[100] ^= 0xff
        name = 'stock-A226BXXSBDZC3-x.img'
        (folder / name).write_bytes(bytes(damaged))
        (folder / (name + '.json')).write_text(json.dumps({'saved': 1, 'bytes': len(damaged),
                                                          'content': saved_boot.content_key(self.stock)}))
        (self.root / 'targets/boot').write_bytes(self.stock)
        kept = saved_boot.keep_current_boot(install.FixtureContext(self.root), lambda *a: None)
        self.assertNotEqual(kept['kept'], '/' + saved_boot.SAVED_BOOT + '/' + name)
        self.assertEqual((self.root / 'cache' / kept['kept'].lstrip('/')).read_bytes(), self.stock)

    def test_a_cut_download_leaves_nothing_behind(self):
        # 2026-11-14 review: .part- files were removed in saved-boot only.
        for name in (saved_boot.UPDATES, saved_boot.VBMETA_FOLDER):
            (self.root / 'cache' / name).mkdir(parents=True, exist_ok=True)
            (self.root / 'cache' / name / '.part-recovery-1.0.1.img').write_bytes(bytes(4096))
        saved_boot.tidy_cache(types.SimpleNamespace(fs=saved_boot.fs.FixtureContext(self.root)))
        for name in (saved_boot.UPDATES, saved_boot.VBMETA_FOLDER):
            self.assertEqual(list((self.root / 'cache' / name).iterdir()), [])

    def test_cache_keeps_only_the_newest_copy_of_each_partition(self):
        """2026-11-11, on the phone: two flashes of the recovery left 80 MiB of old recoveries on the
        200 MiB cache, and a root install then found no room for its BOOT."""
        self.assertEqual(saved_boot.ROLLBACKS, fl.ROLLBACK_FOLDER)
        folder = self.root / 'cache' / saved_boot.ROLLBACKS
        folder.mkdir()
        for name in ('recovery-20261110-230129.img', 'recovery-20261111-000102.img', 'recovery-20861109-101010.img',
                     'boot-20261110-120000.img', 'boot-20261110-120000-1.img', 'super-odm-20261110-120000.img', 'notes.txt'):
            (folder / name).write_bytes(b'x' * 100)
        fd = os.open(folder, os.O_RDONLY)
        try:
            # A date from Samsung's clock form (the year 2086): which recovery copy is the newest is not
            # known, so the tidy at start (no KEEP) leaves that partition's copies alone (2026-11-12 review:
            # it removed the copy from just before the last write).
            self.assertEqual(saved_boot.prune_rollbacks(fd), 100)
            self.assertEqual(sorted(p.name for p in folder.iterdir()), ['boot-20261110-120000-1.img', 'notes.txt',
                                                                         'recovery-20261110-230129.img', 'recovery-20261111-000102.img',
                                                                         'recovery-20861109-101010.img', 'super-odm-20261110-120000.img'])
            # The copy just written stays, whatever date an older one carries.
            (folder / 'recovery-20491231-235959.img').write_bytes(b'y')
            saved_boot.prune_rollbacks(fd, keep='recovery-20261111-000102.img')
        finally:
            os.close(fd)
        self.assertEqual(sorted(p.name for p in folder.iterdir() if p.name.startswith('recovery')), ['recovery-20261111-000102.img'])

    def test_a_copy_a_write_still_needs_stays_and_counts_as_the_newest(self):
        """2026-11-12 review: the copy save_rollback has just written has its journal already; it was
        left out of its group, so the older copies beside it were never removed."""
        folder = self.root / 'cache' / saved_boot.ROLLBACKS
        folder.mkdir()
        for name in ('boot-20261101-100000.img', 'boot-20261102-100000.img', 'boot-20861109-101010.img'):
            (folder / name).write_bytes(b'x' * 100)
        (folder / 'boot-20861109-101010.img.pending.json').write_text('{}')
        fd = os.open(folder, os.O_RDONLY)
        try:
            self.assertEqual(saved_boot.prune_rollbacks(fd, keep='boot-20861109-101010.img'), 200)
            self.assertEqual(sorted(p.name for p in folder.iterdir()), ['boot-20861109-101010.img', 'boot-20861109-101010.img.pending.json'])
            # Two writes not over: both copies stay, also in the tidy at start.
            (folder / 'boot-20261103-100000.img').write_bytes(b'z')
            (folder / 'boot-20261103-100000.img.pending.json').write_text('{}')
            self.assertEqual(saved_boot.prune_rollbacks(fd), 0)
        finally:
            os.close(fd)
        self.assertEqual(len([p for p in folder.iterdir() if p.suffix == '.img']), 2)

    def test_only_the_newest_downloaded_recovery_stays(self):
        folder = self.root / 'cache' / saved_boot.UPDATES
        folder.mkdir(parents=True)
        for version in ('0.6.99', '0.6.206', '0.6.205'):
            for suffix in ('', '.json', '.sha256'):
                (folder / ('recovery-%s.img%s' % (version, suffix))).write_text('x')
        fd = os.open(folder, os.O_RDONLY)
        try:
            saved_boot.prune_updates(fd)
        finally:
            os.close(fd)
        self.assertEqual(sorted(p.name for p in folder.iterdir()), ['recovery-0.6.206.img', 'recovery-0.6.206.img.json', 'recovery-0.6.206.img.sha256'])

    def test_a_writable_cache_keeps_one_copy_per_partition_and_no_second_copy_of_a_boot(self):
        """Cache mounted for writing is where the old content of a partition goes when no card is in:
        each write replaces the copy before it, and a BOOT kept as a saved BOOT is not kept twice."""
        rows = json.loads((self.root / 'filesystem-fixture.json').read_text())
        rows['volumes'][0]['read_only'] = False
        (self.root / 'filesystem-fixture.json').write_text(json.dumps(rows))
        context = install.FixtureContext(self.root)
        target = context.target('boot')
        first = fl.save_rollback(context, target, lambda *a: None)
        self.assertEqual(first['volume'], 'cache')
        folder = self.root / 'cache' / saved_boot.ROLLBACKS
        (folder / 'boot-20200101-000000.img').write_bytes(b'old')
        second = fl.save_rollback(context, target, lambda *a: None)
        self.assertEqual([p.name for p in folder.iterdir()], [second['path'].rsplit('/', 1)[1]])
        self.assertIsNone(fl.save_rollback(context, target, lambda *a: None, skip_cache=True))
        # Flash a partition: the BOOT there is kept as a saved BOOT, and not once more beside it.
        for p in folder.iterdir():
            p.unlink()
        done = self.flash('stock.img')
        self.assertTrue(done['kept_boot'])
        self.assertIsNone(done['rollback'])
        self.assertEqual(list(folder.iterdir()), [])

    def test_cache_makes_room_from_what_is_there_twice(self):
        """What a full cache gives up first: the same BOOT under a second name and older copies of a
        partition. The stock BOOT, a BOOT that differs and the newest copy of each partition stay."""
        import filesystem as fs
        boots = self.root / 'cache' / saved_boot.SAVED_BOOT
        boots.mkdir(parents=True)
        other = signed(boot_image())
        for name, data, saved in (('android-copy.img', self.stock, 1), ('stock-A226BXXSBDZC3-x.img', self.stock, 5), ('linux-k122.img', other, 2)):
            (boots / name).write_bytes(data)
            (boots / (name + '.json')).write_text(json.dumps({'saved': saved, 'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}))
            (boots / (name + '.sha256')).write_text('x\n')
        # BOOTs with root are made again by Root: the newest of them stays, older ones go.
        for name, saved, root in (('android-magisk-old.img', 6, 'magisk'), ('android-magisk-new.img', 8, 'magisk'), ('android-apatch.img', 7, 'apatch')):
            data = self.stock + name.encode()
            (boots / name).write_bytes(data)
            (boots / (name + '.json')).write_text(json.dumps({'saved': saved, 'root': root, 'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}))
        old = self.root / 'cache' / saved_boot.ROLLBACKS
        old.mkdir()
        for name in ('recovery-20261110-230129.img', 'recovery-20261111-000102.img'):
            (old / name).write_bytes(b'r' * 64)
        (self.root / 'cache/a22x-recovery/settings.json').write_text('{}')
        rows = json.loads((self.root / 'filesystem-fixture.json').read_text())
        rows['volumes'][0]['read_only'] = False
        (self.root / 'filesystem-fixture.json').write_text(json.dumps(rows))
        context = install.FixtureContext(self.root)
        with fs.PinnedVolume(context.fs, 'cache', writing=True) as pin:
            saved_boot.make_room(pin)
        self.assertEqual(sorted(p.name for p in boots.iterdir() if p.suffix == '.img'),
                         ['android-magisk-new.img', 'linux-k122.img', 'stock-A226BXXSBDZC3-x.img'])
        self.assertEqual([p.name for p in old.iterdir()], ['recovery-20261111-000102.img'])
        self.assertTrue((self.root / 'cache/a22x-recovery/settings.json').exists())

    def test_a_full_cache_gives_up_published_linux_boots_that_can_be_downloaded_again(self):
        """2026-11-12, on the phone: three Linux BOOTs, the stock BOOT and a rooted one filled 160 of
        cache's 200 MiB, and a recovery image no longer fitted. An older published Linux BOOT can be
        downloaded again and goes; the current release and a build of one's own stay."""
        import filesystem as fs
        boots = self.root / 'cache' / saved_boot.SAVED_BOOT
        boots.mkdir(parents=True)
        rows = {}
        for name, kind in (('linux-v144.img', 'linux'), ('linux-v146.img', 'linux'), ('linux-v147.img', 'linux'),
                           ('linux-own-build.img', 'linux'), ('stock-A226BXXSBDZC3-x.img', 'android')):
            data = signed(boot_image()) + name.encode()
            rows[name] = hashlib.sha256(data).hexdigest()
            (boots / name).write_bytes(data)
            (boots / (name + '.json')).write_text(json.dumps({'kind': kind, 'saved': 1, 'sha256': rows[name], 'bytes': len(data)}))
            (boots / (name + '.sha256')).write_text(rows[name] + '\n')
        rows_fs = json.loads((self.root / 'filesystem-fixture.json').read_text())
        rows_fs['volumes'][0]['read_only'] = False
        (self.root / 'filesystem-fixture.json').write_text(json.dumps(rows_fs))
        context = install.FixtureContext(self.root)
        older = {rows['linux-v144.img']: 'v144', rows['linux-v146.img']: 'v146'}
        with fs.PinnedVolume(context.fs, 'cache', writing=True) as pin:
            saved_boot.make_room(pin, older)
        self.assertEqual(sorted(p.name for p in boots.iterdir()),
                         sorted(n + suffix for n in ('linux-v147.img', 'linux-own-build.img', 'stock-A226BXXSBDZC3-x.img')
                                for suffix in ('', '.json', '.sha256')))
        # Nothing is known to be downloadable: every Linux BOOT stays.
        with fs.PinnedVolume(context.fs, 'cache', writing=True) as pin:
            saved_boot.make_room(pin, {})
        self.assertEqual(len([p for p in boots.iterdir() if p.suffix == '.img']), 3)
        # The catalog of this recovery names the older releases with their addresses.
        published = saved_boot.downloadable_boots()
        self.assertEqual(sorted(published.values()), ['v144', 'v145', 'v146'])
        current = json.loads(Path(saved_boot.__file__).with_name('catalog.json').read_text())['linux_boot']['sha256']
        self.assertNotIn(current, published)  # the current release is never given up

    def test_cache_is_tidied_at_every_start_and_the_newest_copies_stay(self):
        """2026-11-12, the owner: cache cleans itself of old copies of recovery and BOOT; the settings
        and the newest copies of recovery and BOOT, with the stock one, stay."""
        import filesystem as fs
        import types
        cache = self.root / 'cache'
        boots = cache / saved_boot.SAVED_BOOT
        boots.mkdir(parents=True)
        rows = {}
        for name, kind, saved in (('linux-v145.img', 'linux', 1), ('linux-v146.img', 'linux', 2),
                                  ('stock-A226BXXSBDZC3-x.img', 'android', 3)):
            data = signed(boot_image()) + name.encode()
            rows[name] = hashlib.sha256(data).hexdigest()
            (boots / name).write_bytes(data)
            (boots / (name + '.json')).write_text(json.dumps({'kind': kind, 'saved': saved, 'sha256': rows[name], 'bytes': len(data)}))
            (boots / (name + '.sha256')).write_text(rows[name] + '\n')
        backups = cache / saved_boot.ROLLBACKS
        backups.mkdir(parents=True)
        for name in ('recovery-20261110-100000.img', 'recovery-20261111-100000.img', 'recovery-20261112-100000.img',
                     'boot-20261110-100000.img', 'boot-20261112-100000.img'):
            (backups / name).write_bytes(name.encode())
        (cache / 'a22x-recovery/settings.json').write_text('{"theme": "aurora"}')
        rows_fs = json.loads((self.root / 'filesystem-fixture.json').read_text())
        rows_fs['volumes'][0].update(mounted=False)
        (self.root / 'filesystem-fixture.json').write_text(json.dumps(rows_fs))
        files = fs.FixtureContext(self.root)
        # Both kept Linux BOOTs are older published releases: the newest of them stays all the same.
        with unittest.mock.patch.object(saved_boot, 'downloadable_boots', lambda: {rows['linux-v145.img']: 'v145', rows['linux-v146.img']: 'v146'}):
            self.assertGreaterEqual(saved_boot.tidy_cache(types.SimpleNamespace(fs=files)), 0)
        self.assertEqual(sorted(p.name for p in boots.iterdir() if p.suffix == '.img'), ['linux-v146.img', 'stock-A226BXXSBDZC3-x.img'])
        self.assertEqual(sorted(p.name for p in backups.iterdir()), ['boot-20261112-100000.img', 'recovery-20261112-100000.img'])
        self.assertTrue((cache / 'a22x-recovery/settings.json').is_file())
        self.assertFalse(files.volume('cache')['mounted'])  # left as it was found

    def test_a_signed_vbmeta_with_only_its_flags_set_is_not_damaged(self):
        """2026-11-10 on the phone: root's vbmeta (flags 3, as Magisk writes it for Samsung) was
        refused as VBMETA_DAMAGED. Its hash fits the header with flags 0; other changes still fail."""
        import bootimage
        header = bytearray(256)
        header[:4] = b'AVB0'
        struct.pack_into('>IIQQIQQQQQQQQQQQII', header, 4, 1, 0, 64, 64, 1, 0, 32, 32, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)
        aux = bytes(range(64))
        auth = bytearray(64)
        auth[:32] = hashlib.sha256(bytes(header) + aux).digest()
        signed_meta = bytes(header) + bytes(auth) + aux
        self.assertTrue(bootimage.vbmeta(signed_meta)['hash_checked'])
        off = bytearray(signed_meta)
        struct.pack_into('>I', off, 120, 3)
        found = bootimage.vbmeta(bytes(off))
        self.assertEqual((found['flags'], found['hash_checked'], found['flags_changed']), (3, True, True))
        off[256 + 64 + 5] ^= 1
        with self.assertRaises(bootimage.ImageError):
            bootimage.vbmeta(bytes(off))
        other = bytearray(signed_meta)
        struct.pack_into('>I', other, 120, 1)  # verification still on: no exception for it
        with self.assertRaises(bootimage.ImageError):
            bootimage.vbmeta(bytes(other))

    def test_a_linux_boot_and_a_boot_without_a_tail_stay_as_written(self):
        (self.root / 'cache/linux.img').write_bytes(signed(boot_image()))
        self.assertFalse(self.flash('linux.img')['keeps_recovery'])
        plain = bytearray(4 * MIB)
        plain[:len(stock_boot())] = stock_boot()
        (self.root / 'cache/plain.img').write_bytes(bytes(plain))
        self.assertFalse(self.flash('plain.img')['keeps_recovery'])
        self.assertEqual((self.root / 'targets/boot').read_bytes(), bytes(plain))


if __name__ == '__main__':
    unittest.main()
