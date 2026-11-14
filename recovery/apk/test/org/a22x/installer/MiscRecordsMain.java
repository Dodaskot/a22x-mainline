// SPDX-License-Identifier: MIT
package org.a22x.installer;

import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Paths;

/** What the app does to the recovery's area of misc, on a file, for tests/test_apk_misc.py:
 *    AREA_IN AREA_OUT LANGUAGE ZONE [SSID PASSWORD ADDED]
 *  prints the outcome of each record: written, unchanged, or the reason it was left alone. */
public final class MiscRecordsMain {
    public static void main(String[] args) throws Exception {
        byte[] area = Files.readAllBytes(Paths.get(args[0]));
        String language = args[2].isEmpty() ? null : args[2], zone = args[3].isEmpty() ? null : args[3];
        try {
            String settings = MiscRecords.read(area, "settings");
            String changed = settings == null ? null : MiscRecords.settingsWith(settings, language, zone);
            if (settings == null) System.out.println("settings: none");
            else if (changed == null) System.out.println("settings: unchanged");
            else {
                area = MiscRecords.applied(area, MiscRecords.plan(area, "settings", changed.getBytes(StandardCharsets.UTF_8)));
                System.out.println("settings: written");
            }
        } catch (MiscRecords.Damaged | MiscRecords.Foreign e) {
            System.out.println("settings: " + e.getClass().getSimpleName());
        }
        if (args.length > 4) {
            try {
                String list = MiscRecords.read(area, "wifi");
                if (list == null) System.out.println("wifi: none");
                else {
                    byte[] ssid = args[4].getBytes(StandardCharsets.UTF_8);
                    String next = MiscRecords.wifiWith(list, ssid, MiscRecords.psk(ssid, args[5]), Long.parseLong(args[6]));
                    if (next == null) System.out.println("wifi: kept");
                    else {
                        area = MiscRecords.applied(area, MiscRecords.plan(area, "wifi", next.getBytes(StandardCharsets.UTF_8)));
                        System.out.println("wifi: written");
                    }
                }
            } catch (MiscRecords.Damaged | MiscRecords.Foreign e) {
                System.out.println("wifi: " + e.getClass().getSimpleName());
            }
        }
        Files.write(Paths.get(args[1]), area);
    }
}
