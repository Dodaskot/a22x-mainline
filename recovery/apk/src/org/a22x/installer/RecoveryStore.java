// SPDX-License-Identifier: MIT
package org.a22x.installer;

import android.content.Context;

import java.io.File;
import java.io.FileOutputStream;
import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.TimeZone;

/** What this app writes for the recovery, all through root: the hand-over file and setup scripts
 *  in the cache partition, and recovery or BOOT images into their partitions. */
final class RecoveryStore {
    private RecoveryStore() {
    }

    private static File privateFile(Context context, String name, String text) throws IOException {
        File file = new File(context.getFilesDir(), name);
        try (FileOutputStream out = new FileOutputStream(file)) {
            out.write(text.getBytes(StandardCharsets.UTF_8));
        }
        return file;
    }

    /** The hand-over the recovery has not taken yet, or null. */
    static String waitingHandoff() {
        Root.Result r = Root.run(Scripts.CACHE_OPEN
                + "echo A22X:BEGIN; cat \"$D/handoff.json\" 2>/dev/null || true; echo; echo A22X:END" + Scripts.CACHE_CLOSE, 60);
        return r.ok() ? r.block() : null;
    }

    /** Write the hand-over with this phone's language, zone and time, plus a new network when
     *  SSID is given. The file exists in this app's private folder only while it is copied. */
    static Root.Result sendHandoff(Context context, String ssid, String password) throws IOException {
        String record = Handoff.merge(waitingHandoff(), Locale.getDefault().getLanguage(), TimeZone.getDefault().getID(),
                System.currentTimeMillis() / 1000, ssid, password);
        File file = privateFile(context, "handoff.json", record);
        try {
            return Root.run(Scripts.CACHE_OPEN
                    + "cat " + Root.quote(file.getAbsolutePath()) + " > \"$D/handoff.json.part\" && mv \"$D/handoff.json.part\" \"$D/handoff.json\""
                    + " && echo A22X:OK" + Scripts.CACHE_CLOSE, 60);
        } finally {
            //noinspection ResultOfMethodCallIgnored
            file.delete();
        }
    }

    static Root.Result saveScript(Context context, String name, String text) throws IOException {
        if (!Handoff.scriptNameAllowed(name)) throw new IllegalArgumentException("name");
        if (text.getBytes(StandardCharsets.UTF_8).length > (1 << 20)) throw new IllegalArgumentException("size");
        File file = privateFile(context, "script.part", text);
        try {
            return Root.run(Scripts.CACHE_OPEN
                    + "mkdir -p \"$D/scripts\" && cat " + Root.quote(file.getAbsolutePath()) + " > \"$D/scripts/" + name + "\""
                    + " && echo A22X:OK" + Scripts.CACHE_CLOSE, 60);
        } finally {
            //noinspection ResultOfMethodCallIgnored
            file.delete();
        }
    }

    static List<String> scripts() {
        Root.Result r = Root.run(Scripts.CACHE_OPEN + "echo A22X:BEGIN; ls \"$D/scripts\" 2>/dev/null || true; echo A22X:END" + Scripts.CACHE_CLOSE, 60);
        List<String> names = new ArrayList<>();
        String block = r.ok() ? r.block() : null;
        if (block != null) {
            for (String line : block.split("\n")) {
                if (Handoff.scriptNameAllowed(line.trim())) names.add(line.trim());
            }
        }
        return names;
    }

    /** Write IMAGE (already checked against SHA256) to partition LABEL: the partition must be at
     *  least as large; its old content is saved first in BACKUP_DIR; afterwards the partition is
     *  read back and must equal the image. Prints A22X:OK <saved copy> or A22X:ERROR <reason>. */
    static Root.Result flash(String label, File image, long size, String sha256, String backupDir) {
        return Root.run(Scripts.flash(label, image.getAbsolutePath(), size, sha256, backupDir, Scripts.partitionSize(label),
                Scripts.PLATFORM, "recovery", "boot"), 600);
    }

    /** The Linux BOOT handed to the recovery: kept on cache, not written to the BOOT partition. */
    static Root.Result keepLinuxBoot(File image, long size, String sha256, String release) {
        return Root.run(Scripts.keepLinuxBoot(image.getAbsolutePath(), size, sha256, release), 300);
    }

    /** The installation prepared on the Plan card (2026-11-10): written beside the hand-over. */
    static Root.Result savePlan(Context context, String json) throws IOException {
        File file = privateFile(context, "plan.part", json);
        try {
            return Root.run(Scripts.CACHE_OPEN
                    + "cat " + Root.quote(file.getAbsolutePath()) + " > \"$D/" + Plan.FILE + ".part\" && mv \"$D/" + Plan.FILE + ".part\" \"$D/" + Plan.FILE + "\""
                    + " && echo A22X:OK" + Scripts.CACHE_CLOSE, 60);
        } finally {
            //noinspection ResultOfMethodCallIgnored
            file.delete();
        }
    }

    static Root.Result removePlan() {
        return Root.run(Scripts.CACHE_OPEN + "rm -f \"$D/" + Plan.FILE + "\" && echo A22X:OK" + Scripts.CACHE_CLOSE, 60);
    }

    /** Whether a plan waits for the recovery. */
    static boolean planWaiting() {
        Root.Result r = Root.run(Scripts.CACHE_OPEN + "[ -f \"$D/" + Plan.FILE + "\" ] && echo A22X:OK" + Scripts.CACHE_CLOSE, 60);
        return r.marker("OK") != null;
    }

    /** The name of the network this phone is connected to, or null: only its name (2026-11-10).
     *  The password is typed once on the Wi-Fi card; saved passwords are not read. */
    static String currentNetwork() {
        Root.Result r = Root.run("cmd wifi status 2>/dev/null | sed -n 's/.*connected to \"\\(.*\\)\".*/A22X:SSID \\1/p' | head -n 1", 30);
        String name = r.marker("SSID");
        if (name == null || name.isEmpty() || name.equals("<unknown ssid>")) return null;
        return Handoff.networkProblem(name, null) == null ? name : null;
    }

    /** Android's BOOT, the original Magisk patched when there is one, kept on cache (Scripts.KEEP_BOOT_AUTO). */
    static Root.Result keepBootAuto() {
        return Root.run(Scripts.keepBootAuto(), 180);
    }

    static Root.Result restartIntoRecovery() {
        return Root.run("sync; reboot recovery", 60);
    }
}
