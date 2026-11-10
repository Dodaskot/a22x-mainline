#!/usr/bin/env python3
"""Add new interface strings to every catalog and drop ones no longer used."""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))
from generate_i18n import ORDER, read_po, ROOT

HEADER = 'msgid ""\nmsgstr ""\n"Content-Type: text/plain; charset=UTF-8\\n"\n"Language: {code}\\n"\n\n'


def write_po(path, code, messages, catalog):
    with path.open('w') as out:
        out.write(HEADER.format(code=code))
        for value in messages:
            text = catalog.get(value, '')
            out.write('msgid "' + value.replace('"', '\\"') + '"\nmsgstr "' + text.replace('"', '\\"') + '"\n\n')


def main():
    messages = [line for line in (ROOT / 'i18n/messages.txt').read_text().split('\n') if line]
    for code in ORDER:
        path = ROOT / 'i18n' / (code + '.po')
        if path.is_file():
            write_po(path, code, messages, read_po(path))


if __name__ == '__main__':
    main()
