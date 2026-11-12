"""Shells and sign-in screens of a22x-firstboot.sh, run in a throwaway root.

The script's functions run under busybox ash (close to Debian's dash) in a user namespace
chroot, with systemctl, usermod and the package managers replaced by stand-ins that log their
arguments. Nothing outside the temporary root is touched.
"""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / 'platform/a22x-firstboot.sh'
BUSYBOX = shutil.which('busybox')
APPLETS = ('sh', 'sed', 'awk', 'grep', 'cut', 'head', 'tr', 'wc', 'mkdir', 'rm', 'cp', 'mv', 'chmod', 'chown',
           'cat', 'printf', 'echo', 'date', 'id', 'mktemp', 'ln', 'ls', 'test', '[', 'sleep', 'true', 'false',
           'sort', 'touch', 'env', 'readlink', 'basename', 'dirname', 'tar', 'timeout')
STUB = '#!/bin/sh\necho "%s $*" >> /calls\n'


def usable():
    if not BUSYBOX or not shutil.which('unshare'):
        return False
    return subprocess.run(['unshare', '-r', '-m', 'true'], capture_output=True).returncode == 0


def functions():
    """Every function of the script, without its main flow."""
    text = SCRIPT.read_text()
    return text[text.index('oem_cleanup() {'):text.index('resolv_link=$(readlink')]


@unittest.skipUnless(usable(), 'needs busybox and user namespaces')
class Shells(unittest.TestCase):
    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix='a22x-fbshells-', dir=os.environ.get('TMPDIR')))
        self.addCleanup(shutil.rmtree, self.root, True)
        for d in ('bin', 'usr/bin', 'usr/sbin', 'etc', 'tmp', 'home/user', 'usr/lib/systemd/system',
                  'usr/share/wayland-sessions', 'usr/share/xsessions', 'opt/a22x-conn', 'var/lib'):
            (self.root / d).mkdir(parents=True, exist_ok=True)
        shutil.copy(BUSYBOX, self.root / 'bin/busybox')
        for applet in APPLETS:
            (self.root / 'bin' / applet).symlink_to('busybox')
        for name in ('systemctl', 'usermod', 'useradd', 'gsettings', 'loginctl'):
            self.stub(name)
        self.stub('dbus-run-session', '#!/bin/sh\nexec "$@"\n')
        self.write('usr/bin/getent', '#!/bin/sh\ncase "$1" in group) grep -q "^$2:" /etc/group ;; '
                                     'passwd) grep "^$2:" /etc/passwd ;; esac\n', 0o755)
        self.write('etc/passwd', 'root:x:0:0::/root:/bin/sh\nuser:x:1000:1000::/home/user:/bin/sh\n'
                                 'greeter:x:964:964::/var/lib/greeter:/usr/bin/nologin\n')
        self.write('etc/group', 'video:x:985:\ninput:x:984:\nrender:x:983:\naudio:x:982:\n')
        self.write('usr/lib/systemd/system/greetd.service', '[Unit]\nConflicts=getty@tty1.service\n')
        self.write('etc/greetd/config.toml', '[terminal]\nvt = 1\n[default_session]\ncommand = "agreety"\nuser = "greeter"\n')
        self.write('usr/bin/phosh-session', '#!/bin/sh\nCOMPOSITOR="/usr/bin/phoc"\n'
                                            'if [ -x /usr/bin/phoc ]; then\n  COMPOSITOR=/usr/bin/phoc\nfi\n'
                                            'if [ -f ~/.phoshdebug ]; then\n  . ~/.phoshdebug\nfi\n'
                                            'echo "phosh via $COMPOSITOR"\n', 0o755)
        self.write('usr/share/wayland-sessions/plasma-mobile.desktop', '[Desktop Entry]\nName=Plasma Mobile\nExec=startplasmamobile\n')
        self.write('usr/share/wayland-sessions/phosh.desktop', '[Desktop Entry]\nName=Phosh\nExec=phosh-session\n')
        self.write('usr/share/xsessions/plasmax11.desktop', '[Desktop Entry]\nName=Plasma (X11)\nExec=startplasma-x11\n')
        self.write('lib.sh', functions())

    def write(self, path, text, mode=0o644):
        p = self.root / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text)
        p.chmod(mode)

    def stub(self, name, body=None):
        self.write('usr/bin/' + name, body or STUB % name, 0o755)

    def run_sh(self, body, env=''):
        driver = ('PATH=/usr/local/bin:/usr/bin:/usr/sbin:/bin\nexport PATH\nLOG=/fb.log\n'
                  'say() { echo "say: $*" >> /calls; }\n. /lib.sh\n' + env + '\n' + body + '\n')
        self.write('driver.sh', driver)
        out = self.chroot('/bin/sh', '/driver.sh')
        self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
        return out.stdout

    def chroot(self, *argv):
        # /dev/null is bound in from outside: a user namespace cannot make device nodes.
        (self.root / 'dev').mkdir(exist_ok=True)
        (self.root / 'dev/null').touch()
        inner = 'mount --bind /dev/null "$0/dev/null" && exec chroot "$0" "$@"'
        return subprocess.run(['unshare', '-r', '-m', 'sh', '-c', inner, str(self.root), *argv],
                              capture_output=True, text=True, timeout=60)

    def read(self, path):
        return (self.root / path).read_text()

    def calls(self):
        p = self.root / 'calls'
        return p.read_text() if p.exists() else ''

    def session(self, shells, greeter, new_user='user'):
        self.run_sh('session', f'SHELLS="{shells}"\nGREETER={greeter}\nNEW_USER={new_user}\n')

    def test_lomiri_describes_the_phone_to_deviceinfo(self):
        # lomiri-session takes its grid unit from deviceinfo: 8 px for an unknown device replaced
        # the wrapper's 24 (Ubuntu 26.04, 2026-11-09).
        self.session('lomiri', 'auto')
        self.assertIn('DEVICEINFO_DEVICE_NAME=a22x', self.read('usr/bin/a22x-lomiri-session'))
        info = self.read('etc/deviceinfo/devices/a22x.yaml')
        self.assertIn('a22x:\n  Names:\n', info)
        # Scale 3 crashed Mir: 2408 is not a multiple of 3 (2026-11-09); 16 px is scale 2.
        self.assertIn('  GridUnit: 16\n', info)
        self.assertIn('GRID_UNIT_PX=16', self.read('usr/bin/a22x-lomiri-session'))
        self.assertIn('  DeviceType: phone\n', info)

    def test_lomiri_keyboard_and_ubuntu_language_packages(self):
        # Lomiri's keyboard crashed without its English and emoji data; GNOME stayed English on
        # Ubuntu without its language pack (2026-11-09).
        self.write('etc/os-release', 'ID=ubuntu\n')
        out = self.run_sh('echo "extras: $(shell_extras lomiri apt) | $(locale_packages apt)"',
                          'SHELLS="plasma gnome lomiri"\nA22X_LOCALE=es_ES')
        self.assertIn('lomiri-keyboard-english lomiri-keyboard-emoji lomiri-keyboard-spanish', out)
        self.assertIn('locales language-pack-es language-pack-gnome-es language-pack-kde-es', ' '.join(out.split()))
        self.write('etc/os-release', 'ID=debian\n')
        out = self.run_sh('echo "langs: $(locale_packages apt)"', 'SHELLS="gnome"\nA22X_LOCALE=es_ES')
        self.assertEqual(out.strip(), 'langs: locales')

    def test_passwords_are_stored_as_sha512(self):
        # The account is shared with every system beside, musl ones too: postmarketOS could not
        # read the distributions' yescrypt (2026-11-10).
        self.stub('chpasswd')
        self.run_sh('set_password user 246810')
        self.assertIn('chpasswd -c SHA512', self.calls())
        # postmarketOS: the account made at the first start is the one its phone shell runs as.
        self.write('usr/lib/systemd/system-generators/plasma-user', 'user_file="/etc/default_user"\n')
        self.stub('useradd'); self.stub('usermod'); self.stub('getent')
        self.run_sh('account', 'NEW_USER=anna\nNEW_PASSWORD=246810')
        self.assertEqual(self.read('etc/default_user'), 'anna\n')

    def test_the_setup_swap_leaves_room_for_the_packages(self):
        """2026-11-10: a fixed 4 GiB swap on a nearly full userdata left apt no room for GNOME.
        df, dd, mkswap and swapon are busybox applets, which ash runs before any file in PATH:
        they are shell functions here, so nothing real is written."""
        stand_ins = ('dd() { echo "dd $*" >> /calls; }\nmkswap() { echo "mkswap $*" >> /calls; }\n'
                     'swapon() { [ "$1" = --show ] && return 0; echo "swapon $*" >> /calls; }\nchmod() { :; }\n')
        for free_mib, expected in ((12288, 'count=4096'), (7168, 'count=2048'), (5530, None)):
            self.write('calls', '')
            df = 'df() { echo "Filesystem 1024-blocks Used Available Capacity Mounted"; echo "/dev/x 99999999 1 %d 1%% /"; }\n' % (free_mib * 1024)
            self.run_sh(stand_ins + df + 'swap_on || echo refused >> /calls')
            calls = self.calls()
            if expected:
                self.assertIn(expected, calls)
                self.assertIn('swapon /var/a22x-swap', calls)
            else:
                self.assertNotIn('dd ', calls)
                self.assertIn('say: no temporary swap: the packages need the free space', calls)
                self.assertIn('refused', calls)
        # A disk that fills up while the file is written: the part already written goes away.
        self.write('var/a22x-swap', 'partly written')
        df = 'df() { echo header; echo "/dev/x 99999999 1 %d 1%% /"; }\n' % (12288 * 1024)
        self.run_sh(stand_ins.replace('dd() { echo "dd $*" >> /calls; }', 'dd() { return 1; }') + df + 'swap_on || echo refused >> /calls')
        self.assertFalse((self.root / 'var/a22x-swap').exists())
        # swap_off removes the file even when it was never switched on.
        self.write('var/a22x-swap', 'left over')
        self.run_sh('swapoff() { return 255; }\nswap_off')
        self.assertFalse((self.root / 'var/a22x-swap').exists())

    def test_a_password_from_the_android_app_is_set_as_its_hash(self):
        """2026-11-10: the app's plan carries a SHA-512 hash, never the password."""
        self.stub('chpasswd', '#!/bin/sh\necho "chpasswd $* $(cat)" >> /calls\n')
        self.stub('useradd'); self.stub('usermod')
        self.stub('getent', '#!/bin/sh\ncase "$1" in shadow) echo "root:x:1::::::" ;; passwd) echo "olena:x:1000:1000::/home/olena:/bin/bash" ;; esac\n')
        hashed = '$6$a22xsalt$' + 'B' * 86
        self.run_sh('account', "NEW_USER=olena\nNEW_PASSWORD_HASH='%s'\nPRIMARY_USER=1" % hashed)
        calls = self.calls()
        self.assertIn('chpasswd -e olena:' + hashed, calls)
        self.assertIn('chpasswd -e root:' + hashed, calls)  # a clean install's root, as with a typed password
        self.assertNotIn('chpasswd -c', calls)

    def test_automatic_sign_in_with_two_shells(self):
        self.session('phosh plasma', 'auto')
        config = self.read('etc/greetd/config.toml')
        self.assertIn('[initial_session]\ncommand = "/usr/bin/a22x-shell"\nuser = "user"', config)
        self.assertIn('[default_session]\ncommand = "/usr/bin/a22x-shell"\nuser = "user"', config)
        self.assertIn('vt = 1', config)
        self.assertEqual(self.read('etc/a22x/shells'),
                         'phosh|Phosh|/usr/bin/a22x-session\nplasma|Plasma Mobile|/usr/bin/a22x-plasma-session\n')
        # a22x-shell runs as the user, and /etc/a22x is private (0700): it reads a public copy
        # (the text login on Arch returned at once without it, 2026-11-09).
        self.assertEqual(self.read('usr/share/a22x/shells'), self.read('etc/a22x/shells'))
        self.assertEqual((self.root / 'usr/share/a22x/shells').stat().st_mode & 0o777, 0o644)
        self.assertIn('list=/usr/share/a22x/shells', self.read('usr/bin/a22x-shell'))
        self.assertIn('/usr/share/a22x/shells', self.read('usr/bin/a22x-switch-shell'))
        self.assertIn('exec startplasmamobile', self.read('usr/bin/a22x-plasma-session'))
        switch = self.read('usr/local/share/applications/a22x-switch-plasma.desktop')
        self.assertIn('Exec=/usr/bin/a22x-switch-shell plasma', switch)
        self.assertNotIn('NotShowIn', switch)  # Phosh is GNOME too: the running shell's launcher is hidden per user
        self.assertIn('X-Purism-FormFactor=Workstation;Mobile;', switch)  # in Phosh's phone app grid
        self.assertIn('Name[uk]=Перейти до Plasma Mobile', switch)
        self.assertIn('systemctl enable --force greetd.service', self.calls())
        # The image's own greetd file is kept beside.
        self.assertIn('user = "greeter"', self.read('etc/greetd/config.toml.image'))

    def test_plasma_gives_only_kwin_the_gpu_path(self):
        # 2026-11-09: kmsro for the whole session crashed Plasma's X11 helpers; the Arch that worked
        # set it for KWin's service only, and scaled X11 programs.
        self.stub('phrog')
        self.session('phosh plasma', 'phrog')
        self.assertNotIn('MESA_LOADER_DRIVER_OVERRIDE', self.read('usr/bin/a22x-plasma-session'))
        self.assertEqual(self.read('etc/systemd/user/plasma-kwin_wayland.service.d/50-a22x-gpu.conf'),
                         '[Service]\nEnvironment=MESA_LOADER_DRIVER_OVERRIDE=kmsro XWAYLAND_NO_GLAMOR=1\n')
        # X11 programs under Xwayland run without glamor on every system (2026-11-09, Fedora/Plasma).
        self.assertEqual(self.read('etc/environment.d/90-a22x-xwayland.conf'), 'XWAYLAND_NO_GLAMOR=1\n')
        self.assertIn('XWAYLAND_NO_GLAMOR=1', self.read('usr/bin/a22x-session'))
        self.assertIn('XWAYLAND_NO_GLAMOR=1', self.read('usr/local/libexec/a22x-greeter'))
        self.assertIn('[Xwayland]\nScale=2.75\n', self.read('etc/xdg/kwinrc'))
        self.assertFalse((self.root / 'etc/environment.d/90-a22x-gpu.conf').exists())
        self.session('phosh plasma', 'phrog')
        self.assertEqual(self.read('etc/xdg/kwinrc').count('[Xwayland]'), 1)

    def test_one_shell_has_no_switch_launcher(self):
        self.session('phosh', 'auto')
        self.assertFalse(list((self.root / 'usr/local/share/applications').glob('a22x-switch-*'))
                         if (self.root / 'usr/local/share/applications').exists() else [])

    def test_phrog_lists_only_the_shells_recovery_set_up(self):
        self.stub('phrog')
        self.session('phosh plasma', 'phrog')
        config = self.read('etc/greetd/config.toml')
        self.assertNotIn('initial_session', config)
        self.assertIn('[default_session]\ncommand = "/usr/local/libexec/a22x-greeter"\nuser = "greeter"', config)
        greeter = self.read('usr/local/libexec/a22x-greeter')
        self.assertIn('/usr/share/a22x/sessions/*.desktop', greeter)
        self.assertIn('wayland-sessions|xsessions', greeter)
        self.assertIn('gnome-session --session=phrog', self.read('usr/local/libexec/a22x-greeter-session'))
        self.assertEqual(self.read('etc/phrog/phoc.ini'), '[output:DSI-1]\nscale=2.75\n')
        self.assertEqual(sorted(p.name for p in (self.root / 'usr/share/a22x/sessions').iterdir()),
                         ['a22x-phosh.desktop', 'a22x-plasma.desktop'])
        self.assertIn('DesktopNames=KDE;', self.read('usr/share/a22x/sessions/a22x-plasma.desktop'))
        self.assertIn('Exec=/usr/bin/a22x-switch-shell\n', self.read('usr/local/share/applications/a22x-switch-shell.desktop'))
        self.assertIn('usermod -aG video greeter', self.calls())
        self.assertEqual(oct((self.root / 'var/lib/a22x-greeter').stat().st_mode & 0o777), '0o700')

    def greeter_uid(self):
        for line in (self.root / 'etc/passwd').read_text().splitlines():
            if line.startswith('greeter:'):
                return line.split(':')[2]
        raise AssertionError('the fixture has no greeter account')

    def test_old_phrog_sees_the_shells_through_its_service_manager(self):
        # Phrog 0.46 (Debian 13) has the two folders built in and ignores XDG_DATA_DIRS. gnome-session
        # starts it from the greeter account's service manager, so the view is set there (2026-11-09).
        uid = self.greeter_uid()
        self.write('etc/systemd/system/greetd.service.d/90-a22x-sessions.conf', 'from image 96\n')
        self.stub('phrog', '#!/bin/sh\n# sessions: /usr/share/wayland-sessions/*.desktop\nexit 0\n')
        self.session('phosh plasma', 'phrog')
        dropin = self.read('etc/systemd/system/user@%s.service.d/90-a22x-sessions.conf' % uid)
        self.assertEqual(dropin, '[Service]\nBindReadOnlyPaths=/usr/share/a22x/sessions:/usr/share/wayland-sessions\n'
                                 'TemporaryFileSystem=/usr/share/xsessions:ro\n')
        self.assertFalse((self.root / 'etc/systemd/system/greetd.service.d').exists())
        self.assertTrue((self.root / 'usr/share/xsessions').is_dir())
        self.assertIn('say: old Phrog', self.calls())

    def test_new_phrog_leaves_the_greeter_account_as_it_is(self):
        uid = self.greeter_uid()
        self.write('etc/systemd/system/user@%s.service.d/90-a22x-sessions.conf' % uid, 'left from an older Phrog\n')
        self.stub('phrog')
        self.session('phosh plasma', 'phrog')
        self.assertFalse((self.root / ('etc/systemd/system/user@%s.service.d' % uid)).exists())
        self.assertNotIn('old Phrog', self.calls())

    def test_phrog_defaults_to_the_main_shell_and_keeps_its_settings(self):
        self.stub('phrog')
        self.stub('glib-compile-schemas')
        self.write('usr/local/share/glib-2.0/schemas/mobi.phosh.phrog.gschema.xml', '<schemalist/>')
        self.session('plasma phosh', 'phrog')
        override = self.read('usr/local/share/glib-2.0/schemas/90_a22x-phrog.gschema.override')
        self.assertEqual(override, "[mobi.phosh.phrog]\nlast-session='a22x-plasma'\nlast-user='user'\n")
        self.assertIn('glib-compile-schemas /usr/local/share/glib-2.0/schemas', self.calls())
        self.assertFalse((self.root / 'usr/share/glib-2.0/schemas/90_a22x-phrog.gschema.override').exists())
        # The settings service must see the greeter's home before gsettings starts it.
        session = self.read('usr/local/libexec/a22x-greeter-session')
        self.assertLess(session.index('dbus-update-activation-environment'), session.index('gsettings set'))

    def test_phosh_starts_unlocked_only_after_a_login_screen(self):
        self.session('phosh', 'auto')
        self.write('usr/bin/phosh-session', '#!/bin/sh\necho "gdm=[$GDMSESSION]"\n', 0o755)
        run = lambda env: self.run_sh(env + ' HOME=/home/user sh /usr/bin/a22x-session').strip()
        self.assertEqual(run(''), 'gdm=[]')
        self.assertEqual(run('GDMSESSION=a22x-phosh'), 'gdm=[phosh]')
        self.assertEqual(run('A22X_SIGNED_IN=1'), 'gdm=[phosh]')

    def test_language_and_clock_of_the_recovery(self):
        self.write('etc/locale.gen', '#en_US.UTF-8 UTF-8\n#uk_UA.UTF-8 UTF-8\n')
        self.stub('locale-gen')
        self.write('usr/bin/locale', '#!/bin/sh\nprintf "C\\nuk_UA.utf8\\n"\n', 0o755)
        self.write('usr/share/zoneinfo/Etc/GMT-1', 'TZif')
        self.run_sh('system_locale; system_timezone', 'A22X_LANGUAGE=uk\nA22X_UTC_OFFSET=60\n')
        self.assertIn('\nuk_UA.UTF-8 UTF-8\n', self.read('etc/locale.gen'))
        self.assertEqual(self.read('etc/locale.conf'), 'LANG=uk_UA.UTF-8\n')
        self.assertEqual(os.readlink(self.root / 'etc/localtime'), '../usr/share/zoneinfo/Etc/GMT-1')
        # A locale that could not be made, an offset of no whole hour: nothing changes.
        (self.root / 'etc/locale.conf').unlink()
        (self.root / 'etc/localtime').unlink()
        self.write('usr/bin/locale', '#!/bin/sh\necho C\n', 0o755)
        self.run_sh('system_locale; system_timezone', 'A22X_LANGUAGE=uk\nA22X_UTC_OFFSET=330\n')
        self.assertFalse((self.root / 'etc/locale.conf').exists())
        self.assertFalse(os.path.lexists(self.root / 'etc/localtime'))
        self.assertIn('the uk_UA locale is not available', self.calls())

    def test_language_and_city_picked_in_the_installer(self):
        self.write('etc/locale.gen', '#ru_RU.UTF-8 UTF-8\n#uk_UA.UTF-8 UTF-8\n')
        self.stub('locale-gen')
        self.write('usr/bin/locale', '#!/bin/sh\nprintf "C\\nru_RU.utf8\\n"\n', 0o755)
        self.write('usr/share/zoneinfo/Europe/Kyiv', 'TZif')
        self.write('usr/share/zoneinfo/Etc/GMT-1', 'TZif')
        self.run_sh('system_locale; system_timezone',
                    'A22X_LANGUAGE=uk\nA22X_LOCALE=ru_RU\nA22X_UTC_OFFSET=60\nA22X_TIMEZONE=Europe/Kyiv\n')
        self.assertEqual(self.read('etc/locale.conf'), 'LANG=ru_RU.UTF-8\n')
        self.assertEqual(os.readlink(self.root / 'etc/localtime'), '../usr/share/zoneinfo/Europe/Kyiv')
        # A zone this system lacks, or a path out of the tz data: the recovery's offset instead.
        for zone in ('Europe/Atlantis', '../../etc/passwd'):
            self.run_sh('system_timezone', 'A22X_UTC_OFFSET=60\nA22X_TIMEZONE=%s\n' % zone)
            self.assertEqual(os.readlink(self.root / 'etc/localtime'), '../usr/share/zoneinfo/Etc/GMT-1')
        self.assertIn('time zone Europe/Atlantis is unknown here', self.calls())
        out = self.run_sh('locale_packages dnf; locale_packages apt', 'A22X_LOCALE=pt_BR\n')
        self.assertEqual(out.split(), ['glibc-langpack-pt', 'locales'])

    def test_phrog_greeter_builds_its_own_session_list(self):
        """The greeter wrapper itself, run as greetd would start it, up to phoc."""
        self.stub('phrog')
        self.session('phosh plasma', 'phrog')
        self.write('usr/bin/phoc', '#!/bin/sh\necho "phoc $*"\necho "XDG_DATA_DIRS=$XDG_DATA_DIRS"\n', 0o755)
        self.write('usr/share/applications/x.desktop', '')
        self.write('usr/local/share/applications/y.desktop', '')
        out = self.run_sh('mkdir -p /run/user/964\nGREETD_SOCK=/run/greetd.sock XDG_RUNTIME_DIR=/run/user/964 '
                          'sh /usr/local/libexec/a22x-greeter')
        self.assertIn('phoc -S -C /etc/phrog/phoc.ini -E /usr/local/libexec/a22x-greeter-session', out)
        run = self.root / 'run/user/964/a22x-greeter'
        self.assertEqual(sorted(p.name for p in (run / 'sessions/wayland-sessions').iterdir()),
                         ['a22x-phosh.desktop', 'a22x-plasma.desktop'])
        self.assertFalse((run / 'usr/wayland-sessions').exists())
        self.assertFalse((run / 'usr/xsessions').exists())
        self.assertTrue((run / 'usr/applications').is_symlink() and (run / 'local/applications').is_symlink())

    def test_phrog_missing_falls_back_to_automatic_sign_in(self):
        self.session('phosh', 'phrog')
        self.assertIn('initial_session', self.read('etc/greetd/config.toml'))
        self.assertIn('say: the Phrog login screen is not available', self.calls())

    def test_text_login_runs_the_switcher(self):
        self.stub('tuigreet')
        self.write('etc/pam.d/greetd', '#%PAM-1.0\n@include login\n-session optional pam_gnome_keyring.so auto_start\n')
        self.write('etc/pam.d/greetd-greeter', '#%PAM-1.0\n@include login\n')
        self.write('etc/default/console-setup', 'CHARMAP="UTF-8"\nFONTFACE="Fixed"\nFONTSIZE="8x16"\n')
        self.session('phosh sxmo', 'text')
        # greetd's sessions are Wayland ones from the start: pam_env runs before pam_systemd
        # (GNOME Shell refused a "tty" session, Ubuntu 24.04, 2026-11-09); only once.
        self.assertEqual(self.read('etc/pam.d/greetd').splitlines()[:3],
                         ['#%PAM-1.0', 'session optional pam_env.so readenv=0 user_readenv=0 conffile=/etc/security/a22x-greetd-env.conf',
                          '@include login'])
        self.assertEqual(self.read('etc/security/a22x-greetd-env.conf'), 'XDG_SESSION_TYPE DEFAULT=wayland\n')
        # The default session, the shell itself with automatic sign-in, opens with greetd-greeter
        # (GNOME after "Switch to GNOME" found a "tty" session, Ubuntu 24.04, 2026-11-10).
        self.assertEqual(self.read('etc/pam.d/greetd-greeter').splitlines()[:2],
                         ['#%PAM-1.0', 'session optional pam_env.so readenv=0 user_readenv=0 conffile=/etc/security/a22x-greetd-env.conf'])
        self.assertIn('command = "tuigreet --cmd \'env A22X_SIGNED_IN=1 /usr/bin/a22x-shell\' --remember --asterisks --time"\nuser = "greeter"',
                      self.read('etc/greetd/config.toml'))
        self.assertTrue((self.root / 'usr/local/share/applications/a22x-switch-sxmo.desktop').exists())
        # The boot splash stops before the text login, which also gets a large console font when
        # one is installed (2026-11-09, Arch: the splash moved over it, and 8x16 was tiny).
        self.assertIn('boot_splash_stop', self.read('etc/systemd/system/greetd.service.d/80-a22x-text-login.conf'))
        self.assertIn('say: text login: no large console font package', self.calls())
        self.write('usr/share/kbd/consolefonts/ter-132b.psf.gz', '')
        self.write('etc/vconsole.conf', 'KEYMAP=us\nFONT=lat2-16\n')
        self.session('phosh sxmo', 'text')
        self.assertEqual(self.read('etc/vconsole.conf'), 'KEYMAP=us\nFONT=ter-132b\n')
        # greetd's own VT gets the font too (Debian's console-setup only covers tty1-6).
        self.stub('setfont')
        self.session('phosh sxmo', 'text')
        self.assertIn('ExecStartPre=-/usr/bin/setfont -C /dev/tty1 /usr/share/kbd/consolefonts/ter-132b.psf.gz',
                      self.read('etc/systemd/system/greetd.service.d/80-a22x-text-login.conf'))
        self.assertEqual(self.read('etc/pam.d/greetd').count('a22x-greetd-env.conf'), 1)
        self.assertEqual(self.read('etc/pam.d/greetd-greeter').count('a22x-greetd-env.conf'), 1)
        # Debian and Ubuntu read the font from console-setup.
        self.assertIn('FONTFACE="Terminus"\nFONTSIZE="16x32"\n', self.read('etc/default/console-setup'))

    def test_sddm_uses_kwin_and_only_these_shells(self):
        # Debian's unit, and gdm3 (pulled in by GNOME) installed last (Ubuntu 26.04, 2026-11-09).
        self.write('usr/lib/systemd/system/sddm.service',
                   "[Service]\nExecStartPre=/bin/sh -c '[ \"$(cat /etc/X11/default-display-manager 2>/dev/null)\" = \"/usr/bin/sddm\" ]'\n"
                   'ExecStart=/usr/bin/sddm\n')
        self.write('etc/X11/default-display-manager', '/usr/sbin/gdm3\n')
        self.write('usr/share/sddm/themes/breeze/metadata.desktop', '[SddmGreeterTheme]\n')
        self.stub('kwin_wayland')
        self.session('plasma phosh', 'sddm')
        self.assertEqual(self.read('etc/X11/default-display-manager'), '/usr/bin/sddm\n')
        # Breeze instead of SDDM's built-in desktop screen, and the main shell preselected.
        self.assertIn('[Theme]\nCurrent=breeze\n', self.read('etc/sddm.conf.d/10-a22x.conf'))
        self.assertEqual(self.read('var/lib/sddm/state.conf'),
                         '[Last]\nUser=user\nSession=/usr/local/share/wayland-sessions/a22x-plasma.desktop\n')
        conf = self.read('etc/sddm.conf.d/10-a22x.conf')
        self.assertIn('CompositorCommand=kwin_wayland --drm', conf)
        self.assertIn('SessionDir=/usr/local/share/wayland-sessions', conf)
        # KWin scales the panel itself (2.7): Qt adds 0.8 for a 2.2 login screen.
        self.assertIn('QT_SCALE_FACTOR=0.8,', conf)
        self.assertEqual(sorted(p.name for p in (self.root / 'usr/local/share/wayland-sessions').iterdir()),
                         ['a22x-phosh.desktop', 'a22x-plasma.desktop'])
        self.assertIn('Session=a22x-plasma', self.read('var/lib/AccountsService/users/user'))
        self.assertIn('systemctl enable --force sddm.service', self.calls())
        self.assertIn('systemctl disable greetd.service', self.calls())
        # A Wayland login screen and no X11 shell: Xorg is left as the distribution set it.
        self.assertFalse((self.root / 'etc/X11/xorg.conf.d/20-a22x-display.conf').exists())

    def test_sddm_without_a_compositor_points_xorg_at_the_panel(self):
        self.write('usr/lib/systemd/system/sddm.service', '')
        self.session('phosh', 'sddm')
        conf = self.read('etc/sddm.conf.d/10-a22x.conf')
        self.assertNotIn('DisplayServer=wayland', conf)
        self.assertNotIn('[Theme]', conf)
        self.assertIn('no Breeze theme', self.calls())
        self.assertIn('Option "PrimaryGPU" "yes"', self.read('etc/X11/xorg.conf.d/20-a22x-display.conf'))

    def test_gdm_hides_the_distribution_sessions(self):
        self.write('usr/lib/systemd/system/gdm.service', '')
        self.write('etc/X11/default-display-manager', '/usr/bin/sddm\n')
        self.session('gnome xfce', 'gdm')
        self.assertIn('Hidden=true', self.read('usr/local/share/wayland-sessions/phosh.desktop'))
        self.assertIn('Hidden=true', self.read('usr/local/share/xsessions/plasmax11.desktop'))
        self.assertIn('Exec=/usr/bin/a22x-xfce-start', self.read('usr/local/share/xsessions/a22x-xfce.desktop'))
        # Xorg takes the MediaTek display device, not the GPU without outputs (2026-11-09).
        self.assertIn('MatchDriver "mediatek|mediatek-drm"', self.read('etc/X11/xorg.conf.d/20-a22x-display.conf'))
        self.assertIn('GDK_SCALE=2 GDK_DPI_SCALE=1.375', self.read('usr/bin/a22x-xfce-start'))
        self.assertIn('startx /usr/bin/a22x-xfce-start', self.read('usr/bin/a22x-xfce-session'))
        self.assertIn('Exec=/usr/bin/a22x-gnome-session', self.read('usr/local/share/wayland-sessions/a22x-gnome.desktop'))
        self.assertEqual(self.read('etc/environment.d/90-a22x-gpu.conf'), 'MESA_LOADER_DRIVER_OVERRIDE=kmsro\n')
        self.assertIn('systemctl enable --force gdm.service', self.calls())
        # A unit that does not check Debian's choice leaves the file alone.
        self.assertEqual(self.read('etc/X11/default-display-manager'), '/usr/bin/sddm\n')

    def test_lightdm_and_a_missing_display_manager(self):
        self.write('usr/lib/systemd/system/lightdm.service', '')
        self.write('usr/share/xgreeters/lightdm-gtk-greeter.desktop', '')
        self.session('xfce', 'lightdm')
        conf = self.read('etc/lightdm/lightdm.conf.d/50-a22x.conf')
        # LightDM reads the session folders only from [LightDM] (Manjaro started Plasma's desktop
        # session from the distribution's folder, 2026-11-09); the seat keys stay under [Seat:*].
        self.assertTrue(conf.startswith('[LightDM]\nsessions-directory=/usr/local/share/wayland-sessions:/usr/local/share/xsessions\n[Seat:*]\n'), conf)
        self.assertIn('user-session=a22x-xfce', conf)
        self.assertIn('greeter-session=lightdm-gtk-greeter', conf)
        self.assertIn('greeter-wrapper=/usr/local/libexec/a22x-lightdm-greeter', conf)
        self.assertIn('GDK_SCALE=2', self.read('usr/local/libexec/a22x-lightdm-greeter'))
        # No on-screen keyboard installed: the login screen is still sized for the panel, and the log says why.
        greeter = self.read('etc/xdg/lightdm/lightdm-gtk-greeter.conf.d/50-a22x.conf')
        self.assertEqual(greeter, '[greeter]\nxft-dpi=132\n')
        self.assertIn('say: LightDM: no on-screen keyboard package', self.calls())
        self.stub('onboard')
        self.session('xfce', 'lightdm')
        greeter = self.read('etc/xdg/lightdm/lightdm-gtk-greeter.conf.d/50-a22x.conf')
        self.assertEqual(greeter, '[greeter]\nxft-dpi=132\nkeyboard=onboard --xid\na11y-states=+keyboard\n')
        (self.root / 'usr/bin/onboard').unlink()
        self.stub('xvkbd')
        self.session('xfce', 'lightdm')
        self.assertIn('keyboard=xvkbd -xdm -compact\n', self.read('etc/xdg/lightdm/lightdm-gtk-greeter.conf.d/50-a22x.conf'))
        (self.root / 'calls').unlink()
        (self.root / 'usr/lib/systemd/system/lightdm.service').unlink()
        self.session('phosh', 'lightdm')
        self.assertIn('say: lightdm is not installed', self.calls())
        self.assertIn('systemctl enable --force greetd.service', self.calls())

    def test_an_image_with_phosh_but_no_greetd_starts_it_through_its_own_unit(self):
        """2026-11-12, Manjaro ARM Phosh on the phone: "unit greetd.service does not exist", Phosh
        stayed disabled and the phone stopped at a text login. The image's own phosh.service is
        enabled instead, for the account chosen in the recovery and through a22x-shell."""
        (self.root / 'usr/lib/systemd/system/greetd.service').unlink()
        self.write('usr/lib/systemd/system/phosh.service', '[Service]\nExecStart=capsh --noamb -- -c "exec /usr/bin/phosh-session"\nUser=1000\n')
        self.session('phosh', 'auto')
        calls = self.calls()
        self.assertIn('systemctl enable --force phosh.service', calls)
        self.assertNotIn('systemctl enable --force greetd.service', calls)
        self.assertIn('say: this image has no greetd', calls)
        self.assertEqual(self.read('etc/systemd/system/phosh.service.d/10-a22x.conf'),
                         '[Service]\nUser=user\nExecStart=\nExecStart=/usr/bin/a22x-shell\n')
        self.assertTrue((self.root / 'usr/bin/a22x-shell').is_file())
        # With greetd there, or with another main shell, nothing changes: greetd signs in.
        (self.root / 'calls').unlink()
        self.write('usr/lib/systemd/system/greetd.service', '[Unit]\n')
        self.session('phosh', 'auto')
        self.assertIn('systemctl enable --force greetd.service', self.calls())

    def test_an_image_with_phosh_says_whether_phoc_must_be_built_for_the_phone(self):
        """2026-11-12, the owner: "why is phoc no longer built by itself for this phone?" An image
        that brings Phosh is asked what the build script would build; nothing is built where the
        image's wlroots is fine, where the build is there already, or without pacman."""
        build = 'opt/a22x-conn/build/a22x-build-phoc.sh'
        needed = lambda: self.run_sh('phone_build_needed && echo yes || echo no').strip().splitlines()[-1]
        self.write(build, '#!/bin/sh\n[ "$1" = --key ] && echo "libwlroots-0.20.so 0.20.2"\n')
        self.assertEqual(needed(), 'no')                       # no pacman, no phoc
        self.stub('pacman')
        self.assertEqual(needed(), 'no')
        self.write('usr/bin/phoc', '#!/bin/sh\n', 0o755)
        self.assertEqual(needed(), 'yes')
        self.write('opt/a22x-phoc/built-for', 'libwlroots-0.20.so 0.20.2\n')
        self.assertEqual(needed(), 'no')                       # built for exactly this already
        self.write('opt/a22x-phoc/built-for', 'libwlroots-0.19.so 0.19.1\n')
        self.assertEqual(needed(), 'yes')                      # built for an older wlroots
        self.write(build, '#!/bin/sh\necho "a22x-build-phoc: /usr/lib/libwlroots-0.20.so already accepts Phosh\'s surfaces; nothing to do"\n')
        self.assertEqual(needed(), 'no')
        self.write(build, '#!/bin/sh\nexit 0\n')
        self.assertEqual(needed(), 'no')

    def test_the_gpu_driver_is_named_as_the_installed_mesa_names_it(self):
        """2026-11-12, Manjaro ARM's image (Mesa 24.0.2): asked for "kmsro", Mesa found no driver,
        phoc drew in software and Phosh's buttons answered late. That Mesa keeps kmsro under the
        display driver's name; a newer one knows "kmsro"."""
        self.session('phosh', 'auto')
        tool = self.read('usr/bin/a22x-gpu-driver')
        self.assertIn('mediatek_dri.so', tool)
        self.assertEqual(self.run_sh('sh /usr/bin/a22x-gpu-driver; mesa_driver').split(), ['kmsro', 'kmsro'])
        self.assertIn('MESA_LOADER_DRIVER_OVERRIDE=$(/usr/bin/a22x-gpu-driver', self.read('usr/bin/a22x-session'))
        self.write('usr/lib/dri/mediatek_dri.so', '')           # Mesa up to 24.1
        self.write('usr/lib/dri/panfrost_dri.so', '')
        self.assertEqual(self.run_sh('sh /usr/bin/a22x-gpu-driver; mesa_driver').split(), ['mediatek', 'mediatek'])
        self.session('plasma', 'sddm')
        self.assertEqual(self.read('etc/systemd/user/plasma-kwin_wayland.service.d/50-a22x-gpu.conf'),
                         '[Service]\nEnvironment=MESA_LOADER_DRIVER_OVERRIDE=mediatek XWAYLAND_NO_GLAMOR=1\n')
        self.write('usr/lib/dri/kmsro_dri.so', '')              # a Mesa that has both names
        self.assertEqual(self.run_sh('sh /usr/bin/a22x-gpu-driver; mesa_driver').split(), ['kmsro', 'kmsro'])

    def test_phosh_session_uses_the_fixed_phoc_when_built(self):
        self.session('phosh', 'auto')
        self.write('opt/a22x-phoc/bin/phoc', '#!/bin/sh\n', 0o755)
        out = self.run_sh('HOME=/home/user sh /usr/bin/a22x-session')
        self.assertEqual(out.strip(), 'phosh via /opt/a22x-phoc/bin/phoc')
        (self.root / 'opt/a22x-phoc/bin/phoc').unlink()
        self.write('usr/bin/phoc', '#!/bin/sh\n', 0o755)
        out = self.run_sh('HOME=/home/user sh /usr/bin/a22x-session')
        self.assertEqual(out.strip(), 'phosh via /usr/bin/phoc')

    def test_sxmo_session_stops_its_helpers_when_sway_ends(self):
        # 2026-11-09 (Mobian): after Sway ended, Sxmo's services kept running under the next login.
        self.session('phosh sxmo', 'auto')
        self.write('usr/bin/sxmo_winit.sh', '#!/bin/sh\necho "winit $SXMO_SWAY_SCALE $XDG_CURRENT_DESKTOP"\nexit 3\n', 0o755)
        self.stub('systemctl')
        out = self.run_sh('sh /usr/bin/a22x-sxmo-session; echo "status $?"')
        self.assertIn('winit 2.75 sway', out)
        self.assertIn('status 3', out)
        self.assertIn('systemctl --user stop sxmo_*.service', self.calls())

    def test_switcher_picks_the_chosen_shell_and_gives_up_a_broken_one(self):
        self.session('phosh plasma', 'auto')
        self.write('usr/bin/a22x-session', '#!/bin/sh\necho started phosh\n', 0o755)
        self.write('usr/bin/a22x-plasma-session', '#!/bin/sh\necho started plasma\n', 0o755)
        start = 'HOME=/home/user sh /usr/bin/a22x-shell'
        self.assertEqual(self.run_sh(start).strip(), 'started phosh')
        self.run_sh('HOME=/home/user XDG_SESSION_ID=3 sh /usr/bin/a22x-switch-shell plasma')
        self.assertIn('loginctl terminate-session 3', self.calls())
        self.assertEqual(self.read('home/user/.config/a22x/shell'), 'plasma\n')
        self.assertEqual(self.run_sh(start).strip(), 'started plasma')
        self.assertEqual(self.run_sh(start).strip(), 'started plasma')
        # A third start within seconds: Plasma died twice at once, so the main shell comes back.
        self.assertEqual(self.run_sh(start).strip(), 'started phosh')
        self.assertFalse((self.root / 'home/user/.config/a22x/shell').exists())
        out = self.chroot('/bin/sh', '/usr/bin/a22x-switch-shell', 'nope')
        self.assertEqual(out.returncode, 2)

    def test_only_the_running_shell_hides_its_launcher(self):
        """Phosh calls itself Phosh:GNOME, so NotShowIn cannot tell it from GNOME (2026-11-10)."""
        self.session('phosh plasma', 'auto')
        self.write('usr/bin/a22x-session', '#!/bin/sh\necho started phosh\n', 0o755)
        self.write('usr/bin/a22x-plasma-session', '#!/bin/sh\necho started plasma\n', 0o755)
        own = self.root / 'home/user/.local/share/applications'
        self.write('home/user/.local/share/applications/a22x-switch-mine.desktop', '[Desktop Entry]\nName=Mine\n')
        start = 'HOME=/home/user sh /usr/bin/a22x-shell'
        self.assertEqual(self.run_sh(start).strip(), 'started phosh')
        self.assertIn('Hidden=true', (own / 'a22x-switch-phosh.desktop').read_text())
        self.assertFalse((own / 'a22x-switch-plasma.desktop').exists())
        # Asking for the running shell signs nobody out.
        self.run_sh('HOME=/home/user XDG_SESSION_ID=3 sh /usr/bin/a22x-switch-shell phosh')
        self.assertNotIn('terminate-session', self.calls())
        self.run_sh('HOME=/home/user XDG_SESSION_ID=3 sh /usr/bin/a22x-switch-shell plasma')
        self.assertEqual(self.run_sh(start).strip(), 'started plasma')
        self.assertIn('Hidden=true', (own / 'a22x-switch-plasma.desktop').read_text())
        self.assertFalse((own / 'a22x-switch-phosh.desktop').exists())
        self.assertEqual((own / 'a22x-switch-mine.desktop').read_text(), '[Desktop Entry]\nName=Mine\n')  # not ours

    def test_builds_are_kept_current_after_updates(self):
        body = 'build_for_phone'
        self.stub('pacman')
        self.write('usr/bin/phoc', '#!/bin/sh\n', 0o755)
        # The scripts stand in for the builds: one makes the phoc launcher, as the real one does.
        self.write('opt/a22x-conn/build/a22x-build-phoc.sh',
                   'mkdir -p /opt/a22x-phoc/bin && printf "#!/bin/sh\\n" > /opt/a22x-phoc/bin/phoc && chmod 755 /opt/a22x-phoc/bin/phoc\n')
        self.write('opt/a22x-conn/build/a22x-build-phrog.sh', 'exit 1\n')
        self.run_sh(body, 'SHELLS="phosh"\nGREETER=auto\n')
        self.assertIn("say: building phoc's wlroots fix", self.calls())
        self.assertNotIn('Phrog', self.calls())
        self.assertIn('ExecStart=/usr/local/sbin/a22x-rebuild', self.read('etc/systemd/system/a22x-rebuild.service'))
        self.assertIn('ConditionACPower=true', self.read('etc/systemd/system/a22x-rebuild.service'))
        self.assertIn('Target = wlroots*', self.read('etc/pacman.d/hooks/90-a22x-rebuild.hook'))
        self.assertIn('systemctl enable a22x-rebuild.timer', self.calls())
        # Phrog that could not be built is said, and nothing else stops.
        (self.root / 'calls').unlink()
        self.run_sh(body, 'SHELLS="plasma"\nGREETER=phrog\n')
        self.assertIn('say: Phrog was not built', self.calls())

    def test_a_kept_build_is_used_when_the_versions_match(self):
        # 2026-11-09: a build made once is kept with its versions and used instead of building again.
        import shutil
        self.stub('pacman')
        self.write('usr/bin/phoc', '#!/bin/sh\n', 0o755)
        key = 'libwlroots-0.20.so 0.20.2'
        script = ('[ "$1" = --key ] && { echo "%s"; exit 0; }\n'
                  'if [ "$(cat /opt/a22x-phoc/built-for 2>/dev/null)" = "%s" ]; then echo already >> /calls; exit 0; fi\n'
                  'echo built >> /calls\nmkdir -p /opt/a22x-phoc/bin\nprintf "#!/bin/sh\\n" > /opt/a22x-phoc/bin/phoc\n'
                  'echo "%s" > /opt/a22x-phoc/built-for\n') % (key, key, key)
        self.write('opt/a22x-conn/build/a22x-build-phoc.sh', script)
        env = 'SHELLS="phosh"\nGREETER=auto\n'
        self.run_sh('build_for_phone', env)
        self.assertIn('built', self.calls().splitlines())
        self.assertEqual(self.read('var/cache/a22x-build/phoc.key'), key + '\n')
        # A new system: the kept build is put in place and nothing is built.
        shutil.rmtree(self.root / 'opt/a22x-phoc')
        (self.root / 'calls').unlink()
        self.run_sh('build_for_phone', env)
        calls = self.calls()
        self.assertIn('say: using the phoc build kept for ' + key, calls)
        self.assertIn('already', calls.splitlines())
        self.assertNotIn('built', calls.splitlines())
        self.assertTrue((self.root / 'opt/a22x-phoc/bin/phoc').exists())
        # Another wlroots: the kept build stays unused and a new one is made.
        shutil.rmtree(self.root / 'opt/a22x-phoc')
        (self.root / 'calls').unlink()
        self.write('var/cache/a22x-build/phoc.key', 'libwlroots-0.19.so 0.19.1\n')
        self.run_sh('build_for_phone', env)
        self.assertIn('built', self.calls().splitlines())
        self.assertEqual(self.read('var/cache/a22x-build/phoc.key'), key + '\n')

    def test_nothing_is_built_on_other_distributions(self):
        self.run_sh('build_for_phone', 'SHELLS="phosh"\nGREETER=phrog\n')
        self.assertNotIn('building', self.calls())
        self.assertFalse((self.root / 'etc/systemd/system/a22x-rebuild.service').exists())

    def test_package_lists_follow_shells_and_sign_in(self):
        body = ('manager() { echo pacman; }\npacman_keys() { :; }\n'
                'pacman_upgrade() { echo "upgrade $*" >> /calls; }\n'
                'available() { [ "$2" != phrog ]; }\ninstall_one() { echo "extra $2" >> /calls; }\npackages')
        self.run_sh(body, 'SHELLS="phosh plasma"\nGREETER=phrog\n')
        upgrade = next(l for l in self.calls().splitlines() if l.startswith('upgrade '))
        for name in ('phosh', 'phoc', 'plasma-mobile', 'greetd', 'stevia', 'sudo'):
            self.assertIn(' %s ' % name, upgrade + ' ')
        self.assertNotIn('sddm', upgrade)
        self.assertIn('say: no phrog package', self.calls())
        (self.root / 'calls').unlink()
        self.run_sh(body.replace('echo pacman', 'echo pacman'), 'SHELLS="xfce"\nGREETER=sddm\n')
        calls = self.calls()
        self.assertIn(' sddm', calls)
        self.assertIn('extra weston', calls)
        self.assertIn('xorg-xinit', calls)


if __name__ == '__main__':
    unittest.main()
