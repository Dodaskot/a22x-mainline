#!/usr/bin/env python3
"""Bounded offline interpreter for the qualified HX83112F packet formatter.

This is NOT a CPU/chip emulator: only formatter instructions and two leaf
helpers are admitted. Unknown instructions, memory reads or writes fail closed.
The exact user's firmware is decoded with analyze-himax-nds32.py; no firmware
payload is distributed. Execution never contacts hardware.
"""
import importlib.util
import re
from pathlib import Path

GP = 0x4000c8e8
RECORDS = 0x100052dc
ENTRY = 0x4000ac8e
FIFO = 0x80060000
MASK = 0xffffffff


def load_instructions(blob):
    spec = importlib.util.spec_from_file_location('hx_nds', Path(__file__).with_name('analyze-himax-nds32.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    _, records, _ = mod.analyze(blob)
    return {r['pc']: r for r in records}


class ReportOracle:
    def __init__(self, instructions):
        self.code = instructions

    def run(self, contacts, count, metadata, swap=False, state_mode=False, selector=15,
            bitmap=0, status_flag=0x5a):
        if len(contacts) != 10 or not 0 <= count <= 10:
            raise ValueError('Only the qualified ten-slot report is supported')
        mem = {}
        def seed(addr, value, width):
            for i in range(width):
                mem[addr+i] = (value >> (8*i)) & 255
        for i, (x, y, width) in enumerate(contacts):
            seed(RECORDS+12*i, x, 4)
            seed(RECORDS+12*i+4, y, 4)
            seed(RECORDS+12*i+8, width, 1)
        seed(RECORDS+144, bitmap if state_mode else metadata, 2)
        seed(RECORDS+146, count, 1)
        seed(GP-654, 10, 1)
        seed(GP-652, 4 if swap else 0, 1)
        seed(GP-764, 2 if state_mode else 0, 1)
        seed(GP-1896, metadata, 2)
        seed(GP-2070, status_flag, 1)
        seed(0x100072ec, selector, 1)
        regs = dict(a0=RECORDS, gp=GP, lp=0xffffffff)
        fifo = bytearray()
        writes = []
        def val(s):
            if re.fullmatch(r'-?\d+|0x[0-9a-f]+', s):
                return int(s, 0) if s.startswith(('0x', '-0x')) else int(s)
            if s not in regs:
                raise ValueError(f'Uninitialized register {s} at {pc:#x}')
            return regs[s]
        def put(reg, value):
            regs[reg] = value & MASK
        def read(addr, width):
            if any(addr+i not in mem for i in range(width)):
                raise ValueError(f'Unseeded read {addr:#x}/{width} at {pc:#x}')
            return sum(mem[addr+i] << (8*i) for i in range(width))
        def write(addr, value, width):
            if addr == FIFO and width == 4:
                fifo.extend((value & MASK).to_bytes(4, 'little'))
            elif addr == GP-2078 and width == 1:
                seed(addr, value, width)
                writes.append((addr, value & 255))
            else:
                raise ValueError(f'Unexpected write {addr:#x}/{width} at {pc:#x}')
        pc, ifc, steps = ENTRY, None, 0
        while True:
            if not (ENTRY <= pc < 0x4000ade6 or 0x40009120 <= pc < 0x40009128
                    or 0x40008fca <= pc < 0x40008fd4 or 0x40009096 <= pc < 0x4000909e):
                raise ValueError(f'Execution left formatter scope: {pc:#x}')
            steps += 1
            if steps > 10000:
                raise ValueError('Formatter instruction budget exceeded')
            r = self.code[pc]
            asm = r.get('expanded_assembly', r['assembly'])
            op, _, tail = asm.partition(' ')
            args = tail.split(', ')
            nxt = pc + r['size']
            if op == 'push25':
                if pc != ENTRY:
                    raise ValueError('Unexpected nested stack frame')
            elif op == 'pop25':
                if pc != 0x4000ade4 or ifc is not None:
                    raise ValueError('Unexpected return state')
                if len(fifo) != 56:
                    raise ValueError(f'Unexpected FIFO byte count {len(fifo)}')
                return bytes(fifo), dict(steps=steps, writes=writes)
            elif op in ('nop',):
                pass
            elif op == 'jal':
                put('lp', nxt)
                nxt = int(args[0], 16)
            elif op == 'ret5':
                nxt = val('lp')
            elif op == 'ifcall9':
                if ifc is not None:
                    raise ValueError('Unexpected nested IFC')
                ifc, nxt = nxt, int(args[0], 16)
            elif op == 'ifret16':
                if ifc is not None:
                    nxt, ifc = ifc, None
            elif op == 'j8':
                nxt = int(args[0], 16)
            elif op in ('beqz38', 'beqzs8', 'bnezs8', 'bnec'):
                target = int(args[-1], 16)
                cond = (val(args[0]) == 0 if op == 'beqz38' else
                        val('ta') == 0 if op == 'beqzs8' else
                        val('ta') != 0 if op == 'bnezs8' else
                        val(args[0]) != val(args[1]))
                if cond:
                    nxt = target
            elif op in ('lbi.gp', 'lhi.gp'):
                put(args[0], read(GP + int(args[1][1:-1]), 1 if op == 'lbi.gp' else 2))
            elif op == 'sbi.gp':
                write(GP + int(args[1][1:-1]), val(args[0]), 1)
            elif op in ('lbi', 'lhi', 'lwi', 'lwi450', 'swi450'):
                match = re.fullmatch(r'\[(\w+)(?: \+ (-?\d+))?\]', args[1])
                if not match:
                    raise ValueError(f'Unsupported address syntax {asm}')
                addr = (val(match[1]) + int(match[2] or 0)) & MASK
                if op == 'swi450':
                    write(addr, val(args[0]), 4)
                else:
                    put(args[0], read(addr, {'lbi':1, 'lhi':2}.get(op, 4)))
            elif op in ('movi55', 'mov55'):
                put(args[0], val(args[1]))
            elif op == 'sethi':
                put(args[0], val(args[1]) << 12)
            elif op in ('zeb33', 'zeh33'):
                put(args[0], val(args[1]) & (255 if op == 'zeb33' else 65535))
            elif op in ('slt45', 'slti45'):
                put('ta', int(val(args[0]) < val(args[1])))
            elif op == 'btst':
                put(args[0], (val(args[1]) >> val(args[2])) & 1)
            elif op in ('addi45', 'add45', 'subi45'):
                put(args[0], val(args[0]) + (-1 if op == 'subi45' else 1)*val(args[1]))
            elif op in ('addi', 'addi333', 'add333'):
                put(args[0], val(args[1]) + val(args[2]))
            elif op == 'subri':
                put(args[0], val(args[2]) - val(args[1]))
            elif op in ('ori', 'andi'):
                put(args[0], val(args[1]) | val(args[2]) if op == 'ori'
                    else val(args[1]) & val(args[2]))
            elif op == 'min':
                put(args[0], min(val(args[1]), val(args[2])))
            elif op in ('slli', 'srli'):
                put(args[0], val(args[1]) << val(args[2]) if op == 'slli'
                    else val(args[1]) >> val(args[2]))
            elif op in ('add_slli', 'or_slli', 'add_srli', 'or_srli'):
                shifted = (val(args[2]) << val(args[3]) if op.endswith('slli')
                           else val(args[2]) >> val(args[3]))
                put(args[0], val(args[1]) + shifted if op.startswith('add')
                    else val(args[1]) | shifted)
            elif op == 'maddr32':
                put(args[0], val(args[0]) + val(args[1])*val(args[2]))
            else:
                raise ValueError(f'Unsupported formatter instruction {pc:#x}: {asm}')
            pc = nxt
