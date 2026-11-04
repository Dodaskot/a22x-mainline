#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-2.0-only
"""Run the charger tests without opening device nodes or issuing power actions."""

import argparse
import os
from pathlib import Path
import shlex
import subprocess
import tempfile

from build import HERE, target_compiler, verify_static_aarch64

TESTS = (
    ("policy", ["tests/test_policy.c", "src/charger_policy.c"], []),
    ("controller_io", ["tests/test_controller_io.c", "src/charger_policy.c"],
     ["-Wno-unused-function"]),
    ("controller_input", ["tests/test_controller_input.c", "src/charger_policy.c"],
     ["-Wno-unused-function"]),
    ("charger_ui", ["tests/test_charger_ui.c"], []),
)


def run_suite(label, compiler, flags, runner, directory):
    print(f"=== {label} ===", flush=True)
    for name, sources, extra in TESTS:
        binary = directory / f"{label}-{name}"
        subprocess.run(
            compiler
            + ["-std=c11", "-Wall", "-Wextra", "-Werror", "-Isrc"]
            + flags + extra + sources + ["-lm", "-o", str(binary)],
            cwd=HERE, check=True,
        )
        if label == "aarch64":
            verify_static_aarch64(binary)
        subprocess.run(runner + [str(binary)], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sanitize", action="store_true",
                        help="also run native AddressSanitizer/UBSan tests")
    parser.add_argument("--aarch64", action="store_true",
                        help="also cross-compile static tests and run under qemu-aarch64")
    args = parser.parse_args()
    compiler = shlex.split(os.environ.get("HOST_CC", "cc"))
    if not compiler:
        parser.error("HOST_CC must name a native compiler")
    try:
        with tempfile.TemporaryDirectory(prefix="a22x-charger-tests-") as tmp:
            directory = Path(tmp)
            run_suite("native", compiler, ["-O2"], [], directory)
            if args.sanitize:
                run_suite(
                    "sanitize", compiler,
                    ["-O1", "-g", "-fno-omit-frame-pointer",
                     "-fsanitize=address,undefined", "-fno-sanitize-recover=all"],
                    [], directory,
                )
            if args.aarch64:
                runner = shlex.split(os.environ.get("QEMU_AARCH64", "qemu-aarch64"))
                if not runner:
                    parser.error("QEMU_AARCH64 must name the AArch64 user emulator")
                run_suite("aarch64", target_compiler(), ["-O2", "-static"],
                          runner, directory)
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        parser.exit(1, f"charger tests failed: {error}\n")
    print("All requested charger test suites passed; no phone access.")


if __name__ == "__main__":
    main()
