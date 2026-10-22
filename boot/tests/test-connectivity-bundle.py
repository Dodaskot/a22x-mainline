#!/usr/bin/env python3
"""Exercise packaging and rejection paths with synthetic AArch64 ELF fixtures."""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('bundle', ROOT / 'boot/build-connectivity-bundle.py')
bundle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bundle)
with tempfile.TemporaryDirectory() as work:
    base = Path(work)
    modules, firmware, kout = (base / name for name in ('modules', 'firmware', 'kout'))
    modules.mkdir(); firmware.mkdir()
    (kout / 'include/config').mkdir(parents=True)
    (kout / 'include/generated').mkdir(parents=True)
    (kout / 'include/config/kernel.release').write_text('7.2.0-fixture\n')
    (kout / 'include/generated/utsversion.h').write_text('#define UTS_VERSION "#101 fixture"\n')
    (modules / 'kernel-release').write_text('#101 fixture\n')
    source = base / 'fixture.c'
    source.write_text('__attribute__((section(".modinfo"),used)) const char v[]="vermagic=7.2.0-fixture SMP";\n')
    for name in bundle.MODULES:
        subprocess.run(['clang', '--target=aarch64-linux-gnu', '-c', str(source), '-o',
                        str(modules / (name + '.ko'))], check=True)
    for name in (*bundle.FIRMWARE, 'Himax_firmware_fw_jdi.bin'):
        (firmware / name).write_bytes(b'synthetic fixture, not firmware\n')
    tools = ROOT / 'conn/tools'
    himax = firmware / 'Himax_firmware_fw_jdi.bin'
    destination = base / 'bundle'
    bundle.stage(modules, firmware, kout, destination, tools, himax)
    manifest = json.loads((destination / 'manifest.json').read_text())
    assert len(manifest['sha256']) == 16  # five modules, build marker, three scripts, seven firmware/configs
    for path, digest in manifest['sha256'].items():
        assert hashlib.sha256((destination / path).read_bytes()).hexdigest() == digest
    assert not (destination / 'conn/bt_addr').exists()
    for cause in ('build', 'vermagic', 'missing-firmware', 'symlink'):
        (modules / 'kernel-release').write_text('#999 wrong\n' if cause == 'build' else '#101 fixture\n')
        (kout / 'include/config/kernel.release').write_text('wrong\n' if cause == 'vermagic' else '7.2.0-fixture\n')
        file = firmware / bundle.FIRMWARE[0]
        if cause in ('missing-firmware', 'symlink'):
            file.unlink()
        if cause == 'symlink':
            file.symlink_to(himax)
        try:
            bundle.stage(modules, firmware, kout, base / cause, tools, himax)
        except (ValueError, FileNotFoundError):
            pass
        else:
            raise AssertionError('accepted ' + cause)
        if cause in ('missing-firmware', 'symlink'):
            if file.is_symlink(): file.unlink()
            file.write_bytes(b'synthetic fixture\n')
    defaults = base / 'defaults'
    defaults.mkdir()
    for name in ('WMT_SOC.cfg', 'wifi.cfg'):
        (firmware / name).unlink()
        (defaults / name).write_bytes(b'fixture repository config\n')
    bundle.stage(modules, firmware, kout, base / 'fallback', tools, himax, defaults)
    assert (base / 'fallback/firmware/wifi.cfg').read_bytes() == b'fixture repository config\n'
    (firmware / 'wifi.cfg').write_bytes(b'explicit config wins\n')
    bundle.stage(modules, firmware, kout, base / 'override', tools, himax, defaults)
    assert (base / 'override/firmware/wifi.cfg').read_bytes() == b'explicit config wins\n'
print('PASS synthetic AArch64 bundle, manifest hashes, build/vermagic/input rejection; no device run')
