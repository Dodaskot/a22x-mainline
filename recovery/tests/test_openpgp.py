"""2026-11-08: OpenPGP signatures of distribution images, checked against pinned keys."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import openpgp as o

DATA = Path(__file__).resolve().parent / 'data' / 'openpgp'


class TestOpenPGP(unittest.TestCase):
    def setUp(self):
        self.key = (DATA / 'fixture.asc').read_bytes()

    def test_detached_binary_signature_over_a_file(self):
        fingerprint = o.verify_file(DATA / 'data.bin', (DATA / 'data.bin.sig').read_bytes(), self.key)
        self.assertEqual(len(fingerprint), 40)

    def test_armoured_signature_over_bytes(self):
        o.verify_bytes((DATA / 'data.txt').read_bytes(), (DATA / 'data.txt.asc').read_bytes(), self.key)

    def test_text_mode_signature(self):
        o.verify_file(DATA / 'data.txt', (DATA / 'data.txt.textsig').read_bytes(), self.key)

    def test_clearsigned_text_is_returned_without_dash_escapes(self):
        text = o.verify_clearsigned((DATA / 'data.txt.clear').read_bytes(), self.key)
        self.assertIn(b'- dash line', text)
        self.assertNotIn(b'- - dash line', text)

    def test_changed_data_fails(self):
        data = bytearray((DATA / 'data.bin').read_bytes())
        data[10] ^= 1
        with self.assertRaises(o.SignatureError):
            o.verify_bytes(bytes(data), (DATA / 'data.bin.sig').read_bytes(), self.key)
        changed = (DATA / 'data.txt.clear').read_bytes().replace(b'second line', b'second lime')
        with self.assertRaises(o.SignatureError):
            o.verify_clearsigned(changed, self.key)

    def test_another_key_fails(self):
        other = (Path(__file__).resolve().parents[1] / 'keys' / 'archlinuxarm.asc').read_bytes()
        with self.assertRaises(o.SignatureError):
            o.verify_bytes((DATA / 'data.txt').read_bytes(), (DATA / 'data.txt.asc').read_bytes(), other)

    def test_sha1_is_refused(self):
        with self.assertRaises(o.SignatureError):
            o.verify_file(DATA / 'data.bin', (DATA / 'data.bin.sha1sig').read_bytes(), self.key)

    def test_garbage_is_refused(self):
        for junk in (b'', b'not a signature', b'-----BEGIN PGP SIGNATURE-----\n\n!!!\n-----END PGP SIGNATURE-----\n'):
            with self.assertRaises(o.SignatureError):
                o.verify_bytes(b'x', junk, self.key)

    def test_pinned_distribution_keys_load(self):
        keys = Path(__file__).resolve().parents[1] / 'keys'
        fingerprints = {k['fingerprint'] for name in ('archlinuxarm.asc', 'ubuntu-cloud.asc', 'fedora.gpg')
                        for k in o.read_keys((keys / name).read_bytes())}
        for expected in ('68B3537F39A313B3E574D06777193F152BDBE6A6', 'D2EB44626FDDC30B513D5BB71A5D6C4C7DB87C81',
                         '4F50A6114CD5C6976A7F1179655A4B02F577861E'):
            self.assertIn(expected, fingerprints)


if __name__ == '__main__':
    unittest.main()
