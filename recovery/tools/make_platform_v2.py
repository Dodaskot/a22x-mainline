#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Derive the v2 platform profile: kernel modules and firmware stay exact, while
per-distribution adapter scripts (Python or static helper) are no longer pinned."""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import distro as d
source, out = json.load(open(sys.argv[1])), sys.argv[2]
ADAPTER = {'etc/systemd/system/a22x-conn.service', 'etc/systemd/system/a22x-wmt-launcher.service',
           'etc/systemd/system/multi-user.target.wants/a22x-conn.service', 'opt/a22x-conn/bt_addr',
           'opt/a22x-conn/conn-start.sh', 'opt/a22x-conn/wmtload.py', 'opt/a22x-conn/wmtlaunch.py',
           'opt/a22x-conn/kernel-release'}
p = {k: v for k, v in source.items() if k not in ('seal', 'boot_sha256')}
p['id'] = d.PROFILE_ID
p['boot_sha256_allowed'] = sorted(d.ALLOWED_BOOTS)
p['entries'] = [e for e in source['entries'] if e['path'] not in ADAPTER]
p['commands'] = [c for c in source['commands'] if c != '/usr/bin/python3']
p['python3_modules'] = []
p['roles'] = {**source['roles'], 'adapter': ['lib', 'opt/a22x-conn']}
p = d.seal(p)
Path(out).write_text(json.dumps(p, sort_keys=True, indent=0) + '\n')
print(d.digest(p), len(p['entries']))
