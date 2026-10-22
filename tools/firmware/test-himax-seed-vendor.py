#!/usr/bin/env python3
"""Compare ordinary stock threshold classification and compiled NDS32 C.

The stock run disables dense-frame early exit and padded-edge postprocessing.
This test does not execute analog acquisition or the later flood-fill stage.
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

SHA='fd4df971965dfe5e550ba4a6a9e94e1b67d5ca128dad3eb74ebaa1873d591f1a'
GP=0x4000c8e8


def vectors():
    cases=[]
    for signal,threshold,per_cell in [([0]*576,0,None),([1]*576,0,None),
        ([100]*576,0,None),([32767]*576,32767,None),([-32768]*576,0,None),
        ([32767]*576,65535,None),([100,101,99,-1]*144,100,None),
        ([1]*576,65535,[0,1,2,65535]*144)]:
        cases.append(dict(signal=signal,threshold=threshold,per_cell=per_cell))
    for above in (149,150,151):
        cases.append(dict(signal=[1]*above+[0]*(576-above),threshold=0,per_cell=None))
    rng=random.Random(5564)
    for _ in range(56):
        # Mix dense, sparse and threshold-tie inputs, with both threshold modes.
        signal=[rng.randrange(-1000,2001) for _ in range(576)]
        threshold=rng.choice([0,100,1000,1900,32767,65535])
        per_cell=[rng.choice([0,100,1000,2000,65535]) for _ in range(576)] if rng.randrange(2) else None
        cases.append(dict(signal=signal,threshold=threshold,per_cell=per_cell))
    return cases


def simulate(prefix,elf,stock,cases,temp):
    tag='stock' if stock else 'open'
    entry='0x40000000' if stock else 'seed_test_entry'
    commands=['set pagination off','set confirm off',f'file "{elf}"',
        'target sim --memory-region 0x40000000,0x20000 --memory-region 0x10000000,0x10000',
        'load',f'break *{entry}','run','delete 1','break *0x40011000']
    commands += ['set $gp=0x4000c8e8','set $itb=0x4000b918'] if stock else ['set $gp=&_SDA_BASE_']
    for i,c in enumerate(cases):
        data=bytearray(65536)
        struct.pack_into('<576h',data,0x37c4,*c['signal'])
        struct.pack_into('<H',data,0x7000,c['threshold'])
        data[0x7408]=int(c['per_cell'] is not None)
        if c['per_cell'] is not None: struct.pack_into('<576H',data,0x600,*c['per_cell'])
        path=temp/f'input-{i}.bin';path.write_bytes(data)
        commands += [f'restore {path} binary 0x10000000','set $sp=0x40010000',
            'set $lp=0x40011000']
        if stock:
            # Ordinary full-scan mode; these omitted policies are documented.
            globals_=bytearray(0x4000)
            struct.pack_into('<H',globals_,GP-2034-0x4000c000,c['threshold'])
            globals_[GP-2157-0x4000c000]=255
            globals_[GP-652-0x4000c000]=0
            path=temp/f'globals-{i}.bin';path.write_bytes(globals_)
            commands += [f'restore {path} binary 0x4000c000','set $pc=0x40005564','continue',
                f'dump binary memory {temp}/stock-labels-{i}.bin {GP+864:#x} {GP+864+1368:#x}',
                f'dump binary memory {temp}/stock-seeds-{i}.bin 0x10001bdc 0x10001d08',
                f'printf "COUNT {i} %u\\n", *(unsigned short*)0x100052d4']
        else:
            commands += ['set $pc=seed_test_entry','continue',
                f'dump binary memory {temp}/open-result-{i}.bin 0x10008000 0x100085ae',
                f'printf "COUNT {i} %u\\n", *(unsigned short*)0x100085ac',
                f'printf "STATUS {i} %d\\n", $r0']
    commands+=['quit']
    script=temp/f'{tag}.gdb';script.write_text('\n'.join(commands)+'\n')
    run=subprocess.run([prefix+'gdb','-q','-batch','-x',str(script)],capture_output=True,text=True,timeout=90)
    text=run.stdout+run.stderr;(temp/f'{tag}.log').write_text(text)
    if run.returncode: raise RuntimeError(f'{tag}: '+text[-1500:])
    counts={int(i):int(count) for i,count in re.findall(r'COUNT (\d+) (\d+)',text)}
    if set(counts)!=set(range(len(cases))): raise RuntimeError('Missing seed cases')
    if not stock:
        statuses=re.findall(r'STATUS (\d+) (-?\d+)',text)
        if len(statuses)!=len(cases) or any(int(status) for _,status in statuses):
            raise RuntimeError('Open classifier returned failure')
    results=[]
    for i,c in enumerate(cases):
        if stock:
            padded=struct.unpack('<684H',(temp/f'stock-labels-{i}.bin').read_bytes())
            labels=[padded[(row+1)*38+col+1] for row in range(16) for col in range(36)]
            seeds=(temp/f'stock-seeds-{i}.bin').read_bytes()[:2*counts[i]]
        else:
            result=(temp/f'open-result-{i}.bin').read_bytes()
            labels=list(struct.unpack_from('<576H',result))
            seeds=result[1152:1152+2*counts[i]]
        results.append(dict(count=counts[i],labels=labels,seeds=list(seeds)))
    return results


def peak_vectors():
    cases=[dict(nodes=[],labels=[253]*576,weights=[0]*684)]
    tied=dict(nodes=[(2,5),(1,1),(5,10)],labels=[253]*576,weights=[0]*684)
    for row,col,weight in [(2,5,100),(1,1,100),(5,10,99)]:
        tied['labels'][(row-1)*36+col-1]=254
        tied['weights'][row*38+col]=weight
    cases.append(tied)
    rng=random.Random(5984)
    for _ in range(62):
        cases.append(dict(nodes=[(rng.randrange(1,17),rng.randrange(1,37))
            for _ in range(rng.randrange(151))],
            labels=[rng.choice([1,253,254]) for _ in range(576)],
            weights=[rng.choice([-32768,-1,0,1,100,32767]) for _ in range(684)]))
    return cases


def simulate_peaks(prefix,elf,stock,cases,temp):
    tag='peak-stock' if stock else 'peak-open'
    entry='0x40000000' if stock else 'seed_test_entry'
    stop='0x40005a4a' if stock else '0x40011000'
    commands=['set pagination off','set confirm off',f'file "{elf}"',
        'target sim --memory-region 0x40000000,0x20000 --memory-region 0x10000000,0x10000',
        'load',f'break *{entry}','run','delete 1',f'break *{stop}']
    commands+=['set $gp=0x4000c8e8','set $itb=0x4000b918'] if stock else ['set $gp=&_SDA_BASE_']
    for i,c in enumerate(cases):
        data=bytearray(65536)
        struct.pack_into('<684h',data,0x41e4,*c['weights'])
        struct.pack_into('<576H',data,0x8000,*c['labels'])
        nodes=bytes(v for pair in c['nodes'] for v in pair)
        data[0x8480:0x8480+len(nodes)]=nodes
        data[0x1bdc:0x1bdc+len(nodes)]=nodes
        struct.pack_into('<H',data,0x85ac,len(c['nodes']))
        path=temp/f'peak-input-{i}.bin';path.write_bytes(data)
        commands += [f'restore {path} binary 0x10000000','set $sp=0x40010000',
                     'set $lp=0x40011000']
        if stock:
            labels=[0]*684
            for row in range(16):
                for col in range(36): labels[(row+1)*38+col+1]=c['labels'][row*36+col]
            path=temp/f'peak-labels-{i}.bin';path.write_bytes(struct.pack('<684H',*labels))
            commands += [f'restore {path} binary {GP+864:#x}','set $r0=0','set $r1=0',
                f'set $r5={len(c["nodes"])}','set $r6=0','set $r7=0',
                'set $r9=0x10001bdc',f'set $r10={GP+864:#x}','set $r11=0x10001e70',
                'set $pc=0x40005a14','continue',
                f'printf "PEAK {i} %u %u %u\\n", $r7, $r6, $r1']
        else:
            commands += ['set $pc=peak_test_entry','continue',
                f'printf "PEAK {i} %u %u %u\\n", *(unsigned char*)0x10009000, '
                '*(unsigned char*)0x10009001, *(unsigned short*)0x10009002',
                f'printf "STATUS {i} %d\\n", $r0']
    commands+=['quit']
    script=temp/f'{tag}.gdb';script.write_text('\n'.join(commands)+'\n')
    run=subprocess.run([prefix+'gdb','-q','-batch','-x',str(script)],capture_output=True,text=True,timeout=60)
    text=run.stdout+run.stderr;(temp/f'{tag}.log').write_text(text)
    if run.returncode: raise RuntimeError(f'{tag}: '+text[-1500:])
    found=re.findall(r'PEAK (\d+) (\d+) (\d+) (\d+)',text)
    if len(found)!=len(cases): raise RuntimeError('Missing peak comparisons: '+text[-1500:])
    results={int(i):(int(row),int(col),int(weight)) for i,row,col,weight in found}
    if not stock:
        statuses={int(i):int(status) for i,status in re.findall(r'STATUS (\d+) (-?\d+)',text)}
        assert len(statuses)==len(cases)
        for i,peak in results.items(): assert statuses[i]==(0 if peak[2] else 1)
    return results


def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--firmware',type=Path,required=True)
    ap.add_argument('--toolchain-prefix',required=True)
    ap.add_argument('--output-dir',type=Path)
    args=ap.parse_args()
    blob=args.firmware.read_bytes()
    if hashlib.sha256(blob).hexdigest()!=SHA: raise ValueError('Unsupported firmware SHA256')
    root=Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory(prefix='hx-seed-vendor-') as td:
        temp=Path(td);prefix=args.toolchain_prefix
        (temp/'code.bin').write_bytes(blob[0x400:0x10400])
        (temp/'stock.S').write_text('.section .firmware,"ax"\n.global firmware_entry\nfirmware_entry:\n.incbin "code.bin"\n')
        (temp/'stock.ld').write_text('ENTRY(firmware_entry)\nSECTIONS { . = 0x40000000; .firmware : { *(.firmware) } }\n')
        subprocess.run([prefix+'gcc','-c','stock.S','-o','stock.o'],cwd=temp,check=True)
        subprocess.run([prefix+'gcc','-nostdlib','-Wl,-T,stock.ld','stock.o','-o','stock.elf'],cwd=temp,check=True)
        (temp/'harness.c').write_text('''#include "seed.h"
int seed_test_entry(void) {
 int mode=*(volatile uint8_t *)0x10007408;
 return hx_seed_extract((struct hx_seed_result *)0x10008000,
   (const int16_t *)0x100037c4,576,*(volatile uint16_t *)0x10007000,
   mode?(const uint16_t *)0x10000600:0,mode?576:0);
}
int peak_test_entry(void) {
 return hx_seed_pick((struct hx_seed_peak *)0x10009000,
   (const struct hx_seed_result *)0x10008000,(const int16_t *)0x100041e4,684);
}
''')
        subprocess.run([prefix+'gcc','-std=c99','-Os','-Wall','-Wextra','-Werror',
            '-ffreestanding','-fno-builtin','-nostdlib','-Wl,-e,seed_test_entry',
            '-Wl,-Ttext=0x40000000','-I',str(root/'firmware/himax'),
            str(root/'firmware/himax/seed.c'),str(temp/'harness.c'),
            '-o',str(temp/'open.elf')],check=True)
        cases=vectors()
        stock=simulate(prefix,temp/'stock.elf',True,cases,temp)
        opened=simulate(prefix,temp/'open.elf',False,cases,temp)
        results=[]
        for i,c in enumerate(cases):
            if stock[i]!=opened[i]:
                raise AssertionError((i,'stock/open seed mismatch',stock[i]['count'],opened[i]['count']))
            selected=[j for j,value in enumerate(c['signal'])
                if value>(c['per_cell'][j] if c['per_cell'] is not None else c['threshold'])]
            expected=[value for j in selected[:150] for value in (j//36+1,j%36+1)]
            assert stock[i]['seeds']==expected
            assert stock[i]['count']==min(150,len(selected))
            results.append(dict(case=i,per_cell=c['per_cell'] is not None,
                scalar_threshold=c['threshold'],above_threshold=len(selected),
                returned_count=stock[i]['count'],labels_sha256=hashlib.sha256(
                    struct.pack('<576H',*stock[i]['labels'])).hexdigest(),passed=True))
        peaks=peak_vectors()
        stock_peaks=simulate_peaks(prefix,temp/'stock.elf',True,peaks,temp)
        open_peaks=simulate_peaks(prefix,temp/'open.elf',False,peaks,temp)
        peak_results=[]
        for i,c in enumerate(peaks):
            assert stock_peaks[i]==open_peaks[i],(i,stock_peaks[i],open_peaks[i])
            best=(0,0,0)
            for row,col in c['nodes']:
                weight=c['weights'][row*38+col]
                if c['labels'][(row-1)*36+col-1]==254 and weight>best[2]:
                    best=(row,col,weight)
            assert best==stock_peaks[i]
            peak_results.append(dict(case=i,seed_count=len(c['nodes']),peak=best,passed=True))
        if args.output_dir:
            args.output_dir.mkdir(parents=True,exist_ok=True)
            (args.output_dir/'results.json').write_text(json.dumps(results,indent=2)+'\n')
            (args.output_dir/'peak-results.json').write_text(json.dumps(peak_results,indent=2)+'\n')
            for tag in ('stock','open','peak-stock','peak-open'):
                (args.output_dir/f'{tag}.log').write_text((temp/f'{tag}.log').read_text())
        print(f'PASS {len(cases)} stock versus compiled-open seed matrices, ordinary mode; labels and row-major capped seeds match')
        print(f'PASS {len(peaks)} stock versus compiled-open peak selections, stable ties and positive/unvisited gating')


if __name__=='__main__': main()
