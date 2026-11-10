// SPDX-License-Identifier: MIT
package org.a22x.installer;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.regex.Pattern;

/** An installation prepared in this app (2026-11-10): a22x-recovery/install-plan.json on the cache
 *  volume. After a restart the recovery shows it, fills its install pages with it and asks for
 *  the usual hold on Install; its install_plan.py checks everything again. The password leaves
 *  this app only as a SHA-512 crypt hash. Plain Java, tested on a computer (HandoffTest). */
final class Plan {
    static final String SCHEMA = "a22x.install-plan.v1";
    static final String FILE = "install-plan.json";
    /** The recovery's own choices (distro.py there), in its order. */
    static final List<String> DESKTOPS = Arrays.asList("phosh", "gnome", "plasma", "xfce", "sxmo", "lomiri", "console", "image");
    static final List<String> SHELLS = Arrays.asList("phosh", "gnome", "plasma", "xfce", "sxmo", "lomiri");
    static final List<String> GREETERS = Arrays.asList("auto", "phrog", "text", "gdm", "sddm", "lightdm");
    static final List<String> APPS = Arrays.asList("browser", "settings", "files", "terminal", "editor", "store", "keyboard",
            "telegram", "ssh", "email", "music", "video", "photos", "documents", "office", "calculator", "clock", "weather",
            "maps", "camera", "contacts", "calendar", "passwords", "flatpak", "chromium", "podcasts", "messages", "git", "monitor");
    static final List<String> LOCALES = Arrays.asList("en_US", "uk_UA", "de_DE", "fr_FR", "pl_PL", "es_ES", "it_IT", "pt_BR",
            "nl_NL", "cs_CZ", "ro_RO", "tr_TR", "ru_RU");
    private static final Pattern USER = Pattern.compile("[a-z_][a-z0-9_-]{0,31}");

    /** What is chosen on the page. */
    static final class Choice {
        String catalog;
        boolean erase = true;
        String desktop = "phosh";
        List<String> shells = new ArrayList<>();
        String signIn = "auto";
        List<String> apps = new ArrayList<>();
        String username = "user";
        String password = "";
        String locale;
        String timezone;
        String script;
    }

    private Plan() {
    }

    /** The system language for an Android language ("uk" → uk_UA), or null. */
    static String locale(String androidLanguage) {
        if (androidLanguage == null) return null;
        String code = androidLanguage.toLowerCase(java.util.Locale.ROOT);
        if (code.equals("ua")) code = "uk";
        for (String locale : LOCALES) if (locale.startsWith(code + "_")) return locale;
        return null;
    }

    /** Why CHOICE cannot be used, as the name of a string resource, or null. */
    static String problem(Choice c, Set<String> catalog) {
        if (c.catalog == null || !catalog.contains(c.catalog)) return "plan_bad_system";
        if (!DESKTOPS.contains(c.desktop)) return "plan_bad_desktop";
        for (String shell : c.shells) if (!SHELLS.contains(shell)) return "plan_bad_desktop";
        if (!c.shells.isEmpty() && !SHELLS.contains(c.desktop)) return "plan_shells_need_desktop";
        if (!GREETERS.contains(c.signIn)) return "plan_bad_sign_in";
        if (!c.signIn.equals("auto") && !SHELLS.contains(c.desktop)) return "plan_sign_in_needs_desktop";
        for (String app : c.apps) if (!APPS.contains(app)) return "plan_bad_app";
        if (c.erase || !c.username.isEmpty()) {
            if (!USER.matcher(c.username).matches()) return "plan_bad_user";
            if (c.password.isEmpty() || c.password.length() > 128 || c.password.indexOf(':') >= 0) return "plan_bad_password";
            for (int i = 0; i < c.password.length(); i++) if (c.password.charAt(i) < 0x20) return "plan_bad_password";
        }
        if (c.locale != null && !LOCALES.contains(c.locale)) return "plan_bad_locale";
        if (c.timezone != null && Handoff.zone(c.timezone) == null) return "plan_bad_zone";
        if (c.script != null && !Handoff.scriptNameAllowed(c.script)) return "bad_name";
        return null;
    }

    /** The file the recovery reads. PASSWORD_HASH: Sha512Crypt of the password, null without an account. */
    static String json(Choice c, String passwordHash, String source, long now) {
        Map<String, Object> plan = new LinkedHashMap<>();
        plan.put("schema", SCHEMA);
        plan.put("source", source.length() > 64 ? source.substring(0, 64) : source);
        plan.put("created_utc", now);
        Map<String, Object> system = new LinkedHashMap<>();
        system.put("catalog", c.catalog);
        plan.put("system", system);
        plan.put("erase_userdata", c.erase);
        plan.put("desktop", c.desktop);
        List<Object> shells = new ArrayList<>();
        for (String shell : SHELLS) if (c.shells.contains(shell) && !shell.equals(c.desktop)) shells.add(shell);
        plan.put("shells", shells);
        plan.put("sign_in", c.signIn);
        List<Object> apps = new ArrayList<>();
        for (String app : APPS) if (c.apps.contains(app)) apps.add(app);
        plan.put("apps", apps);
        if (passwordHash != null) {
            Map<String, Object> account = new LinkedHashMap<>();
            account.put("username", c.username);
            account.put("password_sha512", passwordHash);
            plan.put("account", account);
        }
        if (c.locale != null) plan.put("locale", c.locale);
        if (c.timezone != null) plan.put("timezone", c.timezone);
        if (c.script != null) plan.put("script", c.script);
        return Json.write(plan);
    }

    /** One image of the recovery's catalog, as its install pages show it. */
    static final class Image {
        final String id, name, version, family, desktop;
        final long size;
        final boolean minimal;

        Image(String id, String name, String version, String family, String desktop, long size, boolean minimal) {
            this.id = id;
            this.name = name;
            this.version = version;
            this.family = family;
            this.desktop = desktop;
            this.size = size;
            this.minimal = minimal;
        }

        /** Sxmo and Lomiri are packaged by Debian and Ubuntu only, as on the recovery's pages. */
        boolean debianLike() {
            return family.equals("debian") || family.equals("ubuntu") || family.equals("mobian");
        }
    }

    /** Every image of assets/catalog.json with an id and a name, in its order. */
    @SuppressWarnings("unchecked")
    static List<Image> images(String text) {
        List<Image> found = new ArrayList<>();
        Object parsed = Json.parse(text);
        if (!(parsed instanceof Map)) return found;
        Object list = ((Map<String, Object>) parsed).get("images");
        if (!(list instanceof List)) return found;
        for (Object item : (List<Object>) list) {
            if (!(item instanceof Map)) continue;
            Map<String, Object> m = (Map<String, Object>) item;
            if (!(m.get("id") instanceof String) || !(m.get("name") instanceof String)) continue;
            Object size = m.get("size");
            found.add(new Image((String) m.get("id"), (String) m.get("name"), m.get("version") instanceof String ? (String) m.get("version") : "",
                    m.get("family") instanceof String ? (String) m.get("family") : "", m.get("desktop") instanceof String ? (String) m.get("desktop") : "",
                    size instanceof Number ? ((Number) size).longValue() : 0L, Boolean.TRUE.equals(m.get("minimal"))));
        }
        return found;
    }

    /** The images of the recovery's catalog (assets/catalog.json): id → "name version". */
    @SuppressWarnings("unchecked")
    static Map<String, String> catalog(String text) {
        Map<String, String> found = new LinkedHashMap<>();
        Object parsed = Json.parse(text);
        if (!(parsed instanceof Map)) return found;
        Object images = ((Map<String, Object>) parsed).get("images");
        if (!(images instanceof List)) return found;
        for (Object item : (List<Object>) images) {
            if (!(item instanceof Map)) continue;
            Map<String, Object> image = (Map<String, Object>) item;
            if (!(image.get("id") instanceof String) || !(image.get("name") instanceof String)) continue;
            String version = image.get("version") instanceof String ? " " + image.get("version") : "";
            found.put((String) image.get("id"), image.get("name") + version);
        }
        return found;
    }
}
