#!/usr/bin/env python3
"""Explicit v0.3 device acceptance inside one newly created userdata directory.

Run from the authenticated recovery SSH maintenance session:
  python3 SCRIPT /opt/a22x-recovery/filesystem.py /tmp/NEW-RAM-RECORD-DIRECTORY
The inherited maintenance lease is passed to every helper subprocess. This
script never writes a raw device, exports data, or touches microSD files.
"""
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import struct
import subprocess
import sys
import time
import uuid

EXPECTED_HELPER_SHA256 = 'fe3bead73c347964b0df6554f0928bfc3f59a59a1b10b580cde5d12d40f7a5d7'
LOCK_PATH = Path('/run/lock/a22x-mainline-recovery.lock')
LOCK_ENV = 'A22X_RECOVERY_LOCK_FD'
CONTENT = 'A22x recovery v0.3\nПеревірка Linux: власник, права, посилання.\n'
FILE_NAME = 'перевірка.txt'


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def identity(st):
    return st.st_dev, st.st_ino, stat.S_IFMT(st.st_mode)


def mount_id(fd):
    match = re.search(r'^mnt_id:\s*(\d+)$', Path('/proc/self/fdinfo/' + str(fd)).read_text(), re.M)
    require(match is not None, 'Descriptor has no kernel mount identity')
    return int(match[1])


def no_symlinks(path):
    path = Path(os.path.abspath(path))
    current = Path('/')
    for part in path.parts[1:]:
        current /= part
        require(not stat.S_ISLNK(current.lstat().st_mode), 'Symlink in harness path: ' + str(current))
    return path


def create_ram_record(path):
    path = Path(os.path.abspath(path))
    require(path.name not in ('', '.', '..'), 'A new RAM record-directory name is required')
    no_symlinks(path.parent)
    require(Path('/tmp') in path.parents or Path('/run/a22x-recovery') in path.parents,
            'Record directory must be under /tmp or /run/a22x-recovery')
    mounts = []
    for line in Path('/proc/self/mountinfo').read_text().splitlines():
        left, _, right = line.partition(' - ')
        name = re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), left.split()[4])
        point = Path(name)
        if point == path.parent or point in path.parent.parents:
            mounts.append((len(point.parts), right.split()[0]))
    require(mounts and max(mounts)[1] in ('rootfs', 'tmpfs', 'ramfs'), 'Record directory is not RAM-backed')
    path.mkdir(mode=0o700, exist_ok=False)
    return path


class Acceptance:
    def __init__(self, helper, record):
        self.helper = no_symlinks(helper)
        self.record = create_ram_record(record)
        self.root = '/a22x-recovery-v03-check-' + uuid.uuid4().hex
        self.owned, self.volume, self.initial, self.lease_fd = {}, None, None, None
        self.result = {'schema': 'a22x.filesystem.live.v03', 'ok': False,
                       'test_root': self.root, 'record_directory': str(self.record),
                       'helper_sha256': hashlib.sha256(self.helper.read_bytes()).hexdigest(),
                       'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
                       'started': time.time(), 'steps': [], 'verified': []}

    def save(self):
        (self.record / 'result.json').write_text(json.dumps(self.result, ensure_ascii=True, indent=2) + '\n')

    def validate_lease(self):
        value = os.environ.get(LOCK_ENV, '')
        require(value.isdecimal() and int(value) >= 3, 'Run inside an inherited recovery SSH maintenance lease')
        fd = int(value)
        opened, named = os.fstat(fd), no_symlinks(LOCK_PATH).stat()
        require(stat.S_ISREG(opened.st_mode) and opened.st_nlink == named.st_nlink == 1
                and identity(opened) == identity(named), 'Inherited FD does not identify the recovery operation lock')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.lease_fd = fd  # Never LOCK_UN: the SSH parent owns this shared lease.

    def call(self, action, *args, volume=True, expected_ok=True):
        require(self.lease_fd is not None, 'Maintenance lease has not been validated')
        command = [sys.executable, str(self.helper), '--device', action]
        if volume:
            command += ['--volume', 'userdata', '--expected-volume-token', self.initial['volume_token']]
        command += list(args)
        step = {'argv': command[2:]}
        self.result['steps'].append(step)
        try:
            run = subprocess.run(command, capture_output=True, text=True, timeout=60,
                                 pass_fds=(self.lease_fd,),
                                 env=dict(os.environ, **{LOCK_ENV: str(self.lease_fd)}))
            step.update(exit_code=run.returncode, stderr=run.stderr)
            data = json.loads(run.stdout)
            step['result'] = data
            require(data.get('mode', 'device') == 'device', 'Helper returned a non-device result')
            require(bool(data.get('ok')) == expected_ok and (run.returncode == 0) == expected_ok,
                    action + ' returned an unexpected result: ' + json.dumps(data, ensure_ascii=True))
            return data
        except BaseException as exc:
            step['exception'] = repr(exc)
            raise
        finally:
            self.save()

    def inventory(self):
        volumes = self.call('inventory', volume=False)['volumes']
        matches = [v for v in volumes if v['id'] == 'userdata']
        require(len(matches) == 1, 'Exactly one admitted userdata volume is required')
        self.volume = matches[0]
        return self.volume

    def block_ro(self):
        # A read-only descriptor and BLKROGET only; no raw device write or BLKROSET.
        fd = os.open(self.volume['path'], os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        try:
            st = os.fstat(fd)
            require(stat.S_ISBLK(st.st_mode)
                    and f'{os.major(st.st_rdev)}:{os.minor(st.st_rdev)}' == self.volume['device'],
                    'Userdata block identity changed')
            return bool(struct.unpack('i', fcntl.ioctl(fd, 0x125e, bytes(4)))[0])
        finally:
            os.close(fd)

    def mount(self, mode):
        args = ['--mode', mode] + (['--confirm'] if mode == 'rw' else [])
        self.volume = self.call('mount', *args)['volume']
        require(self.volume['mounted'] and self.volume['mount_owner'] == 'filesystem'
                and self.volume['read_only'] == (mode == 'ro'), 'Requested mount mode was not observed')
        require(self.block_ro() == (mode == 'ro'), 'Kernel BLKROGET does not match the requested guarded mode')
        if mode == 'ro':
            require(self.volume['read_only_guarded'], 'RO mount has no block-level guard')

    def unmount(self):
        self.volume = self.call('unmount')['volume']
        require(not self.volume['mounted'] and self.block_ro() == self.initial['block_read_only'],
                'Unmount did not restore the original block RO flag')

    @contextmanager
    def parent(self, path, writing=False):
        require(path == self.root or path.startswith(self.root + '/'), 'Path is outside the owned test directory')
        require(self.volume and self.volume['mounted'], 'Owned-path access requires the admitted mount')
        if writing:
            require(not self.volume['read_only'] and not self.block_ro(), 'Owned-path mutation requires RW userdata')
        fd = os.open(no_symlinks(self.volume['mountpoint']), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            def check_mount(handle):
                st = os.fstat(handle)
                require(mount_id(handle) == self.volume['mount_id']
                        and f'{os.major(st.st_dev)}:{os.minor(st.st_dev)}' == self.volume['device'],
                        'Owned directory crossed a mount boundary')
            check_mount(fd)
            parts, prefix = path[1:].split('/'), ''
            for name in parts[:-1]:
                prefix += '/' + name
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = child
                check_mount(fd)
                require(prefix in self.owned and identity(os.fstat(fd)) == tuple(self.owned[prefix]['identity']),
                        'Owned parent directory changed: ' + prefix)
            yield fd, parts[-1]
        finally:
            os.close(fd)

    def capture(self, path, kind):
        with self.parent(path) as (fd, name):
            handle = os.open(name, os.O_PATH | os.O_NOFOLLOW, dir_fd=fd)
            try:
                st = os.fstat(handle)
                require(mount_id(handle) == self.volume['mount_id'], 'Created entry crossed a mount boundary')
                actual = 'directory' if stat.S_ISDIR(st.st_mode) else 'symlink' if stat.S_ISLNK(st.st_mode) else 'file' if stat.S_ISREG(st.st_mode) else 'other'
                require(actual == kind, 'Created entry has an unexpected type: ' + path)
                item = {'identity': identity(st), 'type': kind}
                if kind == 'symlink':
                    item['target'] = os.readlink('', dir_fd=handle)
                self.owned[path] = item
                self.result['owned_entries'] = self.owned
                self.save()
            finally:
                os.close(handle)

    def inspect(self, path):
        return self.call('stat', '--path', path)['entry']

    def mkdir(self, path):
        self.call('mkdir', '--path', path, '--confirm')
        self.capture(path, 'directory')

    def make_links(self, source):
        path = source + '/' + FILE_NAME
        with self.parent(path, writing=True) as (fd, name):
            require(identity(os.stat(name, dir_fd=fd, follow_symlinks=False)) == tuple(self.owned[path]['identity']),
                    'Source inode changed before creating owned links')
            for leaf, target in (('relative', FILE_NAME), ('absolute', path)):
                os.symlink(target, leaf, dir_fd=fd)
                self.capture(source + '/' + leaf, 'symlink')
            os.link(name, 'hardlink', src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
            self.capture(source + '/hardlink', 'file')
            os.fsync(fd)

    def verify_tree(self, directory, source):
        names = {FILE_NAME, 'hardlink', 'relative', 'absolute'}
        listing = self.call('list', '--path', directory)['entries']
        require({entry['name'] for entry in listing} == names, 'Unexpected copied-tree entries')
        for name in (FILE_NAME, 'hardlink'):
            data = self.call('read', '--path', directory + '/' + name)
            require(data['content'] == CONTENT and data['sha256'] == self.content_sha
                    and (data['uid'], data['gid'], data['file_mode'], data['nlink']) == (12345, 12345, 0o640, 2),
                    'Persisted content, owner, mode or hard-link count differs')
        for name, literal, resolved in (('relative', FILE_NAME, directory + '/' + FILE_NAME),
                                        ('absolute', source + '/' + FILE_NAME, source + '/' + FILE_NAME)):
            path = directory + '/' + name
            entry = self.inspect(path)
            require(entry.get('link_target') == literal, 'Copied symbolic-link text differs')
            data = self.call('resolve', '--path', path, '--expected-entry-token', entry['entry_token'])
            require(data['resolved_path'] == resolved and data['entry']['type'] == 'file', 'Symbolic-link resolution differs')

    def cleanup(self):
        if self.initial is None:
            return
        errors = []
        removed = []
        try:
            current = self.inventory()
            require(current['volume_token'] == self.initial['volume_token'], 'Userdata identity changed before cleanup')
            if self.owned:
                if current['mounted'] and current['read_only']:
                    self.unmount()
                if not self.volume['mounted']:
                    self.mount('rw')
                for path in sorted(self.owned, key=lambda p: p.count('/'), reverse=True):
                    item = self.owned[path]
                    try:
                        with self.parent(path, writing=True) as (fd, name):
                            st = os.stat(name, dir_fd=fd, follow_symlinks=False)
                            require(identity(st) == tuple(item['identity']), 'Cleanup refused a changed inode: ' + path)
                            if item['type'] == 'symlink':
                                require(os.readlink(name, dir_fd=fd) == item['target'], 'Cleanup refused a changed symlink')
                                os.unlink(name, dir_fd=fd)
                                os.fsync(fd)
                            elif item['type'] == 'file':
                                self.call('delete', '--path', path, '--expected-sha256', self.content_sha, '--confirm')
                            else:
                                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
                                try:
                                    require(not os.listdir(child), 'Cleanup retained a directory containing unknown entries: ' + path)
                                finally:
                                    os.close(child)
                                self.call('delete', '--path', path, '--confirm')
                            removed.append(path)
                    except FileNotFoundError:
                        continue
                    except BaseException as exc:
                        errors.append({'path': path, 'error': repr(exc)})
                with self.parent(self.root) as (fd, name):
                    try:
                        os.stat(name, dir_fd=fd, follow_symlinks=False)
                    except FileNotFoundError:
                        self.result['test_root_absent'] = True
                    else:
                        raise RuntimeError('The owned test directory remains; it was not recursively deleted')
        except BaseException as exc:
            errors.append({'error': repr(exc)})
        finally:
            try:
                # A failed mount can leave only an owned BLKRO receipt behind.
                # The production unmount command knows whether it may release
                # that guard; the harness never clears a block flag itself.
                current = self.inventory()
                require(current['volume_token'] == self.initial['volume_token'], 'Userdata identity changed before final unmount')
                if current['mounted']:
                    require(current['mount_owner'] == 'filesystem', 'Cleanup will not unmount an unrelated mount')
                    self.unmount()
                elif self.block_ro() != self.initial['block_read_only']:
                    self.unmount()
                final = self.inventory()
                self.result['final_userdata'] = {key: final.get(key) for key in
                    ('mounted', 'mount_owner', 'block_read_only', 'read_only_guarded', 'volume_token')}
                require(not final['mounted'] and self.block_ro() == self.initial['block_read_only'],
                        'Final userdata mount or block RO state differs from the initial state')
            except BaseException as exc:
                errors.append({'error': repr(exc)})
            self.result['cleanup_errors'] = errors
            self.result['cleanup_removed'] = removed
            self.result['cleanup_complete'] = not errors
            if errors:
                self.result['ok'] = False

    def run(self):
        try:
            require(self.result['helper_sha256'] == EXPECTED_HELPER_SHA256, 'Production filesystem helper differs from the frozen v0.3 source')
            self.validate_lease()
            initial = self.inventory()
            self.result['initial_userdata'] = dict(initial)
            require(initial['fs_type'] == 'ext4' and initial['mountable'] and initial['write_supported'],
                    'This focused acceptance requires admitted writable ext4 userdata')
            require(not initial['mounted'] and not initial['block_read_only'] and not self.block_ro(),
                    'Userdata must start unmounted and not preexisting block-read-only')
            self.initial = dict(initial)
            self.content_sha = hashlib.sha256(CONTENT.encode('utf-8')).hexdigest()
            text_file = self.record / 'input.txt'
            text_file.write_text(CONTENT, encoding='utf-8')
            self.mount('ro')
            denied = self.call('mkdir', '--path', self.root, '--confirm', expected_ok=False)
            require(denied['error']['code'] == 'VOLUME_READ_ONLY', 'RO filesystem mutation was not rejected')
            self.unmount()
            self.mount('rw')
            self.mkdir(self.root)
            root_entry = self.inspect(self.root)
            self.call('chmod', '--path', self.root, '--mode', '0700', '--expected-entry-token', root_entry['entry_token'], '--confirm')
            source, copied = self.root + '/source', self.root + '/copy'
            self.mkdir(source)
            path = source + '/' + FILE_NAME
            self.call('write', '--path', path, '--create', '--text-file', str(text_file), '--confirm')
            self.capture(path, 'file')
            before = self.inspect(path)
            changed = self.call('chmod', '--path', path, '--mode', '0640', '--expected-entry-token', before['entry_token'], '--confirm')
            require(changed['entry']['mode'] == 0o640, 'chmod did not set 0640')
            stale = self.call('chmod', '--path', path, '--mode', '0600', '--expected-entry-token', before['entry_token'], '--confirm', expected_ok=False)
            require(stale['error']['code'] == 'ENTRY_CHANGED', 'A stale metadata token was accepted')
            owner = self.call('chown', '--path', path, '--uid', '12345', '--gid', '12345',
                              '--expected-entry-token', changed['entry']['entry_token'], '--confirm')['entry']
            require((owner['uid'], owner['gid'], owner['mode']) == (12345, 12345, 0o640), 'Actual numeric ownership change failed')
            self.make_links(source)
            selected = self.inspect(source)
            outcome = self.call('copy-tree', '--path', source, '--destination-volume', 'userdata',
                                '--expected-destination-volume-token', self.initial['volume_token'],
                                '--destination', copied, '--expected-entry-token', selected['entry_token'], '--confirm')
            for suffix, kind in (('', 'directory'), ('/' + FILE_NAME, 'file'), ('/hardlink', 'file'), ('/relative', 'symlink'), ('/absolute', 'symlink')):
                self.capture(copied + suffix, kind)
            require(outcome['published'] and outcome['posix_metadata_preserved']
                    and outcome['hardlinks_preserved'] == 1 and outcome['bytes_copied'] == len(CONTENT.encode('utf-8')),
                    'Directory-copy receipt differs from the selected tree')
            require(self.owned[copied + '/' + FILE_NAME]['identity'] == self.owned[copied + '/hardlink']['identity']
                    and self.owned[copied + '/' + FILE_NAME]['identity'] != self.owned[path]['identity'],
                    'Copy did not create a distinct inode with an internal hard link')
            self.unmount()
            self.mount('ro')
            self.verify_tree(source, source)
            self.verify_tree(copied, source)
            entry = self.inspect(copied)
            denied = self.call('chmod', '--path', copied, '--mode', '0700', '--expected-entry-token', entry['entry_token'], '--confirm', expected_ok=False)
            require(denied['error']['code'] == 'VOLUME_READ_ONLY', 'Metadata change was accepted on a guarded RO mount')
            self.result.update(ok=True, content_sha256=self.content_sha,
                verified=['kernel_ro_guard', 'ro_mutation_refusal', 'chmod_0640', 'chown_12345_12345',
                          'stale_entry_refusal', 'relative_and_volume_absolute_symlinks',
                          'copy_tree_bytes_owner_mode_links', 'hardlink_identity', 'remount_ro_persistence'])
        except BaseException as exc:
            self.result['error'] = repr(exc)
        finally:
            self.cleanup()
            self.result['finished'] = time.time()
            if self.result['ok']:
                self.result['verified'] += ['known_inode_cleanup', 'original_unmounted_block_ro_state_restored']
            self.save()
            print(json.dumps({key: value for key, value in self.result.items() if key != 'steps'}, ensure_ascii=True))
        return 0 if self.result['ok'] else 1


def main():
    try:
        require(len(sys.argv) == 3, 'Usage: live_v03_filesystem_check.py HELPER NEW_RAM_RECORD_DIRECTORY')
        return Acceptance(Path(sys.argv[1]), Path(sys.argv[2])).run()
    except BaseException as exc:
        print(json.dumps({'schema': 'a22x.filesystem.live.v03', 'ok': False, 'error': repr(exc)}, ensure_ascii=True))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
