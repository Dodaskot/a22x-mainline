#!/usr/bin/env python3
"""Offline, exact-image NDS32 analysis. Outputs contain private disassembly.

Requires radare2 rasm2 with the nds32 plugin. Never contacts a controller.
This is a linear listing with EX9 annotations, not a complete control-flow graph.
"""
import argparse
import collections
import hashlib
import json
import re
import struct
import subprocess
import tempfile
from pathlib import Path

SHA = "fd4df971965dfe5e550ba4a6a9e94e1b67d5ca128dad3eb74ebaa1873d591f1a"
BASE = 0x40000000
ITB_OFFSET = 0xb918
# 383 slots cover every EX9 index observed in the selected code ranges.
# This is an observed minimum, not an architectural table-size assertion.
ITB_SLOTS = 383
# Inline switch tables are established by bounds, table-base calculations,
# load width, and an indirect jump; padding follows returns and precedes calls.
SWITCH_TABLES = ((0x7794, 18, 1), (0x7ad8, 6, 1),
                 (0x87f0, 7, 2), (0x9320, 46, 2))
DATA_SPANS = ((0x40, 0x64), (0x5be, 0x5c0), (0x7794, 0x77a6),
              (0x7aba, 0x7abc), (0x7ad8, 0x7ade), (0x87b2, 0x87b4),
              (0x87f0, 0x87fe), (0x9306, 0x9308), (0x9320, 0x937c),
              (0xb5c6, 0xb5c8), (0xb652, 0xb654))
RANGES = tuple(zip((0,) + tuple(end for _, end in DATA_SPANS),
                   tuple(start for start, _ in DATA_SPANS) + (ITB_OFFSET,)))
LINE = re.compile(r"^(0x[0-9a-f]+)\s+(\d+)\s+([0-9a-f]+)\s+(.+)$")


def signed(value, bits):
    return (value ^ (1 << (bits - 1))) - (1 << (bits - 1))


def direct_jump(pc, raw):
    """NDS32 J/JAL: signed imm24, scaled by two, relative to this PC."""
    if len(raw) != 4 or raw[0] not in (0x48, 0x49):
        return None
    return (pc + 2 * signed(int.from_bytes(raw[1:], 'big'), 24)) & 0xffffffff


def ex9_index(raw):
    if len(raw) != 2:
        return None
    value = int.from_bytes(raw, 'big')
    if 0xdd40 <= value <= 0xdd5f:
        return value - 0xdd40
    if 0xea20 <= value <= 0xebff:
        return value - 0xea00
    return None


def word_slot(code, index):
    if not 0 <= index < ITB_SLOTS:
        raise ValueError(f'EX9 index outside qualified table: {index}')
    off = ITB_OFFSET + index * 4
    word = code[off:off + 4]
    if len(word) != 4:
        raise ValueError('Truncated EX9 table')
    return word


def normalize_addresses(text):
    # rasm2 6.2.0 NDS32 prints some backward targets as 0x14000....
    # Preserve raw text separately and apply architectural 32-bit wrap here.
    return re.sub(r'0x[0-9a-f]+',
                  lambda m: f'0x{int(m[0], 16) & 0xffffffff:08x}'
                  if int(m[0], 16) > 0xffffffff else m[0], text)


def decode(data, address, executable='rasm2'):
    with tempfile.TemporaryDirectory(prefix='himax-nds32-') as tmp:
        src = Path(tmp) / 'private.bin'
        src.write_bytes(data)
        result = subprocess.run([executable, '-a', 'nds32', '-b', '32',
                                 '-s', hex(address), '-D', '-B', '-l', str(len(data)),
                                 '-f', str(src)], check=True, capture_output=True, text=True)
    records = []
    consumed = 0
    for line in result.stdout.splitlines():
        m = LINE.fullmatch(line.strip())
        if not m:
            raise ValueError(f'Unexpected decoder output: {line[:100]}')
        pc, size, raw, asm = int(m[1], 16), int(m[2]), bytes.fromhex(m[3]), m[4]
        expected_size = 2 if raw and raw[0] & 0x80 else 4
        if (size != expected_size or len(raw) != size or pc != address + consumed
                or raw != data[consumed:consumed + size]):
            raise ValueError(f'Decoder boundary/byte mismatch at {pc:#x}')
        record = dict(pc=pc, size=size, bytes=raw.hex(), raw_assembly=asm,
                      assembly=normalize_addresses(asm))
        target = direct_jump(pc, raw)
        if target is not None:
            printed = re.search(r'0x[0-9a-f]+', record['assembly'])
            if not printed or int(printed[0], 16) != target:
                raise ValueError(f'Independent branch check failed at {pc:#x}')
            record['direct_target'] = target
        index = ex9_index(raw)
        if index is not None:
            if asm != f'ex9.it {index}':
                raise ValueError(f'Independent EX9 check failed at {pc:#x}')
            record['ex9_index'] = index
        records.append(record)
        consumed += size
    if consumed != len(data):
        raise ValueError('Incomplete disassembly')
    return records


def analyze(blob, executable='rasm2'):
    if hashlib.sha256(blob).hexdigest() != SHA:
        raise ValueError('Unsupported image: offsets are qualified only for the documented SHA256')
    code = blob[0x400:0x10400]
    records = []
    for start, end in RANGES:
        records.extend(decode(code[start:end], BASE + start, executable))
    table = decode(code[ITB_OFFSET:ITB_OFFSET + ITB_SLOTS * 4], BASE + ITB_OFFSET, executable)
    if len(table) != ITB_SLOTS or any(r['size'] != 4 for r in table):
        raise ValueError('EX9 slots are not individual 32-bit instructions')
    counts = collections.Counter()
    for r in records:
        if 'ex9_index' in r:
            index = r['ex9_index']
            word_slot(code, index)
            counts[index] += 1
            r['expanded_assembly'] = table[index]['assembly']
    starts = {r['pc'] for r in records}
    branches = [r for r in records if 'direct_target' in r]
    invalid = [r for r in branches if r['direct_target'] not in starts]
    switches = []
    for off, count, width in SWITCH_TABLES:
        targets = [BASE + off + int.from_bytes(code[off + i * width:off + (i + 1) * width], 'little')
                   for i in range(count)]
        if any(target not in starts for target in targets):
            raise ValueError(f'Switch table boundary mismatch at {off:#x}')
        switches.append(dict(address=BASE + off, count=count, width=width, targets=targets))
    summary = dict(firmware_sha256=SHA, code_sha256=hashlib.sha256(code).hexdigest(),
                   decoder=subprocess.check_output([executable, '-v'], text=True).strip(),
                   execution_base_inferred=BASE, upload_base=0x20000000,
                   reset_target=direct_jump(BASE, code[:4]),
                   gp=0x4000c8e8, fp=0x4000c8e8, initial_sp=0x40010000,
                   instruction_table=BASE + ITB_OFFSET, observed_table_slots=ITB_SLOTS,
                   linear_instruction_count=len(records), ex9_uses=sum(counts.values()),
                   ex9_distinct=len(counts), ex9_max_index=max(counts),
                   direct_jumps_and_calls=len(branches), switch_tables=switches,
                   direct_targets_outside_selected_boundaries=invalid,
                   undecoded=[r for r in records if any(x in r['assembly'].lower()
                              for x in ('unknown', 'invalid', '.word'))],
                   irq_pointer_table=list(struct.unpack_from('<9I', code, 0x40)),
                   limitations=['Static linear analysis, not reachability or execution proof',
                                'CPU model and full ISA revision unknown',
                                'EX9 minimum extent inferred from references; physical table size unknown',
                                '32-bit branch display normalization; raw decoder output retained',
                                'No controller writes or authentication conclusion'])
    return summary, records, table


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('firmware', type=Path)
    ap.add_argument('--output-dir', type=Path, required=True,
                    help='Private output directory; disassembly must not be published as firmware source')
    ap.add_argument('--rasm2', default='rasm2')
    args = ap.parse_args()
    summary, records, table = analyze(args.firmware.read_bytes(), args.rasm2)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, value in [('summary', summary), ('instructions-private', records), ('ex9-private', table)]:
        (args.output_dir / (name + '.json')).write_text(json.dumps(value, indent=2) + '\n')
    listing = []
    for r in records:
        extra = ' ; EX9 => ' + r['expanded_assembly'] if 'expanded_assembly' in r else ''
        listing.append(f"{r['pc']:08x}  {r['bytes']:8s}  {r['assembly']}{extra}")
    (args.output_dir / 'annotated-private.asm').write_text('\n'.join(listing) + '\n')
    print(json.dumps({k: summary[k] for k in ('firmware_sha256', 'reset_target',
        'linear_instruction_count', 'ex9_uses', 'ex9_distinct', 'direct_jumps_and_calls')}, indent=2))
    print('Unresolved direct targets:', len(summary['direct_targets_outside_selected_boundaries']))
    print('Undecoded instructions:', len(summary['undecoded']))


if __name__ == '__main__':
    main()
