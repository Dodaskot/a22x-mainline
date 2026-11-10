#!/usr/bin/python3
"""Run an authenticated USB SSH/SFTP session under the common recovery lease."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import uuid

from access_gate import Gate
from operation_lock import DEVICE_LOCK, ENV_FD, operation_lock


def command_argv(original):
    if original == '/usr/lib/ssh/sftp-server':
        return ['/usr/lib/ssh/sftp-server', '-u', '0077']
    if original:
        return ['/bin/sh', '-c', original]
    return ['/bin/sh', '-l']


def main():
    if os.geteuid() != 0 or 'a22x.recovery=1' not in Path('/proc/cmdline').read_text().split():
        print('This service is only available in A22x recovery.', file=sys.stderr)
        return 126
    # Never trust an FD number supplied by a client environment.
    os.environ.pop(ENV_FD, None)
    gate = Gate()
    if gate.required() and not gate.granted():
        print('Confirm this computer on the phone with the recovery password.', file=sys.stderr)
    if not gate.allow('ssh', os.environ.get('SSH_CONNECTION', '').split(' ')[0]):
        print('Access was not allowed on the phone.', file=sys.stderr)
        return 77
    marker = Path('/run/a22x-recovery/ssh/session-' + uuid.uuid4().hex + '.json')
    try:
        with operation_lock(DEVICE_LOCK, 'device') as fd:
            env = dict(os.environ, PATH='/bin:/sbin:/usr/bin:/usr/sbin',
                       PYTHONHOME='/usr', LD_LIBRARY_PATH='/usr/lib',
                       PYTHONDONTWRITEBYTECODE='1', HOME='/root', TERM=os.environ.get('TERM', 'linux'))
            env[ENV_FD] = str(fd)
            marker.write_text(json.dumps({'pid': os.getpid(), 'kind': 'ssh-maintenance',
                                          'ui_writes_paused': True}))
            marker.chmod(0o600)
            os.umask(0o077)
            child = subprocess.Popen(command_argv(os.environ.get('SSH_ORIGINAL_COMMAND', '')),
                                     env=env, pass_fds=(fd,))
            def forward(signum, _frame):
                try:
                    child.send_signal(signum)
                except ProcessLookupError:
                    pass
            for signum in (signal.SIGHUP, signal.SIGTERM, signal.SIGINT):
                signal.signal(signum, forward)
            rc = child.wait()
            return rc if rc >= 0 else 128 - rc
    except BlockingIOError:
        print('Recovery is busy. Finish the active operation or close the other SSH/SFTP session.', file=sys.stderr)
        return 75
    except OSError as exc:
        print('Recovery session could not acquire its maintenance lease: ' + str(exc), file=sys.stderr)
        return 74
    finally:
        marker.unlink(missing_ok=True)


if __name__ == '__main__':
    raise SystemExit(main())
