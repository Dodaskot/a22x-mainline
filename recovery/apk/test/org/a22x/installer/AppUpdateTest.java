// SPDX-License-Identifier: MIT
package org.a22x.installer;

/** The app's own update (2026-11-12): what GitHub says, and which version is newer. */
public final class AppUpdateTest {
    private static void check(boolean condition, String what) {
        if (!condition) throw new AssertionError(what);
    }

    public static void main(String[] args) {
        check(AppUpdate.newer("0.6.201", "0.6.197"), "newer patch");
        check(AppUpdate.newer("0.6.197-r2", "0.6.197"), "newer revision");
        check(!AppUpdate.newer("0.6.197", "0.6.197-r1"), "older revision");
        check(!AppUpdate.newer("0.6.197", "0.6.197"), "same");
        check(AppUpdate.newer("0.7.0", "0.6.999"), "newer minor");
        check(AppUpdate.newer("1.0.0", "0.6.235") && AppUpdate.newer("v1.0.0", "0.6.206") && !AppUpdate.newer("0.6.235", "1.0.0"), "1.0.0 after 0.6");
        check(!AppUpdate.newer("nightly", "0.6.1"), "no version");
        String reply = "{\"tag_name\":\"v0.6.201\",\"assets\":["
                + "{\"name\":\"recovery-a22x-0.6.201.img\",\"size\":41943040,\"digest\":\"sha256:" + repeat('a') + "\","
                + "\"browser_download_url\":\"https://github.com/Dodaskot/mainline-recovery/releases/download/v0.6.201/recovery-a22x-0.6.201.img\"},"
                + "{\"name\":\"mainline-recovery-installer-0.6.201.apk\",\"size\":63705448,\"digest\":\"sha256:" + repeat('b') + "\","
                + "\"browser_download_url\":\"https://github.com/Dodaskot/mainline-recovery/releases/download/v0.6.201/mainline-recovery-installer-0.6.201.apk\"}]}";
        AppUpdate.Release r = AppUpdate.parse(reply);
        check(r != null && r.version.equals("0.6.201") && r.size == 63705448L && r.sha256.equals(repeat('b')), "the installer asset");
        check(AppUpdate.parse(reply.replace("sha256:" + repeat('b'), "")) == null, "no digest, no update");
        check(AppUpdate.parse(reply.replace("https://github.com/Dodaskot/mainline-recovery/releases/download/v0.6.201/mainline", "http://elsewhere/mainline")) == null,
                "only GitHub's address");
        System.out.println("AppUpdateTest: ok");
    }

    private static String repeat(char c) {
        StringBuilder out = new StringBuilder();
        for (int i = 0; i < 64; i++) out.append(c);
        return out.toString();
    }
}
