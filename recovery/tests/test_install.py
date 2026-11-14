#!/usr/bin/env python3
"""Owned regular-file installer acceptance; no block device or phone access."""
import contextlib
import ctypes
import fcntl
import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest import mock
import zlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import install as i
from operation_lock import ENV_FD


def sparse(chunks, *, block=4096, declared=None, checksum=0, extension=b''):
    total = sum(count for kind, count, data in chunks if kind != i.CRC32)
    header = struct.pack('<IHHHHIIII', i.SPARSE_MAGIC, 1, 0, 28 + len(extension), 12,
                         block, total if declared is None else declared, len(chunks), checksum)
    return header + extension + b''.join(struct.pack('<HHII', kind, 0, count, 12 + len(data)) + data
                                        for kind, count, data in chunks)


def boot(recovery=False):
    header = bytearray(2048)
    header[:8] = b'ANDROID!'
    struct.pack_into('<10I', header, 8, 64, 0x40080000, 64, 0x51100000, 0, 0,
                     0x47c80000, 2048, 2, 0)
    header[48:48 + len(i.BOARD)] = i.BOARD.encode()
    header[64:64 + 39] = b'bootopt=64S3,32N2,64N2 loop.max_part=7\0'
    # Slice lengths must be exact; retain a full header page.
    header = header[:1660] + bytes(2048 - len(header[:1660]))
    struct.pack_into('<IQI', header, 1632, 64 if recovery else 0, 6144 if recovery else 0, 1660)
    struct.pack_into('<IQ', header, 1648, 64, 0x47c80000)
    return bytes(header) + b'K' * 64 + bytes(1984) + b'R' * 64 + bytes(1984) + \
        (b'O' * 64 + bytes(1984) if recovery else b'') + b'D' * 64


class Interrupted(Exception):
    pass


class Fixture:
    """Small explicitly synthetic files; useful for actual UI/helper transport."""
    def __init__(self, root):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.sd, self.backup = self.root / 'sd', self.root / 'sd/linux-fixture'
        self.sd.mkdir()
        self.backup.mkdir()
        (self.sd / 'stock').mkdir()
        (self.root / 'targets').mkdir()
        self.device = {'model': i.MODEL, 'identity': 'fixture:install-owned-regular-images'}
        self.old_bytes, self.new_bytes, self.target_paths = {}, {}, {}
        geometry = {}
        for index, label in enumerate(i.STOCK_ORDER):
            raw = boot(label == 'recovery') if label in ('boot', 'recovery') else bytes([65 + index]) * 16384
            old = raw + bytes(16384 - len(raw))
            target = self.root / 'targets' / (label + '.img')
            target.write_bytes(old)
            self.old_bytes[label], self.target_paths[label] = old, target
            geometry[label] = {'path': str(target.relative_to(self.root)), 'size_bytes': len(old)}
        self.userdata = self.root / 'targets/userdata-reference.img'
        self.userdata.write_bytes(b'unchanged user files\0' * 100)
        (self.backup / 'boot.raw').write_bytes(self.old_bytes['boot'])
        (self.backup / 'userdata.raw').write_bytes(self.userdata.read_bytes())
        manifest = {'schema': 'a22x.backup.v2', 'workflow': 'linux-partclone-v2', 'profile': 'linux',
            'device': self.device, 'source_mode': 'fixture', 'fixture_regular_images': True,
            'snapshot_id': 'fixture-backup', 'created_utc': '2026-11-05T00:00:00Z', 'entries': []}
        for label in ('boot', 'userdata'):
            raw = (self.backup / (label + '.raw')).read_bytes()
            manifest['entries'].append({'label': label, 'archive': label + '.raw', 'raw_bytes': len(raw),
                                       'archive_bytes': len(raw), 'raw_sha256': hashlib.sha256(raw).hexdigest()})
        manifest_raw = i.canonical(manifest) + b'\n'
        (self.backup / 'manifest.json').write_bytes(manifest_raw)
        (self.backup / 'COMPLETE.json').write_bytes(i.canonical({'manifest_sha256': hashlib.sha256(manifest_raw).hexdigest(),
            'all_entries_verified': True}) + b'\n')
        self.config = {'schema': 'a22x.install.fixture.v1', 'device': self.device, 'targets': geometry,
            'userdata': {'path': str(self.userdata.relative_to(self.root))}, 'protected_firmware_matches': True,
            'external_volumes': ['sdcard-p1'], 'boot_id': 'fixture-install-boot', 'known_linux_baseline':
            {label: hashlib.sha256(self.old_bytes[label]).hexdigest() for label in ('boot', 'dtbo', 'vbmeta', 'vbmeta_system')}}
        self.save_config()
        (self.root / 'filesystem-fixture.json').write_text(json.dumps({'schema': 'a22x.filesystem.fixture.v1',
            'volumes': [{'id': 'sdcard-p1', 'path': 'sd', 'fs_type': 'ext4', 'mounted': True, 'read_only': False}]}))
        self.context = i.FixtureContext(self.root)
        self.token = self.context.fs.volume('sdcard-p1')['volume_token']
        self.boot_source = self.sd / 'linux.img'
        changed = bytearray(self.old_bytes['boot'])
        changed[2048] = ord('N')
        self.boot_source.write_bytes(changed)
        self.manifest = self.sd / 'stock/manifest.json'
        records = []
        for index, label in enumerate(i.STOCK_ORDER):
            if i.STOCK_FORMAT[label] == 'sparse':
                raw = sparse([(i.RAW, 1, bytes([97 + index]) * 4096),
                              (i.FILL, 2, b'abcd'), (i.DONT_CARE, 1, b'')])
            elif label in ('boot', 'recovery'):
                raw = bytearray(self.old_bytes[label])
                raw[2048] = ord('S')
                raw = bytes(raw)
            else:
                raw = bytes([97 + index]) * 16384
            filename = label + '.gz'
            path = self.sd / 'stock' / filename
            path.write_bytes(gzip.compress(raw, mtime=0))
            fd = os.open(path, os.O_RDONLY)
            try:
                image = i.inspect_image(fd, 'gzip', i.STOCK_FORMAT[label], 16384, label=label)
                reader = i.ImageReader(fd, 'gzip')
                self.new_bytes[label] = b''.join(i.decode_image(reader, i.STOCK_FORMAT[label], 16384))
            finally:
                os.close(fd)
            records.append({'label': label, 'file': filename, 'image': image})
        bundle = {'schema': i.BUNDLE_SCHEMA, 'model': i.MODEL, 'profile_id': 'fixture-stock',
                  'firmware': 'fixture', 'write_order': list(i.STOCK_ORDER), 'images': records}
        raw = i.canonical(bundle) + b'\n'
        self.manifest.write_bytes(raw)
        profile = i.stock_profile_template(bundle, hashlib.sha256(raw).hexdigest(), qualified=True)
        (self.root / 'install-stock-profile.json').write_bytes(i.canonical(profile) + b'\n')

    def save_config(self):
        (self.root / 'install-fixture.json').write_text(json.dumps(self.config))

    def plan(self, operation='flash-boot'):
        self.context = i.FixtureContext(self.root)
        return i.make_plan(self.context, 'sdcard-p1', '/linux.img' if operation == 'flash-boot' else '/stock/manifest.json',
                           self.token, str(self.backup), operation)

    def pending(self):
        return i.status(self.context)['pending'][0]

    def interrupt_stock(self, boundary='after-chunk', label='super'):
        plan = self.plan('install-stock')
        def cut(kind, current, count):
            if kind == boundary and current == label:
                raise Interrupted()
        try:
            i.execute(self.context, plan, plan['confirmation'], hook=cut)
        except Interrupted:
            return plan, self.pending()
        raise AssertionError('Requested interruption boundary was not reached')


class Formats(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='a22x-install-formats-')
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'input'

    def inspect(self, raw, fmt='raw', target=None, compression='none', label=None):
        self.path.write_bytes(raw)
        fd = os.open(self.path, os.O_RDONLY)
        try:
            return i.inspect_image(fd, compression, fmt, target if target is not None else len(raw), label=label)
        finally:
            os.close(fd)

    def error(self, code, action):
        with self.assertRaises(i.fs.FilesystemError) as caught:
            action()
        self.assertEqual(caught.exception.code, code, str(caught.exception))

    def test_raw_and_gzip_exact_target_hash_with_zero_tail(self):
        raw = b'raw\0image' * 1000
        expected = hashlib.sha256(raw + bytes(16384 - len(raw))).hexdigest()
        for compression, encoded in (('none', raw), ('gzip', gzip.compress(raw, mtime=0))):
            result = self.inspect(encoded, target=16384, compression=compression)
            self.assertEqual(result['target_sha256'], expected)
            self.assertEqual(result['input_sha256'], hashlib.sha256(raw).hexdigest())
            self.assertEqual(result['encoded_sha256'], hashlib.sha256(encoded).hexdigest())

    def test_sparse_raw_fill_holes_crc_and_padding(self):
        prefix = b'A' * 4096 + b'1234' * 1024 + bytes(4096)
        crc = zlib.crc32(prefix)
        image = sparse([(i.RAW, 1, b'A' * 4096), (i.FILL, 1, b'1234'), (i.DONT_CARE, 1, b''),
                        (i.CRC32, 0, struct.pack('<I', crc))], checksum=crc)
        result = self.inspect(image, 'sparse', 16384)
        self.assertEqual(result['target_sha256'], hashlib.sha256(prefix + bytes(4096)).hexdigest())
        self.assertEqual(result['payload_bytes'], 12288)

    def test_full_megabyte_gzip_boundaries_and_fixed_chunk_cursor(self):
        raw = b'A' * i.CHUNK + os.urandom(91)
        result = self.inspect(gzip.compress(raw, mtime=0), target=len(raw), compression='gzip')
        self.assertEqual(result['chunk_sha256'], [hashlib.sha256(raw[:i.CHUNK]).hexdigest(),
                                                hashlib.sha256(raw[i.CHUNK:]).hexdigest()])

    def test_gzip_truncation_crc_concatenation_and_trailing_bytes(self):
        data = gzip.compress(b'hello' * 100, mtime=0)
        cases = [('TRUNCATED_GZIP', data[:-1]), ('INVALID_GZIP', data[:-8] + bytes(8)),
                 ('IMAGE_TRAILING_DATA', data + data), ('IMAGE_TRAILING_DATA', data + b'x')]
        for code, raw in cases:
            with self.subTest(code=code):
                self.error(code, lambda: self.inspect(raw, target=4096, compression='gzip'))

    def test_decompression_bomb_is_bounded(self):
        self.path.write_bytes(gzip.compress(b'A' * 4096, mtime=0))
        fd = os.open(self.path, os.O_RDONLY)
        try:
            reader = i.ImageReader(fd, 'gzip', maximum=1024)
            self.error('IMAGE_EXPANSION_LIMIT', lambda: reader.read(4096))
        finally:
            os.close(fd)

    def test_source_change_during_decode_refuses(self):
        self.path.write_bytes(b'A' * 4096)
        fd = os.open(self.path, os.O_RDONLY)
        try:
            reader = i.ImageReader(fd, 'none')
            self.assertEqual(reader.read(1), b'A')
            self.path.write_bytes(b'B' * 4096)
            reader.read(4095)
            self.error('IMAGE_CHANGED', lambda: reader.read(1))
        finally:
            os.close(fd)

    def test_sparse_declared_output_chunk_lengths_and_unknown_type(self):
        good = sparse([(i.RAW, 1, b'A' * 4096)])
        too_big = bytearray(good)
        struct.pack_into('<I', too_big, 16, 2)
        self.error('IMAGE_TOO_LARGE', lambda: self.inspect(bytes(too_big), 'sparse', 4096))
        short = sparse([(i.RAW, 1, b'A' * 4000)])
        self.error('INVALID_SPARSE_CHUNK', lambda: self.inspect(short, 'sparse', 4096))
        self.error('UNSUPPORTED_SPARSE_CHUNK', lambda: self.inspect(sparse([(0xbeef, 1, b'')]), 'sparse', 4096))
        self.error('SPARSE_OUTPUT_BOUNDS', lambda: self.inspect(sparse([(i.DONT_CARE, 2, b'')], declared=1), 'sparse', 4096))

    def test_sparse_crc_mismatch_trailer_and_extension(self):
        self.error('SPARSE_CRC_MISMATCH', lambda: self.inspect(sparse([(i.DONT_CARE, 1, b'')], checksum=123), 'sparse', 4096))
        self.error('SPARSE_CRC_MISMATCH', lambda: self.inspect(sparse([(i.DONT_CARE, 1, b''),
            (i.CRC32, 0, struct.pack('<I', 42))]), 'sparse', 4096))
        self.error('IMAGE_TRAILING_DATA', lambda: self.inspect(sparse([(i.DONT_CARE, 1, b'')]) + b'x', 'sparse', 4096))
        self.error('UNSUPPORTED_SPARSE_EXTENSION', lambda: self.inspect(sparse([(i.DONT_CARE, 1, b'')], extension=b'x'), 'sparse', 4096))

    def test_sparse_header_and_chunk_count_bounds(self):
        raw = sparse([(i.DONT_CARE, 1, b'')])
        for offset, fmt, value, code in ((8, '<H', 65535, 'INVALID_SPARSE_HEADER'),
                                         (12, '<I', 3, 'INVALID_SPARSE_BLOCK'),
                                         (20, '<I', 65537, 'INVALID_IMAGE_METADATA')):
            changed = bytearray(raw)
            struct.pack_into(fmt, changed, offset, value)
            self.error(code, lambda: self.inspect(bytes(changed), 'sparse', 4096))

    def test_regular_source_not_hardlink_alias(self):
        self.path.write_bytes(b'A' * 4096)
        os.link(self.path, self.path.with_name('alias'))
        fd = os.open(self.path, os.O_RDONLY)
        try:
            self.error('IMAGE_REGULAR_ONLY', lambda: i.ImageReader(fd, 'none'))
        finally:
            os.close(fd)

    def test_boot_header_and_recovery_target_separation(self):
        raw = boot()
        info = i.boot_header(raw[:2048], len(raw))
        self.assertEqual(info['board'], i.BOARD)
        self.assertFalse(info['runtime_boot_tested'])
        recovery = boot(True)
        i.boot_header(recovery[:2048], len(recovery), recovery=True)
        self.error('RECOVERY_IMAGE_FOR_BOOT', lambda: i.boot_header(recovery[:2048], len(recovery)))
        bad = bytearray(raw)
        struct.pack_into('<I', bad, 12, 0x12345678)
        self.error('BOOT_ADDRESS_MISMATCH', lambda: i.boot_header(bad[:2048], len(bad)))
        self.error('BOOT_PAYLOAD_BOUNDS', lambda: i.boot_header(raw[:2048], 4096))


class Lifecycle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='a22x-install-lifecycle-')
        self.addCleanup(self.tmp.cleanup)
        self.f = Fixture(self.tmp.name)
        self.before_userdata = self.f.userdata.read_bytes()

    def execute(self, plan, **kwargs):
        # Exercise the real persisted JSON representation, not Python-only
        # tuples that disappear at the CLI/UI process boundary.
        return i.execute(self.f.context, json.loads(json.dumps(plan)), plan['confirmation'], **kwargs)

    def error(self, code, action):
        with self.assertRaises(i.fs.FilesystemError) as caught:
            action()
        self.assertEqual(caught.exception.code, code, str(caught.exception))

    def untouched(self, labels=i.STOCK_ORDER):
        for label in labels:
            self.assertEqual(self.f.target_paths[label].read_bytes(), self.f.old_bytes[label], label)
        self.assertEqual(self.f.userdata.read_bytes(), self.before_userdata)

    def test_private_plan_cli_roundtrip_boot_and_exact_checkpoint(self):
        f = self.f
        argv = [sys.executable, '-B', str(Path(i.__file__)), '--mode', 'fixture', '--fixture', str(f.root)]
        args = ['boot-plan', '--source-volume', 'sdcard-p1', '--source-path', '/linux.img',
                '--expected-source-token', f.token, '--backup-path', str(f.backup), '--output', str(f.root / 'review.json')]
        env = {key: value for key, value in os.environ.items() if key != ENV_FD}
        made = subprocess.run(argv + args, capture_output=True, env=env, timeout=15)
        self.assertEqual(made.returncode, 0, made.stderr)
        review = json.loads(made.stdout)
        result = subprocess.run(argv + ['execute', '--plan-file', review['plan_path'],
            '--expected-plan-seal', review['plan']['seal'], '--expected-plan-file-sha256', review['plan_file_sha256'],
            '--confirmation', review['plan']['confirmation']], capture_output=True, env=env, timeout=15)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(result.stdout.splitlines()), 1)
        for line in result.stderr.splitlines():
            self.assertEqual(json.loads(line)['event'], 'progress')
        finished = json.loads(result.stdout)
        self.assertTrue(finished['readback_verified'])
        self.assertFalse(finished['physical_partition_writes'])
        self.assertEqual(f.target_paths['boot'].read_bytes(), f.boot_source.read_bytes())
        self.assertEqual((Path(finished['checkpoint_path']) / 'boot.img').read_bytes(), f.old_bytes['boot'])
        self.untouched([label for label in i.STOCK_ORDER if label != 'boot'])
        self.assertFalse(f.context.guard_path.exists())
        self.assertEqual(i.status(f.context)['pending'], [])

    def test_identical_boot_has_zero_writable_target_opens(self):
        f = self.f
        f.boot_source.write_bytes(f.old_bytes['boot'])
        plan = f.plan()
        self.assertEqual(plan['write_order'], [])
        original = i.FixtureContext.open_target
        def opened(context, target, *, write=False):
            self.assertFalse(write, 'No-op operation opened a target writable')
            return original(context, target, write=write)
        with mock.patch.object(i.FixtureContext, 'open_target', opened):
            self.assertTrue(self.execute(plan)['ok'])
        self.untouched()

    def test_stock_order_recovery_last_and_userdata_preserved(self):
        f = self.f
        plan = f.plan('install-stock')
        seen = []
        def hook(boundary, label, count):
            if boundary == 'before-write':
                seen.append(label)
        result = self.execute(plan, hook=hook)
        self.assertEqual(seen, list(i.STOCK_WRITE))
        self.assertEqual(result['next_action'], 'first-android-boot')
        self.assertTrue(result['userdata_written'])
        self.assertFalse(result['automatic_factory_reset'] or result['auto_reboot'])
        for label in i.STOCK_WRITE:
            self.assertEqual(f.target_paths[label].read_bytes(), f.new_bytes[label], label)
        for label in i.CHECKPOINT_LABELS:
            self.assertEqual((Path(result['checkpoint_path']) / (label + '.img')).read_bytes(), f.old_bytes[label])
        self.assertEqual(f.userdata.read_bytes(), self.before_userdata)
        self.assertTrue(i.boot_check(f.context)['verified_completed_transaction'])

    def test_source_change_between_review_and_execute_refuses_before_journal(self):
        plan = self.f.plan()
        data = bytearray(self.f.boot_source.read_bytes()); data[2049] ^= 1
        self.f.boot_source.write_bytes(data)
        self.error('INSTALL_PLAN_STALE', lambda: self.execute(plan))
        self.assertFalse((self.f.sd / '.a22x-installs').exists())
        self.untouched()

    def test_private_plan_and_confirmation_changes_refuse(self):
        plan = self.f.plan()
        review = i.plan_result(self.f.context, plan, 'boot-plan', self.f.root / 'review.json')
        path = Path(review['plan_path']); path.write_bytes(path.read_bytes() + b' ')
        self.error('INSTALL_PLAN_FILE_CHANGED', lambda: i.load_plan_file(path, plan['seal'], review['plan_file_sha256']))
        changed = dict(plan); changed['write_order'] = ['userdata']
        self.error('INSTALL_PLAN_CHANGED', lambda: i.execute(self.f.context, changed, changed['confirmation']))
        self.error('INSTALL_CONFIRMATION_REQUIRED', lambda: i.execute(self.f.context, plan, 'wrong'))
        self.untouched()

    def test_corrupt_last_stock_payload_refuses_without_target_writer(self):
        path = self.f.sd / 'stock/recovery.gz'
        path.write_bytes(path.read_bytes()[:-5])
        self.error('TRUNCATED_GZIP', lambda: self.f.plan('install-stock'))
        self.assertFalse((self.f.sd / '.a22x-installs').exists())
        self.untouched()

    def test_changed_target_identity_or_contents_after_review_refuses(self):
        plan = self.f.plan()
        target = self.f.target_paths['boot']
        replacement = self.f.root / 'replacement'; replacement.write_bytes(target.read_bytes())
        os.replace(replacement, target)
        self.error('INSTALL_PLAN_STALE', lambda: self.execute(plan))
        self.untouched()

    def test_late_target_change_refuses_before_any_writer_opens(self):
        f = self.f
        plan = f.plan('install-stock')
        original = i.FixtureContext.open_target
        def opened(context, target, *, write=False):
            self.assertFalse(write, 'Writer opened after target content changed')
            return original(context, target, write=write)
        def hook(boundary, label, count):
            if boundary == 'preverified':
                f.target_paths['super'].write_bytes(b'Z' * 16384)
        with mock.patch.object(i.FixtureContext, 'open_target', opened):
            self.error('TARGET_CONTENT_CHANGED', lambda: self.execute(plan, hook=hook))
        self.assertEqual(f.target_paths['super'].read_bytes(), b'Z' * 16384)
        self.untouched([label for label in i.STOCK_ORDER if label != 'super'])
        self.assertTrue(f.context.guard_path.exists())

    def test_late_protected_proof_or_profile_change_refuses_before_writers(self):
        for variant in ('profile', 'selector'):
            with self.subTest(variant=variant):
                f = Fixture(self.f.root / ('late-' + variant))
                plan = f.plan('install-stock')
                original_open, original_proof = i.FixtureContext.open_target, i.verify_protected
                changed = [False]
                def opened(context, target, *, write=False):
                    self.assertFalse(write, 'Protected drift allowed a writable target open')
                    return original_open(context, target, write=write)
                def proof(context, profile):
                    return {'fixture_only': True, 'matched': False} if changed[0] and variant == 'selector' else original_proof(context, profile)
                def hook(boundary, label, count):
                    if boundary == 'checkpoint-complete':
                        changed[0] = True
                        if variant == 'profile':
                            path = f.root / 'install-stock-profile.json'
                            path.write_bytes(path.read_bytes() + b' ')
                with mock.patch.object(i.FixtureContext, 'open_target', opened), mock.patch.object(i, 'verify_protected', proof):
                    self.error('STOCK_PROFILE_CHANGED' if variant == 'profile' else 'PROTECTED_FIRMWARE_CHANGED',
                        lambda: i.execute(f.context, json.loads(json.dumps(plan)), plan['confirmation'], hook=hook))
                for label in i.STOCK_WRITE:
                    self.assertEqual(f.target_paths[label].read_bytes(), f.old_bytes[label])

    def test_mounted_target_and_busy_lease_refuse(self):
        self.f.config['mounted_targets'] = ['boot']; self.f.save_config()
        self.error('PARTITION_MOUNTED', self.f.plan)
        self.f.config['mounted_targets'] = []; self.f.save_config()
        with i.operation_lock(self.f.context.lock_path, 'fixture'):
            with self.assertRaises(BlockingIOError):
                self.f.plan()
        self.untouched()

    def test_unqualified_stock_does_not_disable_boot(self):
        path = self.f.root / 'install-stock-profile.json'
        profile = json.loads(path.read_text()); profile['qualified'] = False
        profile['blockers'] = ['PROTECTED_FIRMWARE_PROOF_REQUIRED']; path.write_text(json.dumps(profile))
        status = i.status(self.f.context)
        self.assertEqual(status['blockers'], [])
        self.assertEqual(status['stock_profile']['blockers'], ['PROTECTED_FIRMWARE_PROOF_REQUIRED'])
        self.assertTrue(self.f.plan()['can_execute'])
        self.assertFalse(self.f.plan('install-stock')['can_execute'])
        path.unlink()
        self.assertEqual(i.status(self.f.context)['stock_profile']['blockers'], ['STOCK_PROFILE_UNAVAILABLE'])
        self.assertTrue(self.f.plan()['can_execute'])

    def test_current_userdata_required_only_for_stock(self):
        self.f.userdata.write_bytes(b'changed user files\0' * 100)
        self.assertTrue(self.f.plan()['can_execute'])
        self.error('BACKUP_NO_LONGER_CURRENT', lambda: self.f.plan('install-stock'))

    def test_exact_resume_keeps_original_checkpoint_and_plan_is_read_only(self):
        f = self.f
        original, pending = f.interrupt_stock()
        journal = f.sd / pending['journal_path'].lstrip('/')
        before = journal.read_bytes(), i.fs.stamp(journal.stat())
        plan = i.resume_plan(f.context, pending['journal_volume'], pending['journal_path'], pending['journal_volume_token'])
        self.assertEqual((journal.read_bytes(), i.fs.stamp(journal.stat())), before)
        self.assertTrue(plan['resuming'])
        self.assertFalse(plan['backup_gate']['current_required'])
        self.assertTrue(plan['backup_gate']['current_userdata_required'])
        result = self.execute(plan)
        for label in i.CHECKPOINT_LABELS:
            self.assertEqual((Path(result['checkpoint_path']) / (label + '.img')).read_bytes(), f.old_bytes[label])
        for label in i.STOCK_WRITE:
            self.assertEqual(f.target_paths[label].read_bytes(), f.new_bytes[label])
        self.assertFalse(f.context.guard_path.exists())

    def test_checkpoint_corruption_or_resume_source_change_refuses(self):
        f = self.f
        original, pending = f.interrupt_stock()
        checkpoint = Path(original['checkpoint']['path']) / 'boot.img'
        checkpoint.write_bytes(b'X' * checkpoint.stat().st_size)
        self.error('CHECKPOINT_READBACK_FAILED', lambda: i.resume_plan(f.context,
            pending['journal_volume'], pending['journal_path'], pending['journal_volume_token']))
        self.assertTrue(f.context.guard_path.exists())

    def test_pending_or_malformed_journal_blocks_normal_boot(self):
        f = self.f
        _, pending = f.interrupt_stock('after-chunk', 'boot')
        self.error('INSTALL_PENDING', lambda: i.boot_check(f.context))
        f.context.guard_path.unlink()
        self.error('INSTALL_PENDING', lambda: i.boot_check(f.context))
        journal = f.sd / pending['journal_path'].lstrip('/')
        value = json.loads(journal.read_text()); value['state'] = 'completed'
        journal.write_text(json.dumps(value))
        self.error('INVALID_INSTALL_JOURNAL', lambda: i.boot_check(f.context))

    def test_wrong_empty_card_cannot_hide_interrupted_stock(self):
        f = self.f
        f.interrupt_stock('after-chunk', 'boot')
        f.context.guard_path.unlink()
        f.sd.rename(f.root / 'retained-original-card'); f.sd.mkdir()
        self.error('BOOT_SAFETY_UNPROVEN', lambda: i.boot_check(i.FixtureContext(f.root)))

    def test_a_boot_written_by_flash_a_partition_is_allowed_only_while_it_is_unchanged(self):
        # 2026-11-10: Flash a partition notes each verified write; a normal boot accepts exactly that.
        f = self.f
        new = bytes([0x5a]) * len(f.old_bytes['boot'])
        f.target_paths['boot'].write_bytes(new)
        self.error('BOOT_SAFETY_UNPROVEN', lambda: i.boot_check(f.context))
        self.assertTrue(i.record_flash(f.context, 'boot', hashlib.sha256(b'other').hexdigest()))
        self.error('BOOT_SAFETY_UNPROVEN', lambda: i.boot_check(f.context))
        self.assertTrue(i.record_flash(f.context, 'boot', hashlib.sha256(new).hexdigest()))
        self.assertEqual(i.boot_check(f.context)['verified_recovery_flash'], ['boot'])
        f.target_paths['boot'].write_bytes(bytes([0x5b]) * len(new))
        self.error('BOOT_SAFETY_UNPROVEN', lambda: i.boot_check(f.context))
        (f.root / '.flash-records.json').write_text('{broken')
        self.assertEqual(i.read_flash_records(f.context), {})

    def test_a_changed_boot_whose_contents_are_a_boot_for_this_phone_is_allowed(self):
        # 2026-11-10: no hash list; a torn write breaks the kernel stream and is refused.
        sys.path.insert(0, str(Path(__file__).resolve().parent))
        import test_bootimage as tb
        f = self.f
        size = len(f.old_bytes['boot'])
        image = tb.boot(tb.kernel(appended=tb.MAINLINE_TREE), tb.ramdisk(), tb.dt_table(tb.MAINLINE_TREE))
        f.target_paths['boot'].write_bytes(image + bytes(size - len(image)))
        self.assertEqual(i.boot_check(f.context)['verified_by_contents'], ['boot'])
        torn = bytearray(f.target_paths['boot'].read_bytes())
        torn[2048 + 40:2048 + 120] = bytes(80)
        f.target_paths['boot'].write_bytes(bytes(torn))
        self.error('BOOT_SAFETY_UNPROVEN', lambda: i.boot_check(f.context))
        other = tb.boot(tb.kernel(), tb.ramdisk({'ro.product.bootimage.device': 'a32x'}))
        f.target_paths['boot'].write_bytes(other + bytes(size - len(other)))
        self.error('BOOT_SAFETY_UNPROVEN', lambda: i.boot_check(f.context))

    def test_unchanged_linux_boot_allowed_with_unrelated_empty_or_absent_sd(self):
        self.assertTrue(i.boot_check(self.f.context)['known_linux_boot_safe'])
        path = self.f.root / 'filesystem-fixture.json'
        config = json.loads(path.read_text()); config['volumes'] = []; path.write_text(json.dumps(config))
        value = i.boot_check(i.FixtureContext(self.f.root))
        self.assertFalse(value['journal_storage_present'] or value['durable_state_checked'])
        self.assertTrue(value['known_linux_boot_safe'])

    def test_crash_boundaries_resume_without_replacing_original_platform(self):
        boundaries = [('checkpoint-data', 'boot'), ('checkpoint-complete', None),
            ('before-write', 'super'), ('after-chunk', 'super'), ('after-readback', 'super'),
            ('after-chunk', 'boot'), ('after-readback', 'boot'),
            ('before-complete', None), ('after-complete', None), ('after-clear', None)]
        for number, (boundary, label) in enumerate(boundaries):
            with self.subTest(boundary=boundary, label=label):
                f = Fixture(self.f.root / ('cut-' + str(number)))
                plan = f.plan('install-stock')
                def hook(kind, current, count):
                    if kind == boundary and current == label:
                        raise Interrupted()
                with self.assertRaises(Interrupted):
                    i.execute(f.context, json.loads(json.dumps(plan)), plan['confirmation'], hook=hook)
                pending = i.status(f.context)['pending']
                if boundary != 'after-clear':
                    self.assertEqual(len(pending), 1)
                    p = pending[0]
                    resume = i.resume_plan(f.context, p['journal_volume'], p['journal_path'], p['journal_volume_token'])
                    self.assertTrue(i.execute(f.context, json.loads(json.dumps(resume)), resume['confirmation'])['ok'])
                else:
                    self.assertEqual(pending, [])
                for target in i.STOCK_WRITE:
                    self.assertEqual(f.target_paths[target].read_bytes(), f.new_bytes[target], target)
                for target in i.CHECKPOINT_LABELS:
                    self.assertEqual((Path(plan['checkpoint']['path']) / (target + '.img')).read_bytes(), f.old_bytes[target])
                self.assertEqual(f.userdata.read_bytes(), self.before_userdata)
                self.assertFalse(f.context.guard_path.exists())


class Selector(unittest.TestCase):
    def raw(self, selector=1):
        data = bytearray(512)
        data[179], data[192], data[226] = 64 | selector << 3, 8, 32
        data[212:216] = i.MMC_SECTORS.to_bytes(4, 'little')
        return bytes(data)

    def test_fixed_cmd8_layout_and_readonly_buffer_without_device_access(self):
        with tempfile.NamedTemporaryFile(prefix='a22x-mmc-abi-') as file:
            fd = os.open(file.name, os.O_RDONLY)
            self.addCleanup(os.close, fd)
            calls = []
            def ioctl(number, request, buffer, mutate):
                command = i.MMCReadCommand.from_buffer_copy(buffer)
                fields = {name: getattr(command, name) for name in ('write_flag', 'is_acmd', 'opcode', 'arg',
                    'flags', 'blksz', 'blocks', 'postsleep_min_us', 'postsleep_max_us', 'data_timeout_ns', 'cmd_timeout_ms')}
                calls.append((number, request, fields))
                self.assertTrue(mutate)
                ctypes.memmove(command.data_ptr, self.raw(), 512)
                struct.pack_into('<I', buffer, 16, 0x900)
                return 0
            with mock.patch.object(i.fcntl, 'ioctl', ioctl):
                result = i.read_ext_csd_fd(fd)
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0], (fd, 0xc048b300, {'write_flag': 0, 'is_acmd': 0, 'opcode': 8, 'arg': 0,
                'flags': 0xb5, 'blksz': 512, 'blocks': 1, 'postsleep_min_us': 0, 'postsleep_max_us': 0,
                'data_timeout_ns': 0, 'cmd_timeout_ms': 0}))
            self.assertEqual(result['active_boot_area'], '/dev/mmcblk0boot0')
            self.assertEqual(result['partition_config'], 72)

    def test_selector_change_and_bad_geometry_refuse(self):
        observed = {**i.decode_ext_csd(self.raw()), 'parent_cid_sha256': 'a' * 64}
        mapping = {'active_selector_proven': True, 'active_selector': observed,
                   'paths': ['/dev/mmcblk0boot0']}
        i.check_active_selector(mapping, observed)
        changed = {**i.decode_ext_csd(self.raw(2)), 'parent_cid_sha256': 'a' * 64}
        with self.assertRaises(i.fs.FilesystemError) as caught:
            i.check_active_selector(mapping, changed)
        self.assertEqual(caught.exception.code, 'ACTIVE_BOOT_SELECTOR_CHANGED')
        for offset in (192, 226, 215):
            data = bytearray(self.raw()); data[offset] ^= 0xff
            with self.subTest(offset=offset), self.assertRaises(i.fs.FilesystemError):
                i.decode_ext_csd(bytes(data))
        # 2026-11-13: the 128 GB SM-A226B too; userdata follows the eMMC (Samsung's PIT: the rest).
        data = bytearray(self.raw()); data[212:216] = (244285440).to_bytes(4, 'little')
        self.assertEqual(i.decode_ext_csd(bytes(data))['user_sector_count'], 244285440)
        self.assertEqual(i.userdata_sizes(i.MMC_SECTORS), (53393489920, (122142720 - 33 - 17850368) * 512))
        self.assertEqual(i.userdata_sizes(244285440)[0], (244285440 - 8192 - 17850368) * 512)
        data[212:216] = (60_000_000).to_bytes(4, 'little')
        with self.assertRaises(i.fs.FilesystemError):
            i.decode_ext_csd(bytes(data))


if __name__ == '__main__':
    unittest.main()
