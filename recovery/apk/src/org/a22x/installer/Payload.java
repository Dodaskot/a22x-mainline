// SPDX-License-Identifier: MIT
package org.a22x.installer;

import android.content.Context;

import java.io.ByteArrayOutputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.Map;

/** The recovery and BOOT images this APK installs (assets/images.json names them with size,
 *  SHA-256 and version) and their copies in the app's private folder for flashing. Since
 *  2026-11-12 (the owner: "download BOOT and recovery from my repository, to make the APK
 *  smaller") they are not inside the APK: images.json gives the address of each, the recovery's
 *  release on GitHub and the Linux BOOT of the catalog, and the image is downloaded when it is
 *  needed and checked against the SHA-256 the build wrote. An APK built with the images inside
 *  still works the old way. */
final class Payload {
    static final class Image {
        final String label;
        final String asset;
        final long size;
        final String sha256;
        final String version;
        /** The kernel release of a Linux BOOT; empty for other images. */
        final String release;
        /** Where to download it, when it is not inside the APK. */
        final String url;

        Image(String label, String asset, long size, String sha256, String version) {
            this(label, asset, size, sha256, version, "", null);
        }

        Image(String label, String asset, long size, String sha256, String version, String release, String url) {
            this.url = url;
            this.release = release == null ? "" : release;
            this.label = label;
            this.asset = asset;
            this.size = size;
            this.sha256 = sha256;
            this.version = version;
        }
    }

    private Payload() {
    }

    @SuppressWarnings("unchecked")
    static Image image(Context context, String label) throws IOException {
        Map<String, Object> all = (Map<String, Object>) Json.parse(read(context.getAssets().open("images.json")));
        Map<String, Object> item = (Map<String, Object>) all.get(label);
        if (item == null) throw new IOException("no " + label + " image in this APK");
        String url = (String) item.get("url");
        if (url != null && !url.startsWith("https://github.com/")) throw new IOException("unexpected address");
        return new Image(label, (String) item.get("asset"), ((Number) item.get("size")).longValue(),
                (String) item.get("sha256"), (String) item.get("version"), (String) item.get("release"), url);
    }

    private static String read(InputStream in) throws IOException {
        try (InputStream source = in) {
            ByteArrayOutputStream out = new ByteArrayOutputStream();
            byte[] buffer = new byte[8192];
            int n;
            while ((n = source.read(buffer)) > 0) out.write(buffer, 0, n);
            return out.toString("UTF-8");
        }
    }

    static String sha256(InputStream in) throws IOException {
        try (InputStream source = in) {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            byte[] buffer = new byte[1 << 16];
            int n;
            while ((n = source.read(buffer)) > 0) digest.update(buffer, 0, n);
            StringBuilder hex = new StringBuilder();
            for (byte b : digest.digest()) hex.append(String.format("%02x", b & 0xff));
            return hex.toString();
        } catch (NoSuchAlgorithmException e) {
            throw new IOException(e);
        }
    }

    /** The image as a file of this app, copied from the APK when missing and always checked. */
    static File extract(Context context, Image image) throws IOException {
        File file = new File(context.getFilesDir(), image.label + ".img");
        if (file.isFile() && file.length() == image.size && sha256(new FileInputStream(file)).equals(image.sha256)) {
            return file;
        }
        File part = new File(context.getFilesDir(), image.label + ".img.part");
        try (InputStream in = image.url != null ? open(image.url) : context.getAssets().open(image.asset);
             OutputStream out = new FileOutputStream(part)) {
            byte[] buffer = new byte[1 << 16];
            int n;
            long done = 0;
            while ((n = in.read(buffer)) > 0) {
                done += n;
                if (done > image.size) throw new IOException("the download is larger than the image");
                out.write(buffer, 0, n);
            }
        }
        if (part.length() != image.size || !sha256(new FileInputStream(part)).equals(image.sha256)) {
            //noinspection ResultOfMethodCallIgnored
            part.delete();
            throw new IOException(image.url != null ? "the download does not match its checksum" : "the image inside the APK does not match its checksum");
        }
        if (!part.renameTo(file)) throw new IOException("could not keep the image");
        return file;
    }

    private static InputStream open(String address) throws IOException {
        java.net.HttpURLConnection c = (java.net.HttpURLConnection) new java.net.URL(address).openConnection();
        c.setConnectTimeout(20000);
        c.setReadTimeout(60000);
        c.setRequestProperty("User-Agent", "a22x-installer");
        c.setInstanceFollowRedirects(true);
        if (c.getResponseCode() != 200) throw new IOException("HTTP " + c.getResponseCode() + " for " + address);
        return c.getInputStream();
    }

    static String text(byte[] data) {
        return new String(data, StandardCharsets.UTF_8);
    }
}
