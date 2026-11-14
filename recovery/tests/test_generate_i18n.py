"""read_po of ui/tools/generate_i18n.py (2026-11-14 review): the long entries gettext tools write
over several lines are read like one-line ones."""
from pathlib import Path
import os
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'ui/tools'))
import generate_i18n  # noqa: E402


class ReadPo(unittest.TestCase):
    def test_multi_line_entries_and_the_header(self):
        text = '''msgid ""
msgstr ""
"Content-Type: text/plain; charset=UTF-8\\n"

# a comment
#, fuzzy
msgid "Short"
msgstr "Коротко"

msgid ""
"A long message that a tool "
"wrapped over \\"three\\" lines"
msgstr ""
"Довге повідомлення, яке інструмент "
"переніс на \\"три\\" рядки"

msgid "Untranslated"
msgstr ""

msgid "One line"
msgstr ""
"Переклад лише в наступному рядку"
'''
        with tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR')) as tmp:
            path = Path(tmp) / 'uk.po'
            path.write_text(text)
            catalog = generate_i18n.read_po(path)
        self.assertEqual(catalog, {
            'Short': 'Коротко',
            'A long message that a tool wrapped over "three" lines': 'Довге повідомлення, яке інструмент переніс на "три" рядки',
            'One line': 'Переклад лише в наступному рядку'})


if __name__ == '__main__':
    unittest.main()
