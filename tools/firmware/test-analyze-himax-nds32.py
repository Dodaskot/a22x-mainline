#!/usr/bin/env python3
"""Synthetic NDS32 decoder regressions; no proprietary image required."""
import importlib.util
import shutil
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('nds', Path(__file__).with_name('analyze-himax-nds32.py'))
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


class TestNDS32(unittest.TestCase):
    def test_forward_backward_wrap(self):
        for pc, imm, want in [(0x40000000, 0x20, 0x40000040),
                              (0x40000100, 0xffffc0, 0x40000080),
                              (0xfffffffc, 4, 4), (0, 0xffffff, 0xfffffffe)]:
            for op in (0x48, 0x49):
                raw = bytes([op]) + imm.to_bytes(3, 'big')
                self.assertEqual(m.direct_jump(pc, raw), want)
        self.assertIsNone(m.direct_jump(0, bytes.fromhex('64000008')))

    def test_ex9_limits_and_other_opcodes(self):
        for raw, want in [('dd40', 0), ('dd5f', 31), ('ea20', 32),
                          ('ebff', 511), ('dd9e', None), ('ea00', None),
                          ('64000008', None)]:
            self.assertEqual(m.ex9_index(bytes.fromhex(raw)), want)

    def test_table_bounds(self):
        with self.assertRaises(ValueError):
            m.word_slot(b'', 0)
        for index in (-1, m.ITB_SLOTS):
            with self.assertRaises(ValueError):
                m.word_slot(bytes(65536), index)

    def test_exact_image_gate(self):
        with self.assertRaisesRegex(ValueError, 'Unsupported image'):
            m.analyze(bytes(131072))

    def test_address_display_wrap(self):
        self.assertEqual(m.normalize_addresses('jal 0x140000080'), 'jal 0x40000080')

    def test_short_inline_switch_is_data_and_validates_targets(self):
        # Seven byte offsets followed by one padding byte; no firmware needed.
        code = bytearray(0x80)
        code[0x20:0x28] = bytes([8,12,16,8,16,12,8,0])
        starts = {m.BASE+0x28, m.BASE+0x2c, m.BASE+0x30}
        self.assertEqual(m.switch_targets(code,0x20,7,1,starts),
            [m.BASE+i for i in (0x28,0x2c,0x30,0x28,0x30,0x2c,0x28)])
        code[0x22] = 9  # Would target the middle of a real instruction.
        with self.assertRaisesRegex(ValueError,'boundary mismatch'):
            m.switch_targets(code,0x20,7,1,starts)
        with self.assertRaisesRegex(ValueError,'bounds'):
            m.switch_targets(code,0x7f,7,1,starts)
        self.assertIn((0x650,7,1),m.SWITCH_TABLES)
        self.assertIn((0x650,0x658),m.DATA_SPANS)
        self.assertFalse(any(start<0x658 and end>0x650 for start,end in m.RANGES))

    @unittest.skipUnless(shutil.which('rasm2'), 'rasm2 absent')
    def test_decoder_sign_width_and_ex9(self):
        # Synthetic stream: backward call, EX9, return, barrier.
        rows = m.decode(bytes.fromhex('49ffffc0dd40dd9e64000008'), 0x40000100)
        self.assertEqual([r['size'] for r in rows], [4, 2, 2, 4])
        self.assertEqual(rows[0]['direct_target'], 0x40000080)
        self.assertEqual(rows[1]['ex9_index'], 0)
        self.assertEqual(rows[-1]['assembly'], 'dsb')
        with self.assertRaises(ValueError):
            m.decode(bytes.fromhex('480000'), 0)


if __name__ == '__main__':
    unittest.main()
