#!/usr/bin/python3
"""Start key-only OpenSSH on the USB address, with per-boot RAM host keys."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

from management import proc_start, process_running, read_bounded

RUN = Path('/run/a22x-recovery/ssh')
CONFIG = Path('/etc/a22x-recovery/sshd_config')
AUTHORIZED = Path('/etc/a22x-recovery/authorized_keys')


def public_keys(path):
    raw, truncated = read_bounded(path, 65536)
    if truncated:
        raise ValueError('Authorized public key file exceeds its size limit')
    lines = [s.strip() for s in raw.decode('ascii').splitlines() if s.strip() and not s.startswith('#')]
    if not lines or any(not line.startswith(('ssh-ed25519 ', 'ecdsa-sha2-nistp256 ', 'ssh-rsa '))
                        for line in lines):
        raise ValueError('An explicit authorized public key is required')
    return lines


def write_status(value):
    path = RUN / 'status.json'
    temporary = RUN / 'status.new'
    temporary.write_text(json.dumps(value))
    temporary.chmod(0o600)
    os.replace(temporary, path)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('operation', choices=['start'])
    parser.parse_args()
    if os.geteuid() != 0 or 'a22x.recovery=1' not in Path('/proc/cmdline').read_text().split():
        raise SystemExit('SSH service is only available inside A22x recovery')
    os.umask(0o077)
    RUN.mkdir(parents=True, exist_ok=True, mode=0o700)
    # These paths live in RAM; no persistent filesystem is mounted for keys.
    try:
        old = json.loads((RUN / 'status.json').read_text())
    except (OSError, ValueError):
        old = {}
    if process_running(old):
        print(json.dumps({'ok': True, 'already_running': True, **old}))
        return 0
    state = {'running': False, 'host_key_ephemeral': True, 'address': '172.16.42.1', 'port': 22}
    try:
        state['authorized_keys_count'] = len(public_keys(AUTHORIZED))
        key = RUN / 'ssh_host_ed25519_key'
        if not key.exists():
            subprocess.run(['/usr/bin/ssh-keygen', '-q', '-t', 'ed25519', '-N', '',
                            '-C', 'a22x-recovery-ephemeral', '-f', str(key)],
                           check=True, capture_output=True, timeout=30)
        key.chmod(0o600)
        fingerprint = subprocess.run(['/usr/bin/ssh-keygen', '-lf', str(key) + '.pub'],
                                     check=True, capture_output=True, text=True, timeout=10)
        state['host_fingerprint'] = fingerprint.stdout.split()[1]
        Path('/run/sshd').mkdir(mode=0o755, exist_ok=True)
        Path('/var/empty').mkdir(parents=True, mode=0o755, exist_ok=True)
        subprocess.run(['/usr/sbin/sshd', '-t', '-f', str(CONFIG)],
                       check=True, capture_output=True, timeout=10)
        log = (RUN / 'server.log').open('ab')
        process = subprocess.Popen(['/usr/sbin/sshd', '-D', '-e', '-f', str(CONFIG)],
                                   stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                   start_new_session=True, close_fds=True)
        log.close()
        time.sleep(0.2)
        if process.poll() is not None:
            raise RuntimeError('OpenSSH exited during startup; see SSH server log')
        started = proc_start(process.pid)
        if not started:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=5)
            raise RuntimeError('OpenSSH process identity disappeared during startup')
        state.update(running=True, pid=process.pid, process_start=started, reason=None)
        write_status(state)
        print(json.dumps({'ok': True, **state}))
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        state['reason'] = str(exc)
        if isinstance(exc, subprocess.CalledProcessError):
            error = exc.stderr.decode('utf-8', 'replace') if isinstance(exc.stderr, bytes) else exc.stderr
            state['reason'] += ': ' + (error or '')[:4096]
        write_status(state)
        print(json.dumps({'ok': False, **state}))
        return 2


WIFI_STATUS = RUN / 'wifi.json'
WIFI_CONFIG = RUN / 'sshd-wifi.conf'


def wlan_address():
    """The IPv4 address recovery has on Wi-Fi, or None (wifi.py knows the interface and tools)."""
    try:
        import wifi as wireless
        return wireless.address() or None
    except (ImportError, OSError, subprocess.SubprocessError):
        return None


def wifi(action):
    """SSH over Wi-Fi (2026-11-08): a second OpenSSH on the Wi-Fi address with the USB server's
    rules (keys only, root, the same forced admin session), started and stopped on request."""
    os.umask(0o077)
    RUN.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        old = json.loads(WIFI_STATUS.read_text())
    except (OSError, ValueError):
        old = {}
    if process_running(old):
        os.kill(old['pid'], 15)
        for _ in range(50):
            if not process_running(old):
                break
            time.sleep(0.1)
    state = {'running': False, 'address': None, 'port': 22}
    if action == 'off':
        WIFI_STATUS.write_text(json.dumps(state))
        print(json.dumps({'ok': True, **state}))
        return 0
    address = wlan_address()
    if not address:
        state['reason'] = 'Recovery has no Wi-Fi address'
        WIFI_STATUS.write_text(json.dumps(state))
        print(json.dumps({'ok': False, **state}))
        return 2
    key = RUN / 'ssh_host_ed25519_key'
    if not key.exists():
        subprocess.run(['/usr/bin/ssh-keygen', '-q', '-t', 'ed25519', '-N', '', '-C', 'a22x-recovery-ephemeral', '-f', str(key)],
                       check=True, capture_output=True, timeout=30)
    lines = [l for l in CONFIG.read_text().splitlines() if not l.startswith(('ListenAddress', 'PidFile'))]
    lines += ['ListenAddress %s' % address, 'PidFile %s' % (RUN / 'sshd-wifi.pid')]
    WIFI_CONFIG.write_text('\n'.join(lines) + '\n')
    Path('/run/sshd').mkdir(mode=0o755, exist_ok=True)
    subprocess.run(['/usr/sbin/sshd', '-t', '-f', str(WIFI_CONFIG)], check=True, capture_output=True, timeout=10)
    log = (RUN / 'server-wifi.log').open('ab')
    process = subprocess.Popen(['/usr/sbin/sshd', '-D', '-e', '-f', str(WIFI_CONFIG)], stdin=subprocess.DEVNULL,
                               stdout=log, stderr=log, start_new_session=True, close_fds=True)
    log.close()
    time.sleep(0.3)
    if process.poll() is not None:
        state['reason'] = 'OpenSSH on Wi-Fi exited during startup'
        WIFI_STATUS.write_text(json.dumps(state))
        print(json.dumps({'ok': False, **state}))
        return 2
    state.update(running=True, address=address, pid=process.pid, process_start=proc_start(process.pid))
    WIFI_STATUS.write_text(json.dumps(state))
    print(json.dumps({'ok': True, **state}))
    return 0


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == 'wifi' and sys.argv[2] in ('on', 'off'):
        if os.geteuid() != 0 or 'a22x.recovery=1' not in Path('/proc/cmdline').read_text().split():
            raise SystemExit('SSH service is only available inside A22x recovery')
        try:
            raise SystemExit(wifi(sys.argv[2]))
        except (OSError, subprocess.SubprocessError) as error:
            print(json.dumps({'ok': False, 'running': False, 'reason': str(error)[:300]}))
            raise SystemExit(2)
    raise SystemExit(main())
