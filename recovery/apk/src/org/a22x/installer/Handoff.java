// SPDX-License-Identifier: MIT
package org.a22x.installer;

import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.regex.Pattern;

/** The settings this app hands to the A22x recovery (cache:/a22x-recovery/handoff.json, read once
 *  and deleted by the recovery at its next start; see handoff.py there). Plain Java, no Android
 *  classes, so the rules are tested on a computer. */
final class Handoff {
    static final String SCHEMA = "a22x.handoff.v1";
    static final List<String> LANGUAGES = Arrays.asList("en", "uk", "de", "fr", "pl", "es");
    static final int MAX_WIFI = 8;
    private static final Pattern ZONE = Pattern.compile("[A-Z][A-Za-z_]+(/[A-Za-z0-9_+-]+){1,2}");
    private static final Pattern SCRIPT = Pattern.compile("[A-Za-z0-9][A-Za-z0-9._-]{0,60}\\.sh");

    private Handoff() {
    }

    /** Whether the recovery would take this network: a name of 1-32 bytes and no password (an
     *  open network) or a WPA passphrase of 8-63 characters. */
    static String networkProblem(String ssid, String password) {
        int bytes = ssid == null ? 0 : ssid.getBytes(StandardCharsets.UTF_8).length;
        if (bytes < 1 || bytes > 32) return "ssid";
        if (password == null || password.isEmpty()) return null;
        if (password.length() < 8 || password.length() > 63) return "password";
        for (int i = 0; i < password.length(); i++) {
            char c = password.charAt(i);
            if (c < 0x20 || c > 0x7e) return "password";
        }
        return null;
    }

    static boolean scriptNameAllowed(String name) {
        return name != null && SCRIPT.matcher(name).matches();
    }

    /** The recovery's language for an Android language, or null when the recovery has none. */
    static String language(String androidLanguage) {
        if (androidLanguage == null) return null;
        String code = androidLanguage.toLowerCase(java.util.Locale.ROOT);
        if (code.equals("ua")) code = "uk";
        return LANGUAGES.contains(code) ? code : null;
    }

    static String zone(String id) {
        return id != null && id.length() <= 64 && ZONE.matcher(id).matches() ? id : null;
    }

    /** A new record: what is still waiting in WAITING (networks the recovery has not taken yet),
     *  this phone's language, zone and time, and NEW_SSID/NEW_PASSWORD when a network is added. */
    @SuppressWarnings("unchecked")
    static String merge(String waiting, String androidLanguage, String zoneId, long utcSeconds,
                        String newSsid, String newPassword) {
        List<Object> networks = new ArrayList<>();
        if (waiting != null && !waiting.trim().isEmpty()) {
            try {
                Object old = Json.parse(waiting);
                if (old instanceof Map && SCHEMA.equals(((Map<String, Object>) old).get("schema"))) {
                    Object list = ((Map<String, Object>) old).get("wifi");
                    if (list instanceof List) {
                        for (Object item : (List<Object>) list) {
                            if (item instanceof Map && ((Map<String, Object>) item).get("ssid") instanceof String) {
                                networks.add(item);
                            }
                        }
                    }
                }
            } catch (IllegalArgumentException damaged) {
                // A damaged file is replaced; the recovery would refuse it anyway.
            }
        }
        if (newSsid != null) {
            if (networkProblem(newSsid, newPassword) != null) throw new IllegalArgumentException("network");
            for (int i = networks.size() - 1; i >= 0; i--) {
                if (newSsid.equals(((Map<String, Object>) networks.get(i)).get("ssid"))) networks.remove(i);
            }
            Map<String, Object> network = new LinkedHashMap<>();
            network.put("ssid", newSsid);
            network.put("password", newPassword == null ? "" : newPassword);
            networks.add(0, network);
        }
        while (networks.size() > MAX_WIFI) networks.remove(networks.size() - 1);
        Map<String, Object> record = new LinkedHashMap<>();
        record.put("schema", SCHEMA);
        record.put("source", "android-app");
        if (!networks.isEmpty()) record.put("wifi", networks);
        String language = language(androidLanguage);
        if (language != null) record.put("language", language);
        String zone = zone(zoneId);
        if (zone != null) record.put("timezone", zone);
        if (utcSeconds > 0) record.put("time_utc", utcSeconds);
        return Json.write(record);
    }

    /** How many networks a record carries, without showing them. */
    @SuppressWarnings("unchecked")
    static int networks(String record) {
        try {
            Object value = Json.parse(record);
            Object list = value instanceof Map ? ((Map<String, Object>) value).get("wifi") : null;
            return list instanceof List ? ((List<Object>) list).size() : 0;
        } catch (IllegalArgumentException e) {
            return 0;
        }
    }

    static final String SCRIPT_TEMPLATE =
            "#!/bin/sh\n"
            + "# Runs once as root inside the new system at the end of its setup in the A22x recovery.\n"
            + "# Available: A22X_OS_ID, A22X_OS_LIKE, A22X_PACKAGE_MANAGER, A22X_USER, A22X_DESKTOP,\n"
            + "# and a22x-install PACKAGE... (the system's own package manager).\n"
            + "set -e\n"
            + "a22x-install htop\n";
}
