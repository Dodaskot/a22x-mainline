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

    /** Samsung's BOOT kept on cache before anything is flashed (2026-11-10), where the recovery
     *  keeps BOOT copies: a22x-recovery/saved-boot/android-<kernel>-<sha12>.img with .json and
     *  .sha256. The recovery offers it when a GSI is installed over Linux, and flashes it back from
     *  Flash a partition. Only an Android BOOT (its header names an OS version; a Linux BOOT built
     *  for this phone has none), once per content; a missing cache only skips the copy. Prints
     *  A22X:KEPT <path> or A22X:WARN STOCK_BOOT_NOT_KEPT <why>. */
    static final String SAVE_STOCK_BOOT =
            "keep_stock_boot() {\n"
            + "  b=; for d in /dev/block/by-name/boot /dev/block/bootdevice/by-name/boot /dev/block/platform/*/by-name/boot; do\n"
            + "    [ -e \"$d\" ] && { b=$(readlink -f \"$d\"); break; }; done\n"
            + "  [ -n \"$b\" ] && [ -b \"$b\" ] || { echo A22X:WARN STOCK_BOOT_NOT_KEPT no-boot; return 0; }\n"
            + "  [ \"$(head -c 8 \"$b\")\" = ANDROID! ] || { echo A22X:WARN STOCK_BOOT_NOT_KEPT not-a-boot; return 0; }\n"
            + "  osv=$(od -An -tu4 -j44 -N4 \"$b\" | tr -d ' ')\n"
            + "  [ -n \"$osv\" ] && [ \"$osv\" != 0 ] || { echo A22X:WARN STOCK_BOOT_NOT_KEPT not-android; return 0; }\n"
            + "  c=/cache; m=\n"
            + "  if ! grep -q ' /cache ' /proc/mounts; then\n"
            + "    dev=$(readlink -f /dev/block/by-name/cache 2>/dev/null || true)\n"
            + "    [ -b \"$dev\" ] || dev=$(readlink -f /dev/block/bootdevice/by-name/cache 2>/dev/null || true)\n"
            + "    [ -b \"$dev\" ] || { echo A22X:WARN STOCK_BOOT_NOT_KEPT no-cache; return 0; }\n"
            + "    c=/data/local/tmp/a22x-cache-boot; mkdir -p \"$c\"\n"
            + "    mount -t ext4 -o nosuid,nodev,noexec \"$dev\" \"$c\" || { echo A22X:WARN STOCK_BOOT_NOT_KEPT cache-mount; return 0; }\n"
            + "    m=1\n"
            + "  fi\n"
            + "  dir=\"$c/a22x-recovery/saved-boot\"; (umask 077; mkdir -p \"$dir\")\n"
            + "  tmp=\"$dir/.part-app\"; rm -f \"$tmp\"\n"
            + "  if cat \"$b\" > \"$tmp\" && sync; then\n"
            + "    sha=$(sha256sum \"$tmp\" | cut -d' ' -f1); size=$(stat -c %s \"$tmp\")\n"
            + "    rel=$(printf %s \"$(uname -r)\" | tr -c 'A-Za-z0-9._+-' '_' | cut -c1-48)\n"
            + "    name=\"android-$rel-$(echo \"$sha\" | cut -c1-12).img\"\n"
            + "    if [ -f \"$dir/$name\" ]; then rm -f \"$tmp\"; else\n"
            + "      mv \"$tmp\" \"$dir/$name\"\n"
            + "      build=$(printf %s \"$(getprop ro.build.version.incremental)\" | tr -c 'A-Za-z0-9._+-' '_' | cut -c1-40)\n"
            + "      printf '{\\n \"schema\": \"a22x.saved-boot.v1\",\\n \"kind\": \"android\",\\n \"release\": \"%s\",\\n \"build\": \"%s\",\\n"
            + " \"source\": \"app\",\\n \"sha256\": \"%s\",\\n \"bytes\": %s,\\n \"saved\": %s\\n}\\n' \"$rel\" \"$build\" \"$sha\" \"$size\" \"$(date +%s)\" > \"$dir/$name.json\"\n"
            + "      echo \"$sha  $name\" > \"$dir/$name.sha256\"\n"
            + "    fi\n"
            + "    echo \"A22X:KEPT /a22x-recovery/saved-boot/$name\"\n"
            + "  else rm -f \"$tmp\"; echo A22X:WARN STOCK_BOOT_NOT_KEPT copy; fi\n"
            + "  sync; [ -z \"$m\" ] || umount \"$c\"\n"
            + "  return 0\n"
            + "}\n";

    /** Android's BOOT kept on cache by the app itself, at every start of the app and of Android
     *  (2026-11-12, the owner: "the stock BOOT backed up on cache, so that the APK does it by
     *  itself"). A rooted phone's BOOT is patched; Magisk keeps the original it patched in
     *  /data/magisk_backup_<sha1>/boot.img.gz, so that one is kept as the stock BOOT
     *  (stock-<build>-<sha12>.img). Without it the BOOT there is kept (android-<kernel>-<sha12>.img),
     *  marked with the root in it, so the recovery keeps only the newest rooted one. Only a whole
     *  Android boot image, once per content. Prints A22X:KEPT <path> or A22X:WARN <why>. */
    static final String KEEP_BOOT_AUTO =
            // 2026-11-12 review: one run at a time (the app's start, Android's start and Keep now may
            // overlap and shared one temporary file); a lock older than ten minutes is a dead run's.
            "keep_boot_auto() {\n"
            + "  L=/data/local/tmp/a22x-keep-boot.lock\n"
            + "  [ -d \"$L\" ] && [ -n \"$(find \"$L\" -maxdepth 0 -mmin +10 2>/dev/null)\" ] && rmdir \"$L\" 2>/dev/null\n"
            + "  mkdir \"$L\" 2>/dev/null || { echo A22X:WARN busy; return 0; }\n"
            + "  keep_boot_auto_locked; r=$?; rmdir \"$L\" 2>/dev/null; return $r\n"
            + "}\n"
            + "keep_boot_auto_locked() {\n"
            + "  src=; kind=current; root=\n"
            + "  for f in /data/magisk_backup_*/boot.img.gz; do [ -f \"$f\" ] && { src=$f; kind=stock; }; done\n"
            + "  b=; for d in /dev/block/by-name/boot /dev/block/bootdevice/by-name/boot /dev/block/platform/*/by-name/boot; do\n"
            + "    [ -e \"$d\" ] && { b=$(readlink -f \"$d\"); break; }; done\n"
            + "  if [ \"$kind\" = current ]; then\n"
            + "    [ -n \"$b\" ] && [ -b \"$b\" ] || { echo A22X:WARN no-boot; return 0; }\n"
            + "    [ -d /data/adb/magisk ] && root=magisk; [ -d /data/adb/ap ] && root=apatch\n"
            + "  fi\n"
            + "  c=/cache; m=\n"
            + "  if ! grep -q ' /cache ' /proc/mounts; then\n"
            + "    dev=$(readlink -f /dev/block/by-name/cache 2>/dev/null || true)\n"
            + "    [ -b \"$dev\" ] || dev=$(readlink -f /dev/block/bootdevice/by-name/cache 2>/dev/null || true)\n"
            + "    [ -b \"$dev\" ] || { echo A22X:WARN no-cache; return 0; }\n"
            + "    c=/data/local/tmp/a22x-cache-auto; mkdir -p \"$c\"\n"
            + "    mount -t ext4 -o nosuid,nodev,noexec \"$dev\" \"$c\" || { echo A22X:WARN cache-mount; return 0; }\n"
            + "    m=1\n"
            + "  fi\n"
            + "  dir=\"$c/a22x-recovery/saved-boot\"; (umask 077; mkdir -p \"$dir\")\n"
            + "  tmp=\"$dir/.part-app-auto\"; rm -f \"$tmp\"\n"
            + "  part=$(blockdev --getsize64 \"$b\" 2>/dev/null); [ -n \"$part\" ] || part=33554432\n"
            + "  free=$(df -k \"$dir\" | awk 'NR==2{print $4}')\n"
            + "  if [ \"${free:-0}\" -lt $((part / 1024 + 8192)) ]; then echo A22X:WARN cache-full; sync; [ -z \"$m\" ] || umount \"$c\"; return 0; fi\n"
            + "  if [ \"$kind\" = stock ]; then gzip -dc \"$src\" > \"$tmp\" 2>/dev/null || zcat \"$src\" > \"$tmp\"; else cat \"$b\" > \"$tmp\"; fi\n"
            + "  ok=$?\n"
            + "  osv=$(od -An -tu4 -j44 -N4 \"$tmp\" 2>/dev/null | tr -d ' ')\n"
            + "  if [ \"$ok\" != 0 ]; then rm -f \"$tmp\"; echo A22X:WARN cache-full; sync; [ -z \"$m\" ] || umount \"$c\"; return 0; fi\n"
            + "  if [ \"$(head -c 8 \"$tmp\")\" != ANDROID! ] || [ -z \"$osv\" ] || [ \"$osv\" = 0 ]; then\n"
            + "    rm -f \"$tmp\"; echo A22X:WARN not-android; sync; [ -z \"$m\" ] || umount \"$c\"; return 0\n"
            + "  fi\n"
            + "  sync; sha=$(sha256sum \"$tmp\" | cut -d' ' -f1); size=$(stat -c %s \"$tmp\")\n"
            + "  rel=$(printf %s \"$(uname -r)\" | tr -c 'A-Za-z0-9._+-' '_' | cut -c1-48)\n"
            + "  build=$(printf %s \"$(getprop ro.build.version.incremental)\" | tr -c 'A-Za-z0-9._+-' '_' | cut -c1-40)\n"
            + "  if [ \"$kind\" = stock ]; then name=\"stock-$build-$(echo \"$sha\" | cut -c1-12).img\"; else name=\"android-$rel-$(echo \"$sha\" | cut -c1-12).img\"; fi\n"
            // Kept already only when the image itself is there, not only its description (2026-11-12).
            + "  have=; for s in \"$dir\"/*.sha256; do [ -f \"$s\" ] || continue; read -r h n < \"$s\"; [ \"$h\" = \"$sha\" ] && [ -f \"$dir/$n\" ] && have=1; done\n"
            + "  if [ -n \"$have\" ]; then rm -f \"$tmp\"; echo \"A22X:KEPT already\"; else\n"
            + "    {\n"
            + "      mv \"$tmp\" \"$dir/$name\"\n"
            + "      printf '{\\n \"schema\": \"a22x.saved-boot.v1\",\\n \"kind\": \"android\",\\n \"release\": \"%s\",\\n \"build\": \"%s\",\\n"
            + " \"source\": \"app\",\\n \"root\": \"%s\",\\n \"sha256\": \"%s\",\\n \"bytes\": %s,\\n \"saved\": %s\\n}\\n' \"$rel\" \"$build\" \"$root\" \"$sha\" \"$size\" \"$(date +%s)\" > \"$dir/$name.json\"\n"
            + "      echo \"$sha  $name\" > \"$dir/$name.sha256\"\n"
            + "      echo \"A22X:KEPT /a22x-recovery/saved-boot/$name\"\n"
            + "    }\n"
            + "  fi\n"
            + "  sync; [ -z \"$m\" ] || umount \"$c\"\n"
            + "  return 0\n"
            + "}\n";

    static String keepBootAuto() {
        return KEEP_BOOT_AUTO + "keep_boot_auto\n";
    }

    /** A Linux BOOT handed to the recovery instead of flashed from Android (2026-11-11): kept where
     *  the recovery keeps BOOT copies, a22x-recovery/saved-boot/linux-<release>-<sha12>.img with
     *  .json and .sha256. Since 0.6.178 a Linux system installed by the recovery takes its BOOT
     *  from there and writes it when the system is in place, so Android keeps starting until then.
     *  KEEP_LINUX_BOOT expects D (the recovery's folder on cache), IMG, SIZE, SHA and REL, and
     *  prints A22X:OK <path> or A22X:ERROR <reason>; nothing half-written is left. */
    static final String KEEP_LINUX_BOOT =
            "keep_linux_boot() {\n"
            + "  dir=\"$D/saved-boot\"; mkdir -p \"$dir\" || { echo A22X:ERROR CACHE_FOLDER; return 23; }\n"
            + "  name=\"linux-$REL-$(echo \"$SHA\" | cut -c1-12).img\"\n"
            + "  if [ -f \"$dir/$name\" ] && [ -f \"$dir/$name.json\" ] && [ \"$(sha256sum \"$dir/$name\" | cut -d' ' -f1)\" = \"$SHA\" ]; then\n"
            + "    echo \"A22X:OK /a22x-recovery/saved-boot/$name\"; return 0\n"
            + "  fi\n"
            + "  [ \"$(sha256sum \"$IMG\" | cut -d' ' -f1)\" = \"$SHA\" ] || { echo A22X:ERROR IMAGE_CHANGED; return 12; }\n"
            + "  free=$(df -k \"$dir\" | awk 'NR==2{print $4}')\n"
            + "  [ \"${free:-0}\" -ge $((SIZE / 1024 + 8192)) ] || { echo \"A22X:ERROR CACHE_FULL ${free:-0}\"; return 22; }\n"
            + "  tmp=\"$dir/.part-app-linux\"; rm -f \"$tmp\"\n"
            + "  cat \"$IMG\" > \"$tmp\" && sync || { rm -f \"$tmp\"; echo A22X:ERROR COPY_FAILED; return 24; }\n"
            + "  [ \"$(sha256sum \"$tmp\" | cut -d' ' -f1)\" = \"$SHA\" ] || { rm -f \"$tmp\"; echo A22X:ERROR READBACK_MISMATCH; return 17; }\n"
            + "  mv \"$tmp\" \"$dir/$name\" || { rm -f \"$tmp\"; echo A22X:ERROR COPY_FAILED; return 24; }\n"
            + "  printf '{\\n \"schema\": \"a22x.saved-boot.v1\",\\n \"kind\": \"linux\",\\n \"release\": \"%s\",\\n \"source\": \"app\",\\n"
            + " \"sha256\": \"%s\",\\n \"bytes\": %s,\\n \"saved\": %s\\n}\\n' \"$REL\" \"$SHA\" \"$SIZE\" \"$(date +%s)\" > \"$dir/$name.json\"\n"
            + "  echo \"$SHA  $name\" > \"$dir/$name.sha256\"\n"
            + "  echo \"A22X:OK /a22x-recovery/saved-boot/$name\"; return 0\n"
            + "}\n";

    /** The whole script for the phone: cache found or mounted, the BOOT kept, cache left as it was. */
    static String keepLinuxBoot(String image, long size, String sha256, String release) {
        if (!sha256.matches("[0-9a-f]{64}")) throw new IllegalArgumentException("sha256");
        if (size != BOOT_PARTITION) throw new IllegalArgumentException("size");
        if (!release.matches("[A-Za-z0-9._+-]{1,48}")) throw new IllegalArgumentException("release");
        return "IMG=" + Root.quote(image) + "; SIZE=" + size + "; SHA=" + sha256 + "; REL=" + release + "\n"
                + CACHE_OPEN + KEEP_LINUX_BOOT + "keep_linux_boot; RC=$?" + CACHE_CLOSE + "exit $RC\n";
    }

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
                + SAVE_STOCK_BOOT + "keep_stock_boot\n"
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
