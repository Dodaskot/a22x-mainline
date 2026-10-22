#!/usr/bin/env python3
"""Execute stock and compiled-open centroid blocks in the Andes GDB simulator.

Requires the user's exact supported firmware and historical NDS32 v3 toolchain.
Generated firmware objects/disassembly are never added to the repository.
"""
import argparse
import hashlib
import json
import random
import re
import subprocess
import tempfile
from pathlib import Path

SHA = 'fd4df971965dfe5e550ba4a6a9e94e1b67d5ca128dad3eb74ebaa1873d591f1a'


def vectors():
    cases = []
    for weight, col, row in [(1,1,1), (1,36,16), (1000,1000,1000),
                             (1000,36000,16000), (5,90,40),
                             (1,0xffffffff,0xffffffff), (1000000,18000000,8000000)]:
        for ic, ir in [(0,0),(1,0),(0,1),(1,1)]:
            cases.append(dict(weight=weight,col=col,row=row,inva=ic,invb=ir))
    rng = random.Random(83112)
    for _ in range(36):
        cases.append(dict(weight=rng.randint(1,1<<24),col=rng.getrandbits(32),
                          row=rng.getrandbits(32),inva=rng.randrange(2),invb=rng.randrange(2)))
    # Exercise post-division low32 storage before clipping, including moments
    # that are deliberately outside the physical row/column-weight domain.
    for weight in (1,2,10):
        col_center=(1<<32)*36*weight//2408
        row_center=(1<<32)*16*weight//1080
        for delta in range(-3,6):
            for col,row in [(col_center+delta,1),(1,row_center+delta)]:
                for ic,ir in [(0,0),(1,0),(0,1),(1,1)]:
                    cases.append(dict(weight=weight,col=col,row=row,inva=ic,invb=ir))
    return cases


def run_sim(prefix, elf, cases, stock, temp):
    entry = '0x40000000' if stock else 'hx_signal_scale'
    stop = '0x40002520' if stock else '0x40011000'
    commands = ['set pagination off','set confirm off',f'file "{elf}"',
        'target sim --memory-region 0x40000000,0x20000 --memory-region 0x10000000,0x10000',
        'load',f'break *{entry}','run','delete 1','set $gp=0x4000c8e8',
        'set $itb=0x4000b918',f'break *{stop}']
    if not stock:
        commands += ['set $gp=&_SDA_BASE_']
    for i,c in enumerate(cases):
        commands += ['set $sp=0x40010000','set $lp=0x40011000',
            f'set {{unsigned int}}0x10001000={c["col"]}',
            f'set {{unsigned int}}0x10001004={c["row"]}',
            f'set {{unsigned int}}0x10001008={c["weight"]}']
        if stock:
            commands += ['set $r0=0','set $r1=0','set $r6=0x10001b1c',
                'set $r7=0x10001b1c','set $r8=2407','set $r10=1','set $r11=0',
                'set $r12=2408','set $r13=1080','set $r14=0x10001000',
                f'set {{unsigned int}}0x40010000={c["inva"]}',
                f'set {{unsigned int}}0x40010004={c["invb"]}',
                'set $pc=0x4000248c','continue',
                f'printf "RESULT {i} %u %u\\n", *(unsigned int*)0x10001b1c, *(unsigned int*)0x10001b20']
        else:
            commands += ['set $r0=0x10000000','set $r1=0x10001000',
                f'set $r2={c["inva"]}',f'set $r3={c["invb"]}',
                'set $pc=hx_signal_scale','continue',
                f'printf "RESULT {i} %u %u\\n", *(unsigned short*)0x10000000, *(unsigned short*)0x10000002',
                f'printf "STATUS {i} %d\\n", $r0']
    commands += ['quit']
    tag = 'stock' if stock else 'open'
    script = temp/f'{tag}.gdb'
    script.write_text('\n'.join(commands)+'\n')
    run = subprocess.run([prefix+'gdb','-q','-batch','-x',str(script)],
                         capture_output=True,text=True,timeout=60)
    (temp/f'{tag}.log').write_text(run.stdout+run.stderr)
    if run.returncode:
        raise RuntimeError(f'{tag} simulator failed: '+(run.stdout+run.stderr)[-1200:])
    matches = re.findall(r'RESULT (\d+) (\d+) (\d+)',run.stdout)
    results = {int(i):(int(a),int(b)) for i,a,b in matches}
    if len(matches)!=len(cases) or set(results)!=set(range(len(cases))):
        raise RuntimeError(f'{tag} simulator did not complete every case')
    if not stock:
        statuses = re.findall(r'STATUS (\d+) (-?\d+)',run.stdout)
        if len(statuses)!=len(cases) or any(int(rc) for _,rc in statuses):
            raise RuntimeError('Open C returned a failure status')
    return results


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--firmware',required=True,type=Path)
    ap.add_argument('--toolchain-prefix',required=True,
                    help='Path/prefix ending in nds32le-elf-')
    ap.add_argument('--output-dir',type=Path,
                    help='Optional directory for synthetic results and simulator logs')
    args = ap.parse_args()
    blob = args.firmware.read_bytes()
    if hashlib.sha256(blob).hexdigest()!=SHA:
        raise ValueError('Unsupported firmware SHA256; offsets are image-specific')
    root = Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory(prefix='hx-signal-vendor-') as tmp:
        temp = Path(tmp)
        (temp/'code.bin').write_bytes(blob[0x400:0x10400])
        (temp/'stock.S').write_text('.section .firmware,"ax"\n.global firmware_entry\nfirmware_entry:\n.incbin "code.bin"\n')
        (temp/'stock.ld').write_text('ENTRY(firmware_entry)\nSECTIONS { . = 0x40000000; .firmware : { *(.firmware) } }\n')
        prefix=args.toolchain_prefix
        subprocess.run([prefix+'gcc','-c','stock.S','-o','stock.o'],cwd=temp,check=True)
        subprocess.run([prefix+'gcc','-nostdlib','-Wl,-T,stock.ld','stock.o','-o','stock.elf'],cwd=temp,check=True)
        subprocess.run([prefix+'gcc','-std=c99','-Os','-Wall','-Wextra','-Werror',
            '-ffreestanding','-fno-builtin','-nostdlib','-Wl,-e,hx_signal_scale',
            '-Wl,-Ttext=0x40000000',str(root/'firmware/himax/signal.c'),
            '-lgcc','-o',str(temp/'open.elf')],check=True)
        cases=vectors()
        stock=run_sim(prefix,temp/'stock.elf',cases,True,temp)
        opened=run_sim(prefix,temp/'open.elf',cases,False,temp)
        if args.output_dir:
            args.output_dir.mkdir(parents=True,exist_ok=True)
            for tag in ('stock','open'):
                (args.output_dir/f'{tag}.log').write_text((temp/f'{tag}.log').read_text())
        results=[]
        for i,c in enumerate(cases):
            if stock[i]!=opened[i]:
                raise AssertionError((i,c,stock[i],opened[i]))
            results.append(dict(case=i,inputs=c,stock=stock[i],open=opened[i],passed=True))
        if args.output_dir:
            args.output_dir.mkdir(parents=True,exist_ok=True)
            (args.output_dir/'results.json').write_text(json.dumps(results,indent=2)+'\n')
            for tag in ('stock','open'):
                (args.output_dir/f'{tag}.log').write_text((temp/f'{tag}.log').read_text())
        print(f'PASS {len(cases)} stock-binary versus compiled NDS32 C centroid cases in vendor simulator')


if __name__=='__main__':
    main()
