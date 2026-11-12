"""What a refusal says must be in the interface's list of texts, or it is shown in English whatever
the language is (2026-11-12: "The kernel does not unpack to its end" on a Ukrainian screen)."""
import ast
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


def messages(module):
    """The texts of fail(code, text) and require(condition, code, text) in MODULE."""
    found = set()
    for node in ast.walk(ast.parse((ROOT / module).read_text())):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ('fail', 'require'):
            args = node.args[1:] if node.func.id == 'require' else node.args
            if len(args) >= 2 and isinstance(args[1], ast.Constant) and isinstance(args[1].value, str):
                found.add(args[1].value)
    return found


class Translated(unittest.TestCase):
    def test_every_refusal_of_the_image_check_has_its_translations(self):
        listed = set((ROOT / 'ui/i18n/messages.txt').read_text().splitlines())
        wanted = messages('bootimage.py')
        self.assertGreater(len(wanted), 25)
        self.assertEqual(sorted(wanted - listed), [])
        for language in ('uk', 'de', 'es', 'fr', 'pl'):
            po = (ROOT / 'ui/i18n' / (language + '.po')).read_text()
            for text in wanted:
                quoted = text.replace('\\', '\\\\').replace('"', '\\"')
                match = re.search(r'msgid "%s"\nmsgstr "([^"]*)"' % re.escape(quoted), po)
                self.assertTrue(match and match.group(1), (language, text))


if __name__ == '__main__':
    unittest.main()
