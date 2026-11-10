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
        System.out.println("{\"ok\":true,\"checks\":" + checks + "}");
    }
}
