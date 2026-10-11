#!/usr/bin/env python3
"""Offline A22x DT checks for LK overlay symbols and NFC bus pull-ups."""
import argparse
from pathlib import Path
import struct


def nodes(blob):
    magic, total, off, strings, _, version, *_ = struct.unpack_from('>10I', blob)
    assert magic == 0xd00dfeed and total <= len(blob) and version >= 17
    result, stack = {}, []
    while off < total:
        token, = struct.unpack_from('>I', blob, off); off += 4
        if token == 1:
            end = blob.index(b'\0', off)
            stack.append(blob[off:end].decode()); off = (end + 4) & ~3
            result['/'.join(stack) or '/'] = {}
        elif token == 2:
            stack.pop()
        elif token == 3:
            size, nameoff = struct.unpack_from('>II', blob, off); off += 8
            start = strings + nameoff; end = blob.index(b'\0', start)
            result['/'.join(stack) or '/'][blob[start:end].decode()] = blob[off:off+size]
            off = (off + size + 3) & ~3
        elif token == 4:
            continue
        elif token == 9:
            assert not stack
            return result
        else:
            raise ValueError(f'Invalid FDT token {token}')
    raise ValueError('No FDT_END token')


def validate(base, overlay):
    tree = nodes(base.read_bytes())
    symbols = tree.get('/__symbols__')
    assert symbols, 'Missing __symbols__: build the A22x DTB with DTC -@'
    fixups = nodes(overlay.read_bytes()).get('/__fixups__', {})
    assert fixups, 'Expected stock DTBO external fixups'
    for key in fixups:
        assert key in symbols, f'Stock DTBO requires missing symbol {key}'
        path = symbols[key].rstrip(b'\0').decode()
        assert path in tree and 'phandle' in tree[path], f'Invalid symbol target {key}: {path}'
    nfc = '/i2c@11cb0000/nfc@27'
    if nfc in tree:
        assert tree['/i2c3@11cb0000'].get('status') == b'disabled\0'
        pins = tree['/pinctrl@10005000/nfc-i2c3-pins/pins']
        assert 'bias-disable' not in pins, 'NFC I2C needs stock SCL/SDA pull-ups; bias-disable holds bus low'
        assert 'bias-pull-up' in pins, 'Declare NFC SCL/SDA pull-ups explicitly'
    print(f'PASS: {base.name}, {len(fixups)} stock symbols, NFC pins checked')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('dtb', type=Path)
    parser.add_argument('stock_dtbo', type=Path)
    args = parser.parse_args()
    validate(args.dtb, args.stock_dtbo)
