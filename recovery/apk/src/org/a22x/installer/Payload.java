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

/** The recovery and BOOT images inside this APK (assets/images.json names them with size,
 *  SHA-256 and version) and their copies in the app's private folder for flashing. */
final class Payload {
    static final class Image {
        final String label;
        final String asset;
        final long size;
        final String sha256;
        final String version;
        /** The kernel release of a Linux BOOT; empty for other images. */
        final String release;

        Image(String label, String asset, long size, String sha256, String version) {
            this(label, asset, size, sha256, version, "");
        }

        Image(String label, String asset, long size, String sha256, String version, String release) {
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
        return new Image(label, (String) item.get("asset"), ((Number) item.get("size")).longValue(),
                (String) item.get("sha256"), (String) item.get("version"), (String) item.get("release"));
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
        try (InputStream in = context.getAssets().open(image.asset); OutputStream out = new FileOutputStream(part)) {
            byte[] buffer = new byte[1 << 16];
            int n;
            while ((n = in.read(buffer)) > 0) out.write(buffer, 0, n);
        }
        if (part.length() != image.size || !sha256(new FileInputStream(part)).equals(image.sha256)) {
            //noinspection ResultOfMethodCallIgnored
            part.delete();
            throw new IOException("the image inside the APK does not match its checksum");
        }
        if (!part.renameTo(file)) throw new IOException("could not keep the image");
        return file;
    }

    static String text(byte[] data) {
        return new String(data, StandardCharsets.UTF_8);
    }
}
