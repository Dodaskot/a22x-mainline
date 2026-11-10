"""An installation prepared in the Android app (install_plan.py, 2026-11-10), on fixture volumes."""
import contextlib
import hashlib
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import install_plan as ip  # noqa: E402

HASH = '$6$a22xsalt$' + 'A' * 86


class InstallPlan(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='a22x-plan-', dir=os.environ.get('TMPDIR')))
        self.addCleanup(shutil.rmtree, self.root, True)
        for name in ('cache/a22x-recovery/scripts', 'userdata'):
            (self.root / name).mkdir(parents=True)
        shutil.copy(ROOT / 'catalog.json', self.root / 'catalog.json')
        self.volumes(cache_mounted=True)

    def volumes(self, cache_mounted, userdata_mounted=True):
        (self.root / 'filesystem-fixture.json').write_text(json.dumps({'schema': 'a22x.filesystem.fixture.v1', 'volumes': [
            {'id': 'cache', 'path': 'cache', 'mounted': cache_mounted, 'read_only': False},
            {'id': 'userdata', 'path': 'userdata', 'mounted': userdata_mounted, 'read_only': False}]}))

    def plan(self, **changes):
        record = {'schema': 'a22x.install-plan.v1', 'source': 'android-app 0.6.139', 'created_utc': 1794306000,
                  'system': {'catalog': 'ubuntu-24.04'}, 'erase_userdata': True, 'desktop': 'phosh',
                  'shells': ['gnome', 'phosh'], 'sign_in': 'auto', 'apps': ['calculator', 'ssh'],
                  'account': {'username': 'user', 'password_sha512': HASH}, 'locale': 'uk_UA',
                  'timezone': 'Europe/Kyiv', 'script': 'setup.sh'}
        record.update(changes)
        for key in [k for k, v in changes.items() if v is ...]:
            del record[key]
        data = json.dumps(record).encode()
        (self.root / 'cache' / ip.PLAN).write_bytes(data)
        return hashlib.sha256(data).hexdigest()

    def run_helper(self, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
            code = ip.main(['--fixture', str(self.root), *args])
        return code, json.loads(out.getvalue())

    def test_no_plan(self):
        self.assertEqual(self.run_helper('show'), (0, {'ok': True, 'operation': 'show', 'mode': 'fixture', 'present': False}))

    def test_a_plan_is_shown_as_the_install_pages_take_it(self):
        digest = self.plan()
        (self.root / 'cache/a22x-recovery/scripts/setup.sh').write_text('#!/bin/sh\necho hi\n')
        code, result = self.run_helper('show')
        self.assertEqual(code, 0, result)
        self.assertEqual((result['present'], result['valid'], result['sha256']), (True, True, digest))
        plan = result['plan']
        self.assertEqual(plan['catalog'], 'ubuntu-24.04')
        self.assertEqual(plan['image']['name'], 'Ubuntu Server')
        self.assertEqual(plan['shells'], ['gnome'])  # the main shell is not "more"
        self.assertEqual(plan['apps'], ['ssh', 'calculator'])  # in the installer's order
        self.assertEqual(plan['account'], {'username': 'user', 'password_sha512': HASH})
        self.assertEqual((result['script_path'], result['script_present']), ('/a22x-recovery/scripts/setup.sh', True))
        (self.root / 'cache/a22x-recovery/scripts/setup.sh').write_bytes(b'#!/bin/sh\n\0')
        self.assertFalse(self.run_helper('show')[1]['script_present'])  # not a text file

    def test_wrong_plans_are_named_not_used(self):
        cases = [({'system': {'catalog': 'windows-11'}}, 'UNKNOWN_IMAGE'),
                 ({'system': {'url': 'http://example.org/x.img'}}, 'INVALID_SYSTEM'),
                 ({'account': {'username': 'user', 'password': '246810'}}, 'INVALID_ACCOUNT'),
                 ({'account': {'username': 'user', 'password_sha512': '$1$md5$hash'}}, 'INVALID_ACCOUNT'),
                 ({'account': ...}, 'ACCOUNT_REQUIRED'),
                 ({'password': 'x'}, 'UNKNOWN_FIELD'),
                 ({'desktop': 'image', 'sign_in': 'gdm', 'shells': []}, 'GREETER_NEEDS_DESKTOP'),
                 ({'desktop': 'image', 'shells': ['gnome']}, 'SHELLS_NEED_DESKTOP'),
                 ({'apps': ['ssh', 'rootkit']}, 'INVALID_APP'),
                 ({'timezone': '../../etc/passwd'}, 'INVALID_TIMEZONE'),
                 ({'script': '../setup.sh'}, 'INVALID_SCRIPT'),
                 ({'created_utc': 12}, 'INVALID_TIME'),
                 ({'schema': 'a22x.install-plan.v9'}, 'NOT_A_PLAN')]
        for changes, code in cases:
            with self.subTest(code=code):
                self.plan(**changes)
                status, result = self.run_helper('show')
                self.assertEqual((status, result['valid'], result['problem']['code']), (0, False, code), result)
        (self.root / 'cache' / ip.PLAN).write_text('{"schema": "a22x.install-plan.v1", ')
        self.assertEqual(self.run_helper('show')[1]['error']['code'], 'PLAN_DAMAGED')

    def test_beside_the_current_system_no_account_is_needed(self):
        self.plan(erase_userdata=False, account=..., script=..., desktop='image', shells=[], apps=[])
        result = self.run_helper('show')[1]
        self.assertTrue(result['valid'], result)
        self.assertIsNone(result['plan']['account'])
        self.assertNotIn('script_path', result)

    def test_forget_deletes_exactly_the_plan_shown(self):
        digest = self.plan()
        code, result = self.run_helper('forget', '--expected-sha256', '0' * 64)
        self.assertEqual((code, result['error']['code']), (2, 'PLAN_CHANGED'))
        self.assertTrue((self.root / 'cache' / ip.PLAN).exists())
        self.assertEqual(self.run_helper('forget', '--expected-sha256', digest)[1]['forgotten'], True)
        self.assertFalse((self.root / 'cache' / ip.PLAN).exists())
        self.assertEqual(self.run_helper('forget', '--expected-sha256', digest)[1]['forgotten'], False)

    def test_prepare_mounts_what_the_installation_reads(self):
        self.plan()
        (self.root / 'cache/a22x-recovery/scripts/setup.sh').write_text('#!/bin/sh\n')
        self.volumes(cache_mounted=False, userdata_mounted=False)
        code, result = self.run_helper('prepare')
        self.assertEqual(code, 0, result)
        self.assertEqual(result['mounted'], ['userdata', 'cache'])
        state = json.loads((self.root / '.filesystem-state.json').read_text())
        self.assertEqual(state['cache'], {'mounted': True, 'read_only': True})  # the script is only read
        self.assertEqual(state['userdata'], {'mounted': True, 'read_only': False})
        code, result = self.run_helper('prepare')
        self.assertEqual(result['mounted'], [])  # already there
        # After the installation the plan is deleted although cache was left read-only.
        digest = hashlib.sha256((self.root / 'cache' / ip.PLAN).read_bytes()).hexdigest()
        self.assertTrue(self.run_helper('forget', '--expected-sha256', digest)[1]['forgotten'])
        self.plan()
        self.plan(system={'catalog': 'windows-11'})
        self.assertEqual(self.run_helper('prepare')[1]['error']['code'], 'NO_VALID_PLAN')


if __name__ == '__main__':
    unittest.main()
