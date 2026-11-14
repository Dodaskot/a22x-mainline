"""The platform files as the sources hold them (2026-11-13 review): alsa-lib takes the folder of a
conf.d entry's target as the folder of its includes only when the entry is a symlink; committed as a
plain file, the sound profile of this phone did not load in an installed system."""
from pathlib import Path
import os
import unittest

ROOT = Path(__file__).resolve().parents[1]
ENTRY = ROOT / 'platform/optional/usr/share/alsa/ucm2/conf.d/mt6833_mt6359/mt6833-mt6359.conf'


class PlatformTree(unittest.TestCase):
    def test_the_ucm_entry_is_a_link_to_its_folder(self):
        self.assertTrue(ENTRY.is_symlink())
        self.assertEqual(os.readlink(ENTRY), '../../MediaTek/mt6833/mt6359-sia8109/mt6833-mt6359.conf')
        for name in ('HiFi.conf', 'init.conf'):
            self.assertTrue((ENTRY.resolve().parent / name).is_file(), name)


class LiveTools(unittest.TestCase):
    def test_the_live_filesystem_check_never_hides_its_check_function(self):
        # 2026-11-14 review: a loop variable named check made every later check() call a string.
        import ast
        source = (ROOT / 'tools/live_filesystem_check.py').read_text()
        stored = [node.lineno for node in ast.walk(ast.parse(source))
                  if isinstance(node, ast.Name) and node.id == 'check' and isinstance(node.ctx, ast.Store)]
        self.assertEqual(stored, [])


if __name__ == '__main__':
    unittest.main()
