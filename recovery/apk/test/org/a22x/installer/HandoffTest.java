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

    @SuppressWarnings("unchecked")
    public static void main(String[] args) {
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
        plan();
        System.out.println("{\"ok\":true,\"checks\":" + checks + "}");
    }
}
