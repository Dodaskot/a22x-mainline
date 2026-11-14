#!/usr/bin/env python3
"""Explicit device acceptance: touch only a new, uniquely named test directory."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid

helper = Path(sys.argv[1])
record = Path(sys.argv[2])
record.mkdir(parents=True, exist_ok=False)
steps = []
tokens = {}
test_root = '/a22x-recovery-v02-check-' + uuid.uuid4().hex[:12]
result = {'schema': 'a22x.filesystem.live.v1', 'test_root': test_root,
          'helper_sha256': hashlib.sha256(helper.read_bytes()).hexdigest(),
          'boot_id': Path('/proc/sys/kernel/random/boot_id').read_text().strip(),
          'started': time.time(), 'steps': steps, 'ok': False}

def check(condition, detail=None):
    """Every acceptance criterion, also under python3 -O, which drops assert (2026-11-14 review)."""
    if not condition:
        raise AssertionError(detail)


def call(action, volume=None, *args, expect=True):
    command = [sys.executable, str(helper), '--device', action]
    if volume is not None:
        command += ['--volume', volume, '--expected-volume-token', tokens[volume]]
    command += list(args)
    proc = subprocess.run(command, capture_output=True, text=True, timeout=45)
    data = json.loads(proc.stdout)
    steps.append({'argv': command[3:], 'exit_code': proc.returncode,
                  'result': data, 'stderr': proc.stderr})
    (record / 'result.json').write_text(json.dumps(result, indent=2))
    check(bool(data['ok']) == expect, (action, data))
    check((proc.returncode == 0) == expect, (action, proc.returncode))
    return data

def mount(volume, mode):
    data = call('mount', volume, '--mode', mode, *(['--confirm'] if mode == 'rw' else []))
    v = data['volume']
    check(v['mounted'] and v['read_only'] == (mode == 'ro'))
    if mode == 'ro':
        check(v['block_read_only'] and v['read_only_guarded'], v)
    return v

def unmount(volume):
    v = call('unmount', volume)['volume']
    check(not v['mounted'] and not v['block_read_only'] and not v['read_only_guarded'], v)

try:
    volumes = call('inventory')['volumes']
    for v in volumes:
        tokens[v['id']] = v['volume_token']
    for name in ('userdata', 'sdcard-p1'):
        v = next(v for v in volumes if v['id'] == name)
        check(v['mountable'] and not v['mounted'] and not v['block_read_only'], v)
    before_text = record / 'before.txt'
    after_text = record / 'after.txt'
    before_text.write_text('A22x v0.2 live test\nПеревірка файлів: початок.\n', encoding='utf-8')
    after_text.write_text('A22x v0.2 live test\nПеревірка файлів: зміни збережено.\n', encoding='utf-8')
    mount('userdata', 'ro')
    call('list', 'userdata', '--path', '/', '--limit', '64')
    original_os = call('read', 'userdata', '--path', '/usr/lib/os-release')['sha256']
    denied = call('write', 'userdata', '--path', test_root + '-denied', '--create',
                  '--text-file', str(before_text), '--confirm', expect=False)
    check(denied['error']['code'] == 'VOLUME_READ_ONLY', denied)
    unmount('userdata')
    mount('userdata', 'rw')
    call('mkdir', 'userdata', '--path', test_root, '--confirm')
    path = test_root + '/перевірка.txt'
    created = call('write', 'userdata', '--path', path, '--create',
                   '--text-file', str(before_text), '--confirm')
    first = call('read', 'userdata', '--path', path)
    check(first['content'] == before_text.read_text() and first['sha256'] == created['sha256'])
    changed = call('write', 'userdata', '--path', path, '--expected-sha256', first['sha256'],
                   '--text-file', str(after_text), '--confirm')
    stale = call('write', 'userdata', '--path', path, '--expected-sha256', first['sha256'],
                 '--text-file', str(before_text), '--confirm', expect=False)
    check(stale['error']['code'] == 'FILE_CHANGED', stale)
    copied = test_root + '/copy.txt'
    renamed = test_root + '/перейменовано.txt'
    call('copy', 'userdata', '--path', path, '--destination-volume', 'userdata',
         '--expected-destination-volume-token', tokens['userdata'], '--destination', copied,
         '--expected-sha256', changed['sha256'], '--confirm')
    call('rename', 'userdata', '--path', copied, '--destination', renamed,
         '--expected-sha256', changed['sha256'], '--confirm')
    unmount('userdata')
    mount('userdata', 'ro')
    for name in (path, renamed):  # not "check": that is the function (2026-11-14 review)
        readback = call('read', 'userdata', '--path', name)
        check(readback['content'] == after_text.read_text() and readback['sha256'] == changed['sha256'])
    check(call('read', 'userdata', '--path', '/usr/lib/os-release')['sha256'] == original_os)
    unmount('userdata')
    mount('userdata', 'rw')
    for name in (path, renamed):
        call('delete', 'userdata', '--path', name, '--expected-sha256', changed['sha256'], '--confirm')
    call('delete', 'userdata', '--path', test_root, '--confirm')
    root_entries = call('list', 'userdata', '--path', '/', '--limit', '256')['entries']
    check(not any(x['name'] in (test_root[1:], test_root[1:] + '-denied') for x in root_entries))
    unmount('userdata')
    mount('sdcard-p1', 'ro')
    call('list', 'sdcard-p1', '--path', '/', '--limit', '16')
    unmount('sdcard-p1')
    result.update(ok=True, original_os_sha256=original_os, content_sha256=changed['sha256'],
                  verified=['ro_block_guard', 'ro_write_refusal', 'rw_create_edit',
                            'stale_hash_refusal', 'utf8_paths_content', 'copy_rename',
                            'remount_persistent_readback', 'cleanup', 'sd_read_only'])
except BaseException as exc:
    result['error'] = repr(exc)
finally:
    try:
        call('unmount-all')
        final = call('inventory')['volumes']
        result['final_mounts'] = [{k: v.get(k) for k in ('id', 'mounted', 'block_read_only', 'read_only_guarded')}
                                  for v in final if v['id'] in ('userdata', 'sdcard-p1')]
    except BaseException as exc:
        result['cleanup_error'] = repr(exc)
        result['ok'] = False
    result['finished'] = time.time()
    (record / 'result.json').write_text(json.dumps(result, indent=2))
    print(json.dumps({k: v for k, v in result.items() if k != 'steps'}, ensure_ascii=True))
    print('STEP_COUNT', len(steps))
sys.exit(0 if result['ok'] else 1)
