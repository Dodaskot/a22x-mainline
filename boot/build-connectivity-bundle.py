#!/usr/bin/env python3
"""Stage an opt-in boot bundle from local, explicitly supplied firmware."""
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess

MODULES = ('btif', 'wmt_drv', 'wmt_chrdev_wifi', 'wlan_6833_axi', 'btmtk_stp')
FIRMWARE = ('soc2_2_ram_mcu_1_1_hdr.bin', 'soc2_2_ram_bt_1_1_hdr.bin',
            'soc2_2_ram_wifi_1_1_hdr.bin', 'WIFI_RAM_CODE_soc2_2_1_1.bin',
            'WMT_SOC.cfg', 'wifi.cfg')


def stage(modules, firmware, kout, destination, tools, himax, config_defaults=None):
    release = (kout / 'include/config/kernel.release').read_text().strip()
    header = (kout / 'include/generated/utsversion.h').read_text()
    expected_build = next(line.split('"')[1] for line in header.splitlines()
                          if line.startswith('#define UTS_VERSION "'))
    actual_build = (modules / 'kernel-release').read_text().strip()
    if actual_build != expected_build:
        raise ValueError('module kernel-release does not match KOUT UTS_VERSION')
    inputs = [(modules / (name + '.ko'), Path('conn') / (name + '.ko')) for name in MODULES]
    inputs += [(modules / 'kernel-release', Path('conn/kernel-release'))]
    config_defaults = config_defaults or tools.parent / 'firmware'
    for name in FIRMWARE:
        source = firmware / name
        if name.endswith('.cfg') and not (source.exists() or source.is_symlink()):
            source = config_defaults / name
        inputs.append((source, Path('firmware') / name))
    inputs += [(himax, Path('firmware/Himax_firmware_fw_jdi.bin'))]
    inputs += [(tools / name, Path('conn') / name)
               for name in ('conn-start.sh', 'wmtload.py', 'wmtlaunch.py')]
    for source, _ in inputs:
        if not source.is_file() or source.is_symlink() or not source.stat().st_size:
            raise ValueError(f'missing, empty or symlink input: {source}')
    for name in MODULES:
        vermagic = subprocess.check_output(
            ['modinfo', '-F', 'vermagic', str(modules / (name + '.ko'))], text=True).strip()
        if not vermagic or vermagic.split()[0] != release:
            raise ValueError(f'{name}: vermagic does not match KOUT kernel.release')
    if not release or not expected_build:
        raise ValueError("empty kernel release or build marker")
    if destination.exists():
        raise ValueError('bundle destination already exists')
    destination.mkdir(parents=True)
    manifest = {'kernel_release': release, 'kernel_build': expected_build, 'sha256': {}}
    for source, target in inputs:
        output = destination / target
        output.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, output)
        output.chmod(0o755 if source.suffix in ('.sh', '.py') else 0o644)
        manifest['sha256'][str(target)] = hashlib.sha256(output.read_bytes()).hexdigest()
    (destination / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for argument in ('modules', 'firmware', 'kout', 'destination', 'himax'):
        parser.add_argument('--' + argument, required=True, type=Path)
    args = parser.parse_args()
    stage(args.modules, args.firmware, args.kout, args.destination,
          Path(__file__).resolve().parent.parent / 'conn/tools', args.himax)
