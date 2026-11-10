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

cp "$RECOVERY_IMG" "$work/assets/recovery.img"
cp "$BOOT_IMG" "$work/assets/boot.img"
python3 - "$work/assets" "$RECOVERY_VERSION" "$BOOT_VERSION" <<'P'
import hashlib, json, os, sys
folder, recovery, boot = sys.argv[1:4]
def entry(name, version):
    path = os.path.join(folder, name + '.img')
    with open(path, 'rb') as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    return {'asset': name + '.img', 'size': os.path.getsize(path), 'sha256': digest, 'version': version}
with open(os.path.join(folder, 'images.json'), 'w') as f:
    json.dump({'recovery': entry('recovery', recovery), 'boot': entry('boot', boot)}, f, indent=1)
P

# The rules shared with the recovery are tested on this computer first.
javac -nowarn --release 8 -d "$work/test" "$here"/src/org/a22x/installer/{Json,Handoff,Root,Scripts,Compat}.java "$here"/test/org/a22x/installer/{HandoffTest,ScriptsMain}.java
java -cp "$work/test" org.a22x.installer.HandoffTest

code=$(echo "$RECOVERY_VERSION" | awk -F. '{print $1 * 1000000 + $2 * 1000 + $3}')
"$TOOLS/bin/aapt2" compile --dir "$here/res" -o "$work/res.zip"
"$TOOLS/bin/aapt2" link -o "$work/base.apk" -I "$TOOLS/framework-res.apk" --manifest "$here/AndroidManifest.xml" \
    -A "$work/assets" --min-sdk-version 26 --target-sdk-version 33 --version-code "$code" \
    --version-name "$RECOVERY_VERSION" --java "$work/gen" "$work/res.zip"
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
"$TOOLS/bin/aapt2" dump badging "$out" | head -3
sha256sum "$out"
