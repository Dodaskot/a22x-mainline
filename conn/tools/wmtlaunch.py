#!/usr/bin/env python3
# Minimal replacement for the vendor wmt_launcher (MT6833 CONSYS, soc2_2 RAM patches).
import os, sys, fcntl, struct, select, time, glob

FW = os.environ.get("WMT_FW_DIR", "/lib/firmware")
PREFIX = os.environ.get("WMT_PATCH_SET", "1_1")   # soc2_2_ram_<type>_<PREFIX>_hdr.bin
MAGIC = 0xa0
def IOW(nr, size=8):  return (1 << 30) | (size << 16) | (MAGIC << 8) | nr
def IOWR(nr, size=8): return (3 << 30) | (size << 16) | (MAGIC << 8) | nr
WMT_IOCTL_SET_STP_MODE = IOW(5, 4)
WMT_IOCTL_LPBK_POWER_CTRL = IOW(7, 4)
WMT_IOCTL_FUNC_ONOFF_CTRL = IOW(6, 4)
WMT_IOCTL_SET_PATCH_NUM = IOW(14, 4)
WMT_IOCTL_SET_PATCH_INFO = IOW(15)
WMT_IOCTL_WMT_CFG_NAME = IOWR(21)
WMT_IOCTL_SET_ROM_PATCH_INFO = IOW(31)

def log(*a): print("[launcher]", *a, flush=True)

def rom_patch_info(fd):
    for kind in ("mcu", "bt", "wifi"):
        name = "soc2_2_ram_%s_%s_hdr.bin" % (kind, PREFIX)
        with open(os.path.join(FW, name), "rb") as f:
            hdr = f.read(32)
        info = hdr[0x18:0x20]
        ptype = info[7]
        addr = bytes([0]) + info[1:4]
        buf = bytearray(struct.pack("<I4s", ptype, addr) + name.encode().ljust(256, b"\0"))
        fcntl.ioctl(fd, WMT_IOCTL_SET_ROM_PATCH_INFO, buf)
        log("rom patch type %d %s addr %s" % (ptype, name, addr.hex()))

def main():
    fd = os.open("/dev/stpwmt", os.O_RDWR | os.O_NOCTTY)
    cfg = bytearray(b"WMT_SOC.cfg".ljust(256, b"\0"))
    fcntl.ioctl(fd, WMT_IOCTL_WMT_CFG_NAME, cfg)
    fcntl.ioctl(fd, WMT_IOCTL_SET_STP_MODE, 0x23)   # STP_BTIF_FULL | WMT_FM_COMM << 4
    log("cfg + stp mode set")
    action = sys.argv[1] if len(sys.argv) > 1 else "serve"
    if action != "serve":
        pid = os.fork()
        if pid == 0:
            time.sleep(0.5)
            fd2 = os.open("/dev/stpwmt", os.O_RDWR | os.O_NOCTTY)
            if action == "lpbk":
                r = fcntl.ioctl(fd2, WMT_IOCTL_LPBK_POWER_CTRL, 2)
            else:
                r = fcntl.ioctl(fd2, WMT_IOCTL_FUNC_ONOFF_CTRL, 0xBEEF0000 | int(action))
            log("power on request returned", r)
            os._exit(0)
    deadline = time.time() + (60 if action != "serve" else 1e9)
    while time.time() < deadline:
        r, _, _ = select.select([fd], [], [], 1.0)
        if not r:
            continue
        cmd = os.read(fd, 256).split(b"\0")[0].decode(errors="replace")
        if not cmd:
            time.sleep(0.1)
            continue
        log("cmd", cmd)
        try:
            if cmd == "srh_rom_patch":
                rom_patch_info(fd)
                os.write(fd, b"ok")
            elif cmd == "srh_patch":
                os.write(fd, b"fail")
            else:
                os.write(fd, b"ok")
        except OSError as e:
            log("error", e)
            os.write(fd, b"fail")
main()
