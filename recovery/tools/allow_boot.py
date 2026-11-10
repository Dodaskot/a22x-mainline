#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Allow one more Linux BOOT image in the private platform profile (2026-11-10).

    tools/allow_boot.py PRIVATE_DIR SHA256

Adds SHA256 to boot_sha256_allowed in PRIVATE_DIR/distro-platform-v2.json, seals the profile again
(the seal is the digest of the profile without its seal field) and writes it in the file's own
layout, then updates the three fields of build-update.json that describe it. Both files are first
checked to come back byte for byte from their parsed form, and the old files stay beside them as
*.before-<first 8 hex digits of SHA256>.
"""
import hashlib
import json
from pathlib import Path
import re
import shutil
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import distro  # noqa: E402


def profile_bytes(profile):
    return (json.dumps(profile, indent=0) + '\n').encode()


def update_bytes(update):
    return json.dumps(update).encode()


def main(argv):
    if len(argv) != 3 or not re.fullmatch(r'[0-9a-f]{64}', argv[2]):
        print(__doc__.strip(), file=sys.stderr)
        return 2
    private, value = Path(argv[1]), argv[2]
    profile_path, update_path = private / 'distro-platform-v2.json', private / 'build-update.json'
    raw_profile, raw_update = profile_path.read_bytes(), update_path.read_bytes()
    profile, update = json.loads(raw_profile), json.loads(raw_update)
    distro.check_seal(profile)
    if profile_bytes(profile) != raw_profile or update_bytes(update) != raw_update:
        print('the files are not in the layout this tool writes; nothing changed', file=sys.stderr)
        return 1
    if update.get('distro_platform_sha256') != distro.digest(profile) or \
            update.get('distro_platform_file_sha256') != hashlib.sha256(raw_profile).hexdigest():
        print('build-update.json does not describe this profile; nothing changed', file=sys.stderr)
        return 1
    allowed = profile['boot_sha256_allowed']
    if value in allowed:
        print('already allowed')
        return 0
    unsealed = {key: item for key, item in profile.items() if key != 'seal'}
    unsealed['boot_sha256_allowed'] = sorted(allowed + [value])
    sealed = distro.seal(unsealed)
    distro.check_seal(sealed)
    new_profile = profile_bytes(sealed)
    update = dict(update, distro_platform_sha256=distro.digest(sealed),
                  distro_platform_file_sha256=hashlib.sha256(new_profile).hexdigest(),
                  allowed_linux_boot_sha256=sorted(set(update.get('allowed_linux_boot_sha256', [])) | {value}))
    suffix = '.before-' + value[:8]
    for path in (profile_path, update_path):
        shutil.copy2(path, path.with_name(path.name + suffix))
    profile_path.write_bytes(new_profile)
    update_path.write_bytes(update_bytes(update))
    print(json.dumps({'allowed': len(sealed['boot_sha256_allowed']), 'distro_platform_sha256': update['distro_platform_sha256'],
                      'distro_platform_file_sha256': update['distro_platform_file_sha256']}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main(sys.argv))
