// SPDX-License-Identifier: MIT
package org.a22x.installer;

import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.nio.charset.StandardCharsets;
import java.util.concurrent.TimeUnit;

/** Runs a shell script as root through su (Magisk or another root manager asks the user the
 *  first time). The script is given on standard input, never on a command line. */
final class Root {
    static final class Result {
        final int code;
        final String output;

        Result(int code, String output) {
            this.code = code;
            this.output = output;
        }

        boolean ok() {
            return code == 0;
        }

        /** The value after "A22X:KEY " in the output, or null. */
        String marker(String key) {
            for (String line : output.split("\n")) {
                if (line.startsWith("A22X:" + key)) return line.substring(("A22X:" + key).length()).trim();
            }
            return null;
        }

        /** The text between A22X:BEGIN and A22X:END, or null. */
        String block() {
            int start = output.indexOf("A22X:BEGIN\n");
            int end = output.lastIndexOf("A22X:END");
            if (start < 0 || end < start) return null;
            return output.substring(start + "A22X:BEGIN\n".length(), end);
        }
    }

    private Root() {
    }

    static Result run(String script, long timeoutSeconds) {
        Process process = null;
        try {
            process = new ProcessBuilder("su").redirectErrorStream(true).start();
            try (OutputStream in = process.getOutputStream()) {
                in.write(("set -u\n" + script + "\nexit $?\n").getBytes(StandardCharsets.UTF_8));
            }
            ByteArrayOutputStream collected = new ByteArrayOutputStream();
            InputStream out = process.getInputStream();
            byte[] buffer = new byte[8192];
            int n;
            while ((n = out.read(buffer)) > 0) {
                if (collected.size() < (1 << 20)) collected.write(buffer, 0, n);
            }
            if (!process.waitFor(timeoutSeconds, TimeUnit.SECONDS)) {
                process.destroy();
                return new Result(124, "timed out");
            }
            return new Result(process.exitValue(), collected.toString("UTF-8"));
        } catch (Exception e) {
            if (process != null) process.destroy();
            return new Result(127, String.valueOf(e.getMessage()));
        }
    }

    static boolean available() {
        Result r = run("id -u", 60);
        return r.ok() && r.output.trim().endsWith("0");
    }

    /** Single-quote a value for the shell (only paths this app made itself are passed). */
    static String quote(String value) {
        return "'" + value.replace("'", "'\\''") + "'";
    }
}
