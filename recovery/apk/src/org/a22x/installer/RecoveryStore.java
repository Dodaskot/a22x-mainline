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

    /** One change of the recovery's records at a time: the page and the sync in the background. */
    private static final Object MISC_LOCK = new Object();

    /** The recovery's area of misc (MiscRecords.AREA_SIZE bytes), or null when it cannot be read. */
    static byte[] readMisc() {
        Root.Result r = Root.run(Scripts.miscRead(), 60);
        String text = r.ok() ? r.block() : null;
        if (text == null) return null;
        try {
            byte[] area = java.util.Base64.getMimeDecoder().decode(text.trim());
            return area.length == MiscRecords.AREA_SIZE ? area : null;
        } catch (IllegalArgumentException damaged) {
            return null;
        }
    }

    /** WRITES in their order, each slot read back from the eMMC; false at the first that did not land. */
    static boolean writeMisc(Context context, MiscRecords.Write[] writes) throws IOException {
        for (MiscRecords.Write w : writes) {
            File file = new File(context.getFilesDir(), "misc-slot.part");
            try (FileOutputStream out = new FileOutputStream(file)) {
                out.write(w.slot);
            }
            try {
                Root.Result r = Root.run(Scripts.miscWrite(file.getAbsolutePath(), w.offset), 60);
                String back = r.ok() ? r.block() : null;
                if (back == null || !java.util.Arrays.equals(java.util.Base64.getMimeDecoder().decode(back.trim()), w.slot)) return false;
            } catch (IllegalArgumentException damaged) {
                return false;
            } finally {
                //noinspection ResultOfMethodCallIgnored
                file.delete();
            }
        }
        return true;
    }

    /** Write this phone's language, zone and a new network (when SSID is given) for the recovery.
     *  Recovery 1.0.1 and newer keep them in misc: they go straight into its records there, the
     *  networks still waiting in the hand-over file with them, and the newer entry for a network wins
     *  (MiscRecords.wifiWith), so this app and the recovery never undo each other's password. Only a
     *  record the recovery made is changed: before its first start (it copies the settings of an
     *  older release then) and with an older recovery, the hand-over file is written as before. The
     *  file always carries the time, which moves the recovery's clock forward. The result has
     *  A22X:MISC wifi=… settings=… networks=… beside A22X:OK. */
    static Root.Result sendHandoff(Context context, String ssid, String password) throws IOException {
        synchronized (MISC_LOCK) {
            String waiting = waitingHandoff();
            long now = System.currentTimeMillis() / 1000;
            String language = Handoff.language(Locale.getDefault().getLanguage());
            String zone = Handoff.zone(TimeZone.getDefault().getID());
            boolean wifiDirect = false, settingsDirect = false;
            int networks = -1;
            byte[] area = readMisc();
            if (area != null) {
                try {
                    String settings = MiscRecords.read(area, "settings");
                    if (settings != null) {
                        String changed = MiscRecords.settingsWith(settings, language, zone);
                        if (changed == null) {
                            settingsDirect = true;
                        } else {
                            MiscRecords.Write[] writes = MiscRecords.plan(area, "settings", changed.getBytes(StandardCharsets.UTF_8));
                            settingsDirect = writeMisc(context, writes);
                            if (settingsDirect) area = MiscRecords.applied(area, writes);
                        }
                    }
                } catch (MiscRecords.Damaged | MiscRecords.Foreign | IllegalArgumentException notNow) {
                    // Damaged settings are the recovery's to reset; the hand-over file carries them.
                }
                try {
                    String list;
                    try {
                        list = MiscRecords.read(area, "wifi");
                    } catch (MiscRecords.Damaged damaged) {
                        list = MiscRecords.owned(area) ? "" : null; // the recovery reads it as no network too
                    }
                    if (list != null) {
                        String record = list;
                        long then = Handoff.waitingTime(waiting);
                        for (String[] network : Handoff.waitingNetworks(waiting)) {
                            byte[] raw = network[0].getBytes(StandardCharsets.UTF_8);
                            String next = MiscRecords.wifiWith(record, raw, MiscRecords.psk(raw, network[1]), then);
                            if (next != null) record = next;
                        }
                        if (ssid != null) {
                            if (Handoff.networkProblem(ssid, password) != null) throw new IllegalArgumentException("network");
                            byte[] raw = ssid.getBytes(StandardCharsets.UTF_8);
                            String next = MiscRecords.wifiWith(record, raw, MiscRecords.psk(raw, password), now);
                            if (next != null) record = next;
                        }
                        if (record.equals(list)) {
                            wifiDirect = !list.isEmpty() || (ssid == null && Handoff.waitingNetworks(waiting).isEmpty());
                        } else {
                            MiscRecords.Write[] writes = MiscRecords.plan(area, "wifi", record.getBytes(StandardCharsets.UTF_8));
                            wifiDirect = writeMisc(context, writes);
                            if (wifiDirect) area = MiscRecords.applied(area, writes);
                        }
                        if (wifiDirect) networks = MiscRecords.networks(record).size();
                    }
                } catch (MiscRecords.Damaged | MiscRecords.Foreign notNow) {
                    // The hand-over file carries them.
                }
            }
            String record = Handoff.merge(wifiDirect ? null : waiting, settingsDirect ? null : Locale.getDefault().getLanguage(),
                    settingsDirect ? null : TimeZone.getDefault().getID(), now, wifiDirect ? null : ssid, password);
            File file = privateFile(context, "handoff.json", record);
            try {
                Root.Result r = Root.run(Scripts.CACHE_OPEN
                        + "cat " + Root.quote(file.getAbsolutePath()) + " > \"$D/handoff.json.part\" && mv \"$D/handoff.json.part\" \"$D/handoff.json\""
                        + " && echo A22X:OK" + Scripts.CACHE_CLOSE, 60);
                String note = "\nA22X:MISC wifi=" + wifiDirect + " settings=" + settingsDirect + " networks=" + networks + "\n";
                // The network is saved once it is in misc, whatever became of the file.
                boolean done = r.ok() || (ssid != null && wifiDirect);
                return new Root.Result(done ? 0 : r.code, r.output + note + (done && r.marker("OK") == null ? "A22X:OK\n" : ""));
            } finally {
                //noinspection ResultOfMethodCallIgnored
                file.delete();
            }
        }
    }

    /** From a result of sendHandoff: the networks now saved in the recovery's list in misc, or -1
     *  when the network went into the hand-over file instead. */
    static int savedInMisc(Root.Result r) {
        String misc = r.marker("MISC");
        if (misc == null || !misc.contains("wifi=true")) return -1;
        try {
            return Integer.parseInt(misc.substring(misc.indexOf("networks=") + "networks=".length()).trim());
        } catch (RuntimeException e) {
            return -1;
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
