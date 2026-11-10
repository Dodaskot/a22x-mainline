#!/usr/bin/env python3
"""Write app_icons_data.h: the icon each installer app shows, as the new system will show it.

The package an app becomes is the one a22x-firstboot.sh installs: its app_packages function is run
for every desktop style (gtk, plasma, xfce, lomiri, sxmo) and distribution family, and the first
candidate the family's package index has is taken (Debian trixie, Ubuntu 26.04 and Arch Linux ARM
indexes; Fedora takes its first candidate). The picture is that package's own application icon
from Debian's AppStream metadata (DEP-11, 128 px; trixie's PNG first, else sid's, which are
JPEG XL and decoded with ImageMagick), scaled to APP_ICON_PX for the panel. A package
without an application icon (a service such as openssh-server) gets none, and the interface draws
its plain symbol instead.

    tools/generate_app_icons.py DATA_DIR
DATA_DIR holds trixie-Components-arm64.yml.gz, trixie-icons-{128x128,64x64}.tar.gz, Components-arm64.yml.gz
and icons-{128x128,64x64}.tar.gz (deb.debian.org/debian/dists/{trixie,sid}/main/dep11), Packages-trixie.xz, Packages-resolute-main.xz, Packages-resolute-universe.xz and,
optionally, alarm-extra.db and alarm-core.db (mirror.archlinuxarm.org/aarch64).
"""
from io import BytesIO
import gzip
import lzma
from pathlib import Path
import re
import subprocess
import sys
import tarfile

from PIL import Image
import yaml

UI = Path(__file__).resolve().parents[1]
FIRSTBOOT = UI.parent / 'platform/a22x-firstboot.sh'
PX = 72
STYLES = ('gtk', 'plasma', 'xfce', 'lomiri', 'sxmo')
FAMILIES = ('debian', 'ubuntu', 'fedora', 'arch')
MANAGER = {'debian': 'apt', 'ubuntu': 'apt', 'fedora': 'dnf', 'arch': 'pacman'}
# Package names of other distributions that Debian's metadata knows under its own name.
ALIASES = {'plasma-systemsettings': 'systemsettings', 'systemsettings6': 'systemsettings', 'discover': 'plasma-discover',
           'discover6': 'plasma-discover', 'libreoffice-fresh': 'libreoffice-writer', 'libreoffice': 'libreoffice-writer',
           'MozillaFirefox': 'firefox-esr', 'firefox': 'firefox-esr', 'epiphany': 'epiphany-browser', 'openssh': 'openssh-server',
           'keepassxc': 'keepassxc-full', 'snapshot': 'gnome-snapshot'}


def app_ids():
    text = (UI / 'systems_ui.h').read_text()
    block = re.search(r'systems_app_ids\[\]=\{(.*?)\};', text, re.S).group(1)
    return re.findall(r'"([a-z]+)"', block)


def candidates(apps):
    script = FIRSTBOOT.read_text()
    function = re.search(r'^app_packages\(\) \{\n.*?^\}\n', script, re.S | re.M).group(0)
    calls = ''.join('echo "%s %s %s: $(app_packages %s %s %s)"\n' % (app, family, style, app, MANAGER[family], style)
                    for style in STYLES for family in FAMILIES for app in apps)
    out = subprocess.run(['sh', '-c', function + calls], stdout=subprocess.PIPE, text=True, check=True).stdout
    result = {}
    for line in out.splitlines():
        key, names = line.split(':', 1)
        app, family, style = key.split()
        result[(style, family, app)] = names.split()
    return result


def debian_names(path):
    return set(re.findall(r'(?m)^Package: (\S+)$', lzma.open(path, 'rt', errors='replace').read()))


def arch_names(paths):
    names = set()
    for path in paths:
        if not path.exists():
            return None
        with tarfile.open(path) as db:
            for member in db.getnames():
                head = member.split('/')[0]
                match = re.match(r'(.+)-[^-]+-[^-]+$', head)
                if match:
                    names.add(match.group(1))
    return names


def icons(components):
    loader = getattr(yaml, 'CSafeLoader', yaml.SafeLoader)
    found = {}
    with gzip.open(components, 'rt', errors='replace') as stream:
        for doc in yaml.load_all(stream, Loader=loader):
            if not isinstance(doc, dict) or doc.get('Type') != 'desktop-application':
                continue
            package = doc.get('Package')
            cached = (doc.get('Icon') or {}).get('cached') or []
            # 128 px where the app has it, else 64 px (some ship only small icons).
            best = next((c for width in (128, 64) for c in cached if c.get('width') == width and c.get('scale', 1) == 1), None)
            name = (doc.get('Name') or {}).get('C', '')
            if package and best and package not in found:
                found[package] = ('%dx%d/%s' % (best['width'], best['width'], best['name']), name)
    return found


def main(argv):
    data = Path(argv[1])
    apps = app_ids()
    assert len(apps) == 29, apps
    table = candidates(apps)
    have = {'debian': debian_names(data / 'Packages-trixie.xz'),
            'ubuntu': debian_names(data / 'Packages-resolute-main.xz') | debian_names(data / 'Packages-resolute-universe.xz'),
            'arch': arch_names([data / 'alarm-extra.db', data / 'alarm-core.db']), 'fedora': None}
    sources = []
    for prefix in ('trixie-', ''):
        files = {}
        for size in ('128x128', '64x64'):
            archive = tarfile.open(data / ('%sicons-%s.tar.gz' % (prefix, size)))
            files.update({'%s/%s' % (size, Path(m.name).name): (archive, m) for m in archive.getmembers() if m.isfile()})
        sources.append((icons(data / (prefix + 'Components-arm64.yml.gz')), None, files))
    known = {}
    for found, archive, files in reversed(sources):
        known.update({package: (entry, archive, files) for package, entry in found.items()})

    def picture(entry):
        (name, label), unused, files = entry
        if name not in files:
            return None
        archive, member = files[name]
        raw = archive.extractfile(member).read()
        if not raw.startswith(b'\x89PNG'):
            raw = subprocess.run(['magick', '-', 'png:-'], input=raw, stdout=subprocess.PIPE, check=True).stdout
        return Image.open(BytesIO(raw)).convert('RGBA')
    pictures, index, chosen = [], {}, {}
    for style in STYLES:
        for family in FAMILIES:
            for app in apps:
                names = table[(style, family, app)]
                available = have[family]
                package = next((n for n in names if available is None or n in available), names[0] if names else None)
                chosen[(style, family, app)] = package
                source = package if package in known else ALIASES.get(package)
                if source not in known:
                    continue
                if source not in index:
                    image = picture(known[source])
                    if image is None:
                        continue
                    image = image.resize((PX, PX), Image.LANCZOS)
                    index[source] = len(pictures)
                    pictures.append((source, known[source][0][1], image.tobytes()))
    out = ['/* Generated by tools/generate_app_icons.py from Debian AppStream icons (DEP-11); do not edit. */',
           '#ifndef A22X_APP_ICONS_DATA_H', '#define A22X_APP_ICONS_DATA_H', '#include <stdint.h>',
           '#define APP_ICON_PX %d' % PX, '#define APP_ICONS %du' % len(pictures),
           'static const char*const app_icon_styles[]={%s};' % ','.join('"%s"' % s for s in STYLES),
           'static const char*const app_icon_families[]={%s};' % ','.join('"%s"' % f for f in FAMILIES),
           '/* Index into app_icon_rgba of each app by style and family, or -1 without an icon. */',
           'static const signed char app_icon_of[%d][%d][%d]={' % (len(STYLES), len(FAMILIES), len(apps))]
    for style in STYLES:
        rows = []
        for family in FAMILIES:
            cells = []
            for app in apps:
                package = chosen[(style, family, app)]
                source = package if package in known else ALIASES.get(package)
                cells.append(str(index.get(source, -1)))
            rows.append('{' + ','.join(cells) + '}')
        out.append('    {' + ','.join(rows) + '},')
    out.append('};')
    out.append('static const char*const app_icon_packages[]={%s};' % ','.join('"%s"' % p[0] for p in pictures))
    out.append('static const uint8_t app_icon_rgba[APP_ICONS][APP_ICON_PX*APP_ICON_PX*4]={')
    for package, name, raw in pictures:
        out.append('    /* %s: %s */ {%s},' % (package, name.replace('*/', ''), ','.join(str(b) for b in raw)))
    out += ['};', '#endif', '']
    (UI / 'app_icons_data.h').write_text('\n'.join(out))
    print('%d icons for %d apps; without icon: %s' % (len(pictures), len(apps),
          sorted({chosen[k] or '-' for k in chosen if index.get(chosen[k] if chosen[k] in known else ALIASES.get(chosen[k]), -1) < 0})))


if __name__ == '__main__':
    main(sys.argv)
