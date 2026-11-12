#!/usr/bin/env python3
"""Collect every string the interface can show into i18n/messages.txt.

The UI looks text up by its English form, so the catalog is simply those forms.
Tests and previews are skipped. Run again after changing interface text; a new
string shows in English until its language files are updated.
"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ('recovery_ui.c', 'systems_ui.h', 'install_ui.h', 'wifi_ui.h', 'installer_ui.h', 'images_ui.h', 'adb_ui.h', 'packages_ui.h', 'identity_ui.h', 'android_plan_ui.h', 'wipe_ui.h', 'androidlog_ui.h', 'android_ui.h', 'card_ui.h', 'copies_ui.h', 'source_ui.h', 'screenshot_ui.h', 'charger_ui.h', 'shells_ui.h', 'locale_ui.h', 'terminal_ui.h', 'flash_ui.h', 'theme.h')
HELPERS = ('../flash.py', '../themes.py', '../sysinfo.py', '../samsung_firmware.py', '../android_gsi.py', '../saved_boot.py', '../sevenzip.py', '../root.py', '../bcb.py', '../clock.py', '../zip_install.py', '../sdcard.py', '../identity_backup.py', '../partition_archive.py')
WORDS = {'archive', 'folder', 'read-only', 'finished', 'failed', 'not run', 'unavailable', 'mounted', 'offline', 'raw', 'active', 'yes', 'no', 'live', 'history', 'on', 'off', 'unknown',
         'backup', 'wipe', 'restore', '30 s', 'minimal', 'signed', 'none', 'arm64',
         'Europe', 'America', 'Asia', 'Africa', 'Australia', 'Pacific', 'Atlantic', 'Antarctica', 'Arctic'}
NOT_TEXT = {'—', '%4d-%2d-%2dT%2d:%2d:%2dZ', 'recovery-%s.png', r'\x89PNG\r\n\x1a\n', 'IHDR', 'IDAT', 'IEND', 'en_US', 'uk_UA', 'de_DE', 'fr_FR', 'pl_PL', 'es_ES', 'it_IT', 'pt_BR', 'nl_NL', 'cs_CZ', 'ro_RO', 'tr_TR', 'ru_RU', 'English', 'Deutsch', 'Français', 'Polski', 'Español', 'Italiano', 'Português', 'Nederlands', 'Čeština', 'Română', 'Türkçe', 'UTC%s', 'UTC%s / %s', 'GDM', 'SDDM', 'LightDM', 'Phrog', '%s  /  Recovery %s', 'Galaxy A22 5G', 'Catppuccin Mocha', 'Catppuccin Latte', 'Dracula', 'Nord', 'Tokyo Night', 'Gruvbox Dark', 'Gruvbox Light', 'Solarized Dark', 'Solarized Light', '%.1000s/A22xBackups', 'SUBSYSTEM=power_supply', 'Charging', 'Full', 'DEVPATH=', 'SUBSYSTEM=block', 'not attached', '(no result buffer)', 'Example external repository', 'ext4 microSD - illustrative preview', 'ABC', 'EN', 'UK',
            'Esc', 'Tab', 'PYTHONUNBUFFERED', 'INSTALL-STOCK-', 'partclone-ext4-v2+zstd', 'LUKS', 'Plasma', 'Xfce', 'UTC%c%d', 'UTC%c%d:%02d', 'TZ', 'UTC0',
            'SM-A226B  /  Recovery 0.6', 'A22x Recovery', 'Mainline Recovery', 'USB SSH', 'microSD', 'Android', 'Console', '%u Hz', '60 Hz', '90 Hz', 'Ctrl+C', 'Ctrl+D', 'GiB', 'KiB', 'MiB', 'TiB', 'GNOME', 'Phosh', 'Sxmo', 'Lomiri', 'Del', 'saved\\n', 'FILE TEST', 'PREVIEW', 'SHA-256'}
# Messages from the backup backend that a person is likely to meet.
BACKEND_TEXT = {'BOOT holds no Linux kernel for this phone. Flash a Linux BOOT image first: Install, Flash a partition, boot',  # distro.py boot_identity (2026-11-11)
                # android_gsi.py OLD_KERNEL_WARNING (2026-11-12).
                "This is Android 15 or newer. Google's own builds need kernel 4.19 and restart for ever on this phone's Android kernel 4.14; ROMs made for older phones (Treble GSIs) start.",
                # catalog.json fit_note of the Treble GSIs, shown on an Android image's page (2026-11-12).
                'Treble GSI: made to start on older vendors too',
                # bootimage.py: why an image is refused before anything is written (2026-11-12: IMAGE_DAMAGED was shown in English).
                'A DT table entry is not a device tree',
                'A device tree is cut short',
                'A device tree is larger than its DT table entry',
                'A device tree lies outside its own size',
                'A device tree node name is cut short',
                'A device tree of the DT table lies outside it',
                'A device tree property is cut short',
                'A device tree property lies outside',
                'A hash descriptor is cut short',
                'A vbmeta descriptor lies outside',
                'Neither an AVB hash nor the kernel and ramdisk streams show that the image is complete',
                'Not a device tree',
                'Nothing in this boot image names the Galaxy A22 5G or its MT6833 chip',
                'The AVB footer points outside the partition',
                'The DT table is cut short',
                'The DT table lies outside the partition',
                'The boot image has an unusual page size',
                'The boot image has no kernel',
                'The boot image is larger than the part its AVB footer covers',
                'The boot image is larger than the partition',
                'The kernel does not unpack to its end: the image is damaged or incomplete',
                'The kernel does not unpack: the image is damaged or incomplete',
                'The kernel is neither gzip, LZ4 nor an arm64 Image',
                'The kernel is not an arm64 Image',
                'The ramdisk does not unpack to its end: the image is damaged or incomplete',
                'The ramdisk does not unpack: the image is damaged or incomplete',
                'The vbmeta blocks lie outside the image',
                'The vbmeta hash does not match its contents',
                'There is no AVB vbmeta header',
                'There is no Android DT table',
                'There is no Android boot image header',
                'This partition is not judged by its contents',
                'Unknown device tree token',
                # distro.py boot_for_linux and the two steps after it (2026-11-11).
                'BOOT holds the kernel of Android, and this phone keeps no Linux BOOT: join Wi-Fi so that it can be downloaded, or flash one in Flash a partition',
                'The Linux BOOT of this plan is no longer offered: plan again', 'The downloaded Linux BOOT is not the one the catalog names',
                'The Linux BOOT on cache changed since planning',
                'Insert an SD card or attach a USB drive: while Android uses userdata, downloaded images are kept there', 'There is not enough space on the card for this image', 'The card is mounted read-only as backup storage: disconnect it in Storage first', 'A FAT32 card cannot hold a file of 4 GiB or more', 'A backup with this name already exists', 'Backup destination is read-only', 'The copy changed after it was listed', 'There was not enough free space on userdata for the packages.', 'The package servers could not be reached.', 'A package download did not pass its check.', 'There is not enough space on userdata for this image', 'Choose an image of the store', 'The image is not saved on the phone', 'The image store on userdata is not a folder', 'This system cannot read the saved password of this account. Choose New user with the same name and enter the password again.', 'Shared files require the same group names and GIDs', 'Shared home requires the same user name, UID, primary GID and home path', 'Platform file permissions or owner differ', 'Required kernel, firmware or device adapter file differs', 'Required platform symlink differs', 'A required rootfs directory is missing or symlinked', 'Only systemd distributions are supported by the current device adapter', 'The backup does not fit on this storage. Choose one with more free space', 
                # filesystem.py check (TWRP's "Repair file system", 2026-11-09).
                'The check found errors it could not repair', 'The filesystem could not be checked',
                'Unmount the volume before checking it', 'This recovery checks and repairs ext2, ext3 and ext4 only',
                'The filesystem could not be resized', 'The filesystem size could not be read',
                'Beaver', 'Warm wood and a quiet river',
                # distro.py: the catalog of the installer page and its downloads (2026-11-08).
                'The image changed on its server. The catalog now lists the new version: start the installation again.',
                'The server no longer offers this image. Choose another one.',
                'The image could not be downloaded. Check the Wi-Fi connection and try again.',
                'The downloaded image differs from the catalog. Try again.',
                'Phone shell',  # catalog.json notes
                'Recovery was not connected to Wi-Fi.',
                'Charging', 'Discharging', 'Full', 'Not charging', 'Unlocked', 'Locked', 'Green', 'Yellow', 'Orange', 'Red',
                'Storage is busy: stop USB files, or wait for the running operation to finish',
                'A computer is connected over SSH or SFTP: close that session, then try again',
                'Only cache is wiped this way. Erase other volumes in Files',  # filesystem.py wipe (2026-11-10)
                'This system was set up for another kernel and firmware. Install it again, or remove it',  # distro.py switch
                # filesystem.py reason_text: shown under a volume that cannot be opened.
                'Android logical-partition container; mapping is not implemented', 'Raw image; not a filesystem',
                'Firmware or service partition; file access is not admitted', 'This partition is already used by another block mapping',
                'Filesystem unavailable; Android data may require decryption', 'Encrypted or unsupported filesystem',
                'This kernel has no driver for the detected filesystem', 'Mounted outside the recovery filesystem manager',
                'Read-only filesystem', 'Filesystem encryption is enabled; file keys have not been unlocked',
                'Android verified filesystem; writes are not qualified',
                'The block device was already read-only; this helper will not override it',
                'The block-level read-only guard changed; remount is required', 'bytes',
                # flash.py image kinds, shown on the review page.
                'ext4 filesystem image', 'F2FS filesystem image', 'Raw image', 'Device tree overlays', 'Verified boot data', 'Android super image',
                # distro.py: refusals of an image installation, clean installs included.
                'Userdata holds files but no Linux system. Format it in Files for a clean install.',
                'A clean install needs a new account: userdata has none to keep',
                'Enter a Wi-Fi network: the desktop and apps are downloaded at the first start',
                'This image has no Wi-Fi program, so the desktop and apps cannot be downloaded at the first start. Choose Console without apps, or another image.',
                'Setup scripts must be on external SD/USB storage', 'A setup script can be at most 1 MiB',
                'The setup script must be a text file', 'The setup script changed since planning',
                'Userdata changed since planning', 'Userdata changed during installation',
                'A network name has 1 to 32 bytes', 'A Wi-Fi password has 8 to 63 characters',
                'There is not enough space beside the retained system', 'The image or installation state changed since planning',
                'This image keeps its system on btrfs, which this recovery cannot read',
                'This image keeps its system on xfs, which this recovery cannot read',
                'The image file is damaged or incomplete. Copy it again, then eject the stick safely before unplugging it.',
                'This file is not a recognised system image. Supported: ISO, raw or img disk images, qcow2, VHD, Vagrant box and tar archives, also compressed with xz, zstd, gzip or bzip2.',
                # ramdisk/init: why a confirmed power action stayed in recovery.
                'The phone stayed in recovery: a backup, restore or installation is still running. Wait until it finishes, then try again.',
                'The phone stayed in recovery: a volume is still in use. Stop USB file sharing or the terminal, then try again.',
                'The phone stayed in recovery: an image installation needs attention in Install images.',
                'The phone stayed in recovery: there is no bootable system on userdata, or a system switch must be finished in Systems.',
                'The phone stayed in recovery: the power action was refused.'}
STRING = re.compile(r'"((?:[^"\\\n]|\\.)*)"')


def wanted(value):
    if value == '30 s':
        return True
    if len(value) < 2 or not re.search(r'[A-Za-z]{2}', value) and value != '%s, %d %s':
        return False
    if value in WORDS or value == '%s, %d %s':
        return True
    if re.search(r'recovery-ui|/opt/|/run/|/sys/|/dev/|/proc/|\\"|^--|^/|^\.|=%|=\d|%s=|^%[a-z0-9]+$|\bjson\b|^\{', value):
        return False
    if value in NOT_TEXT or re.search(r'%l|%zu|%[0-9]*l|^\\n|^%s[./-]|^FLASH-BOOT|^ /  skip|^ /  start|^line-%|%$', value):
        return False
    if re.fullmatch(r'[A-Z0-9_]+', value):
        return '_' not in value
    if re.fullmatch(r'[a-z0-9_.:\-]+', value):
        return False
    if re.fullmatch(r'[a-z0-9_]+(-[a-z0-9_]+)*', value):
        return False
    return True


def interface_text(path):
    text = path.read_text()
    if path.name == 'recovery_ui.c':
        start = text.index('static bool preview(App*a,const char*name){')
        text = text[:start] + text[text.index('\nstatic void usage(') if '\nstatic void usage(' in text[start:] else len(text):]
        cut = text.find('int main(')
        text = text[:cut] if cut > 0 else text
    return [match.group(1) for match in STRING.finditer(text)]


def helper_text(path):
    text = path.read_text()
    # "A partition table: ..." too: a sentence may begin with "A " (2026-11-10).
    found = re.findall(r"'([A-Z](?:[a-z]| [a-z])[^'\n]*)'", text)
    # A message with an apostrophe is written in double quotes (2026-11-11: "Samsung's Android BOOT
    # stays" was shown in English in every language). Command help and docstrings are not messages.
    for line in text.splitlines():
        if 'help=' not in line and '"""' not in line and not line.lstrip().startswith('#'):
            found += [value for value in re.findall(r'"([A-Z](?:[a-z]| [a-z])[^"\n]*)"', line) if "'" in value and not value.endswith(': ')]
    return [value for value in found if (' ' in value or value in ('Writing', 'Reading')) and '{' not in value and value not in NOT_TEXT]


def phone_words(path):
    """Labels, sections and units of the phone details; most are single words."""
    text = path.read_text().split('def describe', 1)[1]
    return [value for value in re.findall(r"'([A-Za-z][A-Za-z ]*)'", text) if (value[0].isupper() or value == 'mAh') and value not in ('MemTotal', 'MemAvailable')]


def main():
    found = set()
    for name in SOURCES:
        found.update(value for value in interface_text(ROOT / name) if wanted(value))
    for name in HELPERS:
        found.update(helper_text(ROOT / name))
    # Why a volume cannot be used: filesystem.py returns these as plain text.
    block = (ROOT / '../filesystem.py').read_text().split('def reason_text', 1)[1].split('\n\n', 1)[0]
    found.update(re.findall(r"'[A-Z_]+': '([^']+)'", block))
    found.update(phone_words(ROOT / '../sysinfo.py'))
    found.update(BACKEND_TEXT)
    ordered = sorted(found, key=lambda value: (value.lower(), value))
    (ROOT / 'i18n/messages.txt').write_text(''.join(value + '\n' for value in ordered))
    print(len(ordered), 'messages', sum(len(value.split()) for value in ordered), 'words')


if __name__ == '__main__':
    main()
