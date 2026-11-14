#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Custom ROM GSIs for the catalog (2026-11-10), from their makers' GitHub releases.

The newest release of each repository below gives its arm64 A/B images: GAPPS, MICROG and VANILLA,
never EROFS (the stock 4.14 kernel cannot mount it). GitHub states each file's SHA-256, so the
recovery checks the download as it does for Google's GSIs; the size of the system image inside
the .img.xz comes from the xz index, read with two small range requests. Treble GSIs (TrebleDroid
based) keep what older vendors need, so they are listed for this phone's Android 12 vendor too.

    tools/update_gsi_catalog.py [catalog.json]
"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import android_gsi  # noqa: E402

# Repository, the ROM's name, its maker as the catalog shows it.
REPOSITORIES = (
    ('Doze-off/crdroid_gsi_treble', 'crDroid', 'crDroid, Treble GSI by Doze-off'),
    ('Doze-off/EvoX_treble', 'Evolution X', 'Evolution X, Treble GSI by Doze-off'),
    ('Doze-off/ProjectInfinity-X_gsi', 'Infinity X', 'Infinity X, Treble GSI by Doze-off'),
    ('Doze-off/axion_aosp', 'AxionOS', 'AxionOS, Treble GSI by Doze-off'),
    ('Doze-off/Lunaris-AOSP_gsi', 'Lunaris AOSP', 'Lunaris, Treble GSI by Doze-off'),
    ('Doze-off/WitAqua_treble', 'WitAqua', 'WitAqua, Treble GSI by Doze-off'),
    ('Doze-off/Mist-OS_gsi', 'MistOS', 'MistOS, Treble GSI by Doze-off'),
    ('Doze-off/Matrixx_treble', 'Matrixx', 'Matrixx, Treble GSI by Doze-off'),
    ('Doze-off/Alphadroid_gsi', 'AlphaDroid', 'AlphaDroid, Treble GSI by Doze-off'),
    ('Doze-off/Derpfest_treble', 'DerpFest', 'DerpFest, Treble GSI by Doze-off'),
    ('Doze-off/voltage_a16_treble', 'VoltageOS', 'VoltageOS, Treble GSI by Doze-off'),
    ('cawilliamson/treble_restlessos', 'RestlessOS', 'RestlessOS, Treble GSI by cawilliamson'),
)
TREBLE_LEVELS = [3, 4, 5, 6, 7, 8, 202404, 202504, 202604]


def api(path):
    out = subprocess.run(['curl', '-fsSL', '--max-time', '60', 'https://api.github.com/' + path], capture_output=True, check=True)
    return json.loads(out.stdout)


def unpacked_size(url, size):
    """The xz index of URL, read with two range requests into a sparse file, gives the image size."""
    def get(start, end):
        return subprocess.run(['curl', '-fsSL', '--max-time', '120', '-r', '%d-%d' % (start, end), url], capture_output=True,
                              check=True).stdout
    tail, head = get(max(0, size - 262144), size - 1), get(0, 63)
    with tempfile.NamedTemporaryFile(suffix='.xz') as probe:
        probe.truncate(size)
        probe.write(head)
        probe.seek(size - len(tail))
        probe.write(tail)
        probe.flush()
        fd = os.open(probe.name, os.O_RDONLY)
        try:
            return android_gsi.xz_size(fd)
        finally:
            os.close(fd)


def entries():
    found = []
    for repository, name, maker in REPOSITORIES:
        # The newest release that is neither a draft nor a pre-release, as /releases/latest gives it
        # (2026-11-14 review: per_page=1 took a pre-release for a stable build).
        releases = [r for r in (api('repos/%s/releases?per_page=10' % repository) or [])
                    if not r.get('prerelease') and not r.get('draft')]
        if not releases:
            continue
        release = releases[0]
        title = release.get('name') or ''
        # "crDroid 12.12 | A16 QPR2 | ..." names its Android; RestlessOS's version starts with it (17.0.0).
        android = (re.search(r'\bA(\d{2})\b', title) or re.search(r'^(\d{2})\.\d', release['tag_name']) or [None, ''])[1]
        version = (re.search(r'(\d+(?:\.\d+)+)', title.split('|')[0]) or [None, release['tag_name']])[1]
        for asset in release.get('assets', []):
            file = asset['name']
            if 'arm64' not in file or not file.endswith('.img.xz') or 'EROFS' in file.upper() or file.lower().endswith('erofs.img.xz'):
                continue
            digest = (asset.get('digest') or '').removeprefix('sha256:')
            if not re.fullmatch(r'[0-9a-f]{64}', digest):
                continue
            kind = 'gms' if 'GAPPS' in file.upper() else ('microg' if 'MICROG' in file.upper() else 'vanilla')
            ident = re.sub(r'[^a-z0-9.]+', '-', (name + ' ' + version + ' ' + kind).lower()).strip('-')
            # Two images of one kind in one release (2026-11-14 review: the same id twice): the file names it.
            if any(item['id'] == ident for item in found):
                ident = re.sub(r'[^a-z0-9.]+', '-', (ident + ' ' + file.removesuffix('.img.xz')).lower()).strip('-')[:80]
            url = asset['browser_download_url']
            found.append({'id': ident, 'category': 'port', 'name': name + ' ' + version, 'version': release['published_at'][:10],
                          'android': android, 'sdk': {'15': 35, '16': 36, '17': 37}.get(android, 0), 'variant': kind,
                          'region': 'Global', 'family': 'android', 'maker': maker, 'build': title.split('|')[0].strip()[:40],
                          'patch': release['published_at'][:7], 'url': url, 'file': file, 'size': asset['size'], 'sha256': digest,
                          'system_bytes': unpacked_size(url, asset['size']), 'vendor_levels': TREBLE_LEVELS, 'format': 'img.xz',
                          'fit_note': 'Treble GSI: made to start on older vendors too'})
            print(ident, asset['size'], found[-1]['system_bytes'], file=sys.stderr)
    return found


def main():
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / 'catalog.json'
    catalog = json.loads(path.read_text())
    makers = {maker for _, _, maker in REPOSITORIES}
    kept = [e for e in catalog.get('android', []) if e.get('maker') not in makers]
    catalog['android'] = kept + entries()
    path.write_text(json.dumps(catalog, indent=1) + '\n')
    print(len(catalog['android']), 'Android images', file=sys.stderr)


if __name__ == '__main__':
    main()
