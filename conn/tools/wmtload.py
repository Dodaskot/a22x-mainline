# minimal replacement for the vendor wmt_loader (SoC path)
import os, fcntl, sys
def IOR(n): return (2 << 30) | (4 << 16) | (ord('w') << 8) | n
def IOW(n): return (1 << 30) | (4 << 16) | (ord('w') << 8) | n
fd = os.open("/dev/wmtdetect", os.O_RDWR)
steps = sys.argv[1:] or ["soc", "set", "init"]
chip = 0
for st in steps:
    if st == "soc":
        chip = fcntl.ioctl(fd, IOR(3), 0); print("soc chip id 0x%x" % chip)
    elif st == "adie":
        print("adie chip id 0x%x" % fcntl.ioctl(fd, IOR(9), 0))
    elif st == "set":
        print("set chip", fcntl.ioctl(fd, IOW(1), chip))
    elif st == "hwinit":
        print("soc hw init", fcntl.ioctl(fd, IOR(10), 0))
    elif st == "init":
        print("module init", fcntl.ioctl(fd, IOR(4), chip))
