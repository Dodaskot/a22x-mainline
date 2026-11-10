"""2026-11-08: the package search of the installer page, on saved answers of each site."""
from pathlib import Path
import json
import sys
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import distro as d

DEBIAN = '''<h3>Package keepassxc</h3>
  <ul>
    <li class="trixie"><a class="resultlink" href="/trixie/keepassxc">trixie (stable)</a> (utils):
\tCross Platform Password Manager &amp; more
      <br>2.7.10+dfsg1-1: all
    </li>
  </ul>
<h3>Package keepass2</h3>
  <ul>
    <li class="bookworm"><a class="resultlink" href="/bookworm/keepass2">bookworm</a> (utils): Old
      <br>2.53: amd64
    </li>
    <li class="trixie"><a class="resultlink" href="/trixie/keepass2">trixie (stable)</a> (utils):
\tPassword manager
      <br>2.57+dfsg-1: amd64 i386
    </li>
  </ul>
<h3>Package kpcli</h3>
  <ul>
    <li class="forky"><a class="resultlink" href="/forky/kpcli">forky</a> (utils): Only in testing
      <br>3.8: all
    </li>
  </ul>'''
FEDORA = '''<a class="stretched-link" href="/pkgs/keepassxc/keepassxc">
<span>keepassxc</span></a> - Cross-platform password manager
<a class="stretched-link" href="/pkgs/keepass/keepass/">
<span>keepass</span></a> - Password manager'''
ARCH = json.dumps({'results': [{'pkgname': 'keepassxc', 'pkgver': '2.8.0', 'pkgdesc': 'Password manager'},
                               {'pkgname': 'x86-only', 'pkgver': '1', 'pkgdesc': 'Not built for ARM'}]})


class Device:
    mode = 'device'


class TestPackageSearch(unittest.TestCase):
    def search(self, family, page, query='keepass', release=''):
        with mock.patch.object(d, 'remote_text', return_value=page) as text, mock.patch.object(d, 'ensure_clock'):
            return d.package_search(Device, family, release, query), text.call_args[0][1]

    def test_debian_names_versions_and_arm64(self):
        result, url = self.search('debian', DEBIAN)
        self.assertIn('packages.debian.org/search?keywords=keepass&searchon=names&suite=trixie', url)
        self.assertEqual([(r['name'], r['version'], r['available']) for r in result['results']],
                         [('keepassxc', '2.7.10+dfsg1-1', True), ('keepass2', '2.57+dfsg-1', False)])
        self.assertEqual(result['results'][0]['summary'], 'Cross Platform Password Manager & more')

    def test_ubuntu_release_and_exact_name_first(self):
        result, url = self.search('ubuntu', DEBIAN.replace('trixie', 'noble'), query='keepass2', release='noble')
        self.assertIn('packages.ubuntu.com/search?keywords=keepass2&searchon=names&suite=noble', url)
        self.assertEqual(result['results'][0]['name'], 'keepass2')

    def test_fedora_names(self):
        result, url = self.search('fedora', FEDORA)
        self.assertIn('releases=Fedora+45', url)
        self.assertEqual([(r['name'], r['available']) for r in result['results']], [('keepass', None), ('keepassxc', None)])  # Exact first.

    def test_arch_availability_from_arch_linux_arm(self):
        class Answer:
            def __init__(self, status):
                self.status = status
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
        class Opener:
            def open(self, request, timeout):
                if 'x86-only' in request.full_url:
                    error = OSError('not found')
                    error.code = 404
                    raise error
                return Answer(200)
        with mock.patch.object(d, 'url_opener', return_value=Opener()):
            result, url = self.search('arch', ARCH)
        self.assertIn('archlinux.org/packages/search/json/?q=keepass', url)
        self.assertEqual([(r['name'], r['available']) for r in result['results']], [('keepassxc', True), ('x86-only', False)])

    def test_ubuntu_section_markers_and_ranking(self):
        page = DEBIAN.replace('Password manager', 'Password manager [<strong class="pmarker">universe</strong>]').replace('trixie', 'resolute')
        page = page.replace('<h3>Package keepassxc</h3>', '<h3>Package xkeepass-tool</h3>', 1)
        result, url = self.search('ubuntu', page, query='keepass2')
        self.assertEqual(result['results'][0]['summary'], 'Password manager')
        self.assertEqual([r['name'] for r in result['results']], ['keepass2', 'xkeepass-tool'])

    def test_queries_and_families_are_checked(self):
        for query in ('', 'k', 'a b', 'x;rm', 'é' * 3, 'x' * 41):
            with self.assertRaises(d.fs.FilesystemError):
                d.package_search(Device, 'debian', '', query)
        with self.assertRaises(d.fs.FilesystemError):
            d.package_search(Device, 'gentoo', '', 'vim')

    def test_found_packages_go_into_the_plan(self):
        options = d.image_options('phosh', 'new', 'user', '246810', 0, None, {'ssid': 'Test', 'password': ''}, ['passwords'], None,
                                  ['keepassxc', 'htop', 'keepassxc'])
        self.assertEqual((options['packages'], options['apps']), (['htop', 'keepassxc'], ['passwords']))
        for bad in (['Bad'], ['-x'], ['a b'], ['x' * 101], ['p%d' % n for n in range(41)]):
            with self.assertRaises(d.fs.FilesystemError):
                d.image_options('console', 'keep', None, None, 0, None, None, (), None, bad)


if __name__ == '__main__':
    unittest.main()
