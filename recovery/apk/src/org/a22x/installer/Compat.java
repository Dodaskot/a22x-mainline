// SPDX-License-Identifier: MIT
package org.a22x.installer;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.regex.Pattern;

/** Whether this phone is a Galaxy A22 5G, from several independent signs and never from a serial
 *  number. A custom ROM or a GSI renames the model, but keeps the vendor properties, the chip and,
 *  above all, Samsung's bootloader, whose version starts with the model (A226B…, A226BR…); so the
 *  same phone passes with any Android on it, and another phone fails. Plain Java, tested on a
 *  computer (HandoffTest). */
final class Compat {
    enum State { MATCH, NEUTRAL, CONFLICT }

    static final class Check {
        final String name;
        final String found;
        final State state;

        Check(String name, String found, State state) {
            this.name = name;
            this.found = found;
            this.state = state;
        }
    }

    static final class Result {
        final boolean compatible;
        final List<Check> checks;

        Result(boolean compatible, List<Check> checks) {
            this.compatible = compatible;
            this.checks = checks;
        }
    }

    /** Properties read with getprop besides android.os.Build. */
    static final List<String> PROPERTIES = Arrays.asList(
            "ro.product.vendor.model", "ro.product.odm.model", "ro.product.vendor.device", "ro.product.odm.device",
            "ro.product.vendor.name", "ro.boot.bootloader", "ro.bootloader", "ro.board.platform", "ro.hardware",
            "ro.boot.hardware", "ro.vendor.mediatek.platform");

    private static final Pattern SAMSUNG_BOOTLOADER = Pattern.compile("[A-Z][0-9]{3}[A-Z0-9]{5,}");
    private static final Pattern SOC = Pattern.compile("(mt[0-9]{4}[a-z]?|qcom|msm[0-9]{4}|sm[0-9]{4}|exynos[0-9]*|s5e[0-9]+|ums[0-9]+|gs[0-9]{3}|kona|lahaina|taro|kalama)");

    private Compat() {
    }

    private static List<String> values(Map<String, String> facts, String... keys) {
        List<String> found = new ArrayList<>();
        for (String key : keys) {
            String value = facts.get(key);
            if (value != null && !value.trim().isEmpty() && !found.contains(value.trim())) found.add(value.trim());
        }
        return found;
    }

    private static String shown(List<String> values) {
        return values.isEmpty() ? "" : String.join(", ", values);
    }

    /** FACTS: Build.MODEL as "model", Build.DEVICE "device", Build.PRODUCT "product", Build.BOARD
     *  "board", Build.HARDWARE "hardware", Build.BOOTLOADER "bootloader", and the PROPERTIES. */
    static Result judge(Map<String, String> facts) {
        List<String> models = values(facts, "model", "ro.product.vendor.model", "ro.product.odm.model");
        List<String> devices = values(facts, "device", "product", "ro.product.vendor.device", "ro.product.odm.device", "ro.product.vendor.name");
        List<String> loaders = values(facts, "bootloader", "ro.boot.bootloader", "ro.bootloader");
        List<String> chips = values(facts, "board", "hardware", "ro.board.platform", "ro.hardware", "ro.boot.hardware", "ro.vendor.mediatek.platform");

        boolean modelMatch = false, modelOther = false;
        for (String model : models) {
            String upper = model.toUpperCase(Locale.ROOT);
            if (upper.startsWith("SM-A226")) modelMatch = true;
            else if (upper.startsWith("SM-")) modelOther = true;
        }
        boolean deviceMatch = false;
        for (String device : devices) {
            if (device.toLowerCase(Locale.ROOT).startsWith("a22x")) deviceMatch = true;
        }
        boolean loaderMatch = false, loaderOther = false;
        for (String loader : loaders) {
            String upper = loader.toUpperCase(Locale.ROOT);
            if (upper.startsWith("A226")) loaderMatch = true;
            else if (SAMSUNG_BOOTLOADER.matcher(upper).matches()) loaderOther = true;
        }
        boolean chipMatch = false, chipKnown = false;
        for (String chip : chips) {
            String lower = chip.toLowerCase(Locale.ROOT);
            if (lower.equals("mt6833") || lower.equals("mt6833v")) chipMatch = true;
            if (SOC.matcher(lower).matches()) chipKnown = true;
        }

        List<Check> checks = new ArrayList<>();
        // Samsung's bootloader is never replaced by a ROM: its model prefix decides when present.
        checks.add(new Check("bootloader", shown(loaders),
                loaderMatch ? State.MATCH : loaderOther ? State.CONFLICT : State.NEUTRAL));
        checks.add(new Check("model", shown(models),
                modelMatch ? State.MATCH : modelOther && !loaderMatch ? State.CONFLICT : State.NEUTRAL));
        checks.add(new Check("device", shown(devices), deviceMatch ? State.MATCH : State.NEUTRAL));
        checks.add(new Check("chip", shown(chips),
                chipMatch ? State.MATCH : chipKnown ? State.CONFLICT : State.NEUTRAL));
        boolean identified = loaderMatch || modelMatch || deviceMatch;
        boolean conflict = false;
        for (Check check : checks) conflict |= check.state == State.CONFLICT;
        return new Result(identified && !conflict, checks);
    }
}
