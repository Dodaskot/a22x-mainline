#!/usr/bin/env python3
"""Compare stock/open CPU MMIO traces with explicitly scripted completion.

This is a simulated peripheral. It does not validate a physical engine, its
timing, addresses or safety. Requires the exact user's blob and NDS32 v3 tools.
"""
import argparse
import hashlib
import json
import random
import re
import subprocess
import tempfile
from pathlib import Path

SHA='fd4df971965dfe5e550ba4a6a9e94e1b67d5ca128dad3eb74ebaa1873d591f1a'
STORES=[(0x4000904c,0x800300c8,4,'$r1'),(0x40009050,0x800300d4,4,'$r1'),
    (0x40009058,0x80030034,1,'($r1&255)'),(0x4000905c,0x80030038,1,'($r1&255)'),
    (0x40009062,0x800300c0,1,'$r1'),(0x4000906c,0x800300b8,2,'($r1&65535)'),
    (0x40009074,0x800300bc,2,'($r2&65535)'),(0x40009078,0x80030000,1,'$r1')]


def simulate(prefix,elf,stock,cases,temp):
    entry='0x40000000' if stock else 'raw_test_entry'
    commands=['set pagination off','set confirm off',f'file "{elf}"',
        'target sim --memory-region 0x40000000,0x20000 --memory-region 0x10000000,0x10000 --memory-region 0x80030000,0x1000',
        'load',f'break *{entry}','run','delete 1']
    if stock:
        commands+=['set $gp=0x4000c8e8','set $itb=0x4000b918']
        for pc,address,width,value in STORES:
            commands += [f'break *{pc:#x}','commands','silent',
                f'printf "TRACE W {address:08x} {width} %08x\\n", {value}',
                'continue','end']
        commands+=['break *0x4000907a']
        read_address='$r0'
    else:
        commands+=['set $gp=&_SDA_BASE_','break sim_write','commands','silent',
            'printf "TRACE W %08x %u %08x\\n", $r1, $r2, $r3',
            'continue','end','break sim_read']
        read_address='$r1'
    commands+=['commands','silent','set $busy=($reads<$busy_reads)?1:0',
        'set {unsigned char}0x80030000=$busy',
        f'printf "TRACE R %08x 1 %02x\\n", {read_address}, $busy',
        'set $reads=$reads+1','continue','end','break *0x40011000']
    for i,c in enumerate(cases):
        commands += [f'printf "CASE {i}\\n"','set $sp=0x40010000',
            'set $lp=0x40011000','set $reads=0',f'set $busy_reads={c["busy"]}']
        if stock:
            commands += [f'set $r1={c["parameter"]}',f'set $r2={c["destination"]}',
                         'set $pc=0x40009048']
        else:
            commands += [f'set {{unsigned int}}0x10001000={c["parameter"]}',
                f'set {{unsigned int}}0x10001004={c["destination"]}',
                f'set {{unsigned int}}0x10001008={c["busy"]+1}',
                'set $pc=raw_test_entry']
        commands+=['continue',f'printf "DONE {i} %u\\n", $reads']
        commands += [f'printf "MEM {i} %08x %08x %02x %02x %02x %04x %04x %02x\\n", '
            '*(unsigned int*)0x800300c8, *(unsigned int*)0x800300d4, '
            '*(unsigned char*)0x80030034, *(unsigned char*)0x80030038, '
            '*(unsigned char*)0x800300c0, *(unsigned short*)0x800300b8, '
            '*(unsigned short*)0x800300bc, *(unsigned char*)0x80030000']
        if not stock: commands += [f'printf "STATUS {i} %d\\n", $r0']
    commands+=['quit']
    tag='stock' if stock else 'open'
    script=temp/f'{tag}.gdb';script.write_text('\n'.join(commands)+'\n')
    run=subprocess.run([prefix+'gdb','-q','-batch','-x',str(script)],
        capture_output=True,text=True,timeout=60)
    text=run.stdout+run.stderr
    (temp/f'{tag}.log').write_text(text)
    if run.returncode: raise RuntimeError(f'{tag}: '+text[-1500:])
    records={};snapshots={};current=None;completed=[]
    for line in text.splitlines():
        if line.startswith('CASE '): current=int(line.split()[1]);records[current]=[]
        m=re.fullmatch(r'TRACE ([WR]) ([0-9a-f]+) (\d+) ([0-9a-f]+)',line)
        if m:
            assert current is not None
            records[current].append((m[1],int(m[2],16),int(m[3]),int(m[4],16)))
        if line.startswith('DONE '): completed.append(int(line.split()[1]))
        if line.startswith('MEM '):
            parts=line.split()
            snapshots[int(parts[1])]=tuple(int(value,16) for value in parts[2:])
    if completed!=list(range(len(cases))): raise RuntimeError(f'{tag}: incomplete CPU run')
    if not stock:
        statuses=re.findall(r'STATUS (\d+) (-?\d+)',text)
        if len(statuses)!=len(cases) or any(int(status) for _,status in statuses):
            raise RuntimeError('Open submission failed')
    if len(snapshots)!=len(cases): raise RuntimeError('Missing post-store MMIO snapshots')
    for i,c in enumerate(cases):
        expected=(c['parameter'],c['parameter'],0xd8,0xd8,0,0x5be0,c['destination']&65535,0)
        assert snapshots[i]==expected,(tag,i,snapshots[i],expected)
    return records


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--firmware',type=Path,required=True)
    ap.add_argument('--toolchain-prefix',required=True)
    ap.add_argument('--output-dir',type=Path)
    args=ap.parse_args()
    blob=args.firmware.read_bytes()
    if hashlib.sha256(blob).hexdigest()!=SHA: raise ValueError('Unsupported firmware SHA256')
    root=Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory(prefix='hx-engine-vendor-') as td:
        temp=Path(td);prefix=args.toolchain_prefix
        (temp/'code.bin').write_bytes(blob[0x400:0x10400])
        (temp/'stock.S').write_text('.section .firmware,"ax"\n.global firmware_entry\nfirmware_entry:\n.incbin "code.bin"\n')
        (temp/'stock.ld').write_text('ENTRY(firmware_entry)\nSECTIONS { . = 0x40000000; .firmware : { *(.firmware) } }\n')
        subprocess.run([prefix+'gcc','-c','stock.S','-o','stock.o'],cwd=temp,check=True)
        subprocess.run([prefix+'gcc','-nostdlib','-Wl,-T,stock.ld','stock.o','-o','stock.elf'],cwd=temp,check=True)
        (temp/'harness.c').write_text('''#include "raw-engine.h"
__attribute__((noinline)) int sim_write(void *ctx,uint32_t a,unsigned w,uint32_t v) {
 (void)ctx;
 if(w==1) *(volatile uint8_t *)(uintptr_t)a=(uint8_t)v;
 else if(w==2) *(volatile uint16_t *)(uintptr_t)a=(uint16_t)v;
 else if(w==4) *(volatile uint32_t *)(uintptr_t)a=v;
 else return -1;
 return 0;
}
__attribute__((noinline)) int sim_read(void *ctx,uint32_t a,uint8_t *v) {
 (void)ctx; *v=*(volatile uint8_t *)(uintptr_t)a; return 0;
}
int raw_test_entry(void) {
 const struct hx_raw_engine_io io={0,sim_write,sim_read};
 volatile uint32_t *in=(volatile uint32_t *)0x10001000;
 return hx_raw_engine_submit(&io,in[0],in[1],in[2]);
}
''')
        subprocess.run([prefix+'gcc','-std=c99','-Os','-Wall','-Wextra','-Werror',
            '-ffreestanding','-fno-builtin','-nostdlib','-Wl,-e,raw_test_entry',
            '-Wl,-Ttext=0x40000000','-I',str(root/'firmware/himax'),
            str(root/'firmware/himax/raw-engine.c'),str(temp/'harness.c'),
            '-o',str(temp/'open.elf')],check=True)
        cases=[dict(parameter=p,destination=d,busy=b) for p,d,b in
            [(0,0x100060f0,0),(0xffffffff,0xffffffff,1),(0x80000000,0x12340000,4),
             (0x12345678,0x123460f0,2)]]
        rng=random.Random(9048)
        for _ in range(60): cases.append(dict(parameter=rng.getrandbits(32),
            destination=rng.getrandbits(32),busy=rng.randrange(5)))
        stock=simulate(prefix,temp/'stock.elf',True,cases,temp)
        opened=simulate(prefix,temp/'open.elf',False,cases,temp)
        results=[]
        for i,c in enumerate(cases):
            assert stock[i]==opened[i],(i,c,stock[i],opened[i])
            assert len(stock[i])==8+c['busy']+1
            results.append(dict(case=i,inputs=c,trace=stock[i],passed=True))
        if args.output_dir:
            args.output_dir.mkdir(parents=True,exist_ok=True)
            (args.output_dir/'results.json').write_text(json.dumps(results,indent=2)+'\n')
            for tag in ('stock','open'):
                (args.output_dir/f'{tag}.log').write_text((temp/f'{tag}.log').read_text())
        print(f'PASS {len(cases)} stock versus compiled-open CPU traces; MMIO completion explicitly stubbed')


if __name__=='__main__': main()
