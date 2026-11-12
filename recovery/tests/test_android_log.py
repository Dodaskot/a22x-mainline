"""Why Android did not start, read from the memory its kernel's log outlives a restart in
(android_log.py, 2026-11-12). The region is built here as the phone's Android kernel leaves it: a
ring of console lines after a header, somewhere among other records."""
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import android_log as al


def line(seconds, who, text, cpu=6, irq=False):
    return ('[%12.6f] %s(%d)[%s]%s\n' % (seconds, '-' if irq else ' ', cpu, who, text)).encode()


def start(lines):
    """A start of the phone's Android kernel up to init's work, then LINES."""
    out = [line(0.0, '0:swapper', 'Linux version 4.14.186-27218697 (dpi@SWDK1234) (clang version 12.0.5) #1 SMP PREEMPT'),
           line(0.9, '1:swapper/0', 'mtk-wdt 10007000.watchdog: started'),
           line(3.2, '1:init', 'init: init first stage started!'),
           line(5.6, '1:init', "init: processing action (late-init) from (/system/etc/init/hw/init.rc:503)")]
    return out + list(lines)


BPF = [line(7.850, '1:init', "init: starting service 'bpfloader'..."),
       line(7.866, '861:netbpfload', 'NetBpfLoad: Android V requires kernel 4.19.'),
       line(7.867, '1:init', "init: Service 'bpfloader' (pid 861) exited with status 1 waiting took 0.015000 seconds"),
       line(7.868, '1:init', "init: Service bpfloader has 'reboot_on_failure' option and failed, shutting down system."),
       line(7.869, '1:init', 'init: Reboot start, reason: reboot,bpfloader-failed, reboot_target: bpfloader-failed'),
       line(8.300, '1:init', "init: Service 'vold' (pid 529) received signal 9"),
       line(9.082, '1:init', "reboot: Restarting system with command 'bpfloader-failed'", cpu=0),
       line(9.083, '1:init', 'sec_reboot (0, bpfloader-failed)', cpu=0, irq=True)]


def region(newest, older=(), size=0x8000, at=0x3000, total=0x10000):
    """The store with a console ring of SIZE at AT: OLDER was written first, NEWEST last, and the
    ring wrapped when both do not fit. Around it lie the records of the recovery's own kernel."""
    stream = b''.join(older) + b''.join(newest)
    room = size - 12
    ring = bytearray(room)
    position = 0
    for byte in stream:
        ring[position % room] = byte
        position += 1
    raw = bytearray(os.urandom(total))
    raw[at:at + size] = b'DBGC' + bytes(8) + bytes(ring)   # the header as the recovery's kernel leaves it: emptied
    own = b'[    0.000000] Booting Linux on physical CPU 0x0000000000 [0x412fd050]\n[    0.094535] ramoops: using module parameters\n'
    raw[at + size:at + size + 12 + len(own)] = b'DBGC' + bytes(8) + own
    return bytes(raw)


class LastStart(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='a22x-alog-', dir=os.environ.get('TMPDIR')))
        self.addCleanup(shutil.rmtree, self.root, True)

    def read(self, raw, *more):
        if raw is not None:
            (self.root / 'pstore-raw.bin').write_bytes(raw)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = al.main(['--fixture', str(self.root), 'read', *more])
        return code, json.loads(out.getvalue())

    def test_what_the_owner_met(self):
        """Google's Android 15 on the phone's 4.14 kernel: three starts, each ended by init."""
        code, found = self.read(region(start(BPF), older=start(BPF) + start(BPF)))
        self.assertEqual((code, found['found'], found['code'], found['needs'], found['restart']), (0, True, 'KERNEL_TOO_OLD', '4.19', 'bpfloader-failed'))
        self.assertEqual((found['reason'], found['seconds'], found['kernel']), ('reboot,bpfloader-failed', 9.1, '4.14.186'))
        self.assertIn('NetBpfLoad: Android V requires kernel 4.19.', found['lines'][0])
        self.assertIn("Restarting system with command 'bpfloader-failed'", found['lines'][-1])
        self.assertFalse(any('received signal 9' in text for text in found['lines']))  # the way down is left out

    def test_the_ring_is_put_in_order_when_it_wrapped(self):
        """The newest line lies in the middle of the memory, the oldest right after it."""
        filler = [line(1.0 + n / 1000, '1:swapper/0', 'probe of device %04d took its time and said so at length' % n) for n in range(900)]
        raw = region(start(BPF), older=start(filler))
        seam = raw.index(b'sec_reboot (0, bpfloader-failed)')
        self.assertLess(seam, 0x3000 + 0x8000 - 2000)
        self.assertIn(b'probe of device', raw[seam:0x3000 + 0x8000])  # older lines lie after the newest
        code, found = self.read(raw, '--save', str(self.root / 'last.txt'))
        self.assertEqual((found['code'], found['saved']), ('KERNEL_TOO_OLD', str(self.root / 'last.txt')))
        text = (self.root / 'last.txt').read_text().splitlines()
        self.assertEqual(len(text), found['log_lines'])
        self.assertIn('Linux version 4.14.186', text[0])
        self.assertIn('sec_reboot (0, bpfloader-failed)', text[-1])
        self.assertFalse(any('probe of device' in row for row in text))  # the start before is not part of it
        self.assertEqual(os.stat(self.root / 'last.txt').st_mode & 0o777, 0o600)

    def test_an_ordinary_restart_says_nothing(self):
        for command in ('recovery', 'download', '', 'shutdown,userrequested'):
            ended = [line(250.0, '1:init', 'init: Reboot start, reason: reboot,%s, reboot_target: %s' % (command, command)),
                     line(252.5, '1:init', "reboot: Restarting system with command '%s'" % command if command else 'reboot: Restarting system')]
            code, found = self.read(region(start(ended)))
            self.assertEqual((found['found'], found['code'], found['lines']), (True, '', []), command)
        code, found = self.read(region(start([line(300.0, '1:init', 'reboot: Power down')])))
        self.assertEqual((found['code'], found['restart']), ('', None))

    def test_init_that_gives_up_for_another_reason_is_named(self):
        ended = [line(11.0, '1:init', "init: Service 'apexd-bootstrap' (pid 300) exited with status 1 waiting took 2.000000 seconds"),
                 line(11.1, '1:init', "init: Service apexd-bootstrap has 'reboot_on_failure' option and failed, shutting down system."),
                 line(11.2, '1:init', 'init: Reboot start, reason: reboot,bootloader,bootstrap-apexd-failed, reboot_target: bootloader,bootstrap-apexd-failed'),
                 line(12.5, '1:init', "reboot: Restarting system with command 'bootstrap-apexd-failed'")]
        code, found = self.read(region(start(ended)))
        self.assertEqual((found['code'], found['restart'], found['reason']), ('INIT_GAVE_UP', 'bootstrap-apexd-failed', 'reboot,bootloader,bootstrap-apexd-failed'))
        self.assertEqual(len(found['lines']), 4)

    def test_a_panic_and_a_log_that_just_ends(self):
        code, found = self.read(region(start([line(20.0, '1:init', 'Kernel panic - not syncing: Attempted to kill init! exitcode=0x00000b00', irq=True)])))
        self.assertEqual((found['code'], found['detail']), ('KERNEL_PANIC', 'Attempted to kill init! exitcode=0x00000b00'))
        code, found = self.read(region(start([line(31.0, '412:surfaceflinger', 'mtk_drm: waiting for the panel')])))
        self.assertEqual((found['code'], found['restart'], found['seconds']), ('CUT_SHORT', None, 31.0))

    def test_the_same_start_has_the_same_id_and_another_has_another(self):
        first = self.read(region(start(BPF)))[1]['id']
        self.assertEqual(self.read(None)[1]['id'], first)
        self.assertNotEqual(self.read(region(start(BPF[:-1])))[1]['id'], first)
        self.assertRegex(first, r'^[0-9a-f]{16}$')

    def test_a_store_without_android_lines_says_nothing(self):
        """Only the recovery's own kernel wrote there, or there is no store at all."""
        own = b'[    0.000000] Booting Linux on physical CPU 0x0000000000\n[    1.559525] a22x-recovery: RAM recovery reached\n' * 40
        self.assertEqual(self.read(b'DBGC' + bytes(8) + own + os.urandom(4096))[1], {'ok': True, 'operation': 'read', 'found': False, 'code': ''})
        (self.root / 'pstore-raw.bin').unlink()
        self.assertEqual(self.read(None)[1]['found'], False)


if __name__ == '__main__':
    unittest.main()
