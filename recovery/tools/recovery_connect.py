#!/usr/bin/env python3
"""Connect using the recovery host key verified during this boot's acceptance."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import urllib.request

DEFAULT = os.path.join(os.environ.get('A22X_WORK', '.'), 'artifacts/mainline-recovery-k107-20261105/LATEST_RECOVERY_SSH.json')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--receipt', default=DEFAULT)
    parser.add_argument('--sftp', action='store_true')
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    try:
        receipt = json.loads(Path(args.receipt).read_text())
        if receipt.get('schema') != 'a22x.recovery.ssh.v1':
            raise ValueError('The verified SSH receipt is missing or invalid')
        with urllib.request.urlopen('http://172.16.42.1:8081/status', timeout=12) as reply:
            current = json.loads(reply.read(262145))
        boot = current.get('boot')
        boot = json.loads(boot) if isinstance(boot, str) else boot
        fingerprint = current.get('management', {}).get('ssh', {}).get('host_fingerprint')
        if (not isinstance(boot, dict) or boot.get('boot_id') != receipt['boot_id']
                or current.get('boot_mode', {}).get('mode') != 2
                or fingerprint != receipt['host_fingerprint']):
            raise ValueError('The recovery boot or host key changed. Verify its current fingerprint on the phone or through UART before updating the SSH receipt.')
        known = Path(receipt['known_hosts'])
        if not known.is_file():
            raise ValueError('The pinned known_hosts file is unavailable')
        options = ['-F', '/dev/null', '-o', 'BatchMode=yes', '-o', 'IdentitiesOnly=yes',
                   '-o', 'StrictHostKeyChecking=yes', '-o', 'UserKnownHostsFile=' + str(known),
                   '-o', 'GlobalKnownHostsFile=/dev/null', '-o', 'ConnectTimeout=5',
                   '-i', receipt['client_identity_path']]
        command = args.command[1:] if args.command[:1] == ['--'] else args.command
        if args.sftp and command:
            raise ValueError('Use --sftp without a shell command')
        return subprocess.call(['sftp' if args.sftp else 'ssh', *options, 'root@172.16.42.1', *command])
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
