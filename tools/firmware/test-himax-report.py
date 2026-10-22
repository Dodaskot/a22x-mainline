#!/usr/bin/env python3
"""Host protocol checks and optional differential test against private firmware."""
import argparse
import ctypes
import importlib.util
import json
import random
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
class Contact(ctypes.Structure):
    _fields_ = [('x', ctypes.c_uint16), ('y', ctypes.c_uint16), ('width', ctypes.c_uint8)]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--firmware', type=Path)
    ap.add_argument('--receipt', type=Path)
    args = ap.parse_args()
    with tempfile.TemporaryDirectory(prefix='hx-report-test-') as tmp:
        libpath = Path(tmp)/'report.so'
        subprocess.run(['cc', '-std=c11', '-Wall', '-Wextra', '-Werror', '-O2',
                        '-shared', '-fPIC', str(ROOT/'firmware/himax/report.c'),
                        '-o', str(libpath)], check=True)
        lib = ctypes.CDLL(str(libpath))
        lib.hx_report_encode.argtypes = [ctypes.POINTER(ctypes.c_uint8), ctypes.c_size_t,
                ctypes.POINTER(Contact), ctypes.c_uint, ctypes.c_uint16, ctypes.c_uint, ctypes.c_int]
        lib.hx_report_encode.restype = ctypes.c_int
        lib.hx_report_release.argtypes = [ctypes.POINTER(ctypes.c_uint8), ctypes.c_size_t]
        lib.hx_report_release.restype = ctypes.c_int
        def encode(contacts, count, metadata, tag, swap):
            guard = (ctypes.c_uint8*58)(*([0xa6]*58))
            ptr = ctypes.cast(ctypes.byref(guard, 1), ctypes.POINTER(ctypes.c_uint8))
            items = (Contact*10)(*(Contact(x&65535,y&65535,w) for x,y,w in contacts))
            result = lib.hx_report_encode(ptr, 56, items, count, metadata, tag, swap)
            assert result == 56 and guard[0] == guard[57] == 0xa6
            packet = bytes(guard[1:57])
            assert sum(packet)%256 == 0
            assert packet[50:52] == b'\0\0'
            # Host-parser contract: BE16 coordinates and low-nibble point count.
            for i,(x,y,w) in enumerate(contacts):
                assert int.from_bytes(packet[4*i:4*i+2], 'big') == ((y if swap else x)&65535)
                assert int.from_bytes(packet[4*i+2:4*i+4], 'big') == ((x if swap else y)&65535)
                assert packet[40+i] == w
            assert packet[52]&15 == count
            assert int.from_bytes(packet[53:55], 'little') == metadata
            return packet
        contacts = [(0x1234,0x5678,0x9a)] + [(0xffff,0xffff,0)]*9
        p = encode(contacts,1,0x0302,15,False)
        assert p[:4] == bytes.fromhex('12345678') and p[40] == 0x9a
        assert p[52:55] == bytes.fromhex('f10203')
        out = (ctypes.c_uint8*56)()
        assert lib.hx_report_release(out,56) == 56
        assert bytes(out) == bytes([255])*55 + bytes([0x37])
        items = (Contact*10)()
        for capacity, count, tag in [(55,0,15),(0,0,15),(56,11,15),(56,0,16)]:
            before = bytes(out)
            assert lib.hx_report_encode(out,capacity,items,count,0,tag,False) == -1
            assert bytes(out) == before
        assert lib.hx_report_encode(None,56,items,0,0,15,False) == -1
        assert lib.hx_report_encode(out,56,None,0,0,15,False) == -1
        assert lib.hx_report_release(None,56) == -1
        before=bytes(out)
        assert lib.hx_report_release(out,55) == -1 and bytes(out)==before
        comparisons=0
        steps=[]
        if args.firmware:
            spec=importlib.util.spec_from_file_location('hx_oracle', Path(__file__).with_name('himax-report-oracle.py'))
            mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod)
            oracle=mod.ReportOracle(mod.load_instructions(args.firmware.read_bytes()))
            rng=random.Random(0x83112f)
            for trial in range(500):
                contacts=[(rng.getrandbits(32),rng.getrandbits(32),rng.randrange(256)) for _ in range(10)]
                if trial == 0: contacts=[(0xffffffff,0xffffffff,0)]*10
                if trial == 1: contacts=[(0,0,255)]*10
                count=trial%11;metadata=rng.randrange(65536);selector=rng.randrange(256)
                for swap in (False,True):
                    for mode in (False,True):
                        expected, info=oracle.run(contacts,count,metadata,swap=swap,
                                                state_mode=mode,selector=selector,bitmap=0x155)
                        actual=encode(contacts,count,metadata,min(selector,15) if mode else 15,swap)
                        assert actual==expected, (trial,swap,mode,actual.hex(),expected.hex())
                        assert len(info['writes']) == int(mode)
                        steps.append(info['steps']);comparisons+=1
        receipt=dict(result='PASS',host_contract=True,invalid_arguments_rejected=True,
                     private_firmware_differential_cases=comparisons,
                     instruction_steps_min=min(steps) if steps else None,
                     instruction_steps_max=max(steps) if steps else None,
                     hardware_test=False,scope='ten-slot packet encoder only')
        print(json.dumps(receipt,indent=2))
        if args.receipt: args.receipt.write_text(json.dumps(receipt,indent=2)+'\n')


if __name__ == '__main__':
    main()
