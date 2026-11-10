#!/usr/bin/env python3
"""Build a dedicated 40 MiB A22x RECOVERY image, entirely offline.

Inputs are the exact extracted components and ARM runtime staged by the
2026-11-05 inventory. This program never discovers a phone or flashes a device.
"""
# SPDX-License-Identifier: MIT
from pathlib import Path
import argparse
import datetime
import gzip
import hashlib
import json
import os
import shutil
import stat
import subprocess
import sys
from types import SimpleNamespace

PARTITION_BYTES = 41943040
UI_BINARY_SHA256 = '6b6a8b385a722cc5ed6d59dc8922fc4e1e02e9098ec2a7073795f7fe2444881e'
PINS = {
    'inputs/working-boot/kernel': 'ee0140c23f808ca482f846b0b97281ee938d6f263b81872977470cb629fed34c',
    'inputs/working-boot/dtb': 'dab40e2b791bec807d9beb8a75ea160b23bd172c63b421add5f48b8cfd601755',
    'inputs/stock-recovery/recovery_dtbo': '2efde7e86a5718cbcb926912ce84e3a96c5d292b67488598403118de9e0f3bce',
    'inputs/runtime/bin/busybox': '7d93682be37cf6ed46699f6fff546b80bf133cf8ec342c2d04bc23387f78bc34',
    'inputs/runtime/lib/firmware/Himax_firmware_fw_jdi.bin': 'fd4df971965dfe5e550ba4a6a9e94e1b67d5ca128dad3eb74ebaa1873d591f1a',
    'python-runtime-manifest.json': '6f002c21db374080a2e6753e3fa459fb600508b2cec4d2415ec987a889ce4393',
    'service-runtime-v03-manifest.json': '22450449b37952e919d6a5f5f5220014a134fc2121a87abd3b37c1346099b8d7',
    'compact-runtime-v05-manifest.json': '7842c38fb417ffae50e56667ca646b0ad34bb7eeb4f1f5906b84a8869b0fd2d7',
    'mtp-v05-package-v2/manifest.json': 'ed7b036aa821404d57d1ce2da840faadfb4b4bf4fa51944099ce686389843a9e',
    'terminal-shell-v05-package/manifest.json': '43fad22a263421493d956ae89364182da9f0da08f00993f5d75b3ff23b583f11',
}
GEOMETRY = ('--header_version', '--pagesize', '--base', '--kernel_offset',
            '--ramdisk_offset', '--second_offset', '--tags_offset', '--dtb_offset', '--board')


def require(value, message):
    if not value:
        raise RuntimeError(message)


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for data in iter(lambda: f.read(1024 * 1024), b''):
            h.update(data)
    return h.hexdigest()


def write_json(path, value):
    with Path(path).open('x') as f:
        json.dump(value, f, indent=2, ensure_ascii=False)
        f.write('\n')


def newc(root):
    result = bytearray()
    index = []
    inode = 1

    def put(name, mode, data=b'', rmajor=0, rminor=0):
        nonlocal inode
        encoded = name.encode() + b'\0'
        fields = [inode, mode, 0, 0, 2 if stat.S_ISDIR(mode) else 1, 0,
                  len(data), 0, 0, rmajor, rminor, len(encoded), 0]
        result.extend(b'070701' + b''.join(f'{x:08x}'.encode() for x in fields))
        result.extend(encoded)
        result.extend(bytes((-len(result)) % 4))
        result.extend(data)
        result.extend(bytes((-len(result)) % 4))
        index.append({'path': name, 'mode': oct(mode), 'bytes': len(data),
                      'sha256': hashlib.sha256(data).hexdigest()})
        inode += 1

    put('.', stat.S_IFDIR | 0o755)
    for path in sorted(root.rglob('*')):
        name = path.relative_to(root).as_posix()
        mode = path.lstat().st_mode
        if stat.S_ISDIR(mode):
            put(name, stat.S_IFDIR | 0o755)
        elif stat.S_ISLNK(mode):
            put(name, stat.S_IFLNK | 0o777, os.readlink(path).encode())
        elif stat.S_ISREG(mode):
            put(name, stat.S_IFREG | (0o755 if mode & 0o111 else 0o644), path.read_bytes())
        else:
            raise RuntimeError(f'Unexpected special input: {path}')
    put('dev/console', stat.S_IFCHR | 0o600, rmajor=5, rminor=1)
    put('dev/null', stat.S_IFCHR | 0o666, rmajor=1, rminor=3)
    put('TRAILER!!!', 0)
    result.extend(bytes((-len(result)) % 512))
    return bytes(result), index


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--inputs', type=Path, required=True)
    ap.add_argument('--source', type=Path, default=Path(__file__).resolve().parents[1])
    ap.add_argument('--output', type=Path, required=True, help='New directory, never overwritten')
    ap.add_argument('--qualification', type=Path,
                    help='Completed real-source disposable-image qualification capsule')
    ap.add_argument('--qualification-sha256',
                    help='Independently recorded SHA256 of the qualification capsule')
    ap.add_argument('--distro-profile', type=Path, required=True,
                    help='Audited exact kernel and device-adapter profile for prepared ARM64 roots')
    ap.add_argument('--distro-profile-sha256', required=True,
                    help='Independently recorded SHA256 of the platform profile file')
    ap.add_argument('--stock-profile', type=Path, required=True,
                    help='Qualified fixed stock bundle and retained-firmware read-only proof')
    ap.add_argument('--stock-profile-sha256', required=True,
                    help='Independently recorded SHA256 of the stock installation profile')
    args = ap.parse_args()
    inputs, source, out = args.inputs.resolve(), args.source.resolve(), args.output.resolve()
    require(not out.exists(), 'Output directory already exists; preserve previous build evidence')
    for name, expected in PINS.items():
        require(sha(inputs / name) == expected, f'Input SHA mismatch: {name}')
    baseline = json.loads((inputs / 'input-manifest.json').read_text())
    runtime = json.loads((inputs / 'python-runtime-manifest.json').read_text())
    pyr = inputs / 'inputs/python-runtime'
    for item in runtime['files']:
        path = pyr / item['path']
        if item.get('type') == 'symlink':
            require(path.is_symlink() and os.readlink(path) == item['target'], f'Python symlink mismatch: {path}')
        else:
            require(sha(path) == item['sha256'], f'Python runtime SHA mismatch: {path}')
    services = json.loads((inputs / 'service-runtime-v03-manifest.json').read_text())
    service_root = inputs / 'inputs/service-runtime-v03'
    require(services['verified_apk_signatures'] and services['architecture'] == 'aarch64',
            'Service inputs must be signed ARM packages')
    require({p.relative_to(service_root).as_posix() for p in service_root.rglob('*') if not p.is_dir()}
            == {item['path'] for item in services['files']}, 'Unlisted service runtime file')
    for item in services['files']:
        path = service_root / item['path']
        if item['type'] == 'symlink':
            require(path.is_symlink() and os.readlink(path) == item['target'], f'Service symlink mismatch: {path}')
        else:
            require(not path.is_symlink() and sha(path) == item['sha256'], f'Service runtime SHA mismatch: {path}')
    compact = json.loads((inputs / 'compact-runtime-v05-manifest.json').read_text())
    compact_root = inputs / 'inputs/compact-runtime-v05'
    require(compact['verified_apk_signatures'] and compact['architecture'] == 'aarch64',
            'Compact-image inputs must be signed ARM packages')
    require({p.relative_to(compact_root).as_posix() for p in compact_root.rglob('*') if not p.is_dir()}
            == {item['path'] for item in compact['files']}, 'Unlisted compact runtime file')
    for item in compact['files']:
        path = compact_root / item['path']
        require(item['type'] == 'file' and not path.is_symlink() and sha(path) == item['sha256'],
                f'Compact runtime SHA mismatch: {path}')
    usb_package = inputs / 'mtp-v05-package-v2'
    usb = json.loads((usb_package / 'manifest.json').read_text())
    usb_root = usb_package / 'overlay'
    require(usb['schema'] == 'a22x.usb-files.package.v1' and usb['static_aarch64'],
            'MTP inputs must be the reviewed static ARM package')
    require({item['path'] for item in usb['overlay_files']} == {
        '/bin/umtprd', '/bin/a22x-mtp-sandbox', '/etc/a22x-recovery/usb-files-runtime.json'},
        'Unexpected MTP overlay target')
    require({p.relative_to(usb_root).as_posix() for p in usb_root.rglob('*') if not p.is_dir()}
            == {item['path'].lstrip('/') for item in usb['overlay_files']},
            'Unlisted MTP runtime file')
    for item in usb['overlay_files']:
        path = usb_root / item['path'].lstrip('/')
        require(path.is_file() and not path.is_symlink() and sha(path) == item['sha256'],
                f'MTP runtime SHA mismatch: {path}')
    for name, expected in usb['source_files'].items():
        require(sha(source / name) == expected, f'MTP reviewed source changed: {name}')
    usb_license = usb_package / 'source/patched-umtprd/LICENSE'
    require(sha(usb_license) == '8ceb4b9ee5adedde47b31e975c1d90c73ad27b6b165a1dcd80c7c545eb65b903',
            'MTP upstream license changed')
    shell_package = inputs / 'terminal-shell-v05-package'
    terminal_shell = json.loads((shell_package / 'manifest.json').read_text())
    shell_root = shell_package / 'overlay'
    require(terminal_shell.get('schema') == 'a22x.terminal-shell.package.v1'
            and terminal_shell.get('ok') is True and terminal_shell.get('static_aarch64') is True
            and terminal_shell.get('architecture') == 'aarch64'
            and terminal_shell.get('runtime_argv') == ['ash', '-i']
            and terminal_shell.get('runtime_locale') == 'C.UTF-8'
            and terminal_shell.get('global_busybox_changed') is False,
            'Terminal requires the independently accepted static UTF-8 shell package')
    require(terminal_shell['overlay_files'] == [{
        'path': '/bin/a22x-terminal-shell', 'bytes': 1778336, 'mode': 0o755,
        'sha256': 'feb0c40dc4f6f292830d309b8112601ba625bedbac2b29bcdb31c6ceae7a4a9f'}],
        'The terminal overlay may add only its exact dedicated shell')
    require({p.relative_to(shell_package).as_posix() for p in shell_package.rglob('*') if not p.is_dir()}
            == set(terminal_shell['files']) | {'manifest.json'}, 'Unlisted terminal shell package file')
    for name, item in terminal_shell['files'].items():
        path = shell_package / name
        require(path.is_file() and not path.is_symlink() and path.stat().st_size == item['bytes']
                and sha(path) == item['sha256'], f'Terminal shell package SHA mismatch: {name}')
    for name, expected in terminal_shell['source_files'].items():
        require(sha(source / name) == expected, f'Terminal reviewed source changed: {name}')
    shell_file = shell_root / 'bin/a22x-terminal-shell'
    require(stat.S_IMODE(shell_file.stat().st_mode) == 0o755, 'Terminal shell executable mode differs')
    shell_license = shell_package / 'source/COPYING'
    require(sha(shell_license) == 'bbfc9843646d483c334664f651c208b9839626891d8f17604db2146962f43548',
            'Terminal shell GPL license changed')
    require(sha(args.distro_profile) == args.distro_profile_sha256, 'Distro platform profile SHA mismatch')
    sys.path.insert(0, str(source))
    import distro
    distro.PROFILE_PATH = args.distro_profile.resolve()
    platform_profile = distro.load_profile(SimpleNamespace(mode='device'))
    platform_profile_id = distro.digest(platform_profile)
    require(sha(args.stock_profile) == args.stock_profile_sha256, 'Stock profile file SHA mismatch')
    import install as image_installer
    # Offline validation does not instantiate a physical DeviceContext. The
    # newc writer assigns root ownership to the packaged, SHA-pinned profile.
    stock_profile = image_installer.load_stock_profile(
        SimpleNamespace(mode='fixture', profile_path=args.stock_profile.resolve()), required=True)
    require(stock_profile['file_sha256'] == args.stock_profile_sha256
            and stock_profile['qualified'] is True and stock_profile['blockers'] == [],
            'This release requires a qualified, unblocked stock profile')
    require(set(stock_profile.get('protected_prefixes', {})) == {'bootloader', 'lk', 'tee1'},
            'Stock profile lacks the exact retained-firmware proof set')
    capsule = None
    require(bool(args.qualification) == bool(args.qualification_sha256),
            'Qualification path and recorded SHA256 must be supplied together')
    if args.qualification:
        require(sha(args.qualification) == args.qualification_sha256, 'Qualification capsule SHA mismatch')
        capsule = json.loads(args.qualification.read_text())
        sys.path.insert(0, str(source / 'backend'))
        import qualification
        record = capsule['implementation']
        require(qualification.make_capsule(record, capsule['acceptance']) == capsule,
                'Qualification capsule failed its complete acceptance seal')
        require(record['toolchain'] == json.loads((compact_root / 'etc/a22x-recovery/linux-toolchain.json').read_text()),
                'Qualification belongs to another ARM toolchain')
        require(set(record['sources']) == set(qualification.SOURCE_FILES), 'Incomplete qualified source set')
        for name, expected in record['sources'].items():
            require(sha(source / name) == expected, f'Qualified implementation changed: {name}')
    out.mkdir(parents=True)
    logs = out / 'logs'
    logs.mkdir()

    def run(label, argv, *, data=None, env=None, expected=0, cwd=None):
        result = subprocess.run([str(v) for v in argv], input=data, capture_output=True,
                                timeout=180, env=env, cwd=cwd)
        (logs / (label + '.stdout')).write_bytes(result.stdout)
        (logs / (label + '.stderr')).write_bytes(result.stderr)
        write_json(logs / (label + '.command.json'), {'argv': [str(v) for v in argv],
                   'returncode': result.returncode, 'expected_returncode': expected})
        require(result.returncode == expected, f'{label} failed: rc={result.returncode}; see {logs}')
        return result.stdout

    root = out / 'rootfs'
    shutil.copytree(pyr, root, symlinks=True)
    shutil.copytree(inputs / 'inputs/runtime', root, symlinks=True, dirs_exist_ok=True)
    for item in services['overrides']:
        require(sha(root / item['path']) == item['previous_sha256'], 'Unexpected shared-library baseline')
    shutil.copytree(service_root, root, symlinks=True, dirs_exist_ok=True)
    for item in compact['files']:
        require(not (root / item['path']).exists(), 'Compact overlay must not replace baseline files')
    shutil.copytree(compact_root, root, symlinks=True, dirs_exist_ok=True)
    for item in usb['overlay_files']:
        require(not os.path.lexists(root / item['path'].lstrip('/')),
                'MTP overlay must not replace baseline files')
    shutil.copytree(usb_root, root, symlinks=True, dirs_exist_ok=True)
    require(not os.path.lexists(root / 'bin/a22x-terminal-shell'),
            'Dedicated terminal shell must not replace a baseline file')
    shutil.copytree(shell_root, root, symlinks=True, dirs_exist_ok=True)
    require(sha(root / 'bin/busybox') == PINS['inputs/runtime/bin/busybox'],
            'Recovery-wide BusyBox changed while adding the terminal shell')
    for folder in ('dev', 'proc', 'sys', 'run', 'tmp', 'mnt/backup', 'mnt/volumes', 'sbin',
                   'root', 'var/empty', 'var/log',
                   'etc/a22x-recovery', 'opt/a22x-recovery/backend',
                   'usr/share/licenses/a22x-recovery-ui', 'usr/share/licenses/umtprd',
                   'usr/share/licenses/a22x-terminal-shell'):
        (root / folder).mkdir(parents=True, exist_ok=True)
    shutil.copy2(source / 'ramdisk/init', root / 'init')
    (root / 'init').chmod(0o755)
    for file in sorted((source / 'backend').glob('*.py')):
        shutil.copy2(file, root / 'opt/a22x-recovery/backend' / file.name)
    for name in ('storage.py', 'diagnostics.py', 'filesystem.py', 'management.py',
                 'operation_lock.py', 'ssh_service.py', 'admin_session.py', 'display.py', 'usb_files.py',
                 'terminal.py', 'distro.py', 'install.py'):
        shutil.copy2(source / name, root / 'opt/a22x-recovery' / name)
    systems_cli = root / 'bin/a22x-systems'
    systems_cli.write_text('#!/bin/sh\nexec /usr/bin/python3 /opt/a22x-recovery/distro.py --device "$@"\n')
    systems_cli.chmod(0o755)
    install_cli = root / 'bin/a22x-install'
    install_cli.write_text('#!/bin/sh\nexec /usr/bin/python3 /opt/a22x-recovery/install.py --mode device "$@"\n')
    install_cli.chmod(0o755)
    (root / 'opt/a22x-recovery/admin_session.py').chmod(0o755)
    shutil.copy2(source / 'ramdisk/sshd_config', root / 'etc/a22x-recovery/sshd_config')
    shutil.copy2(inputs / 'service-runtime-v03-manifest.json', root / 'etc/a22x-recovery/service-runtime.json')
    shutil.copy2(inputs / 'compact-runtime-v05-manifest.json', root / 'etc/a22x-recovery/compact-runtime.json')
    shutil.copy2(usb_package / 'manifest.json', root / 'etc/a22x-recovery/usb-files-package.json')
    shutil.copy2(usb_license, root / 'usr/share/licenses/umtprd/LICENSE')
    shutil.copy2(shell_package / 'manifest.json', root / 'etc/a22x-recovery/terminal-shell-package.json')
    shutil.copy2(shell_license, root / 'usr/share/licenses/a22x-terminal-shell/COPYING')
    shutil.copy2(args.distro_profile, root / 'etc/a22x-recovery/distro-platform.json')
    shutil.copy2(args.stock_profile, root / 'etc/a22x-recovery/install-stock-profile.json')
    if capsule:
        shutil.copy2(args.qualification, root / 'etc/a22x-recovery/linux-workflow-qualification.json')
    # AuthenticationMethods publickey and disabled password methods enforce key-only
    # access. The root account must not be locked, which also denies public keys.
    (root / 'etc/passwd').write_text('root::0:0:Recovery administrator:/root:/bin/sh\nsshd:x:22:22:OpenSSH privilege separation:/var/empty:/bin/false\n')
    (root / 'etc/group').write_text('root:x:0:\nsshd:x:22:\n')
    capabilities = json.loads((source / 'kernel/k107-capabilities.json').read_text())
    require(capabilities['kernel_sha256'] == PINS['inputs/working-boot/kernel'],
            'Capability manifest does not describe the packaged kernel')
    shutil.copy2(source / 'kernel/k107-capabilities.json', root / 'etc/a22x-recovery/kernel-capabilities.json')
    shutil.copy2(source / 'backend/device.example.json', root / 'etc/a22x-recovery/device.json')
    for name in ('LICENSE', 'LICENSE.fonts', 'font-provenance.json', 'font-extra-provenance.json',
                 'terminal-font-provenance.json'):
        if (source / 'ui' / name).is_file():
            shutil.copy2(source / 'ui' / name, root / 'usr/share/licenses/a22x-recovery-ui' / name)
    write_json(root / 'etc/a22x-recovery/build.json', {
        'product': 'A22x Mainline Recovery', 'version': '0.5-dev',
        'target_partition': 'recovery', 'kernel': '7.2.0-a22x+ #107',
        'root': 'initramfs', 'decryption': False,
        'device_restore_qualified': bool(capsule),
        'qualified_linux_workflow': 'linux-partclone-v2' if capsule else None,
        'linux_qualification_capsule_id': capsule['capsule_id'] if capsule else None,
        'linux_compact_backup': 'partclone.ext4.v2+zstd',
        'linux_wipe_target': 'userdata' if capsule else None,
        'linux_restore_targets': ['userdata', 'boot'] if capsule else [],
        'automatic_internal_mounts': False,
        'explicit_filesystem_mounts': True, 'file_manager': True,
        'usb_ssh_sftp': True, 'ssh_key_only': True, 'ssh_host_key_ephemeral': True,
        'usb_mtp_explicit_export': True, 'usb_mtp_automatic_start': False,
        'usb_mtp_volume_count': 1, 'usb_mtp_unix_metadata_preservation': False,
        'onscreen_terminal': True, 'terminal_shell': '/bin/a22x-terminal-shell',
        'terminal_shell_sha256': terminal_shell['overlay_files'][0]['sha256'],
        'terminal_shell_package_sha256': PINS['terminal-shell-v05-package/manifest.json'],
        'terminal_utf8_line_editing': True, 'terminal_automatic_start': False,
        'distro_manager': True, 'distro_import_format': 'prepared-arm64-rootfs-bundle-v1',
        'distro_shared_paths': ['/home'], 'distro_retains_boot': True,
        'distro_switch_whole_atomic': False, 'distro_target_hardware_boot_tested': False,
        'distro_platform_sha256': platform_profile_id,
        'distro_platform_file_sha256': args.distro_profile_sha256,
        'flash_boot': True, 'stock_android_install': True,
        'stock_android_profile': stock_profile['id'],
        'stock_android_firmware': stock_profile['firmware'],
        'stock_android_profile_file_sha256': args.stock_profile_sha256,
        'stock_android_manifest_sha256': stock_profile['manifest_sha256'],
        'stock_android_write_order': list(image_installer.STOCK_ORDER),
        'stock_android_hardware_boot_tested': False,
        'installer_userdata_write': False, 'installer_android_mount': False,
        'installer_android_backup_restore': False, 'installer_automatic_reboot': False,
        'ssh_authorized_key_fingerprint': services['authorized_key_fingerprint'],
        'diagnostic_export': True, 'persistent_ui_logs_within_boot': True,
        'ext4_tools': True, 'streaming_restore_engine': True,
        'file_browser_swipe': True, 'display_refresh_control': [60, 90],
        'volume_list_swipe': True, 'editor_pixel_scroll': True,
        'inertial_scrolling': True, 'native_vector_icons': True,
        'display_mode_from_drm': True, 'display_mode_persistent_within_boot': True,
        'display_brightness_control': True, 'display_power_button_blank': True,
        'display_backlight_crash_guard': True,
        'source_sha256': {str(p.relative_to(source)): sha(p) for p in sorted(source.rglob('*'))
                          if p.is_file() and p.suffix in ('.py', '.c', '.h', '.json')
                          and '__pycache__' not in p.parts and 'previews' not in p.parts
                          and 'test-results' not in p.name}})
    run('compile-ui', ['aarch64-linux-gnu-gcc', '-std=c11', '-Os', '-static',
                       '-Wall', '-Wextra', '-Werror', '-fstack-protector-strong', '-D_FORTIFY_SOURCE=2',
                       '-Wl,--build-id=none', 'recovery_ui.c', 'json_min.c',
                       '-o', root / 'bin/recovery-ui'], cwd=source / 'ui')
    run('strip-ui', ['aarch64-linux-gnu-strip', '--strip-unneeded', root / 'bin/recovery-ui'])
    require(sha(root / 'bin/recovery-ui') == UI_BINARY_SHA256,
            'Packaged UI differs from the separately built RAM acceptance candidate')
    run('compile-power', ['aarch64-linux-gnu-gcc', '-std=c11', '-Os', '-static',
                         '-Wall', '-Wextra', '-Werror', '-fstack-protector-strong', '-D_FORTIFY_SOURCE=2',
                         '-Wl,--build-id=none', source / 'tools/recovery_power.c',
                         '-o', root / 'bin/recovery-power'])
    run('strip-power', ['aarch64-linux-gnu-strip', '--strip-unneeded', root / 'bin/recovery-power'])
    run('power-arm-help', ['qemu-aarch64', root / 'bin/recovery-power', '--help'])
    elf = run('ui-elf', ['aarch64-linux-gnu-readelf', '-h', '-l', '-d', root / 'bin/recovery-ui']).decode()
    require('AArch64' in elf and 'INTERP' not in elf, 'UI must be static AArch64')
    shell_elf = run('terminal-shell-elf', ['aarch64-linux-gnu-readelf', '-h', '-l', '-d',
                                        root / 'bin/a22x-terminal-shell']).decode()
    require('AArch64' in shell_elf and 'INTERP' not in shell_elf and '(NEEDED)' not in shell_elf,
            'Dedicated terminal shell must remain static AArch64')
    run('ui-arm-selftest', ['qemu-aarch64', root / 'bin/recovery-ui', '--self-test'])
    run('init-arm-syntax', ['qemu-aarch64', root / 'bin/busybox', 'sh', '-n', root / 'init'])
    env = dict(os.environ, PYTHONHOME=str(root / 'usr'), LD_LIBRARY_PATH=str(root / 'usr/lib'),
               PYTHONDONTWRITEBYTECODE='1')
    smoke = "import http.server,dataclasses,hashlib,gzip,fcntl,json,subprocess,runpy,sys; " \
            "sys.path.insert(0," + repr(str(root / 'opt/a22x-recovery')) + "); " \
            "assert gzip.decompress(gzip.compress(b'a22x'))==b'a22x'; " \
            "runpy.run_path(" + repr(str(root / 'opt/a22x-recovery/storage.py')) + "); " \
            "runpy.run_path(" + repr(str(root / 'opt/a22x-recovery/diagnostics.py')) + "); " \
            "runpy.run_path(" + repr(str(root / 'opt/a22x-recovery/filesystem.py')) + "); " \
            "runpy.run_path(" + repr(str(root / 'opt/a22x-recovery/management.py')) + "); " \
            "runpy.run_path(" + repr(str(root / 'opt/a22x-recovery/ssh_service.py')) + "); " \
            "runpy.run_path(" + repr(str(root / 'opt/a22x-recovery/display.py')) + "); " \
            "runpy.run_path(" + repr(str(root / 'opt/a22x-recovery/usb_files.py')) + "); " \
            "runpy.run_path(" + repr(str(root / 'opt/a22x-recovery/terminal.py')) + "); " \
            "runpy.run_path(" + repr(str(root / 'opt/a22x-recovery/distro.py')) + "); " \
            "runpy.run_path(" + repr(str(root / 'opt/a22x-recovery/install.py')) + "); " \
            "print('ARM_RUNTIME_IMPORTS_PASS')"
    run('python-arm-imports', ['qemu-aarch64', '-L', root, root / 'usr/bin/python3', '-c', smoke], env=env)
    run('backend-arm-help', ['qemu-aarch64', '-L', root, root / 'usr/bin/python3',
                            root / 'opt/a22x-recovery/backend/recoveryctl.py', '--help'], env=env)
    run('filesystem-arm-help', ['qemu-aarch64', '-L', root, root / 'usr/bin/python3',
                               root / 'opt/a22x-recovery/filesystem.py', '--help'], env=env)
    run('management-arm-help', ['qemu-aarch64', '-L', root, root / 'usr/bin/python3',
                               root / 'opt/a22x-recovery/management.py', '--help'], env=env)
    run('display-arm-help', ['qemu-aarch64', '-L', root, root / 'usr/bin/python3',
                            root / 'opt/a22x-recovery/display.py', '--help'], env=env)
    run('usb-files-arm-help', ['qemu-aarch64', '-L', root, root / 'usr/bin/python3',
                              root / 'opt/a22x-recovery/usb_files.py', '--help'], env=env)
    run('terminal-arm-help', ['qemu-aarch64', '-L', root, root / 'usr/bin/python3',
                             root / 'opt/a22x-recovery/terminal.py', '--help'], env=env)
    run('distro-arm-help', ['qemu-aarch64', '-L', root, root / 'usr/bin/python3',
                           root / 'opt/a22x-recovery/distro.py', '--help'], env=env)
    run('systems-wrapper-syntax', ['qemu-aarch64', root / 'bin/busybox', 'sh', '-n', systems_cli])
    run('installer-arm-help', ['qemu-aarch64', '-L', root, root / 'usr/bin/python3',
                              root / 'opt/a22x-recovery/install.py', '--help'], env=env)
    run('installer-wrapper-syntax', ['qemu-aarch64', root / 'bin/busybox', 'sh', '-n', install_cli])
    for name in ('umtprd', 'a22x-mtp-sandbox'):
        usb_elf = run(name + '-elf', ['aarch64-linux-gnu-readelf', '-h', '-l', '-d', root / 'bin' / name]).decode()
        require('AArch64' in usb_elf and 'INTERP' not in usb_elf, 'MTP binaries must be static AArch64')
        run(name + '-arm-version', ['qemu-aarch64', root / 'bin' / name,
                                    '--a22x-version' if name == 'umtprd' else '--version'])
    run('openssh-arm-version', ['qemu-aarch64', '-L', root, root / 'usr/sbin/sshd', '-V'], env=env)
    run('sftp-arm-help', ['qemu-aarch64', '-L', root, root / 'usr/lib/ssh/sftp-server', '-h'], env=env, expected=1)
    run('ext4-arm-version', ['qemu-aarch64', '-L', root, root / 'sbin/e2fsck', '-V'], env=env)
    for name in ('partclone.ext4', 'partclone.chkimg', 'partclone.restore'):
        run(name + '-arm-version', ['qemu-aarch64', '-L', root, root / 'usr/sbin' / name, '--version'], env=env)
    run('zstd-arm-version', ['qemu-aarch64', '-L', root, root / 'usr/bin/zstd', '--version'], env=env)
    raw, listing = newc(root)
    run('cpio-list', ['cpio', '-it', '--quiet'], data=raw)
    write_json(out / 'ramdisk-files.json', listing)
    ramdisk = out / 'ramdisk.cpio.gz'
    ramdisk.write_bytes(gzip.compress(raw, compresslevel=9, mtime=0))
    require(ramdisk.stat().st_size <= 21272576, 'Compressed ramdisk exceeds recovery capacity')
    before = baseline['inputs']['stock-recovery']['mkbootimg_args']
    work = baseline['inputs']['working-boot']['mkbootimg_args']
    cmdline = work['--cmdline'] + ' a22x.recovery=1 rdinit=/init root=/dev/ram'
    values = dict(before)
    values.update({'--kernel': str(inputs / 'inputs/working-boot/kernel'),
                   '--ramdisk': str(ramdisk), '--dtb': str(inputs / 'inputs/working-boot/dtb'),
                   '--recovery_dtbo': str(inputs / 'inputs/stock-recovery/recovery_dtbo'),
                   '--cmdline': cmdline})
    image = out / 'recovery-a22x-mainline-k107-v0.5.img'
    argv = ['mkbootimg']
    for key, value in values.items():
        argv.extend((key, value))
    run('mkbootimg', argv + ['--output', image])
    payload_bytes, payload_sha = image.stat().st_size, sha(image)
    maximum = int(run('avb-capacity', ['avbtool', 'add_hash_footer', '--partition_size',
                                     str(PARTITION_BYTES), '--algorithm', 'NONE', '--calc_max_image_size']))
    require(payload_bytes <= maximum, 'Unpadded image exceeds AVB capacity')
    run('avb-add', ['avbtool', 'add_hash_footer', '--image', image, '--partition_name', 'recovery',
                    '--partition_size', str(PARTITION_BYTES), '--algorithm', 'NONE', '--salt', payload_sha])
    # avbtool resolves hash descriptors by partition name, not the input basename.
    # Keep the descriptive output name, and verify the same bytes as recovery.img.
    verification_alias = out / 'recovery.img'
    verification_alias.symlink_to(image.name)
    run('avb-verify', ['avbtool', 'verify_image', '--image', verification_alias])
    run('avb-info', ['avbtool', 'info_image', '--image', image])
    unpacked = out / 'verified'
    fields = run('unpack-final', ['unpack_bootimg', '--boot_img', image, '--out', unpacked,
                                 '--format', 'mkbootimg', '-0']).decode().split('\0')
    fields = [v for v in fields if v]
    require(len(fields) % 2 == 0, 'Malformed unpack_bootimg output')
    after = dict(zip(fields[::2], fields[1::2]))
    require(all(before[k] == after[k] for k in GEOMETRY), 'Stock recovery load geometry changed')
    require(after['--cmdline'] == cmdline, 'Command line mismatch')
    for name, expected in {'kernel': PINS['inputs/working-boot/kernel'],
                           'dtb': PINS['inputs/working-boot/dtb'],
                           'recovery_dtbo': PINS['inputs/stock-recovery/recovery_dtbo'],
                           'ramdisk': sha(ramdisk)}.items():
        require(sha(unpacked / name) == expected, f'Final {name} differs from verified input')
    require(image.stat().st_size == PARTITION_BYTES, 'Final RECOVERY partition length mismatch')
    report = {'schema': 'a22x.recovery.image.v1', 'status': 'OFFLINE_VERIFIED_NOT_BOOTED',
              'utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
              'image': str(image), 'bytes': image.stat().st_size, 'sha256': sha(image),
              'payload_bytes': payload_bytes, 'avb_max_payload_bytes': maximum,
              'ramdisk_compressed_bytes': ramdisk.stat().st_size, 'ramdisk_raw_bytes': len(raw),
              'ramdisk_entries': len(listing), 'ui_binary_bytes': (root / 'bin/recovery-ui').stat().st_size,
              'ui_binary_sha256': sha(root / 'bin/recovery-ui'),
              'geometry': {k: after[k] for k in GEOMETRY}, 'cmdline': cmdline,
              'exact_kernel107_and_dt_table_reused': True, 'stock_recovery_dtbo_reused': True,
              'avb': 'NONE hash footer verified; not an OEM signature',
              'source_baseline_boot_sha256': baseline['inputs']['working-boot']['sha256'],
              'stock_recovery_sha256': baseline['inputs']['stock-recovery']['sha256'],
              'device_restore_qualified': bool(capsule),
              'linux_qualification_capsule_id': capsule['capsule_id'] if capsule else None,
              'qualification_target': 'owned-new-regular-files' if capsule else None,
              'physical_wipe_restore_tested': False, 'phone_operations': 0,
              'mtp_package_sha256': PINS['mtp-v05-package-v2/manifest.json'],
              'mtp_hardware_acceptance': 'SEPARATE_RECEIPT_REQUIRED',
              'terminal_shell_package_sha256': PINS['terminal-shell-v05-package/manifest.json'],
              'terminal_shell_sha256': terminal_shell['overlay_files'][0]['sha256'],
              'live_recovery_mode2': 'NOT_TESTED', 'live_display_touch': 'NOT_TESTED'}
    write_json(out / 'image-validation.json', report)
    (out / 'SHA256SUMS').write_text(sha(image) + '  ' + image.name + '\n')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
