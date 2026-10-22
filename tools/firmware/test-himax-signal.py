#!/usr/bin/env python3
"""Host C tests; optionally compare recorded synthetic vendor-simulator cases."""
import argparse
import ctypes as C
import json
import random
import subprocess
import tempfile
from pathlib import Path


class Moment(C.Structure):
    _fields_ = [('col_weight', C.c_uint32), ('row_weight', C.c_uint32),
                ('weight', C.c_uint32)]


class Axes(C.Structure):
    _fields_ = [('col_axis', C.c_uint16), ('row_axis', C.c_uint16)]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--cc', default='cc')
    ap.add_argument('--vendor-results', type=Path)
    args = ap.parse_args()
    root = Path(__file__).resolve().parents[2]
    comparisons = 0
    with tempfile.TemporaryDirectory(prefix='hx-signal-') as temp:
        so = Path(temp) / 'signal.so'
        subprocess.run([args.cc, '-std=c99', '-O2', '-Wall', '-Wextra', '-Werror',
                        '-fPIC', '-shared', '-o', str(so),
                        str(root/'firmware/himax/signal.c')], check=True)
        lib = C.CDLL(str(so))
        lib.hx_signal_scale.argtypes = [C.POINTER(Axes), C.POINTER(Moment), C.c_uint, C.c_uint]
        lib.hx_signal_accumulate.argtypes = [C.POINTER(Moment), C.c_int16, C.c_uint, C.c_uint]
        lib.hx_signal_mapped_delta.argtypes = [C.POINTER(C.c_int16), C.c_size_t,
            C.POINTER(C.c_uint16), C.POINTER(C.c_uint16), C.c_size_t,
            C.POINTER(C.c_uint16), C.c_size_t]
        rng = random.Random(83112)
        u16 = C.c_uint16*648
        raw = u16(*(rng.getrandbits(16) for _ in range(648)))
        baseline = u16(*(rng.getrandbits(16) for _ in range(648)))
        mapping = (C.c_uint16*576)(*((i*73)%648 for i in range(576)))
        delta = (C.c_int16*576)(*([1234]*576))
        assert lib.hx_signal_mapped_delta(delta,576,raw,baseline,648,mapping,576)==0
        for i,index in enumerate(mapping):
            want=(raw[index]-baseline[index])&65535
            if want>=32768: want-=65536
            assert delta[i]==want
        for a,b,want in [(0,65535,1),(65535,0,-1),(0,32768,-32768),(32767,65535,-32768)]:
            raw[0],baseline[0]=a,b
            assert lib.hx_signal_mapped_delta(delta,576,raw,baseline,648,mapping,576)==0
            assert delta[0]==want
        for kind in ('bad_mapping','short_output','short_mapping','zero_raw_count'):
            delta[:]=[1234]*576
            mapping[575]=648 if kind=='bad_mapping' else 1
            rc=lib.hx_signal_mapped_delta(delta,575 if kind=='short_output' else 576,
                raw,baseline,0 if kind=='zero_raw_count' else 648,mapping,
                575 if kind=='short_mapping' else 576)
            assert rc==-2 and list(delta)==[1234]*576
        assert lib.hx_signal_mapped_delta(None,576,raw,baseline,648,mapping,576)==-1
        m=Moment(0xffffffff,0xffffffff,0xffffffff)
        assert lib.hx_signal_accumulate(C.byref(m),-32768,17,37)==0
        assert (m.col_weight,m.row_weight,m.weight)==(0xffffffff,)*3
        assert lib.hx_signal_accumulate(C.byref(m),1,17,37)==0
        assert (m.col_weight,m.row_weight,m.weight)==(36,16,0)
        assert lib.hx_signal_accumulate(C.byref(m),2,18,0)==-2
        assert lib.hx_signal_accumulate(C.byref(m),2,0,38)==-2
        assert lib.hx_signal_accumulate(None,2,0,0)==-1
        out=Axes(77,88)
        assert lib.hx_signal_scale(C.byref(out),C.byref(Moment(1,1,0)),0,0)==-3
        assert (out.col_axis,out.row_axis)==(77,88)
        assert lib.hx_signal_scale(C.byref(out),C.byref(Moment(1,1,1)),2,0)==-1
        assert lib.hx_signal_scale(None,C.byref(Moment(1,1,1)),0,0)==-1
        cases=[dict(col=1,row=63629146,weight=1,inva=0,invb=0,actual_a=33,actual_b=26)]
        for _ in range(2000):
            cases.append(dict(col=rng.getrandbits(32),row=rng.getrandbits(32),
                weight=rng.randint(1,0xffffffff),inva=rng.randrange(2),invb=rng.randrange(2)))
        if args.vendor_results:
            vendor=json.loads(args.vendor_results.read_text())
            assert vendor and all(c.get('passed') for c in vendor)
            cases+=vendor
        for c in cases:
            m=Moment(c['col'],c['row'],c['weight'])
            assert lib.hx_signal_scale(C.byref(out),C.byref(m),c['inva'],c['invb'])==0
            qa=c['col']*2408//36//c['weight']
            qb=(c['row']*1080>>4)//c['weight']
            a=0 if qa<=33 else min(2407,(qa-33)&0xffffffff)
            b=0 if qb<=33 else min(1079,(qb-33)&0xffffffff)
            if c['inva']: a=2407-a
            if c['invb']: b=1079-b
            assert (out.col_axis,out.row_axis)==(a,b)
            if 'actual_a' in c: assert (a,b)==(c['actual_a'],c['actual_b'])
            comparisons+=1
    print(f'PASS mapped-delta bounds/wrap, moment clipping/wrap, {comparisons} centroid cases')


if __name__=='__main__':
    main()
