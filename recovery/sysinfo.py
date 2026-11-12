#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""What this phone is, read from the phone itself.

Nothing here describes one unit: serial, memory, storage chip and region all come
from the running system, so the same recovery reports another A22x correctly.
A value the phone does not expose is left out instead of being guessed.
"""
import argparse
import json
import os
from pathlib import Path
import re
import sys

CORES = {0xd03: 'Cortex-A53', 0xd05: 'Cortex-A55', 0xd08: 'Cortex-A72', 0xd09: 'Cortex-A73',
         0xd0a: 'Cortex-A75', 0xd0b: 'Cortex-A76', 0xd0d: 'Cortex-A77', 0xd41: 'Cortex-A78'}
STORAGE_MAKERS = {0x11: 'Kioxia', 0x13: 'Micron', 0x15: 'Samsung', 0x45: 'SanDisk', 0x70: 'Kingston',
                  0x90: 'SK hynix', 0xfe: 'Micron'}
NAMES = {'SM-A226B': 'Galaxy A22 5G', 'SM-A226BR': 'Galaxy A22 5G'}
BATTERY_STATES = ('Charging', 'Discharging', 'Full', 'Not charging')


def read(path, limit=8192):
    try:
        with open(path, 'rb') as source:
            return source.read(limit).decode('utf-8', 'replace').replace('\0', ' ').strip()
    except OSError:
        return ''


def number(text, base=10):
    try:
        return int(text, base)
    except (TypeError, ValueError):
        return None


class Phone:
    def __init__(self, root):
        self.root = Path(root)
        self.boot = {}
        for item in self.text('proc/cmdline').split():
            key, mark, value = item.partition('=')
            if mark:
                self.boot.setdefault(key, value)

    def path(self, name):
        return self.root / name

    def text(self, name):
        return read(self.path(name))

    def glob(self, pattern):
        return sorted(self.root.glob(pattern))

    def model(self):
        """The bootloader version starts with the model: A226BXXSBDZC3 is SM-A226B."""
        match = re.fullmatch(r'([A-Z][0-9]{3}[A-Z0-9]{0,3})[A-Z0-9]{8}', self.boot.get('androidboot.bootloader', ''))
        return 'SM-' + match.group(1) if match else ''

    def codename(self):
        compatible = self.text('sys/firmware/devicetree/base/compatible').split()
        return compatible[0].split(',', 1)[-1] if compatible else ''

    def cores(self):
        counts = {}
        for block in self.text('proc/cpuinfo').split('\n\n'):
            cpu = re.search(r'^processor\s*:\s*(\d+)', block, re.M)
            part = re.search(r'^CPU part\s*:\s*(0x[0-9a-f]+)', block, re.M)
            if cpu and part:
                counts.setdefault(int(part.group(1), 16), []).append(int(cpu.group(1)))
        groups = []
        for part, members in counts.items():
            fastest = 0
            for policy in self.glob('sys/devices/system/cpu/cpufreq/policy*'):
                related = self.text(policy.relative_to(self.root) / 'related_cpus').split()
                if str(members[0]) in related:
                    fastest = number(self.text(policy.relative_to(self.root) / 'cpuinfo_max_freq')) or 0
            groups.append({'name': CORES.get(part, hex(part)), 'count': len(members), 'khz': fastest})
        return sorted(groups, key=lambda group: -group['khz'])

    def memory(self):
        info = {}
        for line in self.text('proc/meminfo').splitlines():
            key, _, value = line.partition(':')
            info[key] = number(value.split()[0]) if value.split() else None
        return info

    def installed_memory(self, total_kib):
        """The firmware states the fitted size; the kernel total is smaller by what it reserves."""
        match = re.search(r'(\d+)G$', self.boot.get('androidboot.dram_info', ''))
        if match:
            return int(match.group(1))
        return -(-total_kib // (1024 * 1024)) if total_kib else 0

    def card(self, host):
        for card in self.glob(f'sys/class/mmc_host/{host}/{host}:*'):
            return card.relative_to(self.root)
        return None

    def block_bytes(self, name):
        sectors = number(self.text(f'sys/block/{name}/size'))
        return sectors * 512 if sectors else 0

    def temperature(self, *names):
        readings = []
        for zone in self.glob('sys/class/thermal/thermal_zone*'):
            relative = zone.relative_to(self.root)
            if self.text(relative / 'type') in names:
                value = number(self.text(relative / 'temp'))
                if value is not None:
                    readings.append(value)
        return max(readings) / 1000 if readings else None

    def battery(self):
        folder = Path('sys/class/power_supply/battery')
        found = {name: self.text(folder / name) for name in ('status', 'capacity', 'voltage_now', 'current_now', 'charge_full_design', 'technology')}
        # The interface's own estimate (2026-11-11): the kernel's level is counted from one reading
        # taken under load at the start, and can be far off. One number is shown everywhere.
        level = number(self.text('run/a22x-recovery/battery-level'))
        if level is not None and 0 <= level <= 100:
            found['capacity'] = str(level)
        return found

    def panel(self):
        for connector in self.glob('sys/class/drm/card*-*'):
            relative = connector.relative_to(self.root)
            if self.text(relative / 'status') == 'connected':
                modes = self.text(relative / 'modes').split()
                if modes:
                    return modes[0]
        return ''

    def touch(self):
        for block in self.text('proc/bus/input/devices').split('\n\n'):
            name = re.search(r'^N: Name="([^"]*)"', block, re.M)
            if name and re.search('touch', name.group(1), re.I):
                return name.group(1)
        return ''


def gigabytes(size):
    return f'{size / 1000 ** 3:.1f}'


def marketed(size):
    """Storage is sold in powers of two: 62.5 GB of blocks is the 64 GB model."""
    gigabyte = 1
    while gigabyte * 1000 ** 3 < size:
        gigabyte *= 2
    return gigabyte


def wear(card, phone):
    levels = [number(item, 16) for item in phone.text(card / 'life_time').split()]
    levels = [level for level in levels if level]
    if not levels:
        return ''
    level = max(levels)
    return '100+' if level >= 11 else f'{(level - 1) * 10}–{level * 10}'


def recovery_version(phone):
    try:
        return str(json.loads(read(phone.path('etc/a22x-recovery/build.json'), 262144)).get('version', ''))
    except ValueError:
        return ''


def describe(root='/'):
    phone = Phone(root)
    boot = phone.boot
    model = phone.model()
    codename = phone.codename()
    chip = phone.text('sys/firmware/devicetree/base/model')
    groups = phone.cores()
    memory = phone.memory()
    fitted = phone.installed_memory(memory.get('MemTotal') or 0)
    internal = phone.card('mmc0')
    internal_bytes = phone.block_bytes('mmcblk0')
    memory_card = phone.block_bytes('mmcblk1')
    battery = phone.battery()
    uname = os.uname() if str(root) == '/' else None
    kernel = phone.text('proc/version')
    uptime = number((phone.text('proc/uptime').split() or ['0'])[0].split('.')[0]) or 0

    def rows(*items):
        return [list(item) for item in items if item[1] not in ('', None)]

    processor = [(('Performance cores' if index == 0 and len(groups) > 1 else 'Efficiency cores' if len(groups) > 1 else 'Cores'),
                  f"{group['count']} × {group['name']}" + (f", {group['khz'] / 1000000:.1f}" if group['khz'] else ''),
                  'GHz' if group['khz'] else '') for index, group in enumerate(groups)]
    heat = phone.temperature('soc-thermal', 'cpu-big-thermal', 'cpu-little-thermal')
    maker = ''
    if internal is not None:
        maker = ' '.join(item for item in (STORAGE_MAKERS.get(number(phone.text(internal / 'manfid'), 16) or 0, ''), phone.text(internal / 'name')) if item)
    voltage = number(battery['voltage_now'])
    design = number(battery['charge_full_design'])
    flow = number(battery['current_now'])
    status = battery['status']
    sections = [
        {'title': 'Device', 'rows': rows(
            ('Name', NAMES.get(model, ''), ''), ('Model', model, ''), ('Code name', codename, ''),
            ('Serial number', boot.get('androidboot.serialno', ''), ''),
            ('Board revision', boot.get('androidboot.hw_id', ''), ''),
            ('Region', ' / '.join(item for item in (boot.get('androidboot.sales_code', ''), boot.get('androidboot.carrierid', '')) if item), ''))},
        {'title': 'Processor', 'rows': rows(('Chip', chip, ''), *processor, ('Temperature', f'{heat:.0f}' if heat is not None else '', '°C'))},
        {'title': 'Memory and storage', 'rows': rows(
            ('Memory', str(fitted) if fitted else '', 'GB'),
            ('Available memory', f"{(memory.get('MemAvailable') or 0) / 1024 / 1024:.1f}" if memory.get('MemAvailable') else '', 'GB'),
            ('Internal storage', gigabytes(internal_bytes) if internal_bytes else '', 'GB'),
            ('Storage chip', maker, ''),
            ('Chip made', phone.text(internal / 'date') if internal is not None else '', ''),
            ('Storage wear', wear(internal, phone) if internal is not None else '', '%'),
            ('Memory card', gigabytes(memory_card) if memory_card else '', 'GB'))},
        {'title': 'Display', 'rows': rows(('Resolution', phone.panel().replace('x', ' × '), ''), ('Touch panel', phone.touch(), ''))},
        {'title': 'Battery', 'rows': rows(
            ('Level', battery['capacity'], '%'), ('State', status if status in BATTERY_STATES else '', ''),
            ('Voltage', f'{voltage / 1000000:.2f}' if voltage else '', 'V'),
            ('Current', f'{flow / 1000:+.0f}' if flow is not None else '', 'mA'),
            ('Design capacity', str(design // 1000) if design else '', 'mAh'), ('Type', battery['technology'], ''))},
        {'title': 'Software', 'rows': rows(
            ('Recovery', recovery_version(phone), ''),
            ('Kernel', (uname.release + ' ' + uname.version.split()[0]) if uname else ' '.join(kernel.split()[2:3]), ''),
            ('Architecture', uname.machine if uname else '', ''),
            ('Bootloader', boot.get('androidboot.bootloader', ''), ''),
            ('Bootloader lock', boot.get('androidboot.vbmeta.device_state', '').capitalize(), ''),
            ('Verified boot', boot.get('androidboot.verifiedbootstate', '').capitalize(), ''),
            ('Running for', f'{uptime // 3600}:{uptime // 60 % 60:02d}' if uptime else '', ''))},
    ]
    cores = sum(group['count'] for group in groups)
    summary = rows(('Processor', f"{max((group['khz'] for group in groups), default=0) / 1000000:.1f}" if groups else '', 'GHz'),
                   ('Cores', str(cores) if cores else '', ''),
                   ('Memory', str(fitted) if fitted else '', 'GB'),
                   ('Storage size', str(marketed(internal_bytes)) if internal_bytes else '', 'GB'))
    return {'ok': True, 'name': NAMES.get(model, model or 'Unknown phone'), 'model': model, 'codename': codename,
            'version': recovery_version(phone), 'summary': summary, 'sections': [section for section in sections if section['rows']]}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=Path('/'), help='another tree laid out like / (tests)')
    print(json.dumps(describe(parser.parse_args(argv).root), ensure_ascii=False, separators=(',', ':')))
    return 0


if __name__ == '__main__':
    sys.exit(main())
