// SPDX-License-Identifier: MIT
package org.a22x.installer;

import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Map;

/** Runs on a computer: java -cp classes org.a22x.installer.MiscRecordsTest. The format against the
 *  recovery's misc_store.py is checked in its tests/test_apk_misc.py. */
public final class MiscRecordsTest {
    private static int checks;

    private static void check(boolean condition, String what) {
        checks++;
        if (!condition) throw new AssertionError(what);
    }

    private static String rep(String text, int times) {
        StringBuilder out = new StringBuilder();
        for (int i = 0; i < times; i++) out.append(text);
        return out.toString();
    }

    private static byte[] area(String settings, String wifi) {
        byte[] area = new byte[MiscRecords.AREA_SIZE];
        if (settings != null) {
            byte[] p = settings.getBytes(StandardCharsets.UTF_8);
            System.arraycopy(MiscRecords.slotBytes("settings", 1, p), 0, area, 0, MiscRecords.SLOT);
            System.arraycopy(MiscRecords.slotBytes("settings", 2, p), 0, area, MiscRecords.SLOT, MiscRecords.SLOT);
        }
        if (wifi != null) {
            byte[] p = wifi.getBytes(StandardCharsets.UTF_8);
            System.arraycopy(MiscRecords.slotBytes("wifi", 1, p), 0, area, 2 * MiscRecords.SLOT, MiscRecords.SLOT);
            System.arraycopy(MiscRecords.slotBytes("wifi", 2, p), 0, area, 3 * MiscRecords.SLOT, MiscRecords.SLOT);
        }
        return area;
    }

    public static void main(String[] args) throws Exception {
        // IEEE 802.11i, annex H.4: the passphrase "password" for the SSID "IEEE".
        check(MiscRecords.psk("IEEE".getBytes(StandardCharsets.US_ASCII), "password")
                .equals("f42c6fc52df0ebef9ebb4b90b38a5f902e83fe1b135a70e23aed762e9710a12e"), "psk vector");
        check(MiscRecords.psk("x".getBytes(StandardCharsets.US_ASCII), "").isEmpty(), "open network");
        check(MiscRecords.psk("x".getBytes(StandardCharsets.US_ASCII), "AB" + rep("cd", 31)).equals("ab" + rep("cd", 31)), "a key as it is");

        // Never the first records: before the recovery's first start, or with an older recovery.
        byte[] empty = new byte[MiscRecords.AREA_SIZE];
        check(MiscRecords.read(empty, "settings") == null, "no record");
        try {
            MiscRecords.plan(empty, "settings", "{}".getBytes(StandardCharsets.UTF_8));
            check(false, "an empty area is never taken");
        } catch (MiscRecords.Foreign expected) {
            checks++;
        }
        byte[] foreign = empty.clone();
        foreign[100] = 7;
        check(MiscRecords.read(foreign, "settings") == null, "another's bytes are no record");

        // The settings: language and zone only, the password kept.
        String secret = "{\"hash\":\"" + rep("ab", 32) + "\",\"language\":\"en\",\"salt\":\"" + rep("0f", 16) + "\",\"theme\":\"graphite\"}\n";
        byte[] a = area(secret, "{\"networks\":[],\"schema\":\"a22x.wifi.saved.v1\"}\n");
        String changed = MiscRecords.settingsWith(MiscRecords.read(a, "settings"), "uk", "Europe/Kyiv");
        check(changed.contains("\"hash\":\"" + rep("ab", 32)) && changed.contains("\"language\":\"uk\"")
                && changed.contains("\"timezone\":\"Europe/Kyiv\""), "settings changed, password kept");
        check(MiscRecords.settingsWith(changed, "uk", "Europe/Kyiv") == null, "nothing to change");
        MiscRecords.Write[] writes = MiscRecords.plan(a, "settings", changed.getBytes(StandardCharsets.UTF_8));
        check(writes[0].offset == MiscRecords.AREA_START && writes[1].offset == MiscRecords.AREA_START + MiscRecords.SLOT, "older slot first");
        byte[] b = MiscRecords.applied(a, writes);
        check(MiscRecords.read(b, "settings").equals(changed), "read back");
        MiscRecords.Slot[] slots = MiscRecords.slots(b, "settings");
        check(slots[0].generation == 3 && slots[1].generation == 4, "generations go on");
        // A copy damaged: the other decides; both: damaged, never none.
        byte[] c = b.clone();
        c[MiscRecords.SECTOR + 2] ^= 1;
        check(MiscRecords.read(c, "settings").equals(changed), "one copy damaged");
        c[MiscRecords.SLOT + MiscRecords.SECTOR + 2] ^= 1;
        try {
            MiscRecords.read(c, "settings");
            check(false, "both copies damaged");
        } catch (MiscRecords.Damaged expected) {
            checks++;
        }

        // The Wi-Fi list: one entry per network, the newer wins wherever it was made.
        String list = MiscRecords.wifiWith("{\"networks\":[],\"schema\":\"a22x.wifi.saved.v1\"}", "Home".getBytes(StandardCharsets.UTF_8),
                MiscRecords.psk("Home".getBytes(StandardCharsets.UTF_8), "first-pass"), 1000);
        check(MiscRecords.wifiWith(list, "Home".getBytes(StandardCharsets.UTF_8), "00", 900) == null, "an older entry never wins");
        String newer = MiscRecords.wifiWith(list, "Home".getBytes(StandardCharsets.UTF_8),
                MiscRecords.psk("Home".getBytes(StandardCharsets.UTF_8), "second-pass"), 2000);
        List<Map<String, Object>> networks = MiscRecords.networks(newer);
        check(networks.size() == 1 && networks.get(0).get("psk").equals(MiscRecords.psk("Home".getBytes(StandardCharsets.UTF_8), "second-pass")),
                "the newer password replaces the older");
        String many = newer;
        for (int i = 0; i < 20; i++) many = MiscRecords.wifiWith(many, ("Net" + i).getBytes(StandardCharsets.UTF_8), "", 3000 + i);
        check(MiscRecords.networks(many).size() == MiscRecords.SAVED_MAX, "SAVED_MAX at most");
        check(MiscRecords.networks(many).get(0).get("ssid_hex").equals(MiscRecords.hex("Net19".getBytes(StandardCharsets.UTF_8))), "newest first");
        check(MiscRecords.networks("not json").isEmpty() && MiscRecords.networks("{\"networks\":5}").isEmpty(), "a damaged list holds none");

        // The scripts write only the recovery's area, a whole slot on a sector.
        check(Scripts.miscWrite("/f", MiscRecords.AREA_START + MiscRecords.SLOT).contains("seek=832"), "slot sector");
        for (long bad : new long[]{0, MiscRecords.AREA_START - 512, MiscRecords.AREA_START + 1, MiscRecords.MISC_SIZE - 512}) {
            try {
                Scripts.miscWrite("/f", bad);
                check(false, "offset " + bad);
            } catch (IllegalArgumentException expected) {
                checks++;
            }
        }
        check(Scripts.miscRead().contains("skip=384 count=128"), "the area only");
        System.out.println("MiscRecordsTest: " + checks + " checks");
    }
}
