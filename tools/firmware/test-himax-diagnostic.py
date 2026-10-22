#!/usr/bin/env python3
"""Build and execute independent CPU-only SRAM diagnostic, never a device."""
import argparse
import hashlib
import json
import re
import struct
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--toolchain', type=Path, required=True)
    ap.add_argument('--receipt', type=Path)
    args = ap.parse_args()
    prefix = args.toolchain.resolve() / 'bin/nds32le-elf-'
    source = ROOT / 'firmware/himax/diagnostic'

    def run(tool, *argv):
        return subprocess.run([str(prefix) + tool, *map(str, argv)],
                              check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=30).stdout

    with tempfile.TemporaryDirectory(prefix='hx-diagnostic-') as temp:
        out = Path(temp)
        run('as', '-EL', '-march=v3', '-o', out/'start.o', source/'start.S')
        run('gcc', '-std=c11', '-EL', '-march=v3', '-mabi=2', '-mno-ex9',
            '-mno-ifc', '-mno-fp-as-gp', '-mcmodel=large', '-ffreestanding',
            '-fno-builtin', '-fno-common', '-fno-stack-protector', '-Os',
            '-Wall', '-Wextra', '-Werror', '-c', source/'main.c', '-o', out/'main.o')
        elf = out/'diagnostic.elf'
        run('ld', '--no-relax', '-T', source/'sram.ld', '-o', elf,
            out/'start.o', out/'main.o')
        if run('nm', '-u', elf).strip():
            raise ValueError('Undefined symbols')
        symbols = {line.split()[2]: int(line.split()[0], 16)
                   for line in run('nm', elf).splitlines() if len(line.split()) == 3}
        assert symbols['diagnostic_start'] == 0x40000100
        assert symbols['result'] == 0x4000f000
        disassembly = run('objdump', '-d', elf)
        assert not re.search(r'\b(ex9|ifcall|ifret)\b', disassembly)
        run('objcopy', '-O', 'binary', elf, out/'diagnostic.bin')
        commands = [
            'set pagination off', 'set confirm off', f'file {elf}',
            'target sim --memory-region 0x40000000,0x10000', 'load',
            'break *_start', 'run', 'delete 1',
            # Exercise startup with GIE initially enabled. No interrupt source
            # or peripheral is modeled by this test.
            'set $ir0 = $ir0 | 1',
            'break *diagnostic_done', 'continue',
            'printf "STATE %x %x\\n", $ir0, $sp',
            f'dump binary memory {out/"mailbox.bin"} 0x4000f000 0x4000f01c',
            'delete 2',
        ]
        # Every non-reset vector must branch directly to our trap. Do not
        # emulate a hardware exception or claim ROM vector behavior.
        for slot in range(1, 16):
            commands += [f'set $pc = {0x40000000+4*slot}', 'stepi',
                         f'printf "VECTOR {slot} %x\\n", $pc']
        commands += ['quit']
        script = out/'test.gdb'
        script.write_text('\n'.join(commands)+'\n')
        log = run('gdb', '-nx', '-nh', '--batch', '-x', script)
        match = re.search(r'STATE ([0-9a-f]+) ([0-9a-f]+)', log)
        assert match, log[:4000]
        psw, sp = [int(x, 16) for x in match.groups()]
        assert psw & 1 == 0 and sp == 0x40010000
        result = struct.unpack('<7I', (out/'mailbox.bin').read_bytes())
        assert result == (0x48584447, 1, 2, 4096, 4096*4097//2, psw, 0x40000000), result
        vectors = re.findall(r'VECTOR (\d+) ([0-9a-f]+)', log)
        assert len(vectors) == 15
        assert all(int(pc, 16) == symbols['diagnostic_trap'] for _, pc in vectors)
        receipt = {'result': 'PASS', 'counter': result[3], 'checksum': result[4],
                   'vector_cases': len(vectors), 'interrupts_disabled': True,
                   'binary_bytes': (out/'diagnostic.bin').stat().st_size,
                   'binary_sha256': hashlib.sha256((out/'diagnostic.bin').read_bytes()).hexdigest(),
                   'hardware_test': False, 'peripheral_model': False,
                   'scope': 'independent CPU startup, finite computation and vector branches'}
        if args.receipt:
            args.receipt.write_text(json.dumps(receipt, indent=2)+'\n')
        print(json.dumps(receipt, indent=2))


if __name__ == '__main__':
    main()
