#!/usr/bin/env python3
"""Synthetic tests: no proprietary image or device is required."""
import importlib.util
import struct
import unittest
from pathlib import Path

spec = importlib.util.spec_from_file_location('inspector', Path(__file__).with_name('inspect-himax.py'))
inspector = importlib.util.module_from_spec(spec)
spec.loader.exec_module(inspector)


def descriptor(code, address, length):
    row = bytearray(struct.pack('<4I', code, address, length, 0))
    row[14] = 0x87
    row[15] = (-sum(row)) % 256
    return row


def fixture():
    data = bytearray(512)
    data[:16] = descriptor(0, 0, 0x400)
    data[16:32] = descriptor(0x0a, 0x80, 32)
    struct.pack_into('<4I', data, 0x80, 0x20000000, 16, 0x100, 2)
    struct.pack_into('<4I', data, 0x90, 0x10007000, 8, 0x140, 0)
    data[0x100:0x110] = bytes(range(16))
    data[0x140:0x148] = b'config!!'
    return data


class InspectionTests(unittest.TestCase):
    def test_relocated_table_and_payloads(self):
        result = inspector.inspect(fixture())
        self.assertEqual(result['partition_table_offset'], 0x80)
        self.assertEqual(len(result['partitions']), 2)
        self.assertEqual(result['partitions'][0]['file_offset'], 0x100)
        self.assertEqual(result['partitions'][1]['target_address'], 0x10007000)

    def test_crc_independent_bytewise_reference(self):
        data = bytes(range(253))
        crc = 0xffffffff
        for byte in data[:252]:
            crc ^= byte
            for _ in range(8):
                crc = (crc >> 1) ^ (0x82f63b78 if crc & 1 else 0)
        self.assertEqual(inspector.crc_driver(data), crc)
        self.assertEqual(inspector.crc_driver(data), inspector.crc_driver(data[:252]))

    def test_modified_payload_changes_crc(self):
        data = fixture()
        before = inspector.inspect(data)['partitions'][0]['driver_crc32c_raw']
        data[0x107] ^= 1
        self.assertNotEqual(inspector.inspect(data)['partitions'][0]['driver_crc32c_raw'], before)

    def test_bad_descriptor_checksum(self):
        data = fixture()
        data[20] ^= 1
        with self.assertRaisesRegex(ValueError, 'checksum'):
            inspector.inspect(data)

    def test_missing_table(self):
        data = fixture()
        data[16:32] = bytes(16)
        with self.assertRaisesRegex(ValueError, 'TP_CONFIG_TABLE'):
            inspector.inspect(data)

    def test_truncated_partition_table(self):
        with self.assertRaisesRegex(ValueError, 'count or table bounds'):
            inspector.inspect(fixture()[:0x90])

    def test_partition_payload_out_of_bounds(self):
        data = fixture()
        struct.pack_into('<I', data, 0x98, 0x1ff)
        with self.assertRaisesRegex(ValueError, 'exceeds file'):
            inspector.inspect(data)

    def test_empty_payload_rejected(self):
        data = fixture()
        struct.pack_into('<I', data, 0x94, 0)
        with self.assertRaisesRegex(ValueError, 'empty'):
            inspector.inspect(data)

    def test_signature_required(self):
        with self.assertRaisesRegex(ValueError, 'signature'):
            inspector.inspect(bytes(512))


if __name__ == '__main__':
    unittest.main()
