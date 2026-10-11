#!/usr/bin/env python3
"""Send lines to the phone's UART console via the TCP bridge and print the reply.
usage: uartcmd.py [--wait S] 'line1' 'line2' ...   (each line waits S seconds, default 2)
Expects a serial-to-TCP bridge on 127.0.0.1:7001 (A22X_UART_PORT).
Everything received is also appended to $A22X_UART_LOG (default uart-current.log)."""
import socket, sys, time, os
wait = 2.0
args = sys.argv[1:]
if args[:1] == ['--wait']:
    wait = float(args[1]); args = args[2:]
log = open(os.environ.get('A22X_UART_LOG', 'uart-current.log'), 'ab')
s = socket.create_connection(('127.0.0.1', int(os.environ.get('A22X_UART_PORT', '7001'))), timeout=5)
s.settimeout(0.2)
def drain(t):
    out = b''; end = time.time() + t
    while time.time() < end:
        try:
            d = s.recv(65536)
            if not d: break
            out += d
        except socket.timeout:
            pass
    log.write(out); log.flush()
    return out
drain(0.5)
for line in args:
    s.sendall(line.encode() + b'\r')
    sys.stdout.write(drain(wait).decode(errors='replace').replace('\r', ''))
s.close()
