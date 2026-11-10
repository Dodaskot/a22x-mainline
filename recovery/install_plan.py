#!/usr/bin/python3
"""An installation prepared in the Android app (2026-11-10): the recovery offers it at startup.

The app writes a22x-recovery/install-plan.json on the cache volume beside handoff.json, then
restarts into the recovery:

    {"schema": "a22x.install-plan.v1", "source": "android-app 0.6.139", "created_utc": 1794306000,
     "system": {"catalog": "ubuntu-24.04"}, "erase_userdata": true,
     "desktop": "phosh", "shells": ["gnome"], "sign_in": "auto", "apps": ["ssh", "calculator"],
     "account": {"username": "user", "password_sha512": "$6$salt$hash"},
     "locale": "uk_UA", "timezone": "Europe/Kyiv", "script": "setup.sh"}

Nothing here installs anything. The recovery fills its own install pages with the plan; the
person reviews them and holds Install as for any other installation, and distro.py checks every
choice again. The password arrives only as a SHA-512 crypt hash; a script is named, and read from
a22x-recovery/scripts/ where the app saves it.

    install_plan.py --device show                 the plan, checked; "present": false when none
    install_plan.py --device prepare              mount what the installation reads: userdata with
                                                  write access, cache read-only for the script
    install_plan.py --device forget --expected-sha256 HEX     delete exactly that plan
"""
# SPDX-License-Identifier: MIT
import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent))

import filesystem as fs  # noqa: E402

PLAN = Path('a22x-recovery/install-plan.json')
SCRIPTS = Path('a22x-recovery/scripts')
SCHEMA = 'a22x.install-plan.v1'
LIMIT = 64 * 1024
SCRIPT_LIMIT = 1 << 20
EARLIEST = 1793491200  # 2026-11-01
LATEST = 4102444800  # 2100-01-01
USERNAME = re.compile(r'[a-z_][a-z0-9_-]{0,31}')
# SHA-512 crypt as the app makes it: $6$ (optionally rounds=N$), a salt of up to 16, 86 characters.
SHA512_CRYPT = re.compile(r'\$6\$(?:rounds=[1-9][0-9]{3,8}\$)?[./A-Za-z0-9]{1,16}\$[./A-Za-z0-9]{86}')
SCRIPT_NAME = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,60}\.sh')  # As Handoff.java allows.
KEYS = {'schema', 'source', 'created_utc', 'system', 'erase_userdata', 'desktop', 'shells', 'sign_in', 'apps',
        'account', 'locale', 'timezone', 'script'}


class PlanError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def require(condition, code, message):
    if not condition:
        raise PlanError(code, message)


def check(record, catalog):
    """The plan as the install pages take it, or PlanError. CATALOG: {id: entry} of distro.py."""
    import distro
    require(isinstance(record, dict) and record.get('schema') == SCHEMA, 'NOT_A_PLAN', 'This is not an A22x installation plan')
    require(set(record) <= KEYS, 'UNKNOWN_FIELD', 'The plan has fields this recovery does not know: ' + ', '.join(sorted(set(record) - KEYS)))
    created = record.get('created_utc', 0)
    require(isinstance(created, int) and not isinstance(created, bool) and (created == 0 or EARLIEST <= created <= LATEST),
            'INVALID_TIME', 'The plan has an impossible date')
    source = record.get('source', '')
    require(isinstance(source, str) and len(source) <= 64, 'INVALID_SOURCE', 'The plan names its source wrongly')
    system = record.get('system')
    require(isinstance(system, dict) and set(system) == {'catalog'} and isinstance(system['catalog'], str),
            'INVALID_SYSTEM', 'The plan must name an image of the catalog')
    entry = catalog.get(system['catalog'])
    require(entry is not None, 'UNKNOWN_IMAGE', 'This recovery does not know the image ' + system['catalog'][:64])
    require(entry.get('unavailable') is not True, 'IMAGE_UNAVAILABLE', 'The server no longer offers this image')
    erase = record.get('erase_userdata', True)
    require(isinstance(erase, bool), 'INVALID_ERASE', 'The plan must say whether userdata is erased')
    desktop = record.get('desktop', 'image')
    require(desktop in distro.DESKTOPS, 'INVALID_DESKTOP', 'Unknown desktop choice')
    shells = record.get('shells', [])
    require(isinstance(shells, list) and all(s in distro.SHELLS for s in shells) and len(set(shells)) == len(shells),
            'INVALID_SHELL', 'Unknown shell choice')
    shells = [s for s in distro.SHELLS if s in shells and s != desktop]
    require(not shells or desktop in distro.SHELLS, 'SHELLS_NEED_DESKTOP', 'More shells need a desktop as the main shell')
    sign_in = record.get('sign_in', 'auto')
    require(sign_in in distro.GREETERS, 'INVALID_GREETER', 'Unknown sign-in screen')
    require(sign_in == 'auto' or desktop in distro.SHELLS, 'GREETER_NEEDS_DESKTOP', 'A sign-in screen needs a desktop')
    apps = record.get('apps', [])
    require(isinstance(apps, list) and all(a in distro.APPS for a in apps), 'INVALID_APP', 'Unknown app choice')
    account = record.get('account')
    if account is not None:
        require(isinstance(account, dict) and set(account) == {'username', 'password_sha512'}
                and isinstance(account['username'], str) and USERNAME.fullmatch(account['username'])
                and isinstance(account['password_sha512'], str) and SHA512_CRYPT.fullmatch(account['password_sha512']),
                'INVALID_ACCOUNT', 'The account of the plan is not valid')
    require(account is not None or not erase, 'ACCOUNT_REQUIRED', 'A clean install needs a new account')
    locale = record.get('locale')
    require(locale is None or locale in distro.LOCALES, 'INVALID_LOCALE', 'Unknown system language')
    zone = record.get('timezone')
    require(zone is None or (isinstance(zone, str) and len(zone) <= 64 and distro.TIMEZONE.fullmatch(zone)),
            'INVALID_TIMEZONE', 'Unknown time zone')
    script = record.get('script')
    require(script is None or (isinstance(script, str) and SCRIPT_NAME.fullmatch(script)), 'INVALID_SCRIPT',
            'The setup script of the plan has a name this recovery does not take')
    return {'source': source, 'created_utc': created, 'catalog': system['catalog'],
            'image': {k: entry[k] for k in ('name', 'version', 'family', 'file', 'size', 'desktop') if k in entry},
            'erase_userdata': erase, 'desktop': desktop, 'shells': shells, 'sign_in': sign_in,
            'apps': [a for a in distro.APPS if a in apps], 'account': account, 'locale': locale, 'timezone': zone,
            'script': script}


def read(root):
    """(plan record, sha256 of its bytes), or (None, None) when the volume has no plan."""
    path = Path(root) / PLAN
    if not path.is_file() or path.is_symlink():
        return None, None
    data = path.read_bytes()[:LIMIT + 1]
    require(len(data) <= LIMIT, 'PLAN_TOO_LARGE', 'The plan file is too large')
    try:
        record = json.loads(data.decode('utf-8'))
    except (UnicodeDecodeError, ValueError):
        raise PlanError('PLAN_DAMAGED', 'The plan file is not valid JSON') from None
    return record, hashlib.sha256(data).hexdigest()


def script_state(root, name):
    """Whether the named script is in the app's folder as a regular text file of at most 1 MiB."""
    if not name:
        return None
    path = Path(root) / SCRIPTS / name
    if not path.is_file() or path.is_symlink() or path.stat().st_size > SCRIPT_LIMIT:
        return False
    return b'\0' not in path.read_bytes()


def show(context):
    import distro
    import settings
    catalog = {e['id']: e for e in distro.catalog_entries(context) if isinstance(e, dict) and e.get('id')}
    with settings.volume_root(context, 'cache', 'ro') as root:
        record, digest = read(root)
        if record is None:
            return {'present': False}
        try:
            plan = check(record, catalog)
        except PlanError as exc:
            return {'present': True, 'sha256': digest, 'valid': False, 'problem': {'code': exc.code, 'message': str(exc)}}
        found = script_state(root, plan['script'])
    return {'present': True, 'sha256': digest, 'valid': True, 'plan': plan,
            **({'script_path': '/' + str(SCRIPTS / plan['script']), 'script_present': found} if plan['script'] else {})}


def prepare(context):
    """Mount what the installation reads, as the person would in Files: userdata with write access
    (the install pages need it), and cache read-only while a script of the plan is read from it."""
    result = show(context)
    require(result.get('present') and result.get('valid'), 'NO_VALID_PLAN', 'There is no valid plan to prepare')
    mounted = []
    userdata = context.volume('userdata')
    if not userdata['mounted']:
        require(userdata['mountable'], 'USERDATA_NOT_MOUNTABLE',
                'Userdata cannot be mounted: it holds Android data or no file system. Format it in Files for a clean install.')
        context.mount('userdata', 'rw', True, userdata['volume_token'])
        mounted.append('userdata')
    else:
        require(not userdata['read_only'] and userdata['write_supported'], 'USERDATA_READ_ONLY',
                'Userdata is mounted read-only. Mount it with write access in Systems.')
    if result['plan']['script'] and result.get('script_present'):
        cache = context.volume('cache')
        if not cache['mounted']:
            context.mount('cache', 'ro', False, cache['volume_token'])
            mounted.append('cache')
    return {**result, 'mounted': mounted}


def forget(context, expected):
    import settings
    require(isinstance(expected, str) and re.fullmatch(r'[0-9a-f]{64}', expected), 'INVALID_ARGUMENT', 'Name the plan by its SHA-256')
    # prepare left cache read-only for the script; the plan is deleted with write access.
    cache = context.volume('cache')
    if cache['mounted'] and (cache['read_only'] or not cache['write_supported']):
        context.unmount('cache')
    with settings.volume_root(context, 'cache', 'rw') as root:
        record, digest = read(root)
        if record is None:
            return {'forgotten': False}
        require(digest == expected, 'PLAN_CHANGED', 'The plan changed since it was shown')
        (Path(root) / PLAN).unlink()
    return {'forgotten': True}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--device', action='store_true')
    mode.add_argument('--fixture', type=Path)
    commands = parser.add_subparsers(dest='command', required=True)
    commands.add_parser('show')
    commands.add_parser('prepare')
    commands.add_parser('forget').add_argument('--expected-sha256', required=True)
    args = parser.parse_args(argv)
    try:
        context = fs.FixtureContext(args.fixture) if args.fixture else fs.DeviceContext()
        with context.operation_lock():
            result = {'show': lambda: show(context), 'prepare': lambda: prepare(context),
                      'forget': lambda: forget(context, args.expected_sha256)}[args.command]()
        print(json.dumps({'ok': True, 'operation': args.command, 'mode': context.mode, **result}, separators=(',', ':')))
        return 0
    except (PlanError, fs.FilesystemError) as exc:
        code, message = getattr(exc, 'code', 'PLAN_FAILED'), getattr(exc, 'message', None) or str(exc)
    except (OSError, ValueError) as exc:
        code, message = 'IO_ERROR', str(exc)
    print(json.dumps({'ok': False, 'operation': args.command, 'error': {'code': code, 'message': message[:300]}}, separators=(',', ':')))
    return 2


if __name__ == '__main__':
    raise SystemExit(main())
