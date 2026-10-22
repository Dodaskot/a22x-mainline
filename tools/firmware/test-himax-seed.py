#!/usr/bin/env python3
"""Synthetic host-C threshold/seed tests; no controller or firmware required."""
import ctypes as C
import random
import subprocess
import tempfile
from pathlib import Path


class Seed(C.Structure):
    _fields_=[('row',C.c_uint8),('col',C.c_uint8)]


class Result(C.Structure):
    _fields_=[('labels',C.c_uint16*576),('seeds',Seed*150),('count',C.c_uint16)]


class Peak(C.Structure):
    _fields_=[('row',C.c_uint8),('col',C.c_uint8),('weight',C.c_uint16)]


def main():
    root=Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory(prefix='hx-seed-') as td:
        so=Path(td)/'seed.so'
        subprocess.run(['cc','-std=c99','-O2','-Wall','-Wextra','-Werror','-fPIC',
            '-shared',str(root/'firmware/himax/seed.c'),'-o',str(so)],check=True)
        lib=C.CDLL(str(so));lib.hx_seed_extract.argtypes=[C.POINTER(Result),
            C.POINTER(C.c_int16),C.c_size_t,C.c_uint16,C.POINTER(C.c_uint16),C.c_size_t]
        lib.hx_seed_pick.argtypes=[C.POINTER(Peak),C.POINTER(Result),C.POINTER(C.c_int16),C.c_size_t]
        rng=random.Random(5564)
        cases=[([0]*576,0,None),([1]*576,0,None),([32767]*576,32767,None),
            ([-32768]*576,0,None),([32767]*576,65535,None),
            ([100,101,99,-1]*144,100,None),
            ([1]*576,65535,[0,1,2,65535]*144)]
        for above in (149,150,151):
            cases.append(([1]*above+[0]*(576-above),0,None))
        for _ in range(64):
            cases.append(([rng.randrange(-32768,32768) for _ in range(576)],
                rng.randrange(65536),[rng.randrange(65536) for _ in range(576)] if rng.randrange(2) else None))
        for signal,threshold,per_cell in cases:
            out=Result();C.memset(C.byref(out),0xa5,C.sizeof(out))
            src=(C.c_int16*576)(*signal)
            thresholds=(C.c_uint16*576)(*per_cell) if per_cell is not None else None
            assert lib.hx_seed_extract(C.byref(out),src,576,threshold,thresholds,576 if thresholds is not None else 0)==0
            expected=[i for i,value in enumerate(signal) if value>(per_cell[i] if per_cell is not None else threshold)]
            assert list(out.labels)==[254 if i in expected else 253 for i in range(576)]
            assert out.count==min(150,len(expected))
            assert [(s.row,s.col) for s in out.seeds[:out.count]]==[(i//36+1,i%36+1) for i in expected[:150]]
            assert all((s.row,s.col)==(0xa5,0xa5) for s in out.seeds[out.count:])
        out=Result();C.memset(C.byref(out),0xa5,C.sizeof(out));saved=bytes(out)
        src=(C.c_int16*576)();thresholds=(C.c_uint16*576)()
        for count,ptr,tcount,want in [(575,None,0,-2),(576,thresholds,575,-2)]:
            assert lib.hx_seed_extract(C.byref(out),src,count,0,ptr,tcount)==want
            assert bytes(out)==saved
        assert lib.hx_seed_extract(C.byref(out),None,576,0,None,0)==-1
        assert lib.hx_seed_extract(None,src,576,0,None,0)==-1
        r=Result();r.count=3
        r.seeds[0]=Seed(2,5);r.seeds[1]=Seed(1,1);r.seeds[2]=Seed(5,10)
        weights=(C.c_int16*684)()
        for row,col,w in [(2,5,100),(1,1,100),(5,10,99)]:
            r.labels[(row-1)*36+col-1]=254;weights[row*38+col]=w
        peak=Peak(9,9,9)
        assert lib.hx_seed_pick(C.byref(peak),C.byref(r),weights,684)==0
        assert (peak.row,peak.col,peak.weight)==(2,5,100) # first tied entry
        r.labels[1*36+4]=1 # visited first entry
        assert lib.hx_seed_pick(C.byref(peak),C.byref(r),weights,684)==0
        assert (peak.row,peak.col,peak.weight)==(1,1,100)
        for i in range(684): weights[i]=-1
        saved_peak=bytes(peak)
        assert lib.hx_seed_pick(C.byref(peak),C.byref(r),weights,684)==1
        assert bytes(peak)==saved_peak
        r.seeds[2]=Seed(0,1)
        assert lib.hx_seed_pick(C.byref(peak),C.byref(r),weights,684)==-2
        assert bytes(peak)==saved_peak
        r.count=151
        assert lib.hx_seed_pick(C.byref(peak),C.byref(r),weights,684)==-2
        assert lib.hx_seed_pick(C.byref(peak),C.byref(r),weights,683)==-2
        assert lib.hx_seed_pick(None,C.byref(r),weights,684)==-1
    print(f'PASS {len(cases)} seed matrices: strict thresholds, ties, cap150, full labels, unused slots and bounds')
    print('PASS peak selection: stable ties, visited seeds, nonpositive weights and invalid bounds')


if __name__=='__main__': main()
