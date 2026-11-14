"""The password's promises, checked over every reachable state (2026-11-14: reviews found edge cases
one by one in both directions; this walks them all, as the review suggested).

P1: a password stored and not cleared never reads as "no password".
P2: with no password stored (never set, cleared, or a set or clear that stopped halfway), nothing
    asks for userdata to be erased.
P3: with no password and the settings on a cache that can be read, the recovery opens: a volume
    after them that cannot be looked at locks nothing (2026-11-14 review: userdata that would not
    mount locked such a phone for good). With no settings on the cache it stays locked then: a
    password may be on userdata beside an empty cache (releases 0.6.180 to 0.6.206 saved there
    whenever cache could not be written at that moment), and P1 comes first.

Each case: a start state, a first operation done whole, a second one stopped at every step it
writes (a crash, or an I/O error), each under several states of the two volumes, as filesystem.py
can show them (a list of volumes that fails for a moment, one that fails once and then not, block
holders, no filesystem, mounted by another owner, ...); the operations save what a read gives, as
the interface does (load, then store). Then reads under other states of the volumes and after
things that befall the disk from outside.

Three bounds, all settings.py's: a volume whose filesystem is gone at the read is as one erased,
which gives the defaults (only Download Mode or a format brings that about), so P1 leaves out reads
where the volume that kept the password or its mark has none; a volume that had no filesystem while
a password was set or cleared and has one at the read is as one restored from an older image, whose
older settings come back with it, so P1 and P2 leave such reads out; and settings whose content was
damaged from outside are damaged whatever the mark says (they may hold an older release's password),
so P2 leaves that event out."""
import contextlib
import errno
import itertools
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import settings  # noqa: E402

LABELS = ('cache', 'userdata')
KINDS = {  # mountable, mounted, write_supported, read_only_reason_code, reason_code, opens read-only, opens for writing
    'now': (True, False, True, None, None, True, True),
    'later': (True, False, True, None, None, True, False),
    'outside': (False, True, False, None, 'VOLUME_MOUNTED_OUTSIDE_MANAGER', True, False),
    'never': (True, False, False, 'FSCRYPT_KEYS_UNAVAILABLE', None, True, False),
    'dead': (True, False, True, None, None, False, False),
    'nofs': (False, False, False, None, 'ENCRYPTED_OR_UNRECOGNIZED_USERDATA', False, False),
    'holders': (False, False, False, None, 'PARTITION_HAS_BLOCK_HOLDERS', False, False),
    'flaky': None,   # the list of volumes fails (a card or a stick coming or going)
    'sysfs': None,   # the same, as the bare OSError a vanishing sysfs entry gives
    'blink': None}   # fails once in a run, then shows the volume as 'now'
READABLE = ('now', 'later', 'outside', 'never')
BASE = {'language': 'uk', 'theme': 'graphite'}
SECRET = {'hash': 'ab' * 32, 'salt': 'cd' * 16}
OP_KINDS = (('now', 'now'), ('now', 'later'), ('later', 'now'), ('nofs', 'now'), ('now', 'dead'),
            ('flaky', 'now'), ('now', 'flaky'), ('blink', 'now'), ('now', 'blink'), ('holders', 'now'))
READ_KINDS = (('now', 'now'), ('later', 'later'), ('outside', 'now'), ('never', 'now'), ('now', 'dead'), ('dead', 'now'),
              ('nofs', 'now'), ('now', 'nofs'), ('nofs', 'nofs'), ('flaky', 'now'), ('now', 'flaky'), ('blink', 'now'),
              ('holders', 'now'), ('now', 'sysfs'), ('sysfs', 'now'), ('now', 'holders'))
EVENTS = ('none', 'folder lost', 'folder a dangling link', 'folder a link to a real folder', 'settings a link to another file',
          'settings a folder', 'corrupt', 'mark lost', 'userdata mark unreadable', 'settings unreadable')


class Crash(BaseException):
    """The power went: nothing after this step happens."""


class World:
    def __init__(self, base):
        self.base = Path(base)
        self.kinds = dict.fromkeys(LABELS, 'now')
        self.fault = None  # ('crash' or 'eio', step)
        self.steps = 0
        self.blinked = set()
        self.unreadable = set()  # path suffixes whose lookups fail with EIO

    def root(self, label):
        return self.base / label

    def run(self, kinds):
        self.kinds = dict(zip(LABELS, kinds))
        self.blinked = set()


class Context:
    lock_path, mode = '/nonexistent', 'fixture'

    def __init__(self, world):
        self.world = world

    def kind(self, label):
        kind = self.world.kinds[label]
        if kind == 'blink':
            if label not in self.world.blinked:
                self.world.blinked.add(label)
                raise settings.fs.FilesystemError('BLOCK_GEOMETRY_CHANGED', 'The block devices changed')
            return 'now'
        if kind == 'sysfs':
            raise FileNotFoundError(errno.ENOENT, 'uevent')
        if kind == 'flaky':
            raise settings.fs.FilesystemError('BLOCK_GEOMETRY_CHANGED', 'The block devices changed')
        return kind

    def volume(self, label):
        mountable, mounted, supported, code, reason = KINDS[self.kind(label)][:5]
        return {'id': label, 'mounted': mounted, 'mountable': mountable, 'write_supported': supported,
                'read_only_reason_code': code, 'reason_code': reason, 'volume_token': 't-' + label, 'fs_type': 'ext4'}

    def unmount(self, label, token=None):
        pass

    def format(self, label, kind, confirmed, token):
        root = self.world.root(label)
        for child in list(root.iterdir()):
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink()


class Properties(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='a22x-props-', dir=os.environ.get('TMPDIR')))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        # The store of releases up to 1.0.1 (settings.read_volumes, write_volumes), read since
        # 2026-11-14 only while misc holds no record: tested here as it was, through load, store and
        # reset_damaged too. The record in misc: tests/test_settings_misc.py.
        for name, real in (('read_disk', settings.read_volumes), ('write_disk', settings.write_volumes)):
            patcher = mock.patch.object(settings, name, real)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.world = None

        @contextlib.contextmanager
        def volume_root(context, label, mode):
            kind = self.world.kinds[label]
            kind = 'now' if kind in ('blink', 'flaky', 'sysfs') else kind
            ro, rw = KINDS[kind][5:]
            if not (rw if mode == 'rw' else ro):
                raise OSError(errno.EBUSY, 'cannot open %s %s' % (label, mode))
            yield self.world.root(label)

        def counted(real):
            def step(*args, **kwargs):
                world = self.world
                if world.fault is not None and world.fault[1] == world.steps:
                    world.steps += 1
                    if world.fault[0] == 'crash':
                        raise Crash()
                    raise OSError(errno.EIO, 'Input/output error')
                world.steps += 1
                return real(*args, **kwargs)
            return step

        def failing(real):
            def look(path, *args, **kwargs):
                if self.world is not None and any(str(path).endswith(suffix) for suffix in self.world.unreadable):
                    raise OSError(errno.EIO, 'Input/output error')
                return real(path, *args, **kwargs)
            return look
        patches = [mock.patch.object(settings, 'volume_root', volume_root),
                   mock.patch.object(settings, 'lease', lambda *a, **k: contextlib.nullcontext()),
                   mock.patch.object(settings.fs, 'DeviceContext', lambda: Context(self.world)),
                   mock.patch.object(settings, 'RAM_COPY', self.tmp / 'ram.json'),
                   mock.patch.object(settings, 'copy_themes', lambda root: None),
                   mock.patch.object(settings, 'write_bytes', counted(settings.write_bytes)),
                   mock.patch.object(settings, 'set_mark', counted(settings.set_mark)),
                   mock.patch.object(settings, 'drop_mark', counted(settings.drop_mark)),
                   mock.patch.object(settings, 'clear_entry', counted(settings.clear_entry)),
                   mock.patch.object(settings.os, 'stat', failing(os.stat)),
                   mock.patch.object(settings.os, 'lstat', failing(os.lstat)),
                   mock.patch.object(settings.fs, 'open_regular', failing(settings.fs.open_regular))]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def new_world(self, name):
        world = World(self.tmp / name)
        for label in LABELS:
            world.root(label).mkdir(parents=True)
        return world

    def copy(self, world, name):
        other = World(self.tmp / name)
        shutil.copytree(world.base, other.base, symlinks=True)
        return other

    def run_op(self, world, op, kinds, fault=None):
        """The operation's outcome: True, False, 'crash', or 'skipped' when the read it starts from
        failed (the interface then saves nothing)."""
        self.world = world
        world.run(kinds)
        world.fault, world.steps = None, 0
        try:
            if op == 'start':
                settings.RAM_COPY.unlink(missing_ok=True)
                world.fault = fault
                with contextlib.suppress(settings.Damaged, OSError):
                    settings.load()
                return True
            try:
                record = settings.read_disk()
            except (settings.Damaged, OSError):
                return 'skipped'
            if op == 'set':
                record = {**record, **SECRET}
            elif op == 'clear':
                record = {k: v for k, v in record.items() if k not in SECRET}
            else:
                record = {**record, 'theme': 'aurora' if record.get('theme') != 'aurora' else 'graphite'}
            world.blinked, world.fault = set(), fault
            return settings.write_disk(record)
        except Crash:
            return 'crash'
        finally:
            world.fault = None

    def befall(self, world, event):
        rel = settings.RELATIVE
        for label in LABELS:
            root = world.root(label)
            folder, file = root / rel.parent, root / rel
            real = folder.is_dir() and not folder.is_symlink()
            if event == 'folder lost' and real:
                shutil.rmtree(folder)
            elif event == 'folder a dangling link' and os.path.lexists(folder):
                if real:
                    shutil.rmtree(folder)
                else:
                    folder.unlink()
                folder.symlink_to('/nonexistent/folder')
            elif event == 'folder a link to a real folder' and real:
                folder.rename(root / 'kept')
                folder.symlink_to('kept')
            elif event == 'settings a link to another file' and file.is_file():
                (root / 'other.txt').write_text('not settings')
                file.unlink()
                file.symlink_to('../other.txt')
            elif event == 'settings a folder' and file.is_file():
                file.unlink()
                file.mkdir()
            elif event == 'corrupt' and file.is_file():
                file.write_text('{not json')
            elif event == 'mark lost':
                (root / settings.PASSWORD_MARK).unlink(missing_ok=True)
        if event == 'userdata mark unreadable':
            world.unreadable.add('userdata/' + str(settings.PASSWORD_MARK))
        if event == 'settings unreadable':
            world.unreadable.add(str(rel))

    def read(self, world, kinds):
        self.world = world
        world.run(kinds)
        try:
            return settings.read_disk()
        except (settings.Damaged, OSError) as error:
            return error

    def keepers(self, world):
        """The volumes that keep a password or its mark now."""
        found = set()
        for label in LABELS:
            root = world.root(label)
            with contextlib.suppress(OSError, ValueError):
                if json.loads((root / settings.RELATIVE).read_text()).get('hash'):
                    found.add(label)
            if os.path.lexists(root / settings.PASSWORD_MARK):
                found.add(label)
        return found

    def check(self, label, world, password, away):
        """PASSWORD: 'yes' (stored and not cleared), 'no', 'maybe-set' or 'maybe-clear' (a set or a
        clear that stopped halfway). AWAY: the volumes that had no filesystem during an operation."""
        counter = itertools.count()
        keepers = self.keepers(world)
        cache_holds = (world.root('cache') / settings.RELATIVE).is_file()
        for kinds in READ_KINDS:
            at = dict(zip(LABELS, kinds))
            restored = any(at[name] != 'nofs' for name in away)
            erased = any(at[name] == 'nofs' for name in keepers)
            for event in EVENTS if kinds == ('now', 'now') else ('none',):
                seen = self.copy(world, '%s-r%d' % (world.base.name, next(counter)))
                self.befall(seen, event)
                outcome = self.read(seen, kinds)
                case = (label, kinds, event, outcome)
                if password == 'yes' and not erased and not restored:
                    self.assertFalse(isinstance(outcome, dict) and not outcome.get('hash'), ('P1',) + case)
                if not restored and event != 'corrupt' and (
                        password in ('no', 'maybe-set') or (password == 'maybe-clear' and event == 'none')):
                    self.assertNotIsInstance(outcome, settings.Damaged, ('P2',) + case)
                if password == 'no' and event == 'none' and cache_holds and at['cache'] in READABLE and not restored:
                    self.assertIsInstance(outcome, dict, ('P3',) + case)
                if kinds == ('nofs', 'nofs'):
                    # No filesystem on either partition: nothing can be kept, the recovery opens.
                    self.assertEqual(outcome, {}, ('P3',) + case)
                shutil.rmtree(seen.base)

    def starts(self):
        fresh = self.new_world('fresh')
        legacy = self.new_world('legacy')
        (legacy.root('cache') / settings.RELATIVE.parent).mkdir()
        (legacy.root('cache') / settings.RELATIVE).write_text(json.dumps({**BASE, **SECRET}))
        # As releases 0.6.180 to 0.6.206 could leave it: the password on userdata (cache could not
        # be written at that moment), beside a cache that is whole and empty; the mark since added.
        moved = self.new_world('moved')
        (moved.root('userdata') / settings.RELATIVE.parent).mkdir()
        (moved.root('userdata') / settings.RELATIVE).write_text(json.dumps({**BASE, **SECRET}))
        (moved.root('userdata') / settings.PASSWORD_MARK).touch()
        return (('fresh', fresh, 'no'), ('legacy', legacy, 'legacy'), ('moved', moved, 'yes'))

    @staticmethod
    def after(state, op, done):
        if op == 'set':
            if done is True:
                return 'yes'
            return state if state == 'yes' or done == 'skipped' else 'maybe-set'
        if op == 'clear':
            if done is True:
                return 'no'
            return 'maybe-clear' if state == 'yes' and done != 'skipped' else state
        return state

    def test_the_promises_hold_whatever_stops_a_write_and_whatever_befalls_the_disk(self):
        cases = 0
        for name, start, password in self.starts():
            for first, kinds1 in itertools.product(('none', 'set', 'clear', 'start'), (('now', 'now'), ('nofs', 'now'))):
                base = self.copy(start, '%s-%s-%s' % (name, first, '-'.join(kinds1)))
                state = password
                if first != 'none':
                    done = self.run_op(base, first, kinds1)
                    if state == 'legacy':
                        # A start that read them adds the mark an older release did not write; a set
                        # or a clear decides by itself.
                        if first == 'start':
                            state = 'yes' if (base.root('cache') / settings.PASSWORD_MARK).is_file() else 'legacy'
                        elif done is True:
                            state = 'yes' if first == 'set' else 'no'
                    else:
                        state = self.after(state, first, done)
                if state not in ('yes', 'no'):
                    continue  # before its first start, a password of an older release has no mark
                for second, kinds2 in itertools.product(('set', 'clear', 'theme', 'start'), OP_KINDS):
                    probe = self.copy(base, base.base.name + '-probe')
                    self.run_op(probe, second, kinds2)
                    steps = probe.steps
                    shutil.rmtree(probe.base)
                    for fault in [None] + [(kind, step) for step in range(steps) for kind in ('crash', 'eio')]:
                        world = self.copy(base, '%s-%s-%s-%s' % (base.base.name, second, '-'.join(kinds2), fault and '%s%d' % fault))
                        done = self.run_op(world, second, kinds2, fault)
                        away = {label for kinds in ((kinds1,) if first != 'none' else ()) + (kinds2,)
                                for label, kind in zip(LABELS, kinds) if kind == 'nofs'}
                        self.check((name, first, kinds1, second, kinds2, fault, done), world, self.after(state, second, done), away)
                        shutil.rmtree(world.base)
                        cases += 1
            shutil.rmtree(start.base, ignore_errors=True)
        self.assertGreater(cases, 1000)


if __name__ == '__main__':
    unittest.main()
