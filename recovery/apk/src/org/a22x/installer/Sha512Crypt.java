// SPDX-License-Identifier: MIT
package org.a22x.installer;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.security.SecureRandom;

/** SHA-512 crypt ($6$), as glibc, chpasswd -e and openssl passwd -6 make it (Ulrich Drepper's
 *  specification). The plan for the recovery carries the account's password only in this form
 *  (2026-11-10). Plain Java, checked against openssl on a computer (HandoffTest). */
final class Sha512Crypt {
    private static final String ALPHABET = "./0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz";
    private static final int ROUNDS = 5000;

    private Sha512Crypt() {
    }

    /** A new hash of PASSWORD with a random salt of 16 characters. */
    static String hash(String password) {
        SecureRandom random = new SecureRandom();
        StringBuilder salt = new StringBuilder();
        for (int i = 0; i < 16; i++) salt.append(ALPHABET.charAt(random.nextInt(ALPHABET.length())));
        return crypt(password, salt.toString());
    }

    /** The hash of PASSWORD with SALT (at most 16 characters of it are used), 5000 rounds. */
    static String crypt(String password, String salt) {
        try {
            byte[] key = password.getBytes(StandardCharsets.UTF_8);
            String saltText = salt.length() > 16 ? salt.substring(0, 16) : salt;
            byte[] s = saltText.getBytes(StandardCharsets.UTF_8);
            MessageDigest md = MessageDigest.getInstance("SHA-512");
            md.update(key);
            md.update(s);
            md.update(key);
            byte[] b = md.digest();
            md.reset();
            md.update(key);
            md.update(s);
            int left = key.length;
            for (; left > 64; left -= 64) md.update(b, 0, 64);
            md.update(b, 0, left);
            for (int n = key.length; n > 0; n >>= 1) {
                if ((n & 1) != 0) md.update(b, 0, 64);
                else md.update(key);
            }
            byte[] a = md.digest();
            md.reset();
            for (int i = 0; i < key.length; i++) md.update(key);
            byte[] dp = md.digest();
            byte[] p = repeat(dp, key.length);
            md.reset();
            for (int i = 0; i < 16 + (a[0] & 0xff); i++) md.update(s);
            byte[] ds = md.digest();
            byte[] sb = repeat(ds, s.length);
            byte[] c = a;
            for (int i = 0; i < ROUNDS; i++) {
                md.reset();
                if ((i & 1) != 0) md.update(p);
                else md.update(c, 0, 64);
                if (i % 3 != 0) md.update(sb);
                if (i % 7 != 0) md.update(p);
                if ((i & 1) != 0) md.update(c, 0, 64);
                else md.update(p);
                c = md.digest();
            }
            StringBuilder out = new StringBuilder("$6$").append(saltText).append('$');
            int[][] order = {{0, 21, 42}, {22, 43, 1}, {44, 2, 23}, {3, 24, 45}, {25, 46, 4}, {47, 5, 26}, {6, 27, 48},
                {28, 49, 7}, {50, 8, 29}, {9, 30, 51}, {31, 52, 10}, {53, 11, 32}, {12, 33, 54}, {34, 55, 13}, {56, 14, 35},
                {15, 36, 57}, {37, 58, 16}, {59, 17, 38}, {18, 39, 60}, {40, 61, 19}, {62, 20, 41}};
            for (int[] t : order) encode(out, c[t[0]], c[t[1]], c[t[2]], 4);
            encode(out, (byte) 0, (byte) 0, c[63], 2);
            return out.toString();
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
    }

    private static byte[] repeat(byte[] digest, int length) {
        byte[] out = new byte[length];
        for (int i = 0; i < length; i++) out[i] = digest[i % 64];
        return out;
    }

    private static void encode(StringBuilder out, byte b2, byte b1, byte b0, int count) {
        int w = ((b2 & 0xff) << 16) | ((b1 & 0xff) << 8) | (b0 & 0xff);
        for (int i = 0; i < count; i++) {
            out.append(ALPHABET.charAt(w & 0x3f));
            w >>= 6;
        }
    }
}
