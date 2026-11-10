// SPDX-License-Identifier: MIT
package org.a22x.installer;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.ComponentName;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.graphics.Typeface;
import android.graphics.drawable.GradientDrawable;
import android.os.Build;
import android.os.Bundle;
import android.text.InputType;
import android.util.TypedValue;
import android.view.Gravity;
import android.view.View;
import android.view.Window;
import android.widget.Button;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;

import java.io.BufferedReader;
import java.io.File;
import java.io.InputStreamReader;
import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.TimeZone;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** One page in the recovery's look: a compatibility check, installing the recovery and/or the
 *  BOOT inside this APK, the hand-over of Wi-Fi, language and time, setup scripts, the theme, and
 *  a restart into the recovery. */
public class MainActivity extends Activity {
    static final String PREFS = "a22x";
    static final String KEY_SYNC = "sync";
    static final String KEY_ROOT_GRANTED = "root";
    static final String KEY_THEME = "theme";
    static final String[] THEMES = {"graphite", "daylight", "aurora"};
    private static final String BACKUPS = "/sdcard/A22xRecovery/backups";

    /** The recovery's Graphite, Daylight and Aurora themes (ui/theme.h there). */
    private static final class Palette {
        final int bg, panel, text, muted, accent, onAccent, container, heroTop, heroBottom, heroText, heroSub, green, red;
        final boolean dark;

        Palette(String theme) {
            dark = !theme.equals("daylight");
            if (theme.equals("graphite")) {
                bg = 0xff0e1013; panel = 0xff191c21; text = 0xfff4f5f7; muted = 0xffa0a8b5; accent = 0xffff9a4b;
                onAccent = 0xff2b1605; container = 0xff3b2617; heroTop = 0xff3b2617; heroBottom = 0xff3b2617;
                heroText = 0xfff4f5f7; heroSub = 0xffd8c3b4; green = 0xff79d7ae; red = 0xffff8b8b;
            } else if (theme.equals("aurora")) {
                bg = 0xff080f1f; panel = 0xff1f283d; text = 0xfff0f6fc; muted = 0xffa9b9ca; accent = 0xff4ae6d6;
                onAccent = 0xff001c1d; container = 0xff005455; heroTop = 0xff008781; heroBottom = 0xff373ba7;
                heroText = 0xffffffff; heroSub = 0xffc9f2ee; green = 0xff6ce5b5; red = 0xfffd8a8c;
            } else {
                bg = 0xfff7f4fd; panel = 0xfffefdff; text = 0xff1c1c2e; muted = 0xff58586e; accent = 0xff4a41e4;
                onAccent = 0xffffffff; container = 0xffdde4ff; heroTop = 0xff6f4ef5; heroBottom = 0xff283dde;
                heroText = 0xffffffff; heroSub = 0xffe4e1ff; green = 0xff007948; red = 0xffc51d28;
            }
        }
    }

    private Palette p;
    private String theme;
    private LinearLayout page;
    private TextView log;
    private Compat.Result compat;

    static String theme(Context context) {
        String saved = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE).getString(KEY_THEME, "daylight");
        for (String known : THEMES) if (known.equals(saved)) return saved;
        return "daylight";
    }

    private static Class<?> launcher(String theme) {
        return theme.equals("graphite") ? Launcher.Graphite.class : theme.equals("aurora") ? Launcher.Aurora.class : Launcher.Daylight.class;
    }

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        theme = theme(this);
        p = new Palette(theme);
        Window window = getWindow();
        window.getDecorView().setBackgroundColor(p.bg);
        try {  // API 21 methods, by reflection: the stubs this is compiled against are older.
            Window.class.getMethod("setStatusBarColor", int.class).invoke(window, p.bg);
            Window.class.getMethod("setNavigationBarColor", int.class).invoke(window, p.bg);
            window.getDecorView().setSystemUiVisibility(p.dark ? 0 : 0x2000 | 0x10);
        } catch (Exception ignored) {
            // Older systems keep their own bar colours.
        }
        compat = Compat.judge(facts());
        ScrollView scroll = new ScrollView(this);
        page = new LinearLayout(this);
        page.setOrientation(LinearLayout.VERTICAL);
        page.setPadding(dp(16), dp(24), dp(16), dp(32));
        scroll.addView(page);
        setContentView(scroll);
        build();
    }

    /** android.os.Build and getprop: no root, no serial number. */
    private static Map<String, String> facts() {
        Map<String, String> facts = new HashMap<>();
        facts.put("model", Build.MODEL);
        facts.put("device", Build.DEVICE);
        facts.put("product", Build.PRODUCT);
        facts.put("board", Build.BOARD);
        facts.put("hardware", Build.HARDWARE);
        facts.put("bootloader", Build.BOOTLOADER);
        try {
            Process process = new ProcessBuilder("getprop").redirectErrorStream(true).start();
            Pattern line = Pattern.compile("\\[([^\\]]+)\\]: \\[([^\\]]*)\\]");
            try (BufferedReader in = new BufferedReader(new InputStreamReader(process.getInputStream(), "UTF-8"))) {
                String text;
                while ((text = in.readLine()) != null) {
                    Matcher m = line.matcher(text);
                    if (m.matches() && Compat.PROPERTIES.contains(m.group(1))) facts.put(m.group(1), m.group(2));
                }
            }
            process.waitFor();
        } catch (Exception ignored) {
            // android.os.Build alone still identifies the phone.
        }
        return facts;
    }

    private int dp(float value) {
        return (int) TypedValue.applyDimension(TypedValue.COMPLEX_UNIT_DIP, value, getResources().getDisplayMetrics());
    }

    private TextView label(CharSequence text, float sp, int color, boolean bold) {
        TextView view = new TextView(this);
        view.setText(text);
        view.setTextSize(TypedValue.COMPLEX_UNIT_SP, sp);
        view.setTextColor(color);
        if (bold) view.setTypeface(Typeface.create("sans-serif-medium", Typeface.NORMAL));
        return view;
    }

    private GradientDrawable shape(int color, float radius) {
        GradientDrawable shape = new GradientDrawable();
        shape.setColor(color);
        shape.setCornerRadius(dp(radius));
        return shape;
    }

    private LinearLayout card(String title, String text) {
        LinearLayout card = new LinearLayout(this);
        card.setOrientation(LinearLayout.VERTICAL);
        card.setPadding(dp(20), dp(18), dp(20), dp(18));
        card.setBackground(shape(p.panel, 26));
        card.addView(label(title, 20, p.text, true));
        if (text != null) {
            TextView body = label(text, 14, p.muted, false);
            body.setPadding(0, dp(6), 0, dp(4));
            card.addView(body);
        }
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        lp.topMargin = dp(14);
        page.addView(card, lp);
        return card;
    }

    private Button button(LinearLayout parent, String text, boolean primary, View.OnClickListener click) {
        Button button = new Button(this);
        button.setText(text);
        button.setAllCaps(false);
        button.setTextSize(TypedValue.COMPLEX_UNIT_SP, 16);
        button.setTextColor(primary ? p.onAccent : p.text);
        button.setBackground(shape(primary ? p.accent : p.container, 28));
        button.setOnClickListener(click);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, dp(52));
        lp.topMargin = dp(12);
        parent.addView(button, lp);
        return button;
    }

    private EditText field(LinearLayout parent, String hint, int type) {
        EditText field = new EditText(this);
        field.setHint(hint);
        field.setInputType(type);
        field.setTextColor(p.text);
        field.setHintTextColor(p.muted);
        field.setBackground(shape(p.bg, 16));
        field.setPadding(dp(14), dp(10), dp(14), dp(10));
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT);
        lp.topMargin = dp(10);
        parent.addView(field, lp);
        return field;
    }

    private CheckBox checkbox(LinearLayout parent, String text, int color, boolean checked) {
        CheckBox box = new CheckBox(this);
        box.setText(text);
        box.setTextColor(color);
        box.setTextSize(TypedValue.COMPLEX_UNIT_SP, 15);
        box.setChecked(checked);
        parent.addView(box);
        return box;
    }

    private SharedPreferences prefs() {
        return getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    private void say(final String line) {
        runOnUiThread(() -> {
            log.append(line + "\n");
            log.setVisibility(View.VISIBLE);
        });
    }

    /** Run WORK off the interface thread after root was granted; BUTTON is disabled meanwhile. */
    private void background(final Button button, final Runnable work) {
        button.setEnabled(false);
        new Thread(() -> {
            try {
                if (!Root.available()) {
                    say(getString(R.string.root_missing));
                    return;
                }
                prefs().edit().putBoolean(KEY_ROOT_GRANTED, true).apply();
                work.run();
            } catch (Exception e) {
                say(getString(R.string.failed, String.valueOf(e.getMessage())));
            } finally {
                runOnUiThread(() -> button.setEnabled(true));
            }
        }).start();
    }

    private void build() {
        page.addView(label(getString(R.string.app_name), 32, p.text, true));
        page.addView(label(getString(R.string.subtitle), 15, p.muted, false));
        addCompat();
        addInstall();
        addWifi();
        addSync();
        addScript();
        addTheme();
        LinearLayout restart = card(getString(R.string.restart_title), null);
        button(restart, getString(R.string.restart), false, v -> background((Button) v, () -> {
            Root.Result r = RecoveryStore.restartIntoRecovery();
            if (!r.ok()) say(getString(R.string.failed, r.output.trim()));
        }));
        log = label("", 13, p.muted, false);
        log.setTypeface(Typeface.MONOSPACE);
        log.setVisibility(View.GONE);
        log.setPadding(dp(4), dp(16), dp(4), 0);
        page.addView(log);
    }

    private void addCompat() {
        LinearLayout card = card(getString(R.string.compat_title), getString(R.string.compat_note));
        for (Compat.Check check : compat.checks) {
            int name = check.name.equals("bootloader") ? R.string.compat_bootloader : check.name.equals("model") ? R.string.compat_model
                    : check.name.equals("device") ? R.string.compat_device : R.string.compat_chip;
            String mark = check.state == Compat.State.MATCH ? "✓" : check.state == Compat.State.CONFLICT ? "✗" : "–";
            int color = check.state == Compat.State.MATCH ? p.green : check.state == Compat.State.CONFLICT ? p.red : p.muted;
            TextView line = label(mark + "  " + getString(name) + ": " + (check.found.isEmpty() ? getString(R.string.compat_unknown) : check.found),
                    14, color, false);
            line.setPadding(0, dp(4), 0, 0);
            card.addView(line);
        }
        TextView verdict = label(getString(compat.compatible ? R.string.compat_ok : R.string.compat_bad), 16,
                compat.compatible ? p.green : p.red, true);
        verdict.setPadding(0, dp(10), 0, 0);
        card.addView(verdict);
        button(card, getString(R.string.check_partitions), false, v -> background((Button) v, () -> {
            Root.Result r = Root.run(Scripts.PARTITIONS, 60);
            for (String text : r.output.split("\n")) {
                String[] parts = text.trim().split(" ");
                if (parts.length != 3 || !parts[0].equals("A22X:PART")) continue;
                try {
                    long size = Long.parseLong(parts[2]);
                    Payload.Image image = Payload.image(this, parts[1]);
                    say((size >= image.size ? "✓ " : "✗ ") + getString(R.string.partition_line, parts[1],
                            size == 0 ? getString(R.string.partition_missing) : mib(size), mib(image.size)));
                } catch (Exception e) {
                    say(getString(R.string.failed, e.getMessage()));
                }
            }
        }));
    }

    private static String mib(long bytes) {
        return String.format(Locale.ROOT, "%d MiB", bytes >> 20);
    }

    private void addInstall() {
        final Payload.Image recovery, boot;
        try {
            recovery = Payload.image(this, "recovery");
            boot = Payload.image(this, "boot");
        } catch (Exception e) {
            card(getString(R.string.install_title), getString(R.string.failed, e.getMessage()));
            return;
        }
        LinearLayout card = card(getString(R.string.install_title), getString(R.string.install_text));
        GradientDrawable gradient = new GradientDrawable(GradientDrawable.Orientation.TL_BR, new int[]{p.heroTop, p.heroBottom});
        gradient.setCornerRadius(dp(26));
        card.setBackground(gradient);
        ((TextView) card.getChildAt(0)).setTextColor(p.heroText);
        ((TextView) card.getChildAt(1)).setTextColor(p.heroSub);
        final CheckBox withRecovery = checkbox(card, getString(R.string.option_recovery, recovery.version, mib(recovery.size)), p.heroText, true);
        final CheckBox withBoot = checkbox(card, getString(R.string.option_boot, boot.version, mib(boot.size)), p.heroText, false);
        final TextView warning = label(getString(R.string.boot_warning), 13, p.heroSub, false);
        warning.setVisibility(View.GONE);
        card.addView(warning);
        TextView sha = label(getString(R.string.sha, recovery.sha256.substring(0, 12)) + " · " + getString(R.string.sha, boot.sha256.substring(0, 12)),
                12, p.heroSub, false);
        sha.setPadding(0, dp(8), 0, 0);
        card.addView(sha);
        final Button install = button(card, getString(R.string.install), true, null);
        install.setBackground(shape(p.dark && theme.equals("graphite") ? p.accent : 0xffffffff, 28));
        install.setTextColor(theme.equals("graphite") ? p.onAccent : p.heroBottom);
        Runnable refresh = () -> {
            warning.setVisibility(withBoot.isChecked() ? View.VISIBLE : View.GONE);
            install.setEnabled(compat.compatible && (withRecovery.isChecked() || withBoot.isChecked()));
            install.setAlpha(install.isEnabled() ? 1f : 0.5f);
        };
        withRecovery.setOnCheckedChangeListener((b, on) -> refresh.run());
        withBoot.setOnCheckedChangeListener((b, on) -> refresh.run());
        refresh.run();
        install.setOnClickListener(v -> {
            final List<Payload.Image> chosen = new ArrayList<>();
            if (withRecovery.isChecked()) chosen.add(recovery);
            if (withBoot.isChecked()) chosen.add(boot);
            StringBuilder message = new StringBuilder();
            for (Payload.Image image : chosen) {
                message.append(getString(image.label.equals("recovery") ? R.string.confirm_recovery : R.string.confirm_boot, image.version)).append("\n\n");
            }
            message.append(getString(R.string.confirm_backup, BACKUPS));
            new AlertDialog.Builder(this)
                    .setTitle(R.string.confirm_title)
                    .setMessage(message.toString())
                    .setNegativeButton(R.string.cancel, null)
                    .setPositiveButton(R.string.install, (d, w) -> background(install, () -> {
                        for (Payload.Image image : chosen) {
                            say(getString(R.string.working) + " " + image.label);
                            try {
                                File file = Payload.extract(this, image);
                                Root.Result r = RecoveryStore.flash(image.label, file, image.size, image.sha256, BACKUPS);
                                String saved = r.marker("OK");
                                if (!r.ok() || saved == null) {
                                    say(getString(R.string.failed, r.marker("ERROR") != null ? r.marker("ERROR") : r.output.trim()));
                                    return;  // Nothing more is written after a failure.
                                }
                                say(getString(R.string.done_flash, image.label, saved));
                            } catch (Exception e) {
                                say(getString(R.string.failed, e.getMessage()));
                                return;
                            }
                        }
                    }))
                    .show();
        });
    }

    private void addWifi() {
        LinearLayout card = card(getString(R.string.wifi_title), getString(R.string.wifi_text));
        final EditText ssid = field(card, getString(R.string.ssid_hint), InputType.TYPE_CLASS_TEXT);
        final EditText password = field(card, getString(R.string.password_hint),
                InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        button(card, getString(R.string.save_to_recovery), true, v -> {
            final String name = ssid.getText().toString();
            final String key = password.getText().toString();
            String problem = Handoff.networkProblem(name, key);
            if (problem != null) {
                say(getString("ssid".equals(problem) ? R.string.bad_ssid : R.string.bad_password));
                return;
            }
            background((Button) v, () -> {
                try {
                    Root.Result r = RecoveryStore.sendHandoff(this, name, key);
                    if (r.marker("OK") != null) {
                        runOnUiThread(() -> password.setText(""));
                        say(getString(R.string.wifi_saved, Handoff.networks(RecoveryStore.waitingHandoff())));
                    } else {
                        say(getString(R.string.failed, r.output.trim()));
                    }
                } catch (Exception e) {
                    say(getString(R.string.failed, e.getMessage()));
                }
            });
        });
    }

    private void addSync() {
        Locale locale = Locale.getDefault();
        LinearLayout card = card(getString(R.string.sync_title),
                getString(R.string.sync_text, locale.getDisplayLanguage(), TimeZone.getDefault().getID()));
        if (Handoff.language(locale.getLanguage()) == null) {
            card.addView(label(getString(R.string.sync_unsupported, locale.getDisplayLanguage()), 13, p.muted, false));
        }
        CheckBox sync = checkbox(card, getString(R.string.sync_switch), p.text, prefs().getBoolean(KEY_SYNC, true));
        sync.setOnCheckedChangeListener((b, on) -> prefs().edit().putBoolean(KEY_SYNC, on).apply());
        button(card, getString(R.string.sync_now), false, v -> background((Button) v, () -> {
            try {
                Root.Result r = RecoveryStore.sendHandoff(this, null, null);
                say(r.marker("OK") != null ? getString(R.string.sync_sent) : getString(R.string.failed, r.output.trim()));
            } catch (Exception e) {
                say(getString(R.string.failed, e.getMessage()));
            }
        }));
    }

    private void addScript() {
        LinearLayout card = card(getString(R.string.script_title), getString(R.string.script_text));
        final EditText name = field(card, getString(R.string.script_name_hint), InputType.TYPE_CLASS_TEXT);
        name.setText("my-setup.sh");
        final EditText text = field(card, "", InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_MULTI_LINE);
        text.setText(Handoff.SCRIPT_TEMPLATE);
        text.setTypeface(Typeface.MONOSPACE);
        text.setTextSize(TypedValue.COMPLEX_UNIT_SP, 13);
        text.setGravity(Gravity.TOP | Gravity.START);
        text.setMinLines(8);
        button(card, getString(R.string.save_to_recovery), true, v -> {
            final String file = name.getText().toString().trim();
            if (!Handoff.scriptNameAllowed(file)) {
                say(getString(R.string.bad_name));
                return;
            }
            final String body = text.getText().toString();
            background((Button) v, () -> {
                try {
                    Root.Result r = RecoveryStore.saveScript(this, file, body);
                    say(r.marker("OK") != null ? getString(R.string.script_saved, file) : getString(R.string.failed, r.output.trim()));
                } catch (Exception e) {
                    say(getString(R.string.failed, e.getMessage()));
                }
            });
        });
        button(card, getString(R.string.show_scripts), false, v -> background((Button) v, () -> {
            List<String> names = RecoveryStore.scripts();
            say(names.isEmpty() ? getString(R.string.scripts_none) : getString(R.string.scripts_list, String.join(", ", names)));
        }));
    }

    /** The theme: this page's colours, the launcher icon and the splash screen follow it. */
    private void addTheme() {
        LinearLayout card = card(getString(R.string.theme_title), getString(R.string.theme_text));
        int[] names = {R.string.theme_graphite, R.string.theme_daylight, R.string.theme_aurora};
        for (int i = 0; i < THEMES.length; i++) {
            final String choice = THEMES[i];
            Button button = button(card, getString(names[i]), choice.equals(theme), v -> {
                if (choice.equals(theme)) return;
                prefs().edit().putString(KEY_THEME, choice).apply();
                PackageManager manager = getPackageManager();
                for (String other : THEMES) {  // The new entry first, so one is always enabled.
                    if (other.equals(choice)) manager.setComponentEnabledSetting(new ComponentName(this, launcher(other)),
                            PackageManager.COMPONENT_ENABLED_STATE_ENABLED, PackageManager.DONT_KILL_APP);
                }
                for (String other : THEMES) {
                    if (!other.equals(choice)) manager.setComponentEnabledSetting(new ComponentName(this, launcher(other)),
                            PackageManager.COMPONENT_ENABLED_STATE_DISABLED, PackageManager.DONT_KILL_APP);
                }
                startActivity(new Intent(this, launcher(choice)).addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_CLEAR_TASK));
                finish();
            });
            Palette preview = new Palette(choice);
            if (!choice.equals(theme)) {
                button.setBackground(shape(preview.bg, 28));
                button.setTextColor(preview.text);
            }
        }
    }
}
