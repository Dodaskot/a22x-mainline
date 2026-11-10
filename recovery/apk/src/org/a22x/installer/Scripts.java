// SPDX-License-Identifier: MIT
package org.a22x.installer;

/** The shell scripts this app runs as root, as plain text: no Android classes, so they are
 *  generated on a computer and run in the recovery against a test device (HandoffTest, and
 *  tools/test-scripts-on-phone.sh). */
final class Scripts {
    /** Finds the cache partition: Android usually has it at /cache; otherwise it is mounted
     *  privately for this one script and unmounted at the end. */
    static final String CACHE_OPEN =
            "C=/cache; MOUNTED=\n"
            + "if ! grep -q ' /cache ' /proc/mounts; then\n"
            + "  dev=$(readlink -f /dev/block/by-name/cache 2>/dev/null || true)\n"
            + "  [ -b \"$dev\" ] || dev=$(readlink -f /dev/block/bootdevice/by-name/cache 2>/dev/null || true)\n"
            + "  [ -b \"$dev\" ] || { echo A22X:ERROR NO_CACHE; exit 20; }\n"
            + "  C=/data/local/tmp/a22x-cache; mkdir -p \"$C\"\n"
            + "  mount -t ext4 -o nosuid,nodev,noexec \"$dev\" \"$C\" || { echo A22X:ERROR CACHE_MOUNT; exit 21; }\n"
            + "  MOUNTED=1\n"
            + "fi\n"
            + "D=\"$C/a22x-recovery\"; umask 077; mkdir -p \"$D\"\n";
    static final String CACHE_CLOSE = "\nsync; [ -z \"$MOUNTED\" ] || umount \"$C\"\n";

    /** Prints A22X:PART <label> <bytes> for the recovery and boot partitions (0 when missing). */
    static final String PARTITIONS =
            "for T in recovery boot; do\n"
            + "  dev=\n"
            + "  for d in /dev/block/by-name/$T /dev/block/bootdevice/by-name/$T /dev/block/platform/*/by-name/$T; do\n"
            + "    [ -e \"$d\" ] && { dev=$(readlink -f \"$d\"); break; }\n"
            + "  done\n"
            + "  if [ -n \"$dev\" ] && [ -b \"$dev\" ]; then echo \"A22X:PART $T $(blockdev --getsize64 \"$dev\")\"; else echo \"A22X:PART $T 0\"; fi\n"
            + "done\n";

    /** The A22x's partitions are exactly this large (GPT of the SM-A226B); with MediaTek's MT6833
     *  they are the second guard after the compatibility check, inside the root script itself. */
    static final long RECOVERY_PARTITION = 41943040L;
    static final long BOOT_PARTITION = 33554432L;
    static final String PLATFORM = "mt6833";

    static long partitionSize(String label) {
        if (label.equals("recovery")) return RECOVERY_PARTITION;
        if (label.equals("boot")) return BOOT_PARTITION;
        throw new IllegalArgumentException(label);
    }

    private Scripts() {
    }

    /** Write IMAGE (already checked against SHA256) to partition LABEL: the partition must be
     *  exactly PARTITION bytes, on a phone whose platform properties name PLATFORM (both checked
     *  before anything is read or written: another phone stops here even if the page let it
     *  through; 2026-11-10, before a test on a OnePlus); its old content is saved first in
     *  BACKUP_DIR; afterwards the partition is read back and must equal the image. Prints
     *  A22X:OK <saved copy> or A22X:ERROR <reason>. LABELS limits the partitions (recovery and
     *  boot in the app). An empty PLATFORM skips that check, for the test on loop devices. */
    static String flash(String label, String image, long size, String sha256, String backupDir, long partition, String platform,
                        String... labels) {
        if (!java.util.Arrays.asList(labels).contains(label)) throw new IllegalArgumentException("label");
        if (!sha256.matches("[0-9a-f]{64}")) throw new IllegalArgumentException("sha256");
        if (partition < size || !platform.matches("[a-z0-9]{0,16}")) throw new IllegalArgumentException("partition");
        String script = "T=" + label + "; IMG=" + Root.quote(image) + "; SIZE=" + size
                + "; SHA=" + sha256 + "; BK=" + Root.quote(backupDir) + "; PSIZE=" + partition + "; PLAT=" + platform + "\n"
                + "if [ -n \"$PLAT\" ]; then\n"
                + "  found=\" $(getprop ro.board.platform) $(getprop ro.hardware) $(getprop ro.vendor.mediatek.platform) \"\n"
                + "  found=$(echo \"$found\" | tr 'A-Z' 'a-z')\n"
                + "  case \"$found\" in *\"$PLAT\"*) ;; *) echo \"A22X:ERROR NOT_THIS_PHONE platform\"; exit 19 ;; esac\n"
                + "fi\n"
                + "dev=\n"
                + "for d in /dev/block/by-name/$T /dev/block/bootdevice/by-name/$T /dev/block/platform/*/by-name/$T; do\n"
                + "  [ -e \"$d\" ] && { dev=$(readlink -f \"$d\"); break; }\n"
                + "done\n"
                + "[ -n \"$dev\" ] && [ -b \"$dev\" ] || { echo A22X:ERROR NO_PARTITION; exit 10; }\n"
                + "psize=$(blockdev --getsize64 \"$dev\")\n"
                + "[ \"$psize\" = \"$PSIZE\" ] || { echo \"A22X:ERROR NOT_THIS_PHONE $T $psize\"; exit 18; }\n"
                + "[ \"$psize\" -ge \"$SIZE\" ] || { echo \"A22X:ERROR TOO_SMALL $psize\"; exit 11; }\n"
                + "[ \"$(sha256sum \"$IMG\" | cut -d' ' -f1)\" = \"$SHA\" ] || { echo A22X:ERROR IMAGE_CHANGED; exit 12; }\n"
                + "mkdir -p \"$BK\" || { echo A22X:ERROR BACKUP_FOLDER; exit 13; }\n"
                + "copy=\"$BK/$T-$(date +%Y%m%d-%H%M%S).img\"\n"
                + "cat \"$dev\" > \"$copy\" && sync || { rm -f \"$copy\"; echo A22X:ERROR BACKUP_FAILED; exit 14; }\n"
                + "[ \"$(stat -c %s \"$copy\")\" = \"$psize\" ] || { echo A22X:ERROR BACKUP_SHORT; exit 15; }\n"
                + "dd if=\"$IMG\" of=\"$dev\" bs=1048576 conv=fsync 2>/dev/null || { echo A22X:ERROR WRITE_FAILED; exit 16; }\n"
                + "sync; echo 3 > /proc/sys/vm/drop_caches\n"
                + "[ \"$(head -c \"$SIZE\" \"$dev\" | sha256sum | cut -d' ' -f1)\" = \"$SHA\" ] || { echo A22X:ERROR READBACK_MISMATCH; exit 17; }\n"
                + "echo \"A22X:OK $copy\"\n";
        return script;
    }
}
