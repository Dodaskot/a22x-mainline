// SPDX-License-Identifier: MIT
package org.a22x.installer;

import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.security.MessageDigest;
import java.util.List;
import java.util.Map;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** This app updates itself from the project's releases on GitHub (2026-11-12, the owner: "add the
 *  update of the APK itself, from GitHub, inside the APK"). The newest release is asked for, its
 *  installer APK downloaded, its size and the SHA-256 GitHub states checked, and it is installed
 *  over this one with root (pm install -r): Android refuses an APK signed by another key, so only a
 *  build of the same signer replaces this app. */
final class AppUpdate {
    static final String API = "https://api.github.com/repos/Dodaskot/mainline-recovery/releases/latest";
    static final Pattern ASSET = Pattern.compile("(?:mainline-recovery|a22x-recovery)-installer-([0-9]+)\\.([0-9]+)\\.([0-9]+)(?:-r([0-9]+))?\\.apk");
    static final long LIMIT = 256L << 20;

    static final class Release {
        String version, file, url, sha256;
        long size;
    }

    private AppUpdate() {
    }

    /** "0.6.197", "0.6.197-r2" or "v0.6.197" as numbers: major, minor, patch, revision; null when no version. */
    static long[] key(String text) {
        Matcher m = Pattern.compile("v?([0-9]+)\\.([0-9]+)\\.([0-9]+)(?:-r([0-9]+))?").matcher(text == null ? "" : text.trim());
        if (!m.matches()) return null;
        return new long[]{Long.parseLong(m.group(1)), Long.parseLong(m.group(2)), Long.parseLong(m.group(3)),
                m.group(4) == null ? 0 : Long.parseLong(m.group(4))};
    }

    /** Whether PUBLISHED is a newer version than INSTALLED. */
    static boolean newer(String published, String installed) {
        long[] a = key(published), b = key(installed);
        if (a == null || b == null) return false;
        for (int i = 0; i < 4; i++) if (a[i] != b[i]) return a[i] > b[i];
        return false;
    }

    /** The installer APK of the newest release in the GitHub answer TEXT; null when it has none
     *  with a SHA-256. */
    @SuppressWarnings("unchecked")
    static Release parse(String text) {
        Object root = Json.parse(text);
        if (!(root instanceof Map)) return null;
        Object assets = ((Map<String, Object>) root).get("assets");
        if (!(assets instanceof List)) return null;
        for (Object item : (List<Object>) assets) {
            if (!(item instanceof Map)) continue;
            Map<String, Object> asset = (Map<String, Object>) item;
            String name = String.valueOf(asset.get("name"));
            Matcher m = ASSET.matcher(name);
            String digest = String.valueOf(asset.get("digest"));
            if (!m.matches() || !digest.matches("sha256:[0-9a-f]{64}")) continue;
            Object size = asset.get("size");
            String url = String.valueOf(asset.get("browser_download_url"));
            if (!(size instanceof Long) || !url.startsWith("https://github.com/")) continue;
            Release r = new Release();
            r.file = name;
            r.version = m.group(1) + "." + m.group(2) + "." + m.group(3) + (m.group(4) == null ? "" : "-r" + m.group(4));
            r.url = url;
            r.size = (Long) size;
            r.sha256 = digest.substring(7);
            return r;
        }
        return null;
    }

    private static InputStream open(String address) throws Exception {
        HttpURLConnection c = (HttpURLConnection) new URL(address).openConnection();
        c.setConnectTimeout(20000);
        c.setReadTimeout(60000);
        c.setRequestProperty("User-Agent", "a22x-installer");
        c.setRequestProperty("Accept", "application/vnd.github+json, application/octet-stream");
        c.setInstanceFollowRedirects(true);
        if (c.getResponseCode() != 200) throw new IllegalStateException("HTTP " + c.getResponseCode());
        return c.getInputStream();
    }

    static Release latest() throws Exception {
        try (InputStream in = open(API)) {
            java.io.ByteArrayOutputStream data = new java.io.ByteArrayOutputStream();
            byte[] buffer = new byte[1 << 14];
            int n;
            while ((n = in.read(buffer)) > 0) {
                if (data.size() > (4 << 20)) throw new IllegalStateException("too large");
                data.write(buffer, 0, n);
            }
            return parse(data.toString("UTF-8"));
        }
    }

    interface Progress {
        void step(long done, long total);
    }

    /** The APK in this app's cache folder, its size and SHA-256 the release's. */
    static File download(File cacheDir, Release release, Progress progress) throws Exception {
        if (release.size <= 0 || release.size > LIMIT) throw new IllegalStateException("size");
        File out = new File(cacheDir, "update.apk");
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        long done = 0;
        try (InputStream in = open(release.url); OutputStream file = new FileOutputStream(out)) {
            byte[] buffer = new byte[1 << 16];
            int n;
            while ((n = in.read(buffer)) > 0) {
                done += n;
                if (done > release.size) throw new IllegalStateException("too large");
                digest.update(buffer, 0, n);
                file.write(buffer, 0, n);
                progress.step(done, release.size);
            }
        }
        StringBuilder hex = new StringBuilder();
        for (byte b : digest.digest()) hex.append(String.format("%02x", b));
        if (done != release.size || !hex.toString().equals(release.sha256)) {
            //noinspection ResultOfMethodCallIgnored
            out.delete();
            throw new IllegalStateException("checksum");
        }
        return out;
    }

    /** Installed over this app with root; pm reads it from a folder of its own. */
    static Root.Result install(File apk, String sha256) {
        if (!sha256.matches("[0-9a-f]{64}")) throw new IllegalArgumentException("sha256");
        return Root.run("T=/data/local/tmp/a22x-installer-update.apk; cat " + Root.quote(apk.getAbsolutePath()) + " > \"$T\" || { echo A22X:ERROR COPY; exit 1; }\n"
                + "[ \"$(sha256sum \"$T\" | cut -d' ' -f1)\" = " + sha256 + " ] || { rm -f \"$T\"; echo A22X:ERROR CHECKSUM; exit 1; }\n"
                + "chmod 644 \"$T\"; out=$(pm install -r \"$T\" 2>&1); rc=$?; rm -f \"$T\"\n"
                + "[ $rc = 0 ] && echo A22X:OK || echo \"A22X:ERROR $out\"; exit $rc\n", 300);
    }
}
