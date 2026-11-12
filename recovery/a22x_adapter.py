# SPDX-License-Identifier: MIT
"""Make an extracted ARM64 root filesystem boot on the A22x BOOT.

Shared by the PC tool and the recovery. It only writes files in the given root:
nothing from the image is executed.
"""
import os
import re
import shutil
import stat
import struct
import tarfile
from pathlib import Path

MASKED = (
    'systemd-firstboot.service', 'systemd-repart.service', 'systemd-growfs-root.service',
    'systemd-boot-update.service', 'systemd-pstore.service', 'cloud-init.service',
    'cloud-init-local.service', 'cloud-init-main.service', 'cloud-init-network.service',
    'cloud-config.service', 'cloud-final.service', 'cloud-init.target',
    'gnome-initial-setup-first-login.service', 'initial-setup.service',
    'livesys.service', 'livesys-late.service', 'snapd.seeded.service', 'snapd.service',
    'snapd.socket', 'apparmor.service', 'unattended-upgrades.service',
    'mobile-usb-gadget.service', 'mobile-usb-network-setup.service', 'eg25-manager.service',
    'multipathd.service', 'multipathd.socket', 'rpi-eeprom-update.service', 'NetworkManager-wait-online.service', 'systemd-networkd-wait-online.service',
    # Images that grow their root partition to the end of the disk at the first start. Here the
    # root is userdata: Manjaro ARM's resize.service ran sfdisk on the eMMC (2026-11-08).
    'resize.service', 'resize-fs.service', 'resizefs.service', 'resize-rootfs.service', 'rpi-resize.service',
    'rpi-resizerootfs.service', 'expand-rootfs.service', 'armbian-resize-filesystem.service', 'growpart.service',
    'systemd-growfs@.service',
    # Cloud images: agents that look for their cloud's metadata server and rewrite networking
    # (google-guest-agent restarted every 45 s on a Fedora GCE image, 2026-11-08).
    'google-guest-agent.service', 'google-osconfig-agent.service', 'google-startup-scripts.service',
    'google-shutdown-scripts.service', 'gce-workload-cert-refresh.timer', 'waagent.service',
    'hypervkvpd.service', 'hypervvssd.service', 'hypervfcopyd.service', 'amazon-ssm-agent.service',
    'nm-cloud-setup.service', 'nm-cloud-setup.timer', 'qemu-guest-agent.service', 'vmtoolsd.service',
    # Manjaro's PinePhone/PineTab first-start script (2026-11-09): it ran resize-fs on the root,
    # created the account "kde" (or "manjaro") with the password 123456 in wheel, set root's
    # password to "root" until its last line, switched the language back to en_US and enabled SDDM.
    # The recovery does its useful part itself (pacman keys, the account, the language). The
    # PinePhone USB network and modem services are for another phone's hardware.
    'pinephone-post-install.service', 'pinetab-post-install.service', 'pinetab2-post-install.service',
    'pinephone-setup-usb-network.service', 'pinephone-modem-scripts.pinephone-modem-setup.service',
    # PinePhone Pro partition mounts (Manjaro ARM); it failed here, as the root is userdata (2026-11-10).
    'pp-prepare-fstab.service',
)


class AdaptError(Exception):
    pass


def die(message):
    raise AdaptError(message)


# ---------- adaptation -------------------------------------------------------

def os_release(root):
    for name in ('etc/os-release', 'usr/lib/os-release'):
        p = root / name
        if p.is_symlink():
            target = os.readlink(p)
            p = root / target.lstrip('/') if target.startswith('/') else p.parent / target
        if p.is_file():
            pairs = (line.split('=', 1) for line in p.read_text(errors='replace').splitlines() if '=' in line)
            return {k: v.strip().strip('"') for k, v in pairs}
    die('No os-release file')


def require_target(root):
    init = root / 'sbin/init'
    if not (init.exists() or init.is_symlink()):
        init = root / 'usr/sbin/init'
    if not (init.exists() or init.is_symlink()):
        die('The rootfs has no /sbin/init')
    target = os.readlink(init) if init.is_symlink() else ''
    if 'systemd' not in target:
        die('Only systemd distributions are supported by the current device adapter')
    probe = root / 'usr/lib/systemd/systemd'
    if not probe.is_file():
        die('systemd is missing from the rootfs')
    head = probe.read_bytes()[:20]
    if head[:4] != b'\x7fELF' or head[4] != 2 or struct.unpack_from('<H', head, 18)[0] != 183:
        die('This image is not ARM64')


# Directories that hold passwords or programs only root runs.
PRIVATE_DIRS = ('/etc/a22x', '/opt/a22x-conn')


def make_parents(path):
    """The missing parents of PATH, owned by root: 0755, so user services can read them. The
    recovery runs with umask 077, and a plain mkdir left /etc/systemd/user.conf.d and
    /etc/wireplumber/wireplumber.conf.d unreadable for the user manager and WirePlumber
    (2026-11-08, Fedora). Private directories stay 0700."""
    missing = []
    parent = path.parent
    while not os.path.lexists(parent):
        missing.append(parent)
        parent = parent.parent
    for directory in reversed(missing):
        directory.mkdir()
        os.chown(directory, 0, 0)
        name = directory.as_posix()
        directory.chmod(0o700 if name.endswith(PRIVATE_DIRS) else 0o755)


def write(root, relative, text, mode=0o644):
    write_bytes(root, relative, text.encode(), mode)


def write_bytes(root, relative, data, mode=0o644):
    path = root / relative
    make_parents(path)
    if path.exists() or path.is_symlink():
        path.unlink()
    path.write_bytes(data)
    os.chown(path, 0, 0)
    path.chmod(mode)


def link_unit(root, relative, target):
    path = root / relative
    make_parents(path)
    if path.exists() or path.is_symlink():
        path.unlink()
    path.symlink_to(target)


def remove(path):
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def clear(path):
    path.mkdir(exist_ok=True)
    for child in path.iterdir():
        remove(child)


def account_file(path, key_fields):
    rows = []
    if path.is_file():
        rows = [line.split(':') for line in path.read_text(errors='replace').splitlines() if line and not line.startswith('#')]
    return rows, key_fields


LOGIN_CONFIGS = ('etc/greetd/*.toml', 'etc/lightdm/lightdm.conf', 'etc/lightdm/lightdm.conf.d/*.conf', 'etc/sddm.conf',
                 'etc/sddm.conf.d/*.conf', 'etc/gdm/custom.conf', 'etc/gdm3/custom.conf', 'etc/gdm3/daemon.conf',
                 'etc/systemd/system/getty@*.service.d/*.conf', 'etc/systemd/system/serial-getty@*.service.d/*.conf')


def rewrite_table(path, change):
    """Rewrite a colon-separated account table in place, keeping its owner and mode."""
    if not path.is_file() or path.is_symlink():
        return
    info = path.stat()
    rows = [line.split(':') for line in path.read_text(errors='replace').splitlines() if line]
    for row in rows:
        change(row)
    path.write_text(''.join(':'.join(r) + '\n' for r in rows))
    os.chown(path, info.st_uid, info.st_gid)
    path.chmod(stat.S_IMODE(info.st_mode))


def adopt_first_account(root, name):
    """Clean install: the image's own first account (UID 1000, such as mobian or alarm) becomes the
    account typed in recovery and keeps its groups. Its password is replaced at the first start."""
    passwd = root / 'etc/passwd'
    users = [line.split(':') for line in passwd.read_text(errors='replace').splitlines() if line]
    if not name or any(row[0] == name for row in users):
        return None
    first = next((row for row in users if len(row) == 7 and row[2] == '1000'), None)
    if first is None:
        return None
    old, old_home, gid = first[0], first[5], first[3]
    first[0], first[5] = name, '/home/' + name
    # The full name is the old login on Mobian ("mobian"), which login screens show (2026-11-09).
    gecos = first[4].split(',')
    if gecos[0].strip().lower() == old.lower():
        gecos[0] = name
        first[4] = ','.join(gecos)
    passwd.write_text(''.join(':'.join(r) + '\n' for r in users))
    groups = [line.split(':') for line in (root / 'etc/group').read_text(errors='replace').splitlines() if line]
    own = any(row[0] == old and len(row) > 2 and row[2] == gid for row in groups)
    members = lambda field: ','.join(name if m == old else m for m in field.split(',') if m)

    def group_row(row):
        if own and row[0] == old:
            row[0] = name
        if len(row) > 3:
            row[3] = members(row[3])

    def gshadow_row(row):
        if own and row[0] == old:
            row[0] = name
        for index in (2, 3):
            if len(row) > index:
                row[index] = members(row[index])

    def rename_first(row):
        if row and row[0] == old:
            row[0] = name

    rewrite_table(root / 'etc/group', group_row)
    rewrite_table(root / 'etc/gshadow', gshadow_row)
    for table in ('etc/shadow', 'etc/subuid', 'etc/subgid'):
        rewrite_table(root / table, rename_first)
    # AccountsService keeps a name, session and picture per login.
    for kind in ('users', 'icons'):
        record = root / 'var/lib/AccountsService' / kind / old
        if record.is_file() and not record.is_symlink() and not os.path.lexists(record.with_name(name)):
            record.rename(record.with_name(name))
    source, target = root / old_home.lstrip('/'), root / 'home' / name
    if old_home.startswith('/home/') and source.is_dir() and not source.is_symlink() and not os.path.lexists(target):
        source.rename(target)
    # Login screens that sign the default account in by name follow it.
    word = re.escape(old)
    rules = ((r'(?m)^([ \t]*user[ \t]*=[ \t]*")%s(")' % word, r'\g<1>%s\g<2>' % name),
             (r'(?m)^([ \t]*autologin-user[ \t]*=[ \t]*)%s(?=[ \t]*$)' % word, r'\g<1>%s' % name),
             (r'(?m)^([ \t]*User[ \t]*=[ \t]*)%s(?=[ \t]*$)' % word, r'\g<1>%s' % name),
             (r'(?m)^([ \t]*AutomaticLogin[ \t]*=[ \t]*)%s(?=[ \t]*$)' % word, r'\g<1>%s' % name),
             (r'(--autologin[ \t]+)%s(?=[ \t\n])' % word, r'\g<1>%s' % name))
    for pattern in LOGIN_CONFIGS:
        for path in root.glob(pattern):
            if not path.is_file() or path.is_symlink():
                continue
            text = path.read_text(errors='replace')
            changed = text
            for rule, replacement in rules:
                changed = re.sub(rule, replacement, changed)
            if changed != text:
                info = path.stat()
                path.write_text(changed)
                os.chown(path, info.st_uid, info.st_gid)
                path.chmod(stat.S_IMODE(info.st_mode))
    return old


def add_accounts(root, accounts, shadow):
    """Add the shared-home accounts with their exact names and numeric IDs."""
    passwd, group = root / 'etc/passwd', root / 'etc/group'
    users = [line.split(':') for line in passwd.read_text().splitlines() if line]
    groups = [line.split(':') for line in group.read_text().splitlines() if line]
    shells = ('/bin/bash', '/usr/bin/bash', '/bin/sh')
    removed = set()
    for item in accounts:
        # Phone images ship a default user at UID 1000. /home is shared, so the phone's
        # accounts keep their numbers and such a default user makes way for them.
        for row in list(users):
            if row[2] == str(item['uid']) and row[0] != item['name']:
                if item['uid'] < 1000:
                    die(f"UID {item['uid']} already belongs to {row[0]} in this image")
                users.remove(row)
                removed.add(row[0])
        for row in list(groups):
            if row[2] == str(item['gid']) and row[0] != item['name'] and item['gid'] >= 1000:
                groups.remove(row)
        if not any(r[0] == item['name'] for r in users):
            shell = item['shell'] if (root / item['shell'].lstrip('/')).exists() else shells[0]
            users.append([item['name'], 'x', str(item['uid']), str(item['gid']), '', item['home'], shell])
        else:
            for row in users:
                if row[0] == item['name']:
                    row[2:4], row[5] = [str(item['uid']), str(item['gid'])], item['home']
        # A group of the same name keeps the shared number too: postmarketOS ships "user" at
        # 10000, and the files of /home (group 1000) had no group in it (HOME_GROUP_MISMATCH,
        # 2026-11-10). Like the users above, a system group below 1000 is never renumbered.
        same = [g for g in groups if g[0] == item['name']]
        if same and same[0][2] != str(item['gid']):
            if item['gid'] < 1000 or int(same[0][2]) < 1000:
                die(f"Group {item['name']} has another number in this image")
            same[0][2] = str(item['gid'])
        if not same:
            groups.append([item['name'], 'x', str(item['gid']), ''])
    for name in ('wheel', 'sudo'):
        for row in groups:
            if row[0] == name:
                members = [m for m in row[3].split(',') if m]
                for item in accounts:
                    if item.get('admin') and item['name'] not in members:
                        members.append(item['name'])
                row[3] = ','.join(members)
    for row in groups:
        if len(row) > 3:
            row[3] = ','.join(m for m in row[3].split(',') if m and m not in removed)
    passwd.write_text(''.join(':'.join(r) + '\n' for r in users))
    group.write_text(''.join(':'.join(r) + '\n' for r in groups))
    table = root / 'etc/shadow'
    rows = [line.split(':') for line in table.read_text().splitlines() if line] if table.is_file() else []
    rows = [row for row in rows if row[0] not in removed]
    for item in accounts:
        secret = shadow.get(item['name'], '!')
        match = [r for r in rows if r[0] == item['name']]
        if match:
            match[0][1] = secret
        else:
            rows.append([item['name'], secret, '20000', '0', '99999', '7', '', '', ''])
    # Debian-based systems check passwords through the shadow group: keep the image's owner and mode.
    info = table.stat() if table.exists() else None
    table.write_text(''.join(':'.join(r) + '\n' for r in rows))
    os.chown(table, 0, info.st_gid if info else 0)
    table.chmod(stat.S_IMODE(info.st_mode) if info else 0o600)


def name_default_user(root, accounts):
    """postmarketOS starts its phone shell as the user named in /etc/default_user, else as UID
    10000, its installer's default (a generator of postmarketos-ui-*). Beside the phone's shared
    accounts that user is UID 1000, and plasma-mobile.service failed to start ("Failed to
    determine credentials for user '10000'", 2026-11-10). Name the first shared account there,
    for any image whose generators read that file."""
    generators = root / 'usr/lib/systemd/system-generators'
    reads = False
    if generators.is_dir():
        for item in generators.iterdir():
            try:
                if item.is_file() and b'/etc/default_user' in item.read_bytes()[:65536]:
                    reads = True
                    break
            except OSError:
                continue
    people = sorted((a for a in accounts if a['uid'] >= 1000), key=lambda a: a['uid'])
    if reads and people:
        write(root, 'etc/default_user', people[0]['name'] + '\n')


USB_SCRIPT = """#!/opt/a22x-conn/busybox sh
# Keep the BOOT-provided USB network reachable under any network manager.
B=/opt/a22x-conn/busybox
$B ifconfig usb0 172.16.42.1 netmask 255.255.255.0 up
mkdir -p /run/a22x-usb
printf 'start 172.16.42.2\\nend 172.16.42.2\\ninterface usb0\\noption subnet 255.255.255.0\\nlease_file /run/a22x-usb/leases\\n' > /run/a22x-usb/udhcpd.conf
: > /run/a22x-usb/leases
exec $B udhcpd -f /run/a22x-usb/udhcpd.conf
"""
# The kernel's splash again once the greeter or compositor has gone, for shutdown and restart:
# stopped after greetd (Before= reverses at shutdown), it shows the logo with the moving light
# until the reset (BOOT kernel 116, 2026-11-09). Older kernels have no such parameter.
SHUTDOWN_SPLASH_UNIT = """[Unit]
Description=A22x logo while the phone shuts down
Before=greetd.service display-manager.service

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/bin/true
ExecStop=/bin/sh -c 'echo 1 > /sys/module/mediatek_drm/parameters/boot_splash_show 2>/dev/null || true'

[Install]
WantedBy=multi-user.target
"""

USB_UNIT = """[Unit]
Description=A22x USB network
After=sysinit.target
Before=multi-user.target

[Service]
ExecStart=/opt/a22x-conn/a22x-usbnet.sh
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
"""
LAUNCHER_UNIT = """[Unit]
Description=a22x WMT launcher (answers CONSYS patch requests)

[Service]
ExecStart=/opt/a22x-conn/a22x-wmt serve
Restart=on-failure
"""
# LK boots the charger after a reset without Samsung's SRAM markers while a cable is
# attached. Markers written early also cover a watchdog reset during shutdown.
SRAM_GUARD = """#!/opt/a22x-conn/busybox sh
# usage: a22x-sram normal|recovery|download|clear
B=/opt/a22x-conn/busybox
case "$1" in
normal) reason=0x12345670 ;;
download) reason=0x12345671 ;;
recovery) reason=0x12345674 ;;
clear) $B devmem 0x11d534 32 0; exec $B devmem 0x11d538 32 0 ;;
*) echo "usage: a22x-sram normal|recovery|download|clear" >&2; exit 2 ;;
esac
$B devmem 0x11d534 32 0x12345678
exec $B devmem 0x11d538 32 $reason
"""
REBOOT_TOOL = """#!/bin/sh
# usage: a22x-reboot [normal|recovery|download]
target=${1:-normal}
/opt/a22x-conn/a22x-sram "$target" || exit 1
exec systemctl reboot --reboot-argument="$target"
"""
SRAM_UNIT = """[Unit]
Description=A22x reset markers: an unexpected reset returns to normal boot
DefaultDependencies=no
After=local-fs.target
Before=sysinit.target

[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/opt/a22x-conn/a22x-sram normal

[Install]
WantedBy=sysinit.target
"""
# 2026-11-12: the recovery's list of systems said "boot not yet tested" of every system for ever,
# because nothing told it that one had started. A system now says so itself, once it lets users
# in: the kernel it started with, in a file of its own /var.
STARTED_FILE = 'var/lib/a22x/started'
STARTED_UNIT = """[Unit]
Description=A22x: note that this system has started on the phone
After=systemd-user-sessions.service

[Service]
Type=oneshot
ExecStart=/opt/a22x-conn/busybox sh -c 'mkdir -p /var/lib/a22x && uname -r > /var/lib/a22x/started.new && mv -f /var/lib/a22x/started.new /var/lib/a22x/started'

[Install]
WantedBy=multi-user.target
"""
SRAM_SHUTDOWN = """#!/opt/a22x-conn/busybox sh
# A real power-off must keep the charger behaviour, so the boot-time markers go.
case "$1" in poweroff|halt) exec /opt/a22x-conn/a22x-sram clear ;; esac
"""
SSH_KEYS_UNIT = """[Unit]
Description=A22x SSH host keys (cloud images leave them to cloud-init)
Before=ssh.service sshd.service

[Service]
Type=oneshot
ExecStart=/usr/bin/ssh-keygen -A

[Install]
WantedBy=multi-user.target
"""
TRIAL_SCRIPT = """#!/opt/a22x-conn/busybox sh
# Test boots: an unconfirmed system returns to recovery by itself.
/opt/a22x-conn/busybox sleep {seconds}
[ -e /var/lib/a22x-accepted ] || exec /usr/bin/a22x-reboot recovery
"""
TRIAL_UNIT = """[Unit]
Description=A22x trial boot: return to recovery unless accepted

[Service]
ExecStart=/opt/a22x-conn/a22x-trial.sh

[Install]
WantedBy=multi-user.target
"""
# The kernel's virtual Loopback card (snd-aloop) is card 0, and PipeWire took it as the default
# output: no sound on a clean Fedora install (2026-11-08). It stays available through ALSA.
LOOPBACK_RULE = """# A22x: the virtual Loopback card is not an output for this phone's desktop.
monitor.alsa.rules = [
  {
    matches = [
      { device.name = "~alsa_card.platform-snd_aloop.*" }
      { api.alsa.card.name = "Loopback" }
    ]
    actions = {
      update-props = { device.disabled = true }
    }
  }
]
"""
LOOPBACK_RULE_LUA = """-- A22x: the virtual Loopback card is not an output for this phone's desktop (WirePlumber 0.4).
table.insert(alsa_monitor.rules, {
  matches = {
    { { "device.name", "matches", "alsa_card.platform-snd_aloop.*" } },
    { { "api.alsa.card.name", "equals", "Loopback" } },
  },
  apply_properties = { ["device.disabled"] = true },
})
"""
FIRSTBOOT_RESUME = """#!/bin/sh
# NetworkManager dispatcher: Wi-Fi came up while the first-boot setup is unfinished.
[ "$1" = wlan0 ] && [ "$2" = up ] || exit 0
[ -f /etc/a22x/firstboot.conf ] || exit 0
systemctl is-active --quiet a22x-firstboot.service && exit 0
exec systemctl start --no-block a22x-firstboot.service
"""

FIRSTBOOT_UNIT = """[Unit]
Description=A22x first boot: network, desktop and accounts
After=a22x-conn.service
# The login screen starts once the account and the session settings are in place.
Before=display-manager.service greetd.service gdm.service sddm.service lightdm.service phosh.service
ConditionPathExists=/etc/a22x/firstboot.conf

[Service]
Type=oneshot
ExecStart=/opt/a22x-conn/a22x-firstboot.sh
TimeoutStartSec=infinity
OOMPolicy=continue
StandardOutput=journal+console

[Install]
WantedBy=multi-user.target
"""
SSHD_DROPIN = 'PermitRootLogin prohibit-password\nPasswordAuthentication yes\n'
# The phone's Android and firmware partitions on the eMMC (nvdata, metadata, cache, optics, ...)
# are not drives for desktops and file managers: Xfce put them on the desktop to mount (Ubuntu
# 26.04, 2026-11-09). udisks ignores them; the microSD card (mmcblk1) stays.
UDISKS_RULE = """# Written by the A22x recovery: hide the internal eMMC partitions from udisks.
SUBSYSTEM=="block", KERNEL=="mmcblk0*", ENV{UDISKS_IGNORE}="1"
"""


def install_platform(root, args):
    """Kernel modules and firmware come from a captured tar or from an installed system."""
    platform = Path(args.platform)
    if platform.is_dir():
        # The image keeps its own /lib (the merged-/usr link); only its owner follows the platform
        # source, which the final check compares with the profile: postmarketOS ships the link as
        # root:1000, and its import stopped at PLATFORM_METADATA_MISMATCH, path "lib" (2026-11-10).
        source_lib, image_lib = platform / 'lib', root / 'lib'
        if source_lib.is_symlink() and image_lib.is_symlink() and os.readlink(source_lib) == os.readlink(image_lib):
            info = os.lstat(source_lib)
            os.chown(image_lib, info.st_uid, info.st_gid, follow_symlinks=False)
        for relative in args.platform_paths:
            source = platform / relative
            if not os.path.lexists(source):
                die(f'Installed system lacks {relative}')
            copy_entry(source, root / relative)
        # Optional files come from the installed system, else from the copy kept in recovery.
        fallback = getattr(args, 'platform_fallback', None)
        for relative in getattr(args, 'platform_optional', ()):
            for base in (platform, Path(fallback) if fallback else None):
                if base is not None and os.path.lexists(base / relative):
                    copy_entry(base / relative, root / relative)
                    break
        return
    with tarfile.open(platform) as tar:
        for item in tar:
            if item.name in ('lib', './lib'):
                # The image keeps its own /lib (the merged-/usr link); only its owner follows the
                # platform profile, which the final check compares: postmarketOS ships the link
                # as root:1000, and its import stopped at PLATFORM_METADATA_MISMATCH (2026-11-10).
                link = root / 'lib'
                if link.is_symlink():
                    os.chown(link, item.uid, item.gid, follow_symlinks=False)
                continue
            tar.extract(item, root, set_attrs=False, numeric_owner=True, filter='fully_trusted')
            target = root / item.name
            os.chown(target, item.uid, item.gid, follow_symlinks=False)
            if not item.issym():
                target.chmod(item.mode)


def copy_entry(source, target):
    info = os.lstat(source)
    make_parents(target)
    if stat.S_ISDIR(info.st_mode):
        target.mkdir(exist_ok=True)
        for child in source.iterdir():
            copy_entry(child, target / child.name)
    else:
        if os.path.lexists(target):
            remove(target)
        if stat.S_ISLNK(info.st_mode):
            target.symlink_to(os.readlink(source))
        else:
            shutil.copyfile(source, target)
    os.chown(target, info.st_uid, info.st_gid, follow_symlinks=False)
    if not stat.S_ISLNK(info.st_mode):
        target.chmod(stat.S_IMODE(info.st_mode))


def adapt(root, args, meta):
    identity = os_release(root)
    require_target(root)
    # The image kernel and bootloader cannot run here: BOOT stays in place.
    for child in (root / 'usr/lib/modules').glob('*') if (root / 'usr/lib/modules').is_dir() else ():
        remove(child)
    clear(root / 'boot')
    if not args.keep_firmware:
        for child in (root / 'usr/lib/firmware').glob('*') if (root / 'usr/lib/firmware').is_dir() else ():
            remove(child)
    install_platform(root, args)
    if not (root / 'lib').is_symlink() or os.readlink(root / 'lib') != 'usr/lib':
        die('Only merged-/usr systems are supported: /lib must link to usr/lib')
    for name in ('wmtload.py', 'wmtlaunch.py'):
        remove(root / 'opt/a22x-conn' / name)
    (root / 'opt/a22x-conn').mkdir(parents=True, exist_ok=True)
    shutil.copyfile(args.helpers / 'a22x-wmt', root / 'opt/a22x-conn/a22x-wmt')
    shutil.copyfile(args.helpers / 'busybox', root / 'opt/a22x-conn/busybox')
    for name in ('a22x-wmt', 'busybox'):
        os.chown(root / 'opt/a22x-conn' / name, 0, 0)
        (root / 'opt/a22x-conn' / name).chmod(0o755)
    write(root, 'opt/a22x-conn/conn-start.sh', (args.helpers / 'conn-start.sh').read_text(), 0o755)
    write(root, 'opt/a22x-conn/a22x-usbnet.sh', USB_SCRIPT, 0o755)
    write(root, 'etc/systemd/system/a22x-wmt-launcher.service', LAUNCHER_UNIT)
    write(root, 'etc/systemd/system/a22x-usbnet.service', USB_UNIT)
    link_unit(root, 'etc/systemd/system/multi-user.target.wants/a22x-usbnet.service',
              '/etc/systemd/system/a22x-usbnet.service')
    write(root, 'etc/systemd/system/a22x-shutdown-splash.service', SHUTDOWN_SPLASH_UNIT)
    link_unit(root, 'etc/systemd/system/multi-user.target.wants/a22x-shutdown-splash.service',
              '/etc/systemd/system/a22x-shutdown-splash.service')
    write(root, 'opt/a22x-conn/a22x-sram', SRAM_GUARD, 0o755)
    write(root, 'usr/bin/a22x-reboot', REBOOT_TOOL, 0o755)
    write(root, 'etc/systemd/system/a22x-sram.service', SRAM_UNIT)
    link_unit(root, 'etc/systemd/system/sysinit.target.wants/a22x-sram.service',
              '/etc/systemd/system/a22x-sram.service')
    write(root, 'usr/lib/systemd/system-shutdown/a22x-sram', SRAM_SHUTDOWN, 0o755)
    write(root, 'etc/systemd/system/a22x-started.service', STARTED_UNIT)
    link_unit(root, 'etc/systemd/system/multi-user.target.wants/a22x-started.service',
              '/etc/systemd/system/a22x-started.service')
    # A root copied from another phone or kept from an earlier installation has not started here.
    remove(root / STARTED_FILE)
    remove(root / 'etc/systemd/network/10-usb0.network')
    for unit in MASKED:
        link_unit(root, f'etc/systemd/system/{unit}', '/dev/null')
    if args.console:
        link_unit(root, 'etc/systemd/system/default.target', '/usr/lib/systemd/system/multi-user.target')
    write(root, 'etc/fstab', '# A22x: the root filesystem is mounted by BOOT.\n')
    # The distribution's ID names the phone: a slot id such as 20261002-0737-postmarketos-...
    # gave the host name 20261002-a22x (2026-11-10).
    name = re.sub(r'[^a-z0-9]+', '-', identity.get('ID', '').lower()).strip('-') or args.id.split('-')[0]
    write(root, 'etc/hostname', name[:40] + '-a22x\n')
    # This panel needs kmsro to reach the GPU, for any display manager or session.
    gpu = '[Manager]\nDefaultEnvironment=MESA_LOADER_DRIVER_OVERRIDE=kmsro\n'
    write(root, 'etc/systemd/system.conf.d/a22x-gpu.conf', gpu)
    write(root, 'etc/systemd/user.conf.d/a22x-gpu.conf', gpu)
    write(root, 'etc/machine-id', '')
    write(root, 'etc/wireplumber/wireplumber.conf.d/50-a22x-loopback.conf', LOOPBACK_RULE)
    write(root, 'etc/wireplumber/main.lua.d/51-a22x-loopback.lua', LOOPBACK_RULE_LUA)
    write(root, 'etc/udev/rules.d/80-a22x-hide-internal.rules', UDISKS_RULE)
    selinux = root / 'etc/selinux/config'
    if selinux.is_file():
        selinux.write_text(re.sub(r'(?m)^SELINUX=.*$', 'SELINUX=disabled', selinux.read_text()))
    (root / 'etc/cloud').mkdir(exist_ok=True) if (root / 'etc/cloud').is_dir() else None
    if (root / 'etc/cloud').is_dir():
        write(root, 'etc/cloud/cloud-init.disabled', '')
    add_accounts(root, meta['accounts'], meta['shadow'])
    name_default_user(root, meta['accounts'])
    ssh = next((p for p in ('usr/lib/systemd/system/sshd.service', 'usr/lib/systemd/system/ssh.service')
                if (root / p).is_file()), None)
    if ssh:
        name = Path(ssh).name
        link_unit(root, f'etc/systemd/system/multi-user.target.wants/{name}', '/' + ssh)
        remove(root / 'etc/systemd/system/ssh.socket')
        write(root, 'etc/ssh/sshd_config.d/10-a22x.conf', SSHD_DROPIN)
        write(root, 'etc/systemd/system/a22x-sshkeys.service', SSH_KEYS_UNIT)
        link_unit(root, 'etc/systemd/system/multi-user.target.wants/a22x-sshkeys.service',
                  '/etc/systemd/system/a22x-sshkeys.service')
    elif not args.allow_no_ssh:
        die('This image has no OpenSSH server; rerun with --allow-no-ssh to rely on the console only')
    if getattr(args, 'firstboot', None):
        write(root, 'etc/a22x/firstboot.conf', args.firstboot, 0o600)
        write(root, 'opt/a22x-conn/a22x-firstboot.sh', (args.helpers / 'a22x-firstboot.sh').read_text(), 0o755)
        # What a distribution lacks for this phone is built in the new system (Arch Linux ARM:
        # phoc's wlroots fix and Phrog, 2026-11-09): the scripts, with the patch and the signing
        # keys they check the sources against.
        build = args.helpers / 'build'
        if build.is_dir():
            for source in sorted(build.rglob('*')):
                if source.is_file() and not source.is_symlink():
                    write_bytes(root, 'opt/a22x-conn/build/' + source.relative_to(build).as_posix(), source.read_bytes(),
                                0o755 if source.suffix == '.sh' else 0o644)
        write(root, 'etc/systemd/system/a22x-firstboot.service', FIRSTBOOT_UNIT)
        # Without a network at the first start, the setup resumes once Wi-Fi comes up.
        write(root, 'etc/NetworkManager/dispatcher.d/90-a22x-firstboot', FIRSTBOOT_RESUME, 0o755)
        link_unit(root, 'etc/systemd/system/multi-user.target.wants/a22x-firstboot.service',
                  '/etc/systemd/system/a22x-firstboot.service')
    if args.trial_seconds:
        write(root, 'opt/a22x-conn/a22x-trial.sh', TRIAL_SCRIPT.format(seconds=args.trial_seconds), 0o755)
        write(root, 'etc/systemd/system/a22x-trial.service', TRIAL_UNIT)
        link_unit(root, 'etc/systemd/system/multi-user.target.wants/a22x-trial.service',
                  '/etc/systemd/system/a22x-trial.service')
    if args.authorized_key:
        key = root / 'root/.ssh/authorized_keys'
        key.parent.mkdir(parents=True, exist_ok=True)
        key.parent.chmod(0o700)
        os.chown(key.parent, 0, 0)
        text = args.authorized_key
        key.write_text(text if text.startswith('ssh-') else Path(text).read_text())
        os.chown(key, 0, 0)
        key.chmod(0o600)
    for name in ('dev', 'proc', 'sys', 'run'):
        path = root / name
        if path.is_symlink():
            path.unlink()
        clear(path)
    for name in ('tmp', 'var/tmp'):
        clear(root / name)
    (root / 'tmp').chmod(0o1777)
    (root / 'var/tmp').chmod(0o1777)
    for child in (root / 'home').glob('*') if (root / 'home').is_dir() else ():
        remove(child)
    (root / 'home').mkdir(exist_ok=True)
    remove(root / 'lost+found') if (root / 'lost+found').exists() else None
    for special in walk_special(root):
        special.unlink()
    normalize_links(root)
    meta['ssh'] = bool(ssh)
    meta['python3'] = (root / 'usr/bin/python3').exists() or (root / 'usr/bin/python3').is_symlink()
    return identity


def normalize_links(root):
    """A relative link with surplus '..' works in Linux but names a path outside the
    rootfs; give it the absolute target it really resolves to."""
    for base, dirs, files in os.walk(root):
        for name in dirs + files:
            path = Path(base) / name
            if not path.is_symlink():
                continue
            target = os.readlink(path)
            if target.startswith('/'):
                continue
            depth, surplus = len(Path(base).relative_to(root).parts), False
            out = []
            for part in target.split('/'):
                if part == '..':
                    if out:
                        out.pop()
                    elif depth:
                        depth -= 1
                    else:
                        surplus = True
                elif part not in ('', '.'):
                    out.append(part)
            if surplus:
                path.unlink()
                path.symlink_to('/' + '/'.join(out))


def walk_special(root):
    for base, dirs, files in os.walk(root):
        for name in files:
            path = Path(base) / name
            mode = path.lstat().st_mode
            if not (stat.S_ISREG(mode) or stat.S_ISLNK(mode)):
                yield path


