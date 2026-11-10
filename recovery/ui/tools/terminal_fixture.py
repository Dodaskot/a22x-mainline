#!/usr/bin/env python3
"""Explicit file-backed UI contract double: no shell, PTY, IPC, mount or USB I/O."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', choices=('fixture',), required=True)
    parser.add_argument('--fixture', type=Path, required=True)
    parser.add_argument('operation', choices=('status', 'start', 'read', 'write', 'resize', 'stop'))
    parser.add_argument('--expected-session-token')
    parser.add_argument('--rows', type=int)
    parser.add_argument('--cols', type=int)
    parser.add_argument('--after-seq', type=int)
    parser.add_argument('--max-bytes', type=int)
    parser.add_argument('--data-base64')
    args = parser.parse_args()
    root = args.fixture
    config = json.loads((root/'terminal-fixture.json').read_text())
    state_path = root/'terminal-fixture-state.json'
    state = json.loads(state_path.read_text()) if state_path.exists() else {'state': 'stopped', 'token': None, 'rows': 21, 'cols': 48, 'output': '', 'base_seq': 0}
    case = config.get('case', 'roundtrip')
    boot = hashlib.sha256(str(root).encode()).hexdigest()
    request = {'operation': args.operation, 'expected_session_token': args.expected_session_token,
               'after_seq': args.after_seq, 'rows': args.rows, 'cols': args.cols}
    if args.data_base64 is not None:
        raw = base64.b64decode(args.data_base64, validate=True)
        request.update(bytes=len(raw), sha256=hashlib.sha256(raw).hexdigest(),
                       controls=[number for number in (3, 4, 9, 13, 27, 127) if bytes((number,)) in raw])
    with (root/'terminal-requests.jsonl').open('a') as stream:
        stream.write(json.dumps(request) + '\n')
    def save():
        state_path.write_text(json.dumps(state))
    def error(code):
        return {'ok': False, 'mode': 'fixture', 'boot_id': boot, 'operation': args.operation,
                'error': {'code': code, 'message': 'Expected UI contract fixture refusal: ' + code}}
    def snapshot():
        stopped = state['state'] == 'stopped'
        return {'ok': True, 'mode': 'fixture', 'operation': args.operation, 'boot_id': boot,
                'available': case != 'unavailable', 'state': state['state'],
                'session_token': None if stopped else state['token'],
                'rows': None if stopped else state['rows'], 'cols': None if stopped else state['cols'],
                'storage_lease_held': not stopped, 'shell_exit_code': 0 if state['state'] == 'exited' else None,
                'command_running': state.get('running', 0) > 0,
                'reason': 'Fixture service unavailable' if case == 'unavailable' else None}
    if args.operation == 'status':
        if state.get('status_failures'):
            state['status_failures'] -= 1
            save()
            return error('IO_ERROR')
        if state['state'] == 'starting':
            if state.get('pending_status', 0):
                state['pending_status'] -= 1
            else:
                state['state'] = 'active'
            save()
        return snapshot()
    if args.operation == 'start':
        if case == 'busy':
            return error('OPERATION_BUSY')
        if state['state'] != 'stopped':
            return error('TERMINAL_ACTIVE')
        assert (args.rows, args.cols) == (21, 48)
        initial = '\x1b[32mУкраїнська: Ґґ Іі Її Єє\x1b[0m\r\nUI-TERMINAL-PRIVATE-SENTINEL\r\n'
        initial += ''.join(f'\x1b[{32+n%3}m{n:03d}\x1b[0m bounded terminal history\r\n' for n in range(120))
        initial += '\x1b[1;32mroot@fixture\x1b[0m:~# '
        state.update(state='active', token='a'*64, rows=args.rows, cols=args.cols,
                     output=base64.b64encode(initial.encode()).decode(), base_seq=37 if case == 'gap' else 0)
        if case == 'start-pending':
            state.update(state='starting', status_failures=1, pending_status=1)
        save()
        return snapshot()
    if args.expected_session_token != state['token']:
        return error('SESSION_CHANGED')
    if args.operation == 'resize':
        state.update(rows=args.rows, cols=args.cols)
        save()
        return snapshot()
    if args.operation == 'read':
        output = base64.b64decode(state['output'])
        start, after = state['base_seq'], args.after_seq
        end = start + len(output)
        assert 0 <= after <= end and args.max_bytes == 16384
        first = max(after, start)
        raw = output[first-start:first-start+args.max_bytes]
        value = {**snapshot(), 'data_base64': base64.b64encode(raw).decode(), 'start_seq': start,
                 'next_seq': first + len(raw), 'end_seq': end, 'lost_bytes': max(0, start-after)}
        if state.get('running'):
            # The command of the background case ends after a few looks, and prints its result.
            state['running'] -= 1
            if not state['running']:
                state['output'] = base64.b64encode(output + b'\r\nBACKGROUND-RESULT\r\nroot@fixture:~# ').decode()
            save()
        if case == 'bad-sequence':
            value['next_seq'] += 1
        if case == 'gap-inflight' and not state.get('gap_scheduled'):
            state.update(base_seq=end+37, gap_scheduled=True)
            save()
        # Deliberately exercise UI redaction for both channels.
        print('UI-TERMINAL-PRIVATE-STDERR', file=sys.stderr)
        return value
    if args.operation == 'write':
        assert 0 < len(raw) <= 4096 and state['state'] == 'active'
        output = base64.b64decode(state['output'])
        if raw == b'\x04':
            state['state'] = 'exited'
            output += b'\r\nexit\r\n'
        else:
            output += raw
            if case == 'background' and b'\r' in raw:
                state['running'] = 5
        state['output'] = base64.b64encode(output).decode()
        save()
        return {**snapshot(), 'bytes_written': len(raw) - (case == 'bad-ack'), 'pending_input_bytes': 0}
    token = state['token']
    state.update(state='stopped', output='', token=None)
    if case == 'stop-race':
        state['status_failures'] = 1
    save()
    return {**snapshot(), 'session_token': token}


if __name__ == '__main__':
    value = main()
    print(json.dumps(value, separators=(',', ':')))
    raise SystemExit(0 if value['ok'] else 2)
