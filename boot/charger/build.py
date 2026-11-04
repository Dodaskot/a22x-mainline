#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Build the initramfs charger with a static AArch64 libc toolchain."""

import argparse
import os
from pathlib import Path
import shlex
import struct
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent


def target_compiler():
    """Keep the libc compiler separate from build-initramfs.sh's memtool CC."""
    default = os.environ.get("CROSS_COMPILE", "aarch64-linux-gnu-") + "gcc"
    command = shlex.split(os.environ.get("CHARGER_CC", default))
    if not command:
        raise ValueError("CHARGER_CC must name a static AArch64 libc compiler")
    return command


def verify_static_aarch64(path):
    data = Path(path).read_bytes()
    if len(data) < 64 or data[:7] != b"\x7fELF\x02\x01\x01":
        raise ValueError("charger must be a little-endian ELF64 executable")
    header = struct.unpack_from("<16sHHIQQQIHHHHHH", data)
    if header[1] != 2 or header[2] != 183:
        raise ValueError("charger must be an AArch64 ET_EXEC executable")
    phoff, phentsize, phnum = header[5], header[9], header[10]
    if not phnum or phentsize < 56 or phoff + phentsize * phnum > len(data):
        raise ValueError("charger has an invalid ELF program-header table")
    for index in range(phnum):
        kind = struct.unpack_from("<I", data, phoff + index * phentsize)[0]
        if kind in (2, 3):  # PT_DYNAMIC, PT_INTERP
            raise ValueError("charger must be static: no dynamic loader or section")


def build(output):
    output = Path(output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    # Leave any previous valid output untouched if compilation or checks fail.
    with tempfile.TemporaryDirectory(prefix=".charger-", dir=output.parent) as tmp:
        candidate = Path(tmp) / "a22x-charger"
        subprocess.run(
            target_compiler()
            + [
                "-std=c11", "-Os", "-Wall", "-Wextra", "-Werror",
                "-static", "-s",
                "-o", str(candidate),
                "src/charger.c", "src/charger_policy.c", "src/charger_ui.c",
                "-lm",
            ],
            cwd=HERE, check=True,
        )
        verify_static_aarch64(candidate)
        candidate.chmod(0o755)
        candidate.replace(output)
    print(f"charger: {output} ({output.stat().st_size} bytes, static AArch64)")
    return output


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=HERE / "build/a22x-charger")
    args = parser.parse_args()
    try:
        build(args.output)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"charger build failed: {error}\n")


if __name__ == "__main__":
    main()
