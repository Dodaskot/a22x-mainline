#!/bin/bash
# SPDX-License-Identifier: MIT
# Build the A22x Recovery installer APK without the Android SDK (2026-11-10).
#
#   TOOLS         folder with bin/aapt2, android-stubs.jar, framework-res.apk, r8.jar, apksig.jar
#                 (aapt2, r8 and apksig from Google's Maven repository, Apache-2.0; the API stubs
#                 from Maven Central; framework-res.apk from Debian)
#   RECOVERY_IMG  the recovery image to embed, RECOVERY_VERSION its version (also the APK's)
#   BOOT_IMG      the BOOT image to embed, BOOT_VERSION its name
#   KEYSTORE      a PKCS12 signing key, KEYPASS a file with its password; a new key is made
#                 when KEYSTORE does not exist. Keep both private.
#
#   apk/build.sh OUT.apk
set -euo pipefail
here=$(cd "$(dirname "$0")" && pwd)
: "${TOOLS:?}" "${RECOVERY_IMG:?}" "${RECOVERY_VERSION:?}" "${BOOT_IMG:?}" "${BOOT_VERSION:?}" "${KEYSTORE:?}" "${KEYPASS:?}"
out=$1
work=$(mktemp -d "${TMPDIR:-/tmp}/a22x-apk.XXXXXX")
trap 'rm -rf "$work"' EXIT
mkdir -p "$work"/assets "$work"/gen "$work"/classes "$work"/test "$work"/dex "$work"/signer

# 2026-11-12: the images are downloaded by the app (images.json names their addresses and SHA-256),
# so the APK stays small; EMBED_IMAGES=1 puts them inside as before.
cp "$RECOVERY_IMG" "$work/assets/recovery.img"
cp "$BOOT_IMG" "$work/assets/boot.img"
# The recovery's catalog, for the Plan card (2026-11-10): the same images its install pages offer.
cp "$here/../catalog.json" "$work/assets/catalog.json"
python3 - "$work/assets" "$RECOVERY_VERSION" "$BOOT_VERSION" "$here/.." "${EMBED_IMAGES:-0}" <<'P'
import hashlib, json, os, sys
folder, recovery, boot, source, embed = sys.argv[1:6]
sys.path.insert(0, source)
import bootimage
def entry(name, version):
    path = os.path.join(folder, name + '.img')
    with open(path, 'rb') as f:
        data = f.read()
    found = {'asset': name + '.img', 'size': len(data), 'sha256': hashlib.sha256(data).hexdigest(), 'version': version}
    if name == 'boot':
        # The kernel release the recovery looks for when it takes a kept Linux BOOT (2026-11-11).
        found['release'] = bootimage.linux_release(data) or ''
        assert found['release'], 'BOOT_IMG holds no Linux kernel release'
    return found
images = {'recovery': entry('recovery', recovery), 'boot': entry('boot', boot)}
if embed != '1':
    # The recovery from its release on GitHub, the Linux BOOT from the catalog's address: both must
    # be the very images given here, or the build stops. The app checks the SHA-256 again after its
    # download, so a release published with another image only fails there (2026-11-12 review): a
    # release that is already out is compared here; one not out yet must get exactly this image.
    images['recovery']['url'] = 'https://github.com/Dodaskot/mainline-recovery/releases/download/v%s/recovery-a22x-%s.img' % (recovery, recovery)
    import urllib.error, urllib.request
    asset = 'recovery-a22x-%s.img' % recovery
    try:
        request = urllib.request.Request('https://api.github.com/repos/Dodaskot/mainline-recovery/releases/tags/v' + recovery,
                                         headers={'Accept': 'application/vnd.github+json', 'User-Agent': 'a22x-apk-build'})
        with urllib.request.urlopen(request, timeout=20) as answer:
            release = json.load(answer)
        published = [a for a in release.get('assets', []) if a.get('name') == asset]
        assert published, 'release v%s has no %s' % (recovery, asset)
        assert published[0].get('digest') == 'sha256:' + images['recovery']['sha256'], \
            'RECOVERY_IMG is not the %s published in release v%s' % (asset, recovery)
        print('recovery: the published %s is this image' % asset, file=sys.stderr)
    except urllib.error.HTTPError as error:
        if error.code != 404:
            raise
        print('recovery: release v%s is not out yet: publish exactly this image, SHA-256 %s' % (recovery, images['recovery']['sha256']), file=sys.stderr)
    with open(os.path.join(source, 'catalog.json')) as f:
        offer = json.load(f)['linux_boot']
    assert offer['sha256'] == images['boot']['sha256'], 'BOOT_IMG is not the Linux BOOT the catalog names'
    images['boot']['url'] = offer['url']
    for name in ('recovery', 'boot'):
        os.remove(os.path.join(folder, name + '.img'))
        del images[name]['asset']
with open(os.path.join(folder, 'images.json'), 'w') as f:
    json.dump(images, f, indent=1)
P

# The rules shared with the recovery are tested on this computer first.
javac -nowarn --release 8 -d "$work/test" "$here"/src/org/a22x/installer/{Json,Handoff,Root,Scripts,Compat,Plan,Sha512Crypt}.java "$here"/test/org/a22x/installer/{HandoffTest,ScriptsMain}.java
java -cp "$work/test" org.a22x.installer.HandoffTest
javac -nowarn --release 8 -cp "$work/test" -d "$work/test" "$here"/src/org/a22x/installer/{Json,Root,AppUpdate}.java "$here"/test/org/a22x/installer/AppUpdateTest.java
java -cp "$work/test" org.a22x.installer.AppUpdateTest
# The backdrops are the recovery's own: every pixel of BackdropMath against its canvas_backdrop().
cc -O2 -I "$here/../ui" -o "$work/backdrop_ref" "$here/test/backdrop_ref.c" -lm
size=$("$work/backdrop_ref" "$work/backdrop-daylight.raw" "$work/backdrop-aurora.raw")
javac -nowarn --release 8 -d "$work/test" "$here"/src/org/a22x/installer/BackdropMath.java "$here"/test/org/a22x/installer/BackdropTest.java
java -cp "$work/test" org.a22x.installer.BackdropTest "$here/res" "$work/backdrop-daylight.raw" "$work/backdrop-aurora.raw" $size
rm -f "$work"/backdrop-*.raw

# APP_REVISION: a new build of the app around the same recovery (2026-11-10: 0.6.138-r2, the theme
# change in place); the version code grows either way, so it installs over the last one.
revision=${APP_REVISION:-0}
code=$(echo "$RECOVERY_VERSION" | awk -F. -v r="$revision" '{print ($1 * 1000000 + $2 * 1000 + $3) * 10 + r}')
name=$RECOVERY_VERSION
[ "$revision" = 0 ] || name="$RECOVERY_VERSION-r$revision"
"$TOOLS/bin/aapt2" compile --dir "$here/res" -o "$work/res.zip"
"$TOOLS/bin/aapt2" link -o "$work/base.apk" -I "$TOOLS/framework-res.apk" --manifest "$here/AndroidManifest.xml" \
    -A "$work/assets" --min-sdk-version 26 --target-sdk-version 33 --version-code "$code" \
    --version-name "$name" --java "$work/gen" "$work/res.zip"
javac -nowarn --release 8 -cp "$TOOLS/android-stubs.jar" -d "$work/classes" $(find "$here/src" "$work/gen" -name '*.java')
java -cp "$TOOLS/r8.jar" com.android.tools.r8.D8 --release --min-api 26 --lib "$TOOLS/android-stubs.jar" \
    --output "$work/dex" $(find "$work/classes" -name '*.class') 2> "$work/d8.log" || { cat "$work/d8.log"; exit 1; }
python3 - "$work/base.apk" "$work/dex/classes.dex" <<'P'
import sys, zipfile
with zipfile.ZipFile(sys.argv[1], 'a', zipfile.ZIP_DEFLATED) as apk:
    apk.write(sys.argv[2], 'classes.dex')
P

if [ ! -e "$KEYSTORE" ]; then
    [ -s "$KEYPASS" ] || { umask 077; python3 -c 'import secrets; print(secrets.token_urlsafe(24))' > "$KEYPASS"; }
    keytool -genkeypair -keystore "$KEYSTORE" -storetype PKCS12 -alias a22x -keyalg RSA -keysize 3072 \
        -validity 10950 -dname "CN=A22x Recovery installer" -storepass:file "$KEYPASS" -keypass:file "$KEYPASS" 2>/dev/null
    chmod 600 "$KEYSTORE"
fi
javac -nowarn -cp "$TOOLS/apksig.jar" -d "$work/signer" "$here/signer/Sign.java"
java -cp "$TOOLS/apksig.jar:$work/signer" Sign "$KEYSTORE" "$KEYPASS" "$work/base.apk" "$out"
# sed reads everything: head would end aapt2 early (SIGPIPE, exit 141 under pipefail) before the hash.
"$TOOLS/bin/aapt2" dump badging "$out" | sed -n 1,3p
sha256sum "$out"
