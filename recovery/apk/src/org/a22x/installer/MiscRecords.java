// SPDX-License-Identifier: MIT
package org.a22x.installer;

import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.regex.Pattern;

/** The recovery's own records in the misc partition (its misc_store.py, since recovery 1.0.1): the
 *  settings and the saved Wi-Fi list, each kept twice in the last 128 KiB of misc, with a generation
 *  and a SHA-256. This app reads that area and writes a record the way the recovery does: both
 *  copies, the one that is not the newest first. So the app and the recovery keep one list of
 *  networks, and the newer entry for a network wins wherever it was made. Plain Java, no Android
 *  classes: the format is checked on a computer against the recovery's (MiscRecordsTest). */
final class MiscRecords {
    static final byte[] MAGIC = "A22XMSC1".getBytes(StandardCharsets.US_ASCII);
    static final int SECTOR = 512;
    static final int SLOT = 32 << 10;
    static final int PAYLOAD_MAX = SLOT - SECTOR;
    /** The area this app reads and writes: misc from 384 KiB to its end at 512 KiB. */
    static final int AREA_START = 384 << 10;
    static final int AREA_SIZE = 128 << 10;
    static final long MISC_SIZE = 512 << 10;
    static final String[] NAMES = {"settings", "wifi"};
    /** wifi.SAVED_MAX in the recovery. */
    static final int SAVED_MAX = 16;
    private static final Pattern SSID_HEX = Pattern.compile("(?:[0-9a-f]{2}){1,32}");
    private static final Pattern HEX64 = Pattern.compile("[0-9A-Fa-f]{64}");

    private MiscRecords() {
    }

    static final class Damaged extends Exception {
        Damaged(String message) {
            super(message);
        }
    }

    /** Another program's bytes in an area that is not the recovery's: never written. */
    static final class Foreign extends Exception {
        Foreign(String message) {
            super(message);
        }
    }

    /** One slot: kind "empty", "valid", "torn", "foreign" or "unreadable". */
    static final class Slot {
        final String kind;
        final long generation;
        final byte[] payload;

        Slot(String kind, long generation, byte[] payload) {
            this.kind = kind;
            this.generation = generation;
            this.payload = payload;
        }
    }

    /** Where the first slot of NAME starts in the area (two slots follow each other). */
    static int offset(String name) {
        if ("settings".equals(name)) return 0;
        if ("wifi".equals(name)) return 2 * SLOT;
        throw new IllegalArgumentException("record");
    }

    private static byte[] label(String name) {
        return Arrays.copyOf(name.getBytes(StandardCharsets.US_ASCII), 8);
    }

    static byte[] sha256(byte[]... parts) {
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            for (byte[] part : parts) digest.update(part);
            return digest.digest();
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
    }

    private static boolean zeros(byte[] data, int from, int to) {
        for (int i = from; i < to; i++) if (data[i] != 0) return false;
        return true;
    }

    /** misc_store.classify: DATA is one slot, or null when it could not be read. */
    static Slot classify(String name, byte[] data) {
        if (data == null) return new Slot("unreadable", 0, null);
        if (zeros(data, 0, data.length)) return new Slot("empty", 0, null);
        if (!Arrays.equals(Arrays.copyOfRange(data, 0, 8), MAGIC)) {
            // A first write cut short in the first bytes of its header: a beginning of the magic only.
            int start = 0;
            while (start < MAGIC.length && data[start] == MAGIC[start]) start++;
            return new Slot(start > 0 && zeros(data, start, data.length) ? "torn" : "foreign", 0, null);
        }
        if (!Arrays.equals(Arrays.copyOfRange(data, 8, 16), label(name))) return new Slot("torn", 0, null);
        ByteBuffer header = ByteBuffer.wrap(data, 16, 16).order(ByteOrder.LITTLE_ENDIAN);
        long generation = header.getLong();
        long length = header.getInt() & 0xffffffffL;
        long flags = header.getInt() & 0xffffffffL;
        if (flags != 0 || length > PAYLOAD_MAX) return new Slot("torn", 0, null);
        byte[] payload = Arrays.copyOfRange(data, SECTOR, SECTOR + (int) length);
        byte[] digest = sha256(Arrays.copyOfRange(data, 0, 32), payload);
        if (!Arrays.equals(Arrays.copyOfRange(data, 32, 64), digest)) return new Slot("torn", 0, null);
        return new Slot("valid", generation, payload);
    }

    /** The two slots of NAME in AREA (AREA_SIZE bytes; null slices are slots that could not be read). */
    static Slot[] slots(byte[] area, String name) {
        Slot[] found = new Slot[2];
        for (int i = 0; i < 2; i++) {
            int from = offset(name) + i * SLOT;
            found[i] = classify(name, area == null ? null : Arrays.copyOfRange(area, from, from + SLOT));
        }
        return found;
    }

    /** misc_store.owned: a copy of the recovery's, whole or cut short, is in the area. */
    static boolean owned(byte[] area) {
        for (String name : NAMES) {
            for (Slot slot : slots(area, name)) if (slot.kind.equals("valid") || slot.kind.equals("torn")) return true;
        }
        return false;
    }

    private static boolean anyForeign(byte[] area) {
        for (String name : NAMES) {
            for (Slot slot : slots(area, name)) if (slot.kind.equals("foreign")) return true;
        }
        return false;
    }

    /** misc_store.decide: the newest whole copy; null when no record was ever whole (or another's
     *  bytes in an area that is not the recovery's); Damaged otherwise. */
    static byte[] decide(Slot[] slots, boolean isOwned) throws Damaged {
        Slot newest = null;
        for (Slot slot : slots) {
            if (slot.kind.equals("valid") && (newest == null || slot.generation > newest.generation)) newest = slot;
        }
        if (newest != null) return newest.payload;
        String a = slots[0].kind, b = slots[1].kind;
        if (a.equals("unreadable") || b.equals("unreadable")) throw new Damaged("unreadable");
        if (a.equals("empty") && (b.equals("empty") || b.equals("torn"))) return null;
        if (b.equals("empty") && a.equals("torn")) return null;
        if (!isOwned && !a.equals("torn") && !b.equals("torn")) return null;
        throw new Damaged("Neither copy of the record is whole");
    }

    /** The record NAME as text, or null when the recovery has none there yet. */
    static String read(byte[] area, String name) throws Damaged {
        byte[] payload = decide(slots(area, name), owned(area));
        return payload == null ? null : new String(payload, StandardCharsets.UTF_8);
    }

    /** One slot write: the slot's offset in misc, and its SLOT bytes (the header sector first). */
    static final class Write {
        final long offset;
        final byte[] slot;

        Write(long offset, byte[] slot) {
            this.offset = offset;
            this.slot = slot;
        }
    }

    static byte[] slotBytes(String name, long generation, byte[] payload) {
        ByteBuffer head = ByteBuffer.allocate(32).order(ByteOrder.LITTLE_ENDIAN);
        head.put(MAGIC).put(label(name)).putLong(generation).putInt(payload.length).putInt(0);
        byte[] header = head.array();
        byte[] slot = new byte[SLOT];
        System.arraycopy(header, 0, slot, 0, 32);
        System.arraycopy(sha256(header, payload), 0, slot, 32, 32);
        System.arraycopy(payload, 0, slot, SECTOR, payload.length);
        return slot;
    }

    /** misc_store.write: PAYLOAD into both slots of NAME, in this order: over the slot that is not the
     *  newest whole copy, then over that one. Foreign when the area is not the recovery's and holds
     *  another's bytes; Damaged when a slot of the record could not be read (never written past). */
    static Write[] plan(byte[] area, String name, byte[] payload) throws Foreign, Damaged {
        if (payload.length > PAYLOAD_MAX) throw new IllegalArgumentException("too large");
        Slot[] slots = slots(area, name);
        if (!owned(area)) {
            if (anyForeign(area)) throw new Foreign("misc holds another program's data");
            throw new Foreign("the recovery has no records in misc yet");
        }
        for (Slot slot : slots) if (slot.kind.equals("unreadable")) throw new Damaged("unreadable");
        int newest = -1;
        for (int i = 0; i < 2; i++) {
            if (slots[i].kind.equals("valid") && (newest < 0 || slots[i].generation > slots[newest].generation)) newest = i;
        }
        long generation = newest < 0 ? 0 : slots[newest].generation;
        int first = newest < 0 ? 0 : 1 - newest;
        long base = AREA_START + offset(name);
        return new Write[]{new Write(base + (long) first * SLOT, slotBytes(name, generation + 1, payload)),
                new Write(base + (long) (1 - first) * SLOT, slotBytes(name, generation + 2, payload))};
    }

    /** AREA after WRITES (to check a plan, or to go on from it). */
    static byte[] applied(byte[] area, Write... writes) {
        byte[] copy = area.clone();
        for (Write w : writes) System.arraycopy(w.slot, 0, copy, (int) (w.offset - AREA_START), SLOT);
        return copy;
    }

    // ------------------------------------------------------------ the records

    /** The settings record with this phone's language and time zone (null leaves one as it is);
     *  everything else in it, the recovery's password above all, is kept as it is. Null when nothing
     *  changes. */
    @SuppressWarnings("unchecked")
    static String settingsWith(String record, String language, String zone) {
        Object parsed = Json.parse(record);
        if (!(parsed instanceof Map)) throw new IllegalArgumentException("settings");
        Map<String, Object> settings = new LinkedHashMap<>((Map<String, Object>) parsed);
        boolean changed = false;
        if (language != null && !language.equals(settings.get("language"))) {
            settings.put("language", language);
            changed = true;
        }
        if (zone != null && !zone.equals(settings.get("timezone"))) {
            settings.put("timezone", zone);
            changed = true;
        }
        return changed ? Json.write(settings) + "\n" : null;
    }

    static String hex(byte[] data) {
        StringBuilder out = new StringBuilder();
        for (byte b : data) out.append(String.format("%02x", b & 0xff));
        return out.toString();
    }

    /** wifi.psk: the 256-bit WPA key of a passphrase (PBKDF2-HMAC-SHA1, the SSID as salt, 4096
     *  rounds); a password of 64 hex digits is a key already; an open network has none. */
    static String psk(byte[] ssid, String password) {
        if (password == null || password.isEmpty()) return "";
        if (HEX64.matcher(password).matches()) return password.toLowerCase(java.util.Locale.ROOT);
        try {
            javax.crypto.Mac mac = javax.crypto.Mac.getInstance("HmacSHA1");
            mac.init(new javax.crypto.spec.SecretKeySpec(password.getBytes(StandardCharsets.UTF_8), "HmacSHA1"));
            byte[] key = new byte[32];
            for (int block = 1, done = 0; done < 32; block++) {
                mac.update(ssid);
                byte[] u = mac.doFinal(new byte[]{0, 0, 0, (byte) block});
                byte[] t = u.clone();
                for (int round = 1; round < 4096; round++) {
                    u = mac.doFinal(u);
                    for (int i = 0; i < t.length; i++) t[i] ^= u[i];
                }
                int n = Math.min(t.length, 32 - done);
                System.arraycopy(t, 0, key, done, n);
                done += n;
            }
            return hex(key);
        } catch (java.security.GeneralSecurityException e) {
            throw new IllegalStateException(e);
        }
    }

    /** The networks of a saved list as the recovery reads it (wifi.networks_of): whole entries only. */
    @SuppressWarnings("unchecked")
    static List<Map<String, Object>> networks(String record) {
        List<Map<String, Object>> whole = new ArrayList<>();
        if (record == null) return whole;
        Object parsed;
        try {
            parsed = Json.parse(record);
        } catch (IllegalArgumentException damaged) {
            return whole;
        }
        Object list = parsed instanceof Map ? ((Map<String, Object>) parsed).get("networks") : null;
        if (!(list instanceof List)) return whole;
        for (Object item : (List<Object>) list) {
            if (!(item instanceof Map)) continue;
            Map<String, Object> network = (Map<String, Object>) item;
            Object ssid = network.get("ssid_hex"), key = network.containsKey("psk") ? network.get("psk") : "";
            if (ssid instanceof String && SSID_HEX.matcher((String) ssid).matches() && key instanceof String) whole.add(network);
        }
        return whole;
    }

    private static long added(Map<String, Object> network) {
        Object value = network.get("added");
        return value instanceof Long ? (Long) value : value instanceof Integer ? (Integer) value : 0;
    }

    /** The saved list with SSID added (its key, ADDED in seconds): one entry per network, and for a
     *  network known already the newer entry wins, wherever it was made (so a password changed in the
     *  recovery is not replaced by an older one from this app). Newest first, SAVED_MAX at most. */
    static String wifiWith(String record, byte[] ssid, String key, long added) {
        String ssidHex = hex(ssid);
        List<Map<String, Object>> networks = networks(record);
        for (Map<String, Object> network : networks) {
            if (ssidHex.equals(network.get("ssid_hex")) && added(network) > added) return null;
        }
        List<Object> kept = new ArrayList<>();
        Map<String, Object> entry = new LinkedHashMap<>();
        entry.put("added", added);
        entry.put("psk", key);
        entry.put("ssid_hex", ssidHex);
        kept.add(entry);
        for (Map<String, Object> network : networks) if (!ssidHex.equals(network.get("ssid_hex"))) kept.add(network);
        kept.sort((x, y) -> Long.compare(added((Map<String, Object>) y), added((Map<String, Object>) x)));
        while (kept.size() > SAVED_MAX) kept.remove(kept.size() - 1);
        Map<String, Object> list = new LinkedHashMap<>();
        list.put("networks", kept);
        list.put("schema", "a22x.wifi.saved.v1");
        return Json.write(list) + "\n";
    }
}
