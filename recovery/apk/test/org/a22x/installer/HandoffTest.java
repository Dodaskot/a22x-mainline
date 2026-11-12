// SPDX-License-Identifier: MIT
package org.a22x.installer;

import java.util.List;
import java.util.Map;

/** Runs on a computer (no Android classes): java -cp classes org.a22x.installer.HandoffTest */
public final class HandoffTest {
    private static int checks;

    private static void check(boolean condition, String what) {
        checks++;
        if (!condition) throw new AssertionError(what);
    }

    private static Map<String, String> facts(String... pairs) {
        Map<String, String> map = new java.util.HashMap<>();
        for (int i = 0; i < pairs.length; i += 2) map.put(pairs[i], pairs[i + 1]);
        return map;
    }

    private static Compat.State state(Compat.Result result, String name) {
        for (Compat.Check check : result.checks) if (check.name.equals(name)) return check.state;
        throw new AssertionError(name);
    }

    /** The same phone passes with stock Android, a custom ROM or a GSI; other phones fail. */
    private static void compat() {
        Compat.Result stock = Compat.judge(facts("model", "SM-A226B", "device", "a22x", "product", "a22xnsxx",
                "bootloader", "A226BXXSBDZC3", "hardware", "mt6833", "ro.board.platform", "mt6833"));
        check(stock.compatible && state(stock, "bootloader") == Compat.State.MATCH, "stock");
        Compat.Result lineage = Compat.judge(facts("model", "Galaxy A22 5G", "device", "a22x", "product", "lineage_a22x",
                "bootloader", "A226BXXSBDZC3", "hardware", "mt6833"));
        check(lineage.compatible && state(lineage, "model") == Compat.State.NEUTRAL, "custom ROM");
        Compat.Result gsi = Compat.judge(facts("model", "phh AOSP", "device", "phhgsi_arm64_ab", "product", "aosp_arm64",
                "ro.product.vendor.model", "SM-A226B", "ro.product.vendor.device", "a22x", "ro.boot.bootloader", "A226BXXSBDYA1",
                "ro.board.platform", "mt6833"));
        check(gsi.compatible, "GSI");
        Compat.Result brazil = Compat.judge(facts("model", "SM-A226BR", "device", "a22x", "bootloader", "A226BRVJU2BWA1", "hardware", "mt6833"));
        check(brazil.compatible, "the SM-A226BR");
        Compat.Result spoofed = Compat.judge(facts("model", "SM-A226B", "device", "a22x", "bootloader", "A325FXXU2BVD1", "hardware", "mt6768"));
        check(!spoofed.compatible && state(spoofed, "bootloader") == Compat.State.CONFLICT, "another Samsung renamed");
        Compat.Result a32 = Compat.judge(facts("model", "SM-A325F", "device", "a32", "bootloader", "A325FXXU2BVD1", "hardware", "mt6768"));
        check(!a32.compatible, "another Samsung");
        Compat.Result redmi = Compat.judge(facts("model", "M2103K19G", "device", "camellian", "hardware", "mt6833", "ro.board.platform", "mt6833"));
        check(!redmi.compatible && state(redmi, "chip") == Compat.State.MATCH, "the same chip, another phone");
        Compat.Result bare = Compat.judge(facts("device", "a22x"));
        check(bare.compatible, "only the code name, nothing against it");
        Compat.Result empty = Compat.judge(facts());
        check(!empty.compatible, "nothing known");
        // 2026-11-10, before the person's test on a OnePlus 15 (Snapdragon): refused by the page.
        Compat.Result oneplus = Compat.judge(facts("model", "CPH2747", "device", "OP5D55L1", "product", "CPH2747EEA",
                "bootloader", "unknown", "hardware", "qcom", "ro.board.platform", "canoe", "ro.boot.hardware", "qcom"));
        check(!oneplus.compatible && state(oneplus, "chip") == Compat.State.CONFLICT, "a OnePlus");
    }

    /** The plan for the recovery: SHA-512 crypt as openssl passwd -6 makes it, and the JSON
     *  install_plan.py takes (2026-11-10). */
    private static void plan() {
        String[][] vectors = {
            {"Hello world!", "$6$saltstring$svn8UoSVapNtMuq1ukKS4tPQd8iKwSMHWjl/O817G3uBnIFNjnQJuesI68u4OTLiBFdcbYEdFCoEOfaS35inz1"},
            {"246810", "$6$saltstring$PPiDvwUYv3930wbGLFb1yeDzkLbaHopTtJR6ICCMVsuSrU7rHbA/lSmFc06LfEdK7.iEjilA9mFrPkmr.jurv0"},
            {"пароль-ґ", "$6$saltstring$gaZ4J4U/LIboJ4vb5wYsi9hkrT8tx1Epq3YNCTKSItyJscPl55WMiMDpHj6RpI0rU4Z1FIJgH38JTOez54Sjo."},
            {"a-very-long-password-that-is-longer-than-sixty-four-bytes-for-the-loop-0123456789",
             "$6$saltstring$k8NvnPdSdaKg3k17pF4ztNmDPXNYNc5YLYiGP.x4IN3Mkt1Drm6.UoD1Vka0kmp0IyzvyKdE0jcU99Uf4NUSZ/"}};
        for (String[] v : vectors) check(Sha512Crypt.crypt(v[0], "saltstring").equals(v[1]), "sha512 crypt of " + v[0]);
        check(Sha512Crypt.crypt("246810", "a22xSalt0123ABCD").equals(
                "$6$a22xSalt0123ABCD$PLyuULaxrkDLMMzwGCfe88HTP/knKVPCz2VuBCZPVbsacZXjuEw6833K5yKPQoMURJd9f189nEre15HUkFVKV0"), "16-character salt");
        String fresh = Sha512Crypt.hash("246810");
        check(fresh.matches("\\$6\\$[./A-Za-z0-9]{16}\\$[./A-Za-z0-9]{86}") && !fresh.equals(Sha512Crypt.hash("246810")), "random salt");
        java.util.Set<String> catalog = new java.util.HashSet<>(java.util.Arrays.asList("ubuntu-24.04", "debian-13"));
        Plan.Choice c = new Plan.Choice();
        c.catalog = "ubuntu-24.04"; c.desktop = "phosh"; c.shells.add("gnome"); c.shells.add("phosh"); c.apps.add("calculator"); c.apps.add("ssh");
        c.username = "user"; c.password = "246810"; c.locale = "uk_UA"; c.timezone = "Europe/Kyiv"; c.script = "my-setup.sh";
        check(Plan.problem(c, catalog) == null, "a good plan");
        String hash = Sha512Crypt.crypt("246810", "saltstring");
        String json = Plan.json(c, hash, "A22x Recovery installer 0.6.139", 1794306000L);
        @SuppressWarnings("unchecked") java.util.Map<String, Object> r = (java.util.Map<String, Object>) Json.parse(json);
        check(Plan.SCHEMA.equals(r.get("schema")) && Boolean.TRUE.equals(r.get("erase_userdata")) && Long.valueOf(1794306000L).equals(r.get("created_utc")), "schema, erase, time");
        check("[gnome]".equals(String.valueOf(r.get("shells"))) && "[ssh, calculator]".equals(String.valueOf(r.get("apps"))), "shells without the main one, apps in order");
        check(json.contains("\"password_sha512\":\"" + hash.replace("/", "\\/") + "\"") || json.contains("\"password_sha512\":\"" + hash + "\""), "the hash");
        check(!json.contains("246810"), "never the password itself");
        check("uk_UA".equals(Plan.locale("uk")) && "uk_UA".equals(Plan.locale("UA")) && Plan.locale("ja") == null, "languages");
        c.catalog = "windows-11"; check("plan_bad_system".equals(Plan.problem(c, catalog)), "unknown image");
        c.catalog = "ubuntu-24.04"; c.password = ""; check("plan_bad_password".equals(Plan.problem(c, catalog)), "no password");
        c.password = "a:b"; check("plan_bad_password".equals(Plan.problem(c, catalog)), "a colon");
        c.password = "246810"; c.username = "Root"; check("plan_bad_user".equals(Plan.problem(c, catalog)), "user name");
        c.username = "user"; c.desktop = "image"; check("plan_shells_need_desktop".equals(Plan.problem(c, catalog)), "shells need a desktop");
        c.shells.clear(); c.signIn = "gdm"; check("plan_sign_in_needs_desktop".equals(Plan.problem(c, catalog)), "sign-in needs a desktop");
        c.signIn = "auto"; c.script = "../x.sh"; check("bad_name".equals(Plan.problem(c, catalog)), "script name");
        java.util.Map<String, String> images = Plan.catalog("{\"images\":[{\"id\":\"ubuntu-24.04\",\"name\":\"Ubuntu Server\",\"version\":\"24.04 LTS\"},{\"name\":\"no id\"}]}");
        check(images.size() == 1 && "Ubuntu Server 24.04 LTS".equals(images.get("ubuntu-24.04")), "catalog");
    }

    /** The root script stops on another phone by itself: exact A22x partition sizes and MT6833. */
    private static void flashGuards() {
        String sha = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";
        String s = Scripts.flash("recovery", "/data/x.img", 41943040L, sha, "/sdcard/A22x", Scripts.RECOVERY_PARTITION, Scripts.PLATFORM,
                "recovery", "boot");
        int write = s.indexOf("dd if=");
        int backup = s.indexOf("cat \"$dev\" > \"$copy\"");
        check(s.indexOf("NOT_THIS_PHONE platform") >= 0 && s.indexOf("NOT_THIS_PHONE platform") < backup, "platform before anything is read");
        check(s.indexOf("[ \"$psize\" = \"$PSIZE\" ]") >= 0 && s.indexOf("[ \"$psize\" = \"$PSIZE\" ]") < backup && backup < write,
                "exact size before the backup and the write");
        check(s.contains("PSIZE=41943040") && s.contains("PLAT=mt6833") && s.contains("tr 'A-Z' 'a-z'"), "A22x values");
        int keep = s.indexOf("keep_stock_boot\n", s.indexOf("NO_PARTITION"));
        check(keep > s.indexOf("NOT_THIS_PHONE platform") && keep < backup && keep < write, "stock BOOT kept on cache before the write");
        check(s.contains("a22x-recovery/saved-boot") && s.contains("\"kind\": \"android\"") && s.contains("-j44 -N4"), "the recovery's saved-boot form");
        check(Scripts.partitionSize("boot") == 33554432L, "boot size");
        try {
            Scripts.flash("recovery", "/data/x.img", 41943040L, sha, "/sdcard/A22x", 1L, "", "recovery");
            check(false, "a partition smaller than the image");
        } catch (IllegalArgumentException expected) {
            // refused while building the script
        }
        try {
            Scripts.flash("super", "/data/x.img", 1L, sha, "/sdcard/A22x", 2L, "", "recovery", "boot");
            check(false, "a partition the app never writes");
        } catch (IllegalArgumentException expected) {
            // refused while building the script
        }
    }

    /** The Linux BOOT handed to the recovery (2026-11-11): kept on cache in the recovery's own form,
     *  never written to a partition. The part that does the keeping is run here, in a folder. */
    @SuppressWarnings("unchecked")
    private static void keepLinuxBoot() throws Exception {
        String sha = "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef";
        String s = Scripts.keepLinuxBoot("/data/boot.img", Scripts.BOOT_PARTITION, sha, "7.2.0-a22x+");
        check(!s.contains("dd if=") && !s.contains("by-name/boot"), "no partition is written");
        check(s.indexOf("CACHE_FULL") < s.indexOf("cat \"$IMG\"") && s.contains("\"kind\": \"linux\"") && s.contains("REL=7.2.0-a22x+"), "the recovery's saved-boot form");
        check(s.endsWith("exit $RC\n") && s.indexOf("umount \"$C\"") > s.indexOf("keep_linux_boot; RC=$?"), "cache is left as it was found");
        for (String[] bad : new String[][] {{"12", "7.2.0-a22x+"}, {sha, "7.2.0 a22x"}, {sha, ""}}) {
            try {
                Scripts.keepLinuxBoot("/data/boot.img", Scripts.BOOT_PARTITION, bad[0], bad[1]);
                check(false, "a bad argument reaches the script");
            } catch (IllegalArgumentException expected) {
                // refused while building the script
            }
        }
        try {
            Scripts.keepLinuxBoot("/data/boot.img", 1L, sha, "7.2.0-a22x+");
            check(false, "an image that is no whole BOOT");
        } catch (IllegalArgumentException expected) {
            // refused while building the script
        }
        java.io.File dir = java.nio.file.Files.createTempDirectory("a22x-keep").toFile();
        java.io.File image = new java.io.File(dir, "boot.img");
        byte[] data = new byte[4096];
        for (int i = 0; i < data.length; i++) data[i] = (byte) (i * 7);
        java.nio.file.Files.write(image.toPath(), data);
        StringBuilder hex = new StringBuilder();
        for (byte b : java.security.MessageDigest.getInstance("SHA-256").digest(data)) hex.append(String.format("%02x", b & 0xff));
        String head = "D=" + dir + "/cache; mkdir -p \"$D\"; IMG=" + image + "; SIZE=4096; REL=7.2.0-a22x+; ";
        String name = "linux-7.2.0-a22x+-" + hex.substring(0, 12) + ".img";
        String[] out = shell(head + "SHA=" + hex + "\n" + Scripts.KEEP_LINUX_BOOT + "keep_linux_boot; echo RC=$?\n");
        check(out[0].contains("A22X:OK /a22x-recovery/saved-boot/" + name) && out[0].contains("RC=0"), "kept: " + out[0] + out[1]);
        java.io.File kept = new java.io.File(dir, "cache/saved-boot/" + name);
        check(java.util.Arrays.equals(java.nio.file.Files.readAllBytes(kept.toPath()), data), "the kept image is the image");
        Map<String, Object> meta = (Map<String, Object>) Json.parse(new String(java.nio.file.Files.readAllBytes(new java.io.File(kept + ".json").toPath()), "UTF-8"));
        check("a22x.saved-boot.v1".equals(meta.get("schema")) && "linux".equals(meta.get("kind")) && "7.2.0-a22x+".equals(meta.get("release"))
                && hex.toString().equals(meta.get("sha256")) && ((Number) meta.get("bytes")).longValue() == 4096L, "what the recovery reads beside it");
        check(new String(java.nio.file.Files.readAllBytes(new java.io.File(kept + ".sha256").toPath()), "UTF-8").equals(hex + "  " + name + "\n"), "its checksum file");
        // Once more: it is there already, and nothing is copied again.
        long before = kept.lastModified();
        out = shell(head + "SHA=" + hex + "\n" + Scripts.KEEP_LINUX_BOOT + "keep_linux_boot; echo RC=$?\n");
        check(out[0].contains("A22X:OK") && kept.lastModified() == before, "kept once");
        // An image that is not the one named: nothing is left on cache.
        String other = hex.substring(1) + (hex.charAt(0) == '0' ? "1" : "0");
        out = shell(head + "SHA=" + other + "\n" + Scripts.KEEP_LINUX_BOOT + "keep_linux_boot; echo RC=$?\n");
        String[] left = new java.io.File(dir, "cache/saved-boot").list();
        check(out[0].contains("A22X:ERROR IMAGE_CHANGED") && out[0].contains("RC=12") && left != null && left.length == 3, "a changed image is refused");
        for (java.io.File f : new java.io.File(dir, "cache/saved-boot").listFiles()) f.delete();
        new java.io.File(dir, "cache/saved-boot").delete();
        new java.io.File(dir, "cache").delete();
        image.delete();
        dir.delete();
    }

    private static String[] shell(String script) throws Exception {
        Process p = new ProcessBuilder("sh", "-c", script).start();
        String out = new String(readAll(p.getInputStream()), "UTF-8"), err = new String(readAll(p.getErrorStream()), "UTF-8");
        p.waitFor();
        return new String[] {out, err};
    }

    private static byte[] readAll(java.io.InputStream in) throws java.io.IOException {
        java.io.ByteArrayOutputStream out = new java.io.ByteArrayOutputStream();
        byte[] buffer = new byte[8192];
        int n;
        while ((n = in.read(buffer)) > 0) out.write(buffer, 0, n);
        return out.toByteArray();
    }

    @SuppressWarnings("unchecked")
    public static void main(String[] args) throws Exception {
        // JSON reads what it writes, including escapes and nesting.
        String text = Json.write(Json.parse("{\"a\":[1,2.5,true,null,\"x\\\"y\\n\\u00e9\"],\"b\":{}}"));
        Map<String, Object> back = (Map<String, Object>) Json.parse(text);
        List<Object> list = (List<Object>) back.get("a");
        check(list.get(0).equals(1L) && list.get(2).equals(Boolean.TRUE) && list.get(3) == null, "values");
        check("x\"y\né".equals(list.get(4)), "escapes");
        for (String broken : new String[]{"{", "{\"a\":}", "[1,]", "\"\u0001\"", "{} x", "nul"}) {
            try {
                Json.parse(broken);
                check(false, "accepted " + broken);
            } catch (IllegalArgumentException expected) {
                checks++;
            }
        }
        // A new record: language, zone, time and a network, in the recovery's schema.
        String record = Handoff.merge(null, "uk", "Europe/Kyiv", 1794300000L, "Home", "secret-pass");
        Map<String, Object> r = (Map<String, Object>) Json.parse(record);
        check(Handoff.SCHEMA.equals(r.get("schema")) && "android-app".equals(r.get("source")), "schema");
        check("uk".equals(r.get("language")) && "Europe/Kyiv".equals(r.get("timezone")) && Long.valueOf(1794300000L).equals(r.get("time_utc")), "language, zone, time");
        check(Handoff.networks(record) == 1, "one network");
        // A later change keeps the networks the recovery has not taken yet and replaces one of the same name.
        String later = Handoff.merge(record, "de", "Europe/Berlin", 1794400000L, null, null);
        check(Handoff.networks(later) == 1 && later.contains("\"language\":\"de\""), "networks kept");
        String again = Handoff.merge(later, "de", "Europe/Berlin", 1794400000L, "Home", "other-pass");
        check(Handoff.networks(again) == 1 && again.contains("other-pass") && !again.contains("secret-pass"), "same name replaced");
        String open = Handoff.merge(again, "de", "Europe/Berlin", 1, "Cafe", "");
        check(Handoff.networks(open) == 2 && open.indexOf("Cafe") < open.indexOf("Home"), "newest first, open network");
        String many = open;
        for (int i = 0; i < 12; i++) many = Handoff.merge(many, "en", "UTC", 1, "Net " + i, "password" + i);
        check(Handoff.networks(many) == Handoff.MAX_WIFI, "at most eight");
        // What the recovery would refuse is not written.
        check(Handoff.networkProblem("", "password1") != null && Handoff.networkProblem("x", "short") != null, "bad networks");
        check(Handoff.networkProblem("0123456789012345678901234567890123", "") != null, "ssid over 32 bytes");
        check(Handoff.networkProblem("Home", "") == null && Handoff.networkProblem("Home", "eight888") == null, "good networks");
        check(Handoff.language("pt") == null && "uk".equals(Handoff.language("UK")) && "uk".equals(Handoff.language("ua")), "languages");
        check(!Handoff.merge(null, "pt", "UTC", 1, null, null).contains("language"), "unknown language left out");
        check(Handoff.zone("../etc/passwd") == null && Handoff.zone("America/Argentina/Buenos_Aires") != null, "zones");
        check(!Handoff.merge("{broken", "en", "Europe/Kyiv", 1, null, null).contains("wifi"), "a damaged file is replaced");
        check(Handoff.scriptNameAllowed("my-setup.sh") && !Handoff.scriptNameAllowed("../x.sh")
                && !Handoff.scriptNameAllowed("x.txt") && !Handoff.scriptNameAllowed(".hidden.sh"), "script names");
        compat();
        flashGuards();
        keepLinuxBoot();
        plan();
        System.out.println("{\"ok\":true,\"checks\":" + checks + "}");
    }
}
