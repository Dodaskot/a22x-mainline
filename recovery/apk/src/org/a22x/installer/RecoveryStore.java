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
        return Root.run(Scripts.flash(label, image.getAbsolutePath(), size, sha256, backupDir, "recovery", "boot"), 600);
    }

    static Root.Result restartIntoRecovery() {
        return Root.run("sync; reboot recovery", 60);
    }
}
