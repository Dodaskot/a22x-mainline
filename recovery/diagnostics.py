#!/usr/bin/env python3
"""Small read-only status endpoint, bound only to the USB NCM address."""
# SPDX-License-Identifier: MIT
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
from pathlib import Path
import subprocess
import struct


def bounded_text(path, limit=8192):
    try:
        with Path(path).open('rb') as f:
            return f.read(limit).decode('utf-8', 'replace')
    except OSError:
        return None


def bounded_json(path, limit=65536):
    text = bounded_text(path, limit + 1)
    if text is None or len(text) > limit:
        return None
    try:
        return json.loads(text)
    except ValueError:
        return None


def crypto_status():
    manifest = bounded_json('/etc/a22x-recovery/kernel-capabilities.json') or {}
    config = manifest.get('config', {})
    nodes = [p for p in ('/dev/tzdev', '/dev/tzdev0') if Path(p).exists()]
    return {
        'android_decryption_ready': False,
        'kernel_fscrypt_configured': config.get('CONFIG_FS_ENCRYPTION') == 'y',
        'kernel_dm_default_key_configured': config.get('CONFIG_DM_DEFAULT_KEY') == 'y',
        'teegris_device_nodes': nodes,
        'optee_device_nodes': [p for p in ('/dev/tee0', '/dev/teepriv0') if Path(p).exists()],
        'keymaster_gatekeeper_integration': 'not implemented',
        'key_material_read': False,
        'kernel_manifest': manifest,
    }


def boot_mode():
    try:
        raw = Path('/proc/device-tree/chosen/atag,boot').read_bytes()
        if len(raw) >= 12:
            size, tag, mode = struct.unpack_from('<III', raw)
            return {'mode': mode, 'tag': hex(tag), 'tag_size': size}
    except OSError:
        pass
    return None


class StatusHandler(BaseHTTPRequestHandler):
    server_version = 'A22xRecoveryStatus/0.5'

    def do_GET(self):
        if self.path not in ('/', '/status'):
            self.send_error(404)
            return
        state = {'product': 'A22x Mainline Recovery', 'version': '0.5-dev',
                 'boot': bounded_text('/run/a22x-recovery/boot.json'),
                 'boot_mode': boot_mode(),
                 'kernel': bounded_text('/proc/version', 1024),
                 'mounts': bounded_text('/proc/self/mountinfo'),
                 'ui_stderr': bounded_text('/run/a22x-recovery/ui.log'),
                 'ui_stdout': bounded_text('/run/a22x-recovery/ui.stdout'),
                 'device_restore_qualified': False,
                 'generic_device_restore_qualified': False,
                 'device_restore_scope': None,
                 'crypto': crypto_status()}
        try:
            run = subprocess.run(['/usr/bin/python3', '/opt/a22x-recovery/backend/recoveryctl.py',
                                  '--device', 'inventory'], capture_output=True, text=True, timeout=10)
            state['inventory_rc'] = run.returncode
            state['inventory'] = json.loads(run.stdout) if len(run.stdout) < 65536 else {'error': 'output_limit'}
            capabilities = state['inventory'].get('capabilities', {})
            state['linux_qualification'] = capabilities.get('linux_qualification')
            state['device_restore_qualified'] = run.returncode == 0 and capabilities.get('linux_restore') is True
            if state['device_restore_qualified']:
                state['device_restore_scope'] = 'linux-partclone-v2'
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            state['inventory'] = {'error': str(exc)}
        try:
            run = subprocess.run(['/usr/bin/python3', '/opt/a22x-recovery/filesystem.py',
                                  '--device', 'inventory'], capture_output=True, text=True, timeout=10)
            state['filesystem_rc'] = run.returncode
            state['filesystems'] = json.loads(run.stdout) if len(run.stdout) < 131072 else {'error': 'output_limit'}
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            state['filesystems'] = {'error': str(exc)}
        try:
            run = subprocess.run(['/usr/bin/python3', '/opt/a22x-recovery/management.py',
                                  '--device', 'status'], capture_output=True, text=True, timeout=10)
            state['management_rc'] = run.returncode
            state['management'] = json.loads(run.stdout) if len(run.stdout) < 65536 else {'error': 'output_limit'}
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            state['management'] = {'error': str(exc)}
        try:
            run = subprocess.run(['/usr/bin/python3', '/opt/a22x-recovery/display.py',
                                  'status'], capture_output=True, text=True, timeout=5)
            state['display_rc'] = run.returncode
            state['display'] = json.loads(run.stdout) if len(run.stdout) < 16384 else {'error': 'output_limit'}
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            state['display'] = {'error': str(exc)}
        try:
            run = subprocess.run(['/usr/bin/python3', '/opt/a22x-recovery/usb_files.py',
                                  'status'], capture_output=True, text=True, timeout=5)
            state['usb_files_rc'] = run.returncode
            state['usb_files'] = json.loads(run.stdout) if len(run.stdout) < 65536 else {'error': 'output_limit'}
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            state['usb_files'] = {'error': str(exc)}
        try:
            run = subprocess.run(['/usr/bin/python3', '/opt/a22x-recovery/terminal.py',
                                  'status'], capture_output=True, text=True, timeout=5)
            state['terminal_rc'] = run.returncode
            state['terminal'] = json.loads(run.stdout) if len(run.stdout) < 16384 else {'error': 'output_limit'}
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            state['terminal'] = {'error': str(exc)}
        data = json.dumps(state, ensure_ascii=True, indent=2).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(data)


if __name__ == '__main__':
    HTTPServer(('172.16.42.1', 8081), StatusHandler).serve_forever()
