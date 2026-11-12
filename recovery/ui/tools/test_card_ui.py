#!/usr/bin/env python3
"""Partition a card through the UI on a fixture file (2026-11-11): the Wipe page, the choice, the
five-second hold, the result and the card read again, with the real sdcard.py."""
import json
import os
from pathlib import Path
import struct
import subprocess
import sys
import tempfile

UI = Path(__file__).resolve().parents[1]
SOURCE = UI.parent
MIB = 1 << 20


def main():
    with tempfile.TemporaryDirectory(prefix='card-ui-', dir=os.environ.get('TMPDIR')) as temporary:
        root = Path(temporary)
        card = root / 'card.img'
        with open(card, 'wb') as out:
            out.truncate(3072 * MIB)
            # What the card held: one ext4 partition named old-system, as probe_magic reads it.
            out.seek(446)
            out.write(struct.pack('<B3sB3sII', 0, b'\xfe\xff\xff', 0x83, b'\xfe\xff\xff', 2048, (3072 * MIB - MIB) // 512))
            out.seek(510)
            out.write(b'\x55\xaa')
            head = bytearray(2048)
            struct.pack_into('<I', head, 1024 + 24, 2)
            struct.pack_into('<I', head, 1024 + 32, 32768)
            head[1080:1082] = b'\x53\xef'
            head[1024 + 120:1024 + 130] = b'old-system'
            out.seek(MIB)
            out.write(head)
        (root / 'sdcard-fixture.json').write_text(json.dumps({'schema': 'a22x.sdcard.fixture.v1', 'disks': [
            {'id': 'card', 'path': 'card.img', 'name': 'Fixture card'}]}))
        workdir = root / 'work'
        workdir.mkdir(mode=0o700)
        environment = dict(os.environ, A22X_RECOVERY_CARD_HELPER=str(SOURCE / 'sdcard.py'))
        done = subprocess.run([str(UI / 'a22x-recovery-ui'), '--fixture-root', str(root), '--workdir', str(workdir),
                               '--python', sys.executable, '--workflow', 'card-partition'],
                              capture_output=True, text=True, timeout=120, env=environment)
        if done.returncode:
            raise AssertionError((done.returncode, done.stdout, done.stderr[-2000:]))
        result = json.loads(done.stdout)
        assert result['ok'] and result['backend_operation'] == 'card-list', result
        with open(card, 'rb') as stream:
            sector = stream.read(512)
            rows = [struct.unpack_from('<B3sB3sII', sector, 446 + 16 * n) for n in range(4)]
            assert [row[2] for row in rows] == [0x0c, 0x82, 0, 0], rows
            assert (rows[0][4] * 512, rows[0][5] * 512) == (4 * MIB, 3072 * MIB - 4 * MIB - 1024 * MIB), rows
            assert (rows[1][4] * 512, rows[1][5] * 512) == (3072 * MIB - 1024 * MIB, 1024 * MIB), rows
            stream.seek(MIB + 1024)
            assert stream.read(1024) == bytes(1024), 'the old filesystem is still found'
    print(json.dumps({'ok': True, 'workflow': 'card-partition', 'device_io': False, 'actual_card_helper': True}))


if __name__ == '__main__':
    main()
