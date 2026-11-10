"""Phone details come from the phone: two units must not read alike. No device is touched."""
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import sysinfo

CPUINFO = ''.join(f'processor\t: {n}\nCPU part\t: {"0xd0b" if n > 5 else "0xd05"}\n\n' for n in range(8))


def write(root, name, text):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def phone(root, serial='R5ONE', ram='4G', sectors=122142720, maker='0x000015', bootloader='A226BXXSBDZC3'):
    write(root, 'proc/cmdline', f'console=ttyS0 androidboot.bootloader={bootloader} androidboot.serialno={serial} androidboot.dram_info=00,00,00,{ram} '
                                'androidboot.sales_code=ORO androidboot.carrierid=EUX androidboot.hw_id=REV1.1 androidboot.verifiedbootstate=orange androidboot.vbmeta.device_state=unlocked')
    write(root, 'proc/cpuinfo', CPUINFO)
    write(root, 'proc/meminfo', 'MemTotal:        3840844 kB\nMemAvailable:    3497272 kB\n')
    write(root, 'proc/uptime', '3725.40 100.00\n')
    write(root, 'proc/version', 'Linux version 7.2.0-a22x+ (builder) #110 SMP\n')
    write(root, 'proc/bus/input/devices', 'I: Bus=0019\nN: Name="mtk-pmic-keys"\n\nI: Bus=0018\nN: Name="himax-touchscreen"\n\n')
    write(root, 'sys/firmware/devicetree/base/model', 'MT6833V/NZA\0')
    write(root, 'sys/firmware/devicetree/base/compatible', 'samsung,a22x\0mediatek,mt6833\0')
    for policy, cpus, khz in (('policy0', '0 1 2 3 4 5', 2000000), ('policy6', '6 7', 2203000)):
        write(root, f'sys/devices/system/cpu/cpufreq/{policy}/related_cpus', cpus)
        write(root, f'sys/devices/system/cpu/cpufreq/{policy}/cpuinfo_max_freq', str(khz))
    card = 'sys/class/mmc_host/mmc0/mmc0:0001/'
    for name, value in (('name', 'DC6DMB'), ('manfid', maker), ('date', '01/2022'), ('life_time', '0x03 0x02')):
        write(root, card + name, value)
    write(root, 'sys/block/mmcblk0/size', str(sectors))
    battery = 'sys/class/power_supply/battery/'
    for name, value in (('status', 'Charging'), ('capacity', '64'), ('voltage_now', '4021000'), ('charge_full_design', '5000000'), ('technology', 'Li-poly')):
        write(root, battery + name, value)
    write(root, 'sys/class/drm/card1-DSI-1/status', 'connected')
    write(root, 'sys/class/drm/card1-DSI-1/modes', '1080x2408\n1080x2408\n')
    write(root, 'sys/class/thermal/thermal_zone0/type', 'soc-thermal')
    write(root, 'sys/class/thermal/thermal_zone0/temp', '31200')
    write(root, 'etc/a22x-recovery/build.json', json.dumps({'version': '0.6.31', 'padding': 'x' * 20000}))


def value(report, label):
    for section in report['sections']:
        for row in section['rows']:
            if row[0] == label:
                return row[1], row[2]
    return None


class Details(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='a22x-sysinfo-', dir=os.environ.get('TMPDIR')))
        self.addCleanup(shutil.rmtree, self.root, True)

    def test_reads_a_phone(self):
        phone(self.root)
        report = sysinfo.describe(self.root)
        self.assertEqual((report['name'], report['model'], report['codename'], report['version']), ('Galaxy A22 5G', 'SM-A226B', 'a22x', '0.6.31'))
        self.assertEqual(value(report, 'Performance cores'), ('2 × Cortex-A76, 2.2', 'GHz'))
        self.assertEqual(value(report, 'Efficiency cores'), ('6 × Cortex-A55, 2.0', 'GHz'))
        self.assertEqual(value(report, 'Memory'), ('4', 'GB'))
        self.assertEqual(value(report, 'Internal storage'), ('62.5', 'GB'))
        self.assertEqual(value(report, 'Storage chip'), ('Samsung DC6DMB', ''))
        self.assertEqual(value(report, 'Storage wear'), ('20–30', '%'))
        self.assertEqual(value(report, 'Touch panel'), ('himax-touchscreen', ''))
        self.assertEqual(value(report, 'State'), ('Charging', ''))
        self.assertEqual(value(report, 'Voltage'), ('4.02', 'V'))
        self.assertEqual(value(report, 'Design capacity'), ('5000', 'mAh'))
        self.assertEqual(value(report, 'Bootloader lock'), ('Unlocked', ''))
        self.assertEqual(value(report, 'Temperature'), ('31', '°C'))
        self.assertEqual(value(report, 'Running for'), ('1:02', ''))
        self.assertEqual(report['summary'], [['Processor', '2.2', 'GHz'], ['Cores', '8', ''], ['Memory', '4', 'GB'], ['Storage size', '64', 'GB']])

    def test_another_unit_reads_differently(self):
        phone(self.root, serial='R5TWO', ram='6G', sectors=244285440, maker='0x000090', bootloader='A226BRZTS1AVA1')
        report = sysinfo.describe(self.root)
        self.assertEqual(report['model'], 'SM-A226BR')
        self.assertEqual(value(report, 'Serial number'), ('R5TWO', ''))
        self.assertEqual(value(report, 'Memory'), ('6', 'GB'))
        self.assertEqual(value(report, 'Storage chip'), ('SK hynix DC6DMB', ''))
        self.assertEqual(report['summary'][3], ['Storage size', '128', 'GB'])

    def test_an_unknown_model_keeps_its_own_name(self):
        phone(self.root, bootloader='A225FXXU1AUG1')
        report = sysinfo.describe(self.root)
        self.assertEqual((report['name'], report['model']), ('SM-A225F', 'SM-A225F'))
        self.assertIsNone(value(report, 'Name'))

    def test_what_is_missing_is_left_out(self):
        report = sysinfo.describe(self.root)
        self.assertTrue(report['ok'])
        self.assertEqual((report['name'], report['model'], report['summary'], report['sections']), ('Unknown phone', '', [], []))

    def test_a_memory_size_the_firmware_does_not_state_is_rounded_up(self):
        phone(self.root)
        write(self.root, 'proc/cmdline', 'console=ttyS0')
        self.assertEqual(value(sysinfo.describe(self.root), 'Memory'), ('4', 'GB'))

    def test_a_worn_chip_says_so(self):
        phone(self.root)
        write(self.root, 'sys/class/mmc_host/mmc0/mmc0:0001/life_time', '0x0b 0x01')
        self.assertEqual(value(sysinfo.describe(self.root), 'Storage wear'), ('100+', '%'))


if __name__ == '__main__':
    unittest.main()
