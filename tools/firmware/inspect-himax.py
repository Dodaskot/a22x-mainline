#!/usr/bin/env python3
"""Read-only HX83112F descriptor/zero-flash inventory; does not emit firmware."""
import argparse
import hashlib
import json
import struct
from pathlib import Path


def crc_driver(data):
    """Matches himax_mcu_Calculate_CRC_with_AP, including word truncation."""
    crc = 0xffffffff
    for (word,) in struct.iter_unpack('<I', data[:len(data) // 4 * 4]):
        crc ^= word
        for _ in range(32):
            crc = (crc >> 1) ^ (0x82f63b78 if crc & 1 else 0)
    return crc


def inspect(data):
    if len(data) < 16 or data[:8] != bytes(8) or data[14] != 0x87:
        raise ValueError('Missing supported Himax descriptor signature')
    descriptors = []
    table = None
    for off in range(0, len(data) - 15, 16):
        row = data[off:off + 16]
        if not any(row):
            break
        if sum(row) % 256:
            raise ValueError(f'Descriptor checksum failure at {off:#x}')
        code, address, length, reserved = struct.unpack('<4I', row)
        descriptors.append(dict(offset=off, map_code=code, address=address,
                                length=length, checksum_valid=True))
        if code == 0x0a:
            table = address
    if table is None or table + 16 > len(data):
        raise ValueError('No bounded old-format TP_CONFIG_TABLE descriptor')
    count = data[table + 12]
    if count < 2 or table + count * 16 > len(data):
        raise ValueError('Invalid partition count or table bounds')
    partitions = []
    for index in range(count):
        target, length, offset, metadata = struct.unpack_from('<4I', data, table + index * 16)
        if offset + length > len(data) or not length:
            raise ValueError(f'Partition {index} exceeds file or is empty')
        payload = data[offset:offset + length]
        partitions.append(dict(index=index, target_address=target,
                               file_offset=offset, size=length,
                               table_metadata=metadata,
                               sha256=hashlib.sha256(payload).hexdigest(),
                               driver_crc32c_raw=crc_driver(payload),
                               trailing_bytes_ignored_by_driver_crc=length % 4))
    versions = {}
    for row in descriptors:
        if row['map_code'] in (0x10000000, 0x10000001, 0x10000005):
            pos = row['address']
            if pos + 2 <= len(data):
                versions[hex(row['map_code'])] = dict(offset=pos, bytes_hex=data[pos:pos+2].hex())
    return dict(size=len(data), sha256=hashlib.sha256(data).hexdigest(),
                descriptors=descriptors, partition_table_offset=table,
                partitions=partitions, version_fields=versions,
                architecture='UNDETERMINED: no ISA identification from descriptor format',
                crc_note='Raw reflected CRC32C initial ffffffff; no final XOR; hardware CRC not measured')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('firmware', type=Path)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args()
    result = inspect(args.firmware.read_bytes())
    output = json.dumps(result, indent=2) + '\n'
    if args.output:
        args.output.write_text(output)
    else:
        print(output, end='')


if __name__ == '__main__':
    main()
