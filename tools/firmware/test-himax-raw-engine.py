#!/usr/bin/env python3
"""Host transport-injection tests, without hardware or proprietary firmware."""
import ctypes as C
import subprocess
import tempfile
from pathlib import Path

WRITE = C.CFUNCTYPE(C.c_int,C.c_void_p,C.c_uint32,C.c_uint,C.c_uint32)
READ = C.CFUNCTYPE(C.c_int,C.c_void_p,C.c_uint32,C.POINTER(C.c_uint8))


class IO(C.Structure):
    _fields_=[('context',C.c_void_p),('write',WRITE),('read8',READ)]


def main():
    root=Path(__file__).resolve().parents[2]
    with tempfile.TemporaryDirectory(prefix='hx-raw-engine-') as temp:
        so=Path(temp)/'engine.so'
        subprocess.run(['cc','-std=c99','-O2','-Wall','-Wextra','-Werror',
            '-fPIC','-shared',str(root/'firmware/himax/raw-engine.c'),'-o',str(so)],check=True)
        lib=C.CDLL(str(so))
        lib.hx_raw_engine_submit.argtypes=[C.POINTER(IO),C.c_uint32,C.c_uint32,C.c_uint]
        parameter=0xfedcba98
        destination=0x123460f0
        expected=[(0x800300c8,4,parameter),(0x800300d4,4,parameter),
            (0x80030034,1,0xd8),(0x80030038,1,0xd8),(0x800300c0,1,0),
            (0x800300b8,2,0x5be0),(0x800300bc,2,0x60f0),(0x80030000,1,1)]
        for failure in range(10):
            writes=[];reads=[]
            @WRITE
            def write(ctx,address,width,value):
                writes.append((address,width,value))
                return -1 if len(writes)==failure else 0
            @READ
            def read(ctx,address,value):
                reads.append(address)
                value[0]=1 if len(reads)<3 else 0
                return -1 if failure==9 else 0
            io=IO(None,write,read)
            rc=lib.hx_raw_engine_submit(C.byref(io),parameter,destination,3)
            if 1<=failure<=8:
                assert rc==-2 and writes==expected[:failure] and not reads
            elif failure==9:
                assert rc==-2 and writes==expected and len(reads)==1
            else:
                assert rc==0 and writes==expected and reads==[0x80030000]*3
        @WRITE
        def ok_write(ctx,address,width,value): return 0
        count=[]
        @READ
        def always_busy(ctx,address,value):
            count.append(address);value[0]=255;return 0
        io=IO(None,ok_write,always_busy)
        assert lib.hx_raw_engine_submit(C.byref(io),0,0xffffffff,2)==-3
        assert len(count)==2
        assert lib.hx_raw_engine_submit(C.byref(io),0,0,0)==-1
        assert len(count)==2
        assert lib.hx_raw_engine_submit(None,0,0,1)==-1
        missing=IO(None,WRITE(),always_busy)
        assert lib.hx_raw_engine_submit(C.byref(missing),0,0,1)==-1
        missing=IO(None,ok_write,READ())
        assert lib.hx_raw_engine_submit(C.byref(missing),0,0,1)==-1
    print('PASS exact eight writes, low16 destination, completion reads, 9 I/O failures, timeout and invalid callbacks')


if __name__=='__main__': main()
