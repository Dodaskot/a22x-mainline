#!/usr/bin/python3
# SPDX-License-Identifier: MIT
"""The clock from the internet (2026-11-10).

Samsung's Android keeps the hardware clock in its own form: after it ran, this kernel read the year
2086 (60 years ahead), the screen showed a wrong date and every certificate failed as expired.
When Wi-Fi gives an address, the time comes from a time server (SNTP, as phones ask NTP), so the
screen shows the exact time and downloads work; the hardware clock is left alone.

    clock.py sync     the clock set from the first time server that answers
"""
import json
import os
from pathlib import Path
import socket
import struct
import sys
import time

FLOOR = 1793491200  # 2026-11-01 UTC: a clock before it fails every certificate.
CEILING = 2524608000  # 2050-01-01 UTC: a clock beyond it is as wrong (Android's hardware clock form).
SERVERS = ('time.cloudflare.com', 'time.google.com', 'pool.ntp.org')
NTP_EPOCH = 2208988800  # 1900-01-01 to 1970-01-01
STATE = Path('/run/a22x-recovery/clock.json')


def plausible(now=None):
    """Whether the clock can be right: between FLOOR and CEILING."""
    now = time.time() if now is None else now
    return FLOOR <= now < CEILING


def ask(host, port=123, timeout=3.0):
    """The offset (seconds) that makes this clock the server's, half the round trip included."""
    address = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_DGRAM)[0][4]
    # 2026-11-13 review: any datagram to the port was taken for the answer, so anyone on the same
    # Wi-Fi could set this clock, and with it what certificates count as valid. The socket now takes
    # only the server's address, and the answer must echo the random transmit time of the request.
    nonce = os.urandom(8)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
        udp.settimeout(timeout)
        udp.connect(address)
        sent = time.time()
        udp.send(b'\x23' + bytes(39) + nonce)  # leap 0, version 4, mode 3 (client); transmit = nonce
        reply = udp.recv(512)
        received = time.time()
    if len(reply) < 48 or reply[0] & 7 != 4 or not 1 <= reply[1] <= 15 or reply[24:32] != nonce:
        raise ValueError('not a time server reply')
    seconds, fraction = struct.unpack('!II', reply[40:48])
    server = seconds - NTP_EPOCH + fraction / 2 ** 32
    return server + (received - sent) / 2 - received


def sync(servers=SERVERS, set_clock=True, state=STATE):
    """The clock set from the first server that answers with a plausible time:
    {'synced': True, 'offset': seconds, 'server': host}, or {'synced': False}."""
    for server in servers:
        host, _, port = server.partition(':') if server.count(':') == 1 else (server, '', '')
        try:
            offset = ask(host, int(port or 123))
        except (OSError, ValueError, struct.error):
            continue
        if not plausible(time.time() + offset):
            continue
        if set_clock and abs(offset) > 0.5:
            time.clock_settime(time.CLOCK_REALTIME, time.time() + offset)
        result = {'synced': True, 'offset': round(offset, 3), 'server': host, 'utc': int(time.time())}
        if state:
            try:
                state.parent.mkdir(parents=True, exist_ok=True)
                state.write_text(json.dumps(result) + '\n')
            except OSError:
                pass
        return result
    return {'synced': False}


def ensure(mode='device'):
    """Before HTTPS: a clock that cannot be right is set from a time server."""
    if mode != 'fixture' and not plausible():
        sync()


def sync_in_background():
    """After Wi-Fi joins: the time asked in a process of its own, so joining does not wait for it."""
    try:
        import subprocess
        subprocess.Popen([sys.executable, '-B', str(Path(__file__).resolve()), 'sync'], stdin=subprocess.DEVNULL,
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True, close_fds=True)
    except OSError:
        pass


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv[:1] != ['sync']:
        print('usage: clock.py sync', file=sys.stderr)
        return 64
    result = sync()
    print(json.dumps(result), flush=True)
    return 0 if result['synced'] else 1


if __name__ == '__main__':
    sys.exit(main())
