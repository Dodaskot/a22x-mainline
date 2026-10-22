#!/usr/bin/env python3
"""Run private stock formatter AND new C encoder on Andes' CPU simulator.

No device access. Requires the exact private firmware and historical official
Andes NDS32 V3 toolchain. Isolated temporary ELF and RAM inputs are deleted.
"""
import argparse
import hashlib
import json
import random
import re
import struct
import subprocess
import tempfile
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
SHA='fd4df971965dfe5e550ba4a6a9e94e1b67d5ca128dad3eb74ebaa1873d591f1a'


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--firmware',type=Path,required=True)
    ap.add_argument('--toolchain',type=Path,required=True,help='Directory containing bin/nds32le-elf-gcc and gdb')
    ap.add_argument('--receipt',type=Path)
    a=ap.parse_args();blob=a.firmware.read_bytes()
    if hashlib.sha256(blob).hexdigest()!=SHA:raise ValueError('Unqualified private firmware SHA')
    tc=a.toolchain.resolve()/'bin'
    def tool(name):return str(tc/('nds32le-elf-'+name))
    def run(cmd):return subprocess.run(cmd,check=True,text=True,capture_output=True,timeout=30)
    with tempfile.TemporaryDirectory(prefix='himax-vendor-sim-') as td:
        p=Path(td);(p/'firmware.bin').write_bytes(blob[0x400:0x10400])
        (p/'firmware.S').write_text('.section .firmware,"ax"\n.global firmware_entry\nfirmware_entry:\n.incbin "'+str(p/'firmware.bin')+'"\n')
        (p/'firmware.ld').write_text('ENTRY(firmware_entry)\nSECTIONS { . = 0x40000000; .firmware : { *(.firmware) } }\n')
        run([tool('as'),'-EL','-march=v3','-o',str(p/'firmware.o'),str(p/'firmware.S')])
        run([tool('ld'),'--no-relax','-T',str(p/'firmware.ld'),'-o',str(p/'firmware.elf'),str(p/'firmware.o')])
        (p/'harness.c').write_text('''#include "report.h"
#include <stddef.h>
struct input { struct hx_contact contacts[10]; unsigned count; unsigned short metadata;
               unsigned tag; int swap; unsigned release; };
_Static_assert(sizeof(struct hx_contact)==6,"contact ABI");
_Static_assert(offsetof(struct input,count)==60,"count ABI");
_Static_assert(offsetof(struct input,tag)==68,"tag ABI");
volatile struct input test_input;
unsigned char test_output[56];
int test_entry(void) {
 if(test_input.release) return hx_report_release(test_output,56);
 return hx_report_encode(test_output,56,(const struct hx_contact*)test_input.contacts,
   test_input.count,test_input.metadata,test_input.tag,test_input.swap);
}
''')
        (p/'open.ld').write_text('ENTRY(test_entry)\nSECTIONS { . = 0x40020000; .text : { *(.text*) *(.rodata*) } . = ALIGN(4096); .data : { *(.data*) } .bss : { *(.bss*) *(COMMON) } }\n')
        flags=['-std=c11','-EL','-march=v3','-mabi=2','-mno-ex9','-mno-ifc','-mno-fp-as-gp',
               '-mcmodel=large','-ffreestanding','-fno-builtin','-fno-common','-fno-stack-protector',
               '-Os','-Wall','-Wextra','-Werror','-I'+str(ROOT/'firmware/himax')]
        for source,name in [(ROOT/'firmware/himax/report.c','report'),(p/'harness.c','harness')]:
            run([tool('gcc'),*flags,'-c',str(source),'-o',str(p/(name+'.o'))])
        run([tool('ld'),'--no-relax','-T',str(p/'open.ld'),'-o',str(p/'open.elf'),str(p/'report.o'),str(p/'harness.o')])
        if run([tool('nm'),'-u',str(p/'open.elf')]).stdout.strip():raise ValueError('Unresolved open encoder symbols')
        run([tool('objcopy'),'-O','binary',str(p/'open.elf'),str(p/'open.bin')])
        symbols={line.split()[2]:int(line.split()[0],16) for line in run([tool('nm'),str(p/'open.elf')]).stdout.splitlines() if len(line.split())==3}
        rng=random.Random(0x83112f);cases=[]
        for i in range(128):
            contacts=[(rng.getrandbits(32),rng.getrandbits(32),rng.randrange(256)) for _ in range(10)]
            if i==0:contacts=[(0xffffffff,0xffffffff,0)]*10
            if i==1:contacts=[(0,0,255)]*10
            cases.append((contacts,i%11,rng.randrange(65536),bool(i&1),bool(i&2),rng.randrange(256),False))
        cases.append(([(0,0,0)]*10,0,0,False,False,0,True))
        for i,(contacts,count,metadata,swap,state,selector,release) in enumerate(cases):
            mem=bytearray(65536);gp=bytearray(4096);cinput=bytearray(80)
            for j,(x,y,w) in enumerate(contacts):
                struct.pack_into('<IIB',mem,0x52dc+j*12,x,y,w)
                struct.pack_into('<HHB',cinput,j*6,x&65535,y&65535,w)
            struct.pack_into('<HB',mem,0x52dc+144,0x155 if state else metadata,count)
            mem[0x72ec]=selector
            for off,value in [(-654,10),(-652,4 if swap else 0),(-764,2 if state else 0),(-2070,90)]:gp[0x8e8+off]=value
            struct.pack_into('<H',gp,0x8e8-1896,metadata)
            struct.pack_into('<IH2xIiI',cinput,60,count,metadata,min(selector,15) if state else 15,int(swap),int(release))
            (p/'data.bin').write_bytes(mem);(p/'globals.bin').write_bytes(gp);(p/'input.bin').write_bytes(cinput)
            script=f'''set pagination off
set confirm off
file {p}/firmware.elf
target sim --memory-region 0x40000000,0x50000 --memory-region 0x10000000,0x10000 --memory-region 0x80060000,0x100
load
b *0x40000000
run
delete 1
restore {p}/data.bin binary 0x10000000
restore {p}/globals.bin binary 0x4000c000
set $gp=0x4000c8e8
set $fp=0x4000c8e8
set $sp=0x40010000
set $itb=0x4000b918
set $r0=0x100052dc
set $lp=0x40011000
set $pc={hex(0x4000ac46 if release else 0x4000ac8e)}
b *0x40009120
commands
silent
printf "FIFO %08x\\n", $r0
continue
end
b *0x40011000
continue
printf "STOCK_DONE %08x\\n", $pc
delete 2
restore {p}/open.bin binary 0x40020000
restore {p}/input.bin binary {hex(symbols['test_input'])}
set $sp=0x40040000
set $lp=0x40011000
set $pc={hex(symbols['test_entry'])}
continue
printf "OPEN_DONE %08x %u\\n", $pc, $r0
dump binary memory {p}/output.bin {hex(symbols['test_output'])} {hex(symbols['test_output']+56)}
quit
'''
            (p/'run.gdb').write_text(script)
            result=run([tool('gdb'),'-nx','-nh','--batch','-x',str(p/'run.gdb')])
            if 'STOCK_DONE 40011000' not in result.stdout or 'OPEN_DONE 40011000 56' not in result.stdout:
                raise ValueError(f'Simulator did not reach verified return: {result.stdout[-1800:]} {result.stderr[-500:]}')
            words=re.findall(r'^FIFO ([0-9a-f]{8})$',result.stdout,re.M)
            expected=b''.join(int(word,16).to_bytes(4,'little') for word in words)
            actual=(p/'output.bin').read_bytes()
            if len(expected)!=56 or actual!=expected or sum(actual)%256:
                raise ValueError(f'Case {i}: stock {expected.hex()} open {actual.hex()}')
        receipt=dict(result='PASS',vendor_simulator_cases=len(cases),stock_firmware_sha256=SHA,
                     compiler=run([tool('gcc'),'--version']).stdout.splitlines()[0],
                     gdb_sha256=hashlib.sha256(Path(tool('gdb')).read_bytes()).hexdigest(),
                     source_sha256=hashlib.sha256((ROOT/'firmware/himax/report.c').read_bytes()).hexdigest(),
                     open_nds32_elf_sha256=hashlib.sha256((p/'open.elf').read_bytes()).hexdigest(),
                     scope='stock formatter and independently compiled C encoder on vendor CPU simulator',
                     release_packet_verified=True,hardware_test=False)
        print(json.dumps(receipt,indent=2))
        if a.receipt:a.receipt.write_text(json.dumps(receipt,indent=2)+'\n')


if __name__=='__main__':main()
