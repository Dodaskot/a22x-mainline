// SPDX-License-Identifier: MIT
package org.a22x.installer;

import android.animation.Animator;
import android.animation.AnimatorListenerAdapter;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.ComponentName;
import android.content.Context;
import android.content.SharedPreferences;
import android.content.pm.PackageManager;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Typeface;
import android.graphics.drawable.BitmapDrawable;
import android.graphics.drawable.ColorDrawable;
import android.graphics.drawable.Drawable;
import android.graphics.drawable.GradientDrawable;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.text.InputType;
import android.text.TextUtils;
import android.util.DisplayMetrics;
import android.util.TypedValue;
import android.view.Display;
import android.view.Gravity;
import android.view.MotionEvent;
import android.view.View;
import android.view.ViewGroup;
import android.view.Window;
import android.view.animation.DecelerateInterpolator;
import android.widget.EditText;
import android.widget.FrameLayout;
import android.widget.ImageView;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.Space;
import android.widget.TextView;

import java.io.BufferedReader;
import java.io.File;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.text.SimpleDateFormat;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Date;
import java.util.HashMap;
import java.util.HashSet;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.TimeZone;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

/** The app in the recovery's look (2026-11-10, the person's wish): a home page of tiles, each
 *  opening a page of its own with a way back, the recovery's own icons and colours, and the
 *  install pages of the recovery for preparing an installation. What only the recovery can do
 *  (installing a system, flashing partitions, backups) is not here; the app installs the recovery
 *  and BOOT, hands Wi-Fi, language, time and scripts over, and prepares the plan. */
public class MainActivity extends Activity {
    static final String PREFS = "a22x";
    static final String KEY_SYNC = "sync";
    static final String KEY_ROOT_GRANTED = "root";
    static final String KEY_THEME = "theme";
    static final String[] THEMES = {"graphite", "daylight", "aurora"};
    private static final String BACKUPS = "/sdcard/A22xRecovery/backups";

    /** The recovery's Graphite, Daylight and Aurora themes (ui/theme.h there). */
    static final class Palette {
        final int bg, bgTop, bgBottom, panel, panel2, text, muted, accent, onAccent, iconFill, green, red, heroTop, heroBottom, heroText, heroSub,
                surfaceAlpha;
        /** The edge with the theme's edge alpha, the shadow with its strength as alpha, the backdrop's drawable (0: plain bg). */
        final int edge, shadow, backdrop;
        final int[] tileFill, tileInk;
        final boolean dark;

        Palette(String theme) {
            dark = !theme.equals("daylight");
            if (theme.equals("graphite")) {
                bg = 0xff0e1013; bgTop = bg; bgBottom = bg; panel = 0xff191c21; panel2 = 0xff282c34; text = 0xfff4f5f7; muted = 0xffa0a8b5;
                accent = 0xffff9a4b; onAccent = 0xff2b1605; iconFill = 0xff302219; green = 0xff79d7ae; red = 0xffff8b8b;
                heroTop = 0xff3b2617; heroBottom = 0xff3b2617; heroText = 0xfff4f5f7; heroSub = 0xffd8c3b4; surfaceAlpha = 255;
                edge = 0xff24282f; shadow = 0; backdrop = 0;
                tileFill = new int[]{0xff302219, 0xff302219, 0xff302219, 0xff302219, 0xff302219, 0xff302219};
                tileInk = new int[]{0xffff9a4b, 0xffff9a4b, 0xffff9a4b, 0xffff9a4b, 0xffff9a4b, 0xffff9a4b};
            } else if (theme.equals("aurora")) {
                bg = 0xff080f1f; bgTop = 0xff0d2236; bgBottom = 0xff151a3c; panel = 0xff1f283d; panel2 = 0xff2b3e57; text = 0xfff0f6fc; muted = 0xffa9b9ca;
                accent = 0xff4ae6d6; onAccent = 0xff001c1d; iconFill = 0xff004d51; green = 0xff6ce5b5; red = 0xfffd8a8c;
                heroTop = 0xff008781; heroBottom = 0xff373ba7; heroText = 0xffffffff; heroSub = 0xffc9f2ee; surfaceAlpha = 150;
                edge = 70 << 24 | 0x5b7088; shadow = 90 << 24; backdrop = R.drawable.backdrop_aurora;
                tileFill = new int[]{0xff23436c, 0xff5b4315, 0xff134e39, 0xff4e3468, 0xff0d4b51, 0xff703138};
                tileInk = new int[]{0xffa6d1ff, 0xfff5cf7d, 0xff7deabd, 0xffdbb8ff, 0xff79e5eb, 0xffffadb3};
            } else {
                bg = 0xfff7f4fd; bgTop = 0xfffbe7dd; bgBottom = 0xffe5ecff; panel = 0xfffefdff; panel2 = 0xffd6dcff; text = 0xff1c1c2e; muted = 0xff58586e;
                accent = 0xff4a41e4; onAccent = 0xffffffff; iconFill = 0xffdbe3ff; green = 0xff007948; red = 0xffc51d28;
                heroTop = 0xff6f4ef5; heroBottom = 0xff283dde; heroText = 0xffffffff; heroSub = 0xffe4e1ff; surfaceAlpha = 176;
                edge = 150 << 24 | 0xffffff; shadow = 46 << 24 | 0x3a2f7a; backdrop = R.drawable.backdrop_daylight;
                tileFill = new int[]{0xffcde7ff, 0xffffe4b3, 0xffbdf3da, 0xffeedbff, 0xffbaf0f6, 0xffffd6d9};
                tileInk = new int[]{0xff085dc7, 0xffa76100, 0xff007852, 0xff7a31ca, 0xff00717f, 0xffc81f40};
            }
        }

        int surface() {
            return (surfaceAlpha << 24) | (panel & 0xffffff);
        }
    }

    private interface Page {
        void build();
    }

    private Palette p;
    private String theme;
    private FrameLayout root;
    private View current;
    private LinearLayout content;
    private TextView toast;
    private final Handler handler = new Handler(Looper.getMainLooper());
    private final Runnable hideToast = () -> toast.animate().alpha(0f).setDuration(220).start();
    private final ArrayList<Page> pages = new ArrayList<>();
    private Compat.Result compat;
    /** Root work running in the background: the launcher entry is not switched meanwhile. */
    private int busy;
    // The plan being prepared on the install pages.
    private final Plan.Choice choice = new Plan.Choice();
    private List<Plan.Image> images = new ArrayList<>();
    private Plan.Image image;
    private String password = "", again = "";
    private boolean withRecovery = true, withBoot = false;
    // The date of the home page, as the recovery's heading shows it. No clock: Android's own
    // status bar shows the time (the person's wish, 2026-11-10).
    private TextView dateLine;
    private final Runnable tick = new Runnable() {
        @Override
        public void run() {
            // The phone's own settings: its language, its date order and its time zone.
            if (dateLine != null) dateLine.setText(dateText(new Date()));
            handler.removeCallbacks(this);
            handler.postDelayed(this, 60000 - System.currentTimeMillis() % 60000 + 50);
        }
    };

    /** The weekday, the day and the month, in the order and the language of the phone's system. */
    static String dateText(Date now) {
        Locale locale = Locale.getDefault();
        String pattern = "EEEE, d MMMM";
        try {  // API 18, by reflection: the stubs this is compiled against are older.
            pattern = (String) android.text.format.DateFormat.class.getMethod("getBestDateTimePattern", Locale.class, String.class)
                    .invoke(null, locale, "EEEEdMMMM");
        } catch (Exception ignored) {
            // The recovery's own order, above.
        }
        String text = new SimpleDateFormat(pattern, locale).format(now);
        return text.isEmpty() ? text : text.substring(0, 1).toUpperCase(locale) + text.substring(1);
    }

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
        windowColors();
        compat = Compat.judge(facts());
        images = loadImages();
        choice.locale = Plan.locale(Locale.getDefault().getLanguage());
        choice.timezone = TimeZone.getDefault().getID();
        choice.apps.add("terminal");
        choice.apps.add("settings");
        root = new FrameLayout(this);
        toast = new TextView(this);
        toast.setAlpha(0f);
        FrameLayout.LayoutParams lp = new FrameLayout.LayoutParams(ViewGroup.LayoutParams.WRAP_CONTENT, ViewGroup.LayoutParams.WRAP_CONTENT,
                Gravity.BOTTOM | Gravity.CENTER_HORIZONTAL);
        lp.bottomMargin = dp(36);
        root.addView(toast, lp);
        setContentView(root);
        open(this::home);
    }

    // ---------------------------------------------------------------- pages and their animation

    private void open(Page page) {
        pages.add(page);
        show(1, null);
    }

    private void back() {
        if (pages.size() > 1) {
            pages.remove(pages.size() - 1);
            show(-1, null);
        } else {
            finish();
        }
    }

    @Override
    public void onBackPressed() {
        back();
    }

    /** Build the top page; DIRECTION 1 forward, -1 back, 0 in place. FROM: grow it from there as a circle. */
    private void show(int direction, View from) {
        final ScrollView scroll = new ScrollView(this);
        scroll.setVerticalScrollBarEnabled(false);
        scroll.setFillViewport(true);
        content = new LinearLayout(this);
        content.setOrientation(LinearLayout.VERTICAL);
        content.setClipChildren(false);  // The cards' shadows reach past their edges.
        content.setPadding(dp(16), dp(14), dp(16), dp(20));
        scroll.addView(content);
        pages.get(pages.size() - 1).build();
        final View old = current;
        current = scroll;
        root.addView(scroll, root.getChildCount() - 1, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT,
                ViewGroup.LayoutParams.MATCH_PARENT));
        if (from != null && old != null) {
            int[] at = new int[2], base = new int[2];
            from.getLocationInWindow(at);
            root.getLocationInWindow(base);
            final int cx = at[0] - base[0] + from.getWidth() / 2, cy = at[1] - base[1] + from.getHeight() / 2;
            final int y = old.getScrollY();
            scroll.setVisibility(View.INVISIBLE);
            scroll.post(() -> {
                scroll.scrollTo(0, y);
                // The new theme's page covers the old one with its own copy of the new backdrop.
                int[] origin = new int[2];
                scroll.getLocationInWindow(origin);
                scroll.setBackground(pageBackdrop(origin[1]));
                float radius = (float) Math.hypot(Math.max(cx, root.getWidth() - cx), Math.max(cy, root.getHeight() - cy));
                try {  // API 21, by reflection: the stubs this is compiled against are older.
                    Animator reveal = (Animator) Class.forName("android.view.ViewAnimationUtils")
                            .getMethod("createCircularReveal", View.class, int.class, int.class, float.class, float.class)
                            .invoke(null, scroll, cx, cy, 0f, radius);
                    reveal.setDuration(520);
                    reveal.setInterpolator(new DecelerateInterpolator(1.6f));
                    reveal.addListener(new AnimatorListenerAdapter() {
                        @Override
                        public void onAnimationEnd(Animator animation) {
                            root.removeView(old);
                            windowColors();
                            scroll.setBackground(null);
                        }
                    });
                    scroll.setVisibility(View.VISIBLE);
                    reveal.start();
                } catch (Exception e) {
                    scroll.setVisibility(View.VISIBLE);
                    root.removeView(old);
                    windowColors();
                    scroll.setBackground(null);
                }
            });
            return;
        }
        if (old == null || direction == 0) {
            if (old != null) root.removeView(old);
            return;
        }
        // The recovery's page change: the new page zooms in, the old one fades.
        scroll.setAlpha(0f);
        scroll.setScaleX(direction > 0 ? 0.94f : 1.04f);
        scroll.setScaleY(direction > 0 ? 0.94f : 1.04f);
        scroll.animate().alpha(1f).scaleX(1f).scaleY(1f).setDuration(240).setInterpolator(new DecelerateInterpolator()).start();
        old.animate().alpha(0f).setDuration(200).withEndAction(() -> root.removeView(old)).start();
    }

    private void refresh() {
        final int y = current != null ? current.getScrollY() : 0;
        show(0, null);
        final View now = current;
        now.post(() -> now.scrollTo(0, y));
    }

    /** The theme's backdrop behind the pages and behind Android's own bars, which are made
     *  transparent: the whole screen flows as the recovery's does. */
    private void windowColors() {
        Window window = getWindow();
        Bitmap backdrop = backdrop();
        window.setBackgroundDrawable(backdrop != null ? new BitmapDrawable(getResources(), backdrop) : new ColorDrawable(p.bg));
        try {
            Window.class.getMethod("setStatusBarColor", int.class).invoke(window, 0);
            Window.class.getMethod("setNavigationBarColor", int.class).invoke(window, 0);
            window.getDecorView().setSystemUiVisibility(p.dark ? 0 : 0x2000 | 0x10);
        } catch (Exception ignored) {
            // Older systems keep their own bar colours.
        }
        try {  // API 29: no grey veil of the system's own over the transparent bars.
            Window.class.getMethod("setNavigationBarContrastEnforced", boolean.class).invoke(window, false);
            Window.class.getMethod("setStatusBarContrastEnforced", boolean.class).invoke(window, false);
        } catch (Exception ignored) {
            // Older systems have none.
        }
    }

    /** The theme's backdrop at the window's size, or null for Graphite's plain colour. */
    private Bitmap backdrop() {
        if (p.backdrop == 0) return null;
        View decor = getWindow().getDecorView();
        int width = decor.getWidth(), height = decor.getHeight();
        if (width <= 0 || height <= 0) {
            DisplayMetrics metrics = new DisplayMetrics();
            try {  // API 17, by reflection: the whole screen, Android's bars included.
                Display.class.getMethod("getRealMetrics", DisplayMetrics.class).invoke(getWindowManager().getDefaultDisplay(), metrics);
            } catch (Exception e) {
                metrics = getResources().getDisplayMetrics();
            }
            width = metrics.widthPixels;
            height = metrics.heightPixels;
        }
        try {
            return Backdrop.get(getResources(), p.backdrop, width, height);
        } catch (RuntimeException | OutOfMemoryError e) {
            return null;  // The plain colour then.
        }
    }

    /** The page's own copy of the backdrop during a theme change, OFFSET pixels below the window's top. */
    private Drawable pageBackdrop(int offset) {
        Bitmap backdrop = backdrop();
        return backdrop != null ? new Backdrop.Shifted(backdrop, offset) : new ColorDrawable(p.bg);
    }

    /** The recovery's surface: its lighter edge under a fill that lets the backdrop through. */
    private Card glass(float radius, boolean pressable) {
        Card card = new Card(dp(radius), p.surface()).edge(p.edge, edgeWidth());
        return pressable ? card.pressedVeil(18) : card;
    }

    /** The recovery's two pixels of edge on its 1080-pixel-wide canvas. */
    private float edgeWidth() {
        return Math.max(1f, getResources().getDisplayMetrics().widthPixels * 2f / 1080f);
    }

    /** A theme's small backdrop as it is, for its card on the theme page. */
    private Bitmap backdropSource(int id) {
        BitmapFactory.Options options = new BitmapFactory.Options();
        options.inScaled = false;
        return BitmapFactory.decodeResource(getResources(), id, options);
    }

    // ---------------------------------------------------------------- the recovery's building blocks

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

    /** One line, ending in "…" when it does not fit (setSingleLine alone cuts the text off). */
    private static void single(TextView view) {
        view.setSingleLine(true);
        view.setEllipsize(TextUtils.TruncateAt.END);
    }

    private GradientDrawable shape(int color, float radius) {
        GradientDrawable shape = new GradientDrawable();
        shape.setColor(color);
        shape.setCornerRadius(dp(radius));
        return shape;
    }

    private ImageView icon(String name, int tint, int size) {
        ImageView view = new ImageView(this);
        int id = getResources().getIdentifier("ic_" + name, "drawable", getPackageName());
        if (id != 0) view.setImageResource(id);
        view.setColorFilter(tint);
        view.setScaleType(ImageView.ScaleType.FIT_CENTER);
        view.setLayoutParams(new LinearLayout.LayoutParams(dp(size), dp(size)));
        return view;
    }

    /** An icon centred in a filled circle, as the recovery's icon tiles. */
    private FrameLayout bubble(String name, int fill, int ink, int size) {
        FrameLayout box = new FrameLayout(this);
        GradientDrawable circle = new GradientDrawable();
        circle.setShape(GradientDrawable.OVAL);
        circle.setColor(fill);
        box.setBackground(circle);
        ImageView glyph = icon(name, ink, size * 5 / 8);
        box.addView(glyph, new FrameLayout.LayoutParams(dp(size * 5 / 8), dp(size * 5 / 8), Gravity.CENTER));
        box.setLayoutParams(new LinearLayout.LayoutParams(dp(size), dp(size)));
        return box;
    }

    /** The recovery's touch: a tile sinks a little and darkens while pressed. */
    private void press(View view, Runnable click) {
        view.setClickable(true);
        view.setOnTouchListener((v, e) -> {
            if (!v.isEnabled()) return false;
            if (e.getActionMasked() == MotionEvent.ACTION_DOWN) {
                v.animate().scaleX(0.97f).scaleY(0.97f).alpha(v.getBackground() instanceof Card ? 1f : 0.86f).setDuration(90).start();
            } else if (e.getActionMasked() == MotionEvent.ACTION_UP || e.getActionMasked() == MotionEvent.ACTION_CANCEL) {
                v.animate().scaleX(1f).scaleY(1f).alpha(v.isEnabled() ? 1f : 0.45f).setDuration(140).start();
            }
            return false;
        });
        view.setOnClickListener(v -> click.run());
    }

    private void heading(String title, String subtitle) {
        LinearLayout row = new LinearLayout(this);
        row.setGravity(Gravity.CENTER_VERTICAL);
        boolean backable = pages.size() > 1;
        if (backable) {
            FrameLayout circle = bubble("back", (0xb0 << 24) | (p.panel2 & 0xffffff), p.accent, 40);
            press(circle, this::back);
            row.addView(circle);
        }
        TextView name = label(title, 26, p.text, true);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1);
        lp.leftMargin = backable ? dp(12) : 0;
        row.addView(name, lp);
        content.addView(row);
        if (subtitle != null) {
            TextView sub = label(subtitle, 13.5f, p.muted, false);
            sub.setPadding(backable ? dp(52) : 0, dp(4), 0, dp(6));
            content.addView(sub);
        }
        space(10);
    }

    private void space(int height) {
        content.addView(new View(this), new LinearLayout.LayoutParams(1, dp(height)));
    }

    /** A rounded panel with the recovery's small accent caption. */
    private LinearLayout panel(String caption) {
        LinearLayout box = new LinearLayout(this);
        box.setOrientation(LinearLayout.VERTICAL);
        box.setBackground(glass(18, false));
        box.setPadding(dp(16), dp(14), dp(16), dp(16));
        if (caption != null) {
            TextView caps = label(caption.toUpperCase(Locale.getDefault()), 12, p.accent, true);
            caps.setPadding(0, 0, 0, dp(8));
            box.addView(caps);
        }
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.bottomMargin = dp(12);
        content.addView(box, lp);
        return box;
    }

    private TextView button(ViewGroup parent, String text, boolean primary, Runnable click) {
        TextView view = label(text, 16, primary ? p.onAccent : p.text, true);
        view.setGravity(Gravity.CENTER);
        view.setBackground(shape(primary ? p.accent : p.panel2, 28));
        // A long label (Ukrainian, German) takes a second line instead of touching the edges.
        view.setMinHeight(dp(54));
        view.setPadding(dp(18), dp(10), dp(18), dp(10));
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.topMargin = dp(10);
        parent.addView(view, lp);
        press(view, click);
        return view;
    }

    private void enable(View view, boolean on) {
        view.setEnabled(on);
        view.setAlpha(on ? 1f : 0.45f);
    }

    private HoldButton hold(ViewGroup parent, String text, long ms, boolean enabled, Runnable action) {
        HoldButton button = new HoldButton(this, text, ms, p.accent, p.onAccent, p.panel2, p.muted, action);
        button.setEnabled(enabled);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(60));
        lp.topMargin = dp(14);
        parent.addView(button, lp);
        return button;
    }

    /** A selectable pill, as the recovery's choice buttons. */
    private TextView chip(String text, boolean on, boolean enabled, Runnable click) {
        TextView view = label(text, 14.5f, on ? p.onAccent : p.text, true);
        view.setGravity(Gravity.CENTER);
        single(view);
        view.setBackground(shape(on ? p.accent : p.panel2, 24));
        view.setPadding(dp(6), 0, dp(6), 0);
        press(view, click);
        enable(view, enabled);
        return view;
    }

    /** CHIPS in rows of COLUMNS, as the recovery lays them out. */
    private void chips(LinearLayout parent, List<TextView> list, int columns) {
        LinearLayout row = null;
        for (int i = 0; i < list.size(); i++) {
            if (i % columns == 0) {
                row = new LinearLayout(this);
                LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(46));
                lp.topMargin = i == 0 ? 0 : dp(8);
                parent.addView(row, lp);
            }
            LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.MATCH_PARENT, 1);
            lp.leftMargin = i % columns == 0 ? 0 : dp(8);
            row.addView(list.get(i), lp);
        }
        if (row != null && list.size() % columns != 0) {
            for (int i = list.size() % columns; i < columns; i++) {
                LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.MATCH_PARENT, 1);
                lp.leftMargin = dp(8);
                row.addView(new View(this), lp);
            }
        }
    }

    private EditText field(ViewGroup parent, String hint, int type, String value) {
        EditText field = new EditText(this);
        field.setHint(hint);
        field.setInputType(type);
        field.setTypeface(Typeface.DEFAULT);  // A password type switches to monospace otherwise.
        field.setTextSize(TypedValue.COMPLEX_UNIT_SP, 16);
        field.setTextColor(p.text);
        field.setHintTextColor(p.muted);
        field.setBackground(shape(p.dark ? p.bg : 0xfff4f2fb, 14));
        field.setPadding(dp(14), dp(10), dp(14), dp(10));
        if (value != null) field.setText(value);
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.topMargin = dp(8);
        parent.addView(field, lp);
        return field;
    }

    /** A row with an icon tile, a title and a note, and a chevron: the recovery's list rows. */
    private LinearLayout row(ViewGroup parent, String iconName, int tone, String title, String note, String trailing, Runnable click) {
        LinearLayout row = new LinearLayout(this);
        row.setGravity(Gravity.CENTER_VERTICAL);
        row.setBackground(glass(20, click != null));
        row.setPadding(dp(14), dp(12), dp(12), dp(12));
        row.addView(bubble(iconName, p.tileFill[tone], p.tileInk[tone], 44));
        LinearLayout texts = new LinearLayout(this);
        texts.setOrientation(LinearLayout.VERTICAL);
        texts.setPadding(dp(14), 0, dp(8), 0);
        texts.addView(label(title, 17, p.text, true));
        if (note != null) {
            TextView sub = label(note, 13, p.muted, false);
            single(sub);
            texts.addView(sub);
        }
        row.addView(texts, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1));
        if (trailing != null) row.addView(label(trailing, 13, p.muted, false));
        if (click != null) {
            row.addView(icon("chevron", p.muted, 22));
            press(row, click);
        }
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT);
        lp.bottomMargin = dp(10);
        parent.addView(row, lp);
        return row;
    }

    private SharedPreferences prefs() {
        return getSharedPreferences(PREFS, Context.MODE_PRIVATE);
    }

    /** A short message at the bottom, as the recovery's toasts. */
    private void say(final String line) {
        runOnUiThread(() -> {
            toast.setText(line);
            toast.setTextColor(p.text);
            toast.setTextSize(TypedValue.COMPLEX_UNIT_SP, 14);
            toast.setBackground(shape(p.dark ? p.panel2 : p.panel, 22));
            toast.setPadding(dp(18), dp(12), dp(18), dp(12));
            toast.setMaxWidth(getResources().getDisplayMetrics().widthPixels - dp(48));
            toast.bringToFront();
            toast.animate().alpha(1f).setDuration(160).start();
            handler.removeCallbacks(hideToast);
            handler.postDelayed(hideToast, line.length() > 80 ? 6000 : 3500);
        });
    }

    /** Run WORK off the interface thread after root was granted. */
    private void background(final Runnable work) {
        busy++;
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
                runOnUiThread(() -> busy--);
            }
        }).start();
    }

    // ---------------------------------------------------------------- the home page

    private void home() {
        // The top line: this phone on the right, which opens the compatibility page. No clock and
        // no "Mainline Recovery" (the person's wish, 2026-11-10): Android's bar shows the time.
        LinearLayout top = new LinearLayout(this);
        top.setGravity(Gravity.CENTER_VERTICAL);
        top.addView(new Space(this), new LinearLayout.LayoutParams(0, 1, 1));
        LinearLayout device = new LinearLayout(this);
        device.setGravity(Gravity.CENTER_VERTICAL);
        device.setBackground(glass(20, true));
        device.setPadding(dp(10), dp(6), dp(8), dp(6));
        device.addView(icon("phone", p.accent, 18));
        TextView model = label(Build.MODEL, 13, p.text, false);
        model.setPadding(dp(6), 0, dp(4), 0);
        single(model);
        model.setMaxWidth(dp(140));
        device.addView(model);
        device.addView(icon("chevron", p.muted, 14));
        press(device, () -> open(this::compatPage));
        top.addView(device);
        content.addView(top);
        space(10);
        content.addView(label(getString(R.string.app_name), 30, p.text, true));
        dateLine = label("", 14, p.muted, false);
        content.addView(dateLine);
        space(14);

        // The hero: what the app is mostly for, prepared here and installed by the recovery.
        FrameLayout hero = new FrameLayout(this);
        // The recovery's hero: top to bottom in the theme's two colours, its two soft white discs
        // where the recovery puts them, a shadow in the lower colour, a dark veil while pressed.
        Card heroFill = new Card(dp(22), p.heroTop).gradient(p.heroTop, p.heroBottom)
                .veil(0.870f, 0.467f, 0.151f, 24).veil(0.771f, 0.820f, 0.105f, 20).pressedVeil(30);
        if (p.backdrop != 0) heroFill.shadow(110 << 24 | (p.heroBottom & 0xffffff), dp(18), dp(6));
        hero.setBackground(heroFill);
        // The icon on top, the title and the line under it at the bottom; a taller screen gives
        // the room between them, a larger font never cuts the text off.
        LinearLayout heroText = new LinearLayout(this);
        heroText.setOrientation(LinearLayout.VERTICAL);
        heroText.setPadding(dp(18), dp(16), dp(52), dp(16));
        heroText.setMinimumHeight(dp(140));
        heroText.addView(bubble("install", 0x2effffff, p.heroText, 42));
        heroText.addView(new Space(this), new LinearLayout.LayoutParams(1, 0, 1));
        TextView heroTitle = label(getString(R.string.home_install), 26, p.heroText, true);
        heroTitle.setPadding(0, dp(10), 0, 0);
        heroText.addView(heroTitle);
        heroText.addView(label(getString(R.string.home_install_sub), 13.5f, p.heroSub, false));
        hero.addView(heroText, new FrameLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT));
        ImageView chevron = icon("chevron", p.heroText, 28);
        FrameLayout.LayoutParams chevronLp = new FrameLayout.LayoutParams(dp(28), dp(28), Gravity.END | Gravity.CENTER_VERTICAL);
        chevronLp.rightMargin = dp(16);
        hero.addView(chevron, chevronLp);
        press(hero, () -> open(this::planSystem));
        flex(hero, 1.4f);

        Payload.Image recoveryImage = payload("recovery");
        flex(row(bento(getString(R.string.home_flash), recoveryImage != null ? recoveryImage.version : "—", "restore", 0, true, 116,
                        () -> open(this::installPage)),
                bento(getString(R.string.compat_title), getString(compat.compatible ? R.string.home_compat_ok : R.string.home_compat_bad), "verify", 1,
                        true, 116, () -> open(this::compatPage))), 1.2f);
        flex(row(bento(getString(R.string.home_wifi), getString(R.string.home_wifi_sub), "wifi", 2, false, 104, () -> open(this::wifiPage)),
                bento(getString(R.string.home_sync), getString(R.string.home_sync_sub), "language", 3, false, 104, () -> open(this::syncPage))), 1f);
        int[] names = {R.string.theme_graphite, R.string.theme_daylight, R.string.theme_aurora};
        LinearLayout cells = row(cell(getString(R.string.home_scripts), getString(R.string.home_scripts_sub), "terminal", 3, () -> open(this::scriptsPage)),
                cell(getString(R.string.theme_title), getString(names[Arrays.asList(THEMES).indexOf(theme)]), "theme", 1, () -> open(this::themePage)),
                cell(getString(R.string.home_restart), getString(R.string.home_restart_sub), "power", 5, () -> open(this::restartPage)));
        flex(cells, 0.6f);
        ((LinearLayout.LayoutParams) cells.getLayoutParams()).bottomMargin = 0;
        tick.run();
    }

    /** A row of the home page that takes a share of a taller screen's free height (WEIGHT); on a
     *  screen too short for the page it keeps its own height and the page scrolls. */
    private void flex(View view, float weight) {
        LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT, weight);
        lp.bottomMargin = dp(12);
        content.addView(view, lp);
    }

    /** Tiles of the same width and height side by side, also when one title takes two lines. */
    private LinearLayout row(View... tiles) {
        LinearLayout row = new LinearLayout(this);
        row.setClipChildren(false);
        for (int i = 0; i < tiles.length; i++) {
            LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.MATCH_PARENT, 1);
            lp.leftMargin = i == 0 ? 0 : dp(tiles.length > 2 ? 10 : 12);
            row.addView(tiles[i], lp);
        }
        return row;
    }

    /** A bento tile: a tinted icon on top, the title and a note at the bottom. */
    private View bento(String title, String note, String iconName, int tone, boolean tonal, int minHeight, Runnable click) {
        LinearLayout tile = new LinearLayout(this);
        tile.setOrientation(LinearLayout.VERTICAL);
        tile.setBackground((tonal ? new Card(dp(18), p.tileFill[tone]).pressedVeil(18) : glass(18, true)).shadow(p.shadow, dp(14), dp(4)));
        tile.setPadding(dp(14), dp(14), dp(14), dp(14));
        tile.setMinimumHeight(dp(minHeight));
        tile.addView(bubble(iconName, tonal ? p.surface() : p.tileFill[tone], p.tileInk[tone], 42));
        // Space, not View: a plain View asked to wrap its content takes all the height offered.
        tile.addView(new Space(this), new LinearLayout.LayoutParams(1, 0, 1));
        TextView name = label(title, 18, p.text, true);
        name.setPadding(0, dp(10), 0, 0);
        tile.addView(name);
        TextView sub = label(note, 12.5f, tonal ? p.text : p.muted, false);
        single(sub);
        tile.addView(sub);
        press(tile, click);
        return tile;
    }

    /** A small cell of the home grid. */
    private View cell(String title, String note, String iconName, int tone, Runnable click) {
        LinearLayout tile = new LinearLayout(this);
        tile.setOrientation(LinearLayout.VERTICAL);
        tile.setBackground(glass(18, true).shadow(p.shadow, dp(14), dp(4)));
        tile.setPadding(dp(12), dp(12), dp(10), dp(12));
        tile.setMinimumHeight(dp(96));
        tile.addView(bubble(iconName, p.tileFill[tone], p.tileInk[tone], 34));
        tile.addView(new Space(this), new LinearLayout.LayoutParams(1, 0, 1));
        TextView name = label(title, 15, p.text, true);
        name.setPadding(0, dp(8), 0, 0);
        single(name);
        tile.addView(name);
        TextView sub = label(note, 12, p.muted, false);
        single(sub);
        tile.addView(sub);
        press(tile, click);
        return tile;
    }

    // ---------------------------------------------------------------- installing the recovery and BOOT

    private Payload.Image payload(String label) {
        try {
            return Payload.image(this, label);
        } catch (Exception e) {
            return null;
        }
    }

    private void installPage() {
        heading(getString(R.string.home_flash), getString(R.string.install_text));
        final Payload.Image recovery = payload("recovery"), boot = payload("boot");
        if (recovery == null || boot == null) {
            panel(null).addView(label(getString(R.string.failed, "assets"), 14, p.red, false));
            return;
        }
        option(getString(R.string.option_recovery, recovery.version, mib(recovery.size)), getString(R.string.sha, recovery.sha256.substring(0, 12)),
                "restore", withRecovery, () -> {
                    withRecovery = !withRecovery;
                    refresh();
                });
        option(getString(R.string.option_boot, boot.version, mib(boot.size)), getString(R.string.sha, boot.sha256.substring(0, 12)), "install",
                withBoot, () -> {
                    withBoot = !withBoot;
                    refresh();
                });
        if (withBoot) content.addView(label(getString(R.string.boot_warning), 13.5f, p.muted, false));
        if (!compat.compatible) content.addView(label(getString(R.string.not_this_phone), 13.5f, p.red, true));
        TextView note = label(getString(R.string.confirm_backup, BACKUPS), 13, p.muted, false);
        note.setPadding(0, dp(8), 0, 0);
        content.addView(note);
        hold(content, getString(R.string.hold_install), 3000, compat.compatible && (withRecovery || withBoot), () -> {
            final List<Payload.Image> chosen = new ArrayList<>();
            if (withRecovery) chosen.add(recovery);
            if (withBoot) chosen.add(boot);
            background(() -> {
                for (Payload.Image item : chosen) {
                    say(getString(R.string.working) + " " + item.label);
                    try {
                        File file = Payload.extract(this, item);
                        // The Linux BOOT goes to the recovery, which writes it when it installs Linux
                        // (0.6.178): flashed from here, Android would not start again.
                        boolean kept = item.label.equals("boot");
                        Root.Result r = kept ? RecoveryStore.keepLinuxBoot(file, item.size, item.sha256, item.release)
                                : RecoveryStore.flash(item.label, file, item.size, item.sha256, BACKUPS);
                        String saved = r.marker("OK");
                        if (!r.ok() || saved == null) {
                            say(getString(R.string.failed, r.marker("ERROR") != null ? r.marker("ERROR") : r.output.trim()));
                            return;  // Nothing more is written after a failure.
                        }
                        say(kept ? getString(R.string.done_keep) : getString(R.string.done_flash, item.label, saved));
                    } catch (Exception e) {
                        say(getString(R.string.failed, e.getMessage()));
                        return;
                    }
                }
            });
        });
    }

    /** A card chosen or not, with the recovery's round mark. */
    private void option(String title, String note, String iconName, boolean on, Runnable click) {
        LinearLayout card = row(content, iconName, on ? 0 : 3, title, note, null, null);
        card.setBackground(new Card(dp(20), on ? ((0x30 << 24) | (p.accent & 0xffffff)) : p.surface()).edge(p.edge, edgeWidth()).pressedVeil(18));
        View mark = new View(this);
        GradientDrawable ring = new GradientDrawable();
        ring.setShape(GradientDrawable.OVAL);
        ring.setColor(on ? p.accent : 0);
        ring.setStroke(dp(2), on ? p.accent : p.muted);
        mark.setBackground(ring);
        card.addView(mark, new LinearLayout.LayoutParams(dp(22), dp(22)));
        press(card, click);
    }

    private static String mib(long bytes) {
        return String.format(Locale.ROOT, "%d MiB", bytes >> 20);
    }

    // ---------------------------------------------------------------- compatibility

    private void compatPage() {
        heading(getString(R.string.compat_title), getString(R.string.compat_note));
        LinearLayout verdict = panel(null);
        verdict.setBackground(new Card(dp(18), (0x30 << 24) | ((compat.compatible ? p.green : p.red) & 0xffffff)).edge(p.edge, edgeWidth()));
        LinearLayout top = new LinearLayout(this);
        top.setGravity(Gravity.CENTER_VERTICAL);
        top.addView(icon(compat.compatible ? "verify" : "erase", compat.compatible ? p.green : p.red, 34));
        TextView text = label(getString(compat.compatible ? R.string.compat_ok : R.string.compat_bad), 16, compat.compatible ? p.green : p.red, true);
        text.setPadding(dp(12), 0, 0, 0);
        top.addView(text, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1));
        verdict.addView(top);
        for (Compat.Check check : compat.checks) {
            int name = check.name.equals("bootloader") ? R.string.compat_bootloader : check.name.equals("model") ? R.string.compat_model
                    : check.name.equals("device") ? R.string.compat_device : R.string.compat_chip;
            // NEUTRAL: reported, neither this phone nor another one (a custom ROM's own model name).
            String mark = check.state == Compat.State.MATCH ? "verify" : check.state == Compat.State.CONFLICT ? "erase" : "phone_info";
            int tone = check.state == Compat.State.MATCH ? 2 : check.state == Compat.State.CONFLICT ? 5 : 3;
            row(content, mark, tone, getString(name), check.found.isEmpty() ? getString(R.string.compat_unknown) : check.found, null, null);
        }
        final LinearLayout parts = panel(getString(R.string.check_partitions));
        button(parts, getString(R.string.check_partitions), false, () -> background(() -> {
            Root.Result r = Root.run(Scripts.PARTITIONS, 60);
            final StringBuilder lines = new StringBuilder();
            for (String text2 : r.output.split("\n")) {
                String[] cells = text2.trim().split(" ");
                if (cells.length != 3 || !cells[0].equals("A22X:PART")) continue;
                try {
                    long size = Long.parseLong(cells[2]);
                    Payload.Image item = Payload.image(this, cells[1]);
                    lines.append(size == Scripts.partitionSize(cells[1]) ? "✓ " : "✗ ").append(getString(R.string.partition_line, cells[1],
                            size == 0 ? getString(R.string.partition_missing) : mib(size), mib(item.size))).append('\n');
                } catch (Exception e) {
                    lines.append(getString(R.string.failed, e.getMessage())).append('\n');
                }
            }
            runOnUiThread(() -> parts.addView(label(lines.toString().trim(), 13.5f, p.text, false)));
        }));
    }

    // ---------------------------------------------------------------- Wi-Fi, language and time, scripts

    private void wifiPage() {
        heading(getString(R.string.wifi_title), getString(R.string.wifi_text));
        LinearLayout box = panel(getString(R.string.caps_network));
        final EditText ssid = field(box, getString(R.string.ssid_hint), InputType.TYPE_CLASS_TEXT, null);
        final EditText key = field(box, getString(R.string.password_hint), InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD, null);
        button(box, getString(R.string.wifi_current), false, () -> background(() -> {
            String found = RecoveryStore.currentNetwork();
            if (found == null) say(getString(R.string.wifi_current_none));
            else runOnUiThread(() -> {
                ssid.setText(found);
                key.requestFocus();
            });
        }));
        button(box, getString(R.string.save_to_recovery), true, () -> {
            final String name = ssid.getText().toString();
            final String secret = key.getText().toString();
            String problem = Handoff.networkProblem(name, secret);
            if (problem != null) {
                say(getString("ssid".equals(problem) ? R.string.bad_ssid : R.string.bad_password));
                return;
            }
            background(() -> {
                try {
                    Root.Result r = RecoveryStore.sendHandoff(this, name, secret);
                    if (r.marker("OK") != null) {
                        runOnUiThread(() -> key.setText(""));
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

    private void syncPage() {
        Locale locale = Locale.getDefault();
        heading(getString(R.string.home_sync), getString(R.string.sync_title));
        LinearLayout box = panel(getString(R.string.caps_now));
        box.addView(label(getString(R.string.sync_text, locale.getDisplayLanguage(), TimeZone.getDefault().getID()), 15, p.text, false));
        if (Handoff.language(locale.getLanguage()) == null) {
            box.addView(label(getString(R.string.sync_unsupported, locale.getDisplayLanguage()), 13, p.muted, false));
        }
        final boolean on = prefs().getBoolean(KEY_SYNC, true);
        List<TextView> toggle = new ArrayList<>();
        toggle.add(chip(getString(R.string.sync_on), on, true, () -> {
            prefs().edit().putBoolean(KEY_SYNC, true).apply();
            refresh();
        }));
        toggle.add(chip(getString(R.string.sync_off), !on, true, () -> {
            prefs().edit().putBoolean(KEY_SYNC, false).apply();
            refresh();
        }));
        LinearLayout auto = panel(getString(R.string.sync_switch));
        chips(auto, toggle, 2);
        button(content, getString(R.string.sync_now), true, () -> background(() -> {
            try {
                Root.Result r = RecoveryStore.sendHandoff(this, null, null);
                say(r.marker("OK") != null ? getString(R.string.sync_sent) : getString(R.string.failed, r.output.trim()));
            } catch (Exception e) {
                say(getString(R.string.failed, e.getMessage()));
            }
        }));
    }

    private void scriptsPage() {
        heading(getString(R.string.script_title), getString(R.string.script_text));
        LinearLayout box = panel(getString(R.string.caps_script));
        final EditText name = field(box, getString(R.string.script_name_hint), InputType.TYPE_CLASS_TEXT, "my-setup.sh");
        final EditText text = field(box, "", InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_MULTI_LINE, Handoff.SCRIPT_TEMPLATE);
        text.setTypeface(Typeface.MONOSPACE);
        text.setTextSize(TypedValue.COMPLEX_UNIT_SP, 12.5f);
        text.setGravity(Gravity.TOP | Gravity.START);
        text.setMinLines(8);
        button(box, getString(R.string.save_to_recovery), true, () -> {
            final String file = name.getText().toString().trim();
            if (!Handoff.scriptNameAllowed(file)) {
                say(getString(R.string.bad_name));
                return;
            }
            final String body = text.getText().toString();
            background(() -> {
                try {
                    Root.Result r = RecoveryStore.saveScript(this, file, body);
                    say(r.marker("OK") != null ? getString(R.string.script_saved, file) : getString(R.string.failed, r.output.trim()));
                } catch (Exception e) {
                    say(getString(R.string.failed, e.getMessage()));
                }
            });
        });
        button(content, getString(R.string.show_scripts), false, () -> background(() -> {
            List<String> names = RecoveryStore.scripts();
            say(names.isEmpty() ? getString(R.string.scripts_none) : getString(R.string.scripts_list, String.join(", ", names)));
        }));
    }

    // ---------------------------------------------------------------- theme and restart

    private void themePage() {
        heading(getString(R.string.theme_title), getString(R.string.theme_text));
        int[] names = {R.string.theme_graphite, R.string.theme_daylight, R.string.theme_aurora};
        for (int i = 0; i < THEMES.length; i++) {
            final String code = THEMES[i];
            Palette look = new Palette(code);
            final LinearLayout card = new LinearLayout(this);
            card.setGravity(Gravity.CENTER_VERTICAL);
            Card fill = new Card(dp(20), look.bg).pressedVeil(18);
            if (look.backdrop != 0) fill.image(backdropSource(look.backdrop));
            fill.edge(code.equals(theme) ? p.accent : look.edge, code.equals(theme) ? dp(3) : edgeWidth());
            card.setBackground(fill);
            card.setPadding(dp(16), dp(16), dp(16), dp(16));
            LinearLayout swatches = new LinearLayout(this);
            for (int color : new int[]{look.heroTop, look.accent, look.tileFill[0], look.tileFill[2]}) {
                View dot = new View(this);
                GradientDrawable circle = new GradientDrawable();
                circle.setShape(GradientDrawable.OVAL);
                circle.setColor(color);
                dot.setBackground(circle);
                LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(dp(22), dp(22));
                lp.rightMargin = dp(6);
                swatches.addView(dot, lp);
            }
            card.addView(swatches);
            TextView title = label(getString(names[i]), 18, look.text, true);
            title.setPadding(dp(10), 0, 0, 0);
            card.addView(title, new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1));
            if (code.equals(theme)) card.addView(icon("verify", p.accent, 24));
            press(card, () -> switchTheme(code, card));
            LinearLayout.LayoutParams lp = new LinearLayout.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, dp(76));
            lp.bottomMargin = dp(12);
            content.addView(card, lp);
        }
        content.addView(label(getString(R.string.theme_icon_note), 13, p.muted, false));
    }

    /** Another theme, in place: the page is built again in the new colours and grows from the
     *  tapped card as a circle, as the recovery's theme change does. The launcher entry (the icon)
     *  changes only when the app is left, in onStop: Android closes an activity whose own entry
     *  gets disabled (2026-11-10, the app closed on a OnePlus). */
    private void switchTheme(String code, View from) {
        if (code.equals(theme)) return;
        prefs().edit().putString(KEY_THEME, code).apply();
        theme = code;
        p = new Palette(code);
        show(0, from);
    }

    private void launcherIcon() {
        String wanted = theme(this);
        try {
            PackageManager manager = getPackageManager();
            manager.setComponentEnabledSetting(new ComponentName(this, launcher(wanted)),
                    PackageManager.COMPONENT_ENABLED_STATE_ENABLED, PackageManager.DONT_KILL_APP);
            for (String other : THEMES) {
                if (!other.equals(wanted)) manager.setComponentEnabledSetting(new ComponentName(this, launcher(other)),
                        PackageManager.COMPONENT_ENABLED_STATE_DISABLED, PackageManager.DONT_KILL_APP);
            }
        } catch (RuntimeException e) {
            // The icon stays as it was; the page keeps the chosen theme anyway.
        }
    }

    @Override
    protected void onResume() {
        super.onResume();
        tick.run();  // The date again, also after a change of the system's language or time zone.
    }

    @Override
    protected void onPause() {
        super.onPause();
        handler.removeCallbacks(tick);
    }

    @Override
    protected void onStop() {
        super.onStop();
        if (busy == 0) launcherIcon();
    }

    private void restartPage() {
        heading(getString(R.string.home_restart), getString(R.string.restart_title));
        LinearLayout box = panel(null);
        box.addView(label(getString(R.string.restart_page_text), 15, p.text, false));
        hold(content, getString(R.string.hold_restart), 2000, true, () -> background(() -> {
            Root.Result r = RecoveryStore.restartIntoRecovery();
            if (!r.ok()) say(getString(R.string.failed, r.output.trim()));
        }));
    }

    // ---------------------------------------------------------------- the install pages of the recovery

    private List<Plan.Image> loadImages() {
        try (BufferedReader in = new BufferedReader(new InputStreamReader(getAssets().open("catalog.json"), StandardCharsets.UTF_8))) {
            StringBuilder text = new StringBuilder();
            for (String line; (line = in.readLine()) != null; ) text.append(line).append('\n');
            return Plan.images(text.toString());
        } catch (Exception e) {
            return new ArrayList<>();
        }
    }

    private static int familyTone(String family) {
        switch (family) {
            case "debian": return 5;
            case "ubuntu": return 1;
            case "fedora": return 0;
            case "arch": return 3;
            case "manjaro": return 2;
            default: return 4;
        }
    }

    /** The catalog, as the recovery's catalog page lists it. */
    private void planSystem() {
        heading(getString(R.string.plan_system_title), getString(R.string.plan_system_sub));
        for (final Plan.Image item : images) {
            String note = String.format(Locale.ROOT, "%.1f MiB", item.size / 1048576.0) + "  /  "
                    + getString(item.desktop.equals("image") ? R.string.plan_with_shell : R.string.plan_minimal);
            row(content, "terminal", familyTone(item.family), item.name + (item.version.isEmpty() ? "" : " " + item.version), note, null, () -> {
                image = item;
                choice.catalog = item.id;
                choice.desktop = item.desktop.equals("image") ? "image" : "phosh";
                choice.shells.clear();
                choice.signIn = "auto";
                open(this::planSetup);
            });
        }
    }

    private static final List<String> SHOWN = Arrays.asList("image", "console", "phosh", "gnome", "plasma", "xfce", "sxmo", "lomiri");

    private String desktopName(String id) {
        return getResources().getStringArray(R.array.plan_desktops)[Plan.DESKTOPS.indexOf(id)];
    }

    /** "Install options" of the recovery: desktop, account, language and time. */
    private void planSetup() {
        heading(getString(R.string.plan_setup_title), getString(R.string.plan_setup_sub));
        LinearLayout imageBox = panel(getString(R.string.caps_image));
        imageBox.addView(label(image != null ? image.name + " " + image.version : "—", 15, p.text, false));
        LinearLayout desktop = panel(getString(R.string.caps_desktop));
        List<TextView> list = new ArrayList<>();
        for (final String id : SHOWN) {
            boolean allowed = image == null || image.debianLike() || !(id.equals("sxmo") || id.equals("lomiri"));
            list.add(chip(desktopName(id), id.equals(choice.desktop), allowed, () -> {
                choice.desktop = id;
                choice.shells.remove(id);
                if (!Plan.SHELLS.contains(id)) {
                    choice.shells.clear();
                    choice.signIn = "auto";
                }
                refresh();
            }));
        }
        chips(desktop, list, 3);
        TextView desktopNote = label(getString(Plan.SHELLS.contains(choice.desktop) ? R.string.plan_desktop_note : R.string.plan_desktop_none), 12.5f,
                p.muted, false);
        desktopNote.setPadding(0, dp(10), 0, 0);
        desktop.addView(desktopNote);
        LinearLayout account = panel(getString(R.string.caps_account));
        final EditText user = field(account, getString(R.string.plan_user_hint), InputType.TYPE_CLASS_TEXT, choice.username);
        final EditText pass = field(account, getString(R.string.plan_password_hint),
                InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD, password);
        final EditText pass2 = field(account, getString(R.string.plan_password_again_hint),
                InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD, again);
        LinearLayout time = panel(getString(R.string.caps_language));
        List<TextView> two = new ArrayList<>();
        final String[] languages = getResources().getStringArray(R.array.plan_locales);
        two.add(chip(choice.locale != null ? languages[Plan.LOCALES.indexOf(choice.locale)] : languages[0], false, true, () -> {
            keep(user, pass, pass2);
            new AlertDialog.Builder(this).setItems(languages, (d, which) -> {
                choice.locale = Plan.LOCALES.get(which);
                refresh();
            }).show();
        }));
        two.add(chip(choice.timezone != null ? choice.timezone : "—", false, true, () -> {
            keep(user, pass, pass2);
            final EditText zone = new EditText(this);
            zone.setText(choice.timezone);
            new AlertDialog.Builder(this).setTitle(R.string.plan_zone_hint).setView(zone)
                    .setNegativeButton(R.string.cancel, null)
                    .setPositiveButton(android.R.string.ok, (d, w) -> {
                        String value = zone.getText().toString().trim();
                        if (Handoff.zone(value) == null) say(getString(R.string.plan_bad_zone));
                        else {
                            choice.timezone = value;
                            refresh();
                        }
                    }).show();
        }));
        chips(time, two, 2);
        button(content, getString(R.string.next), true, () -> {
            keep(user, pass, pass2);
            if (!password.equals(again)) {
                say(getString(R.string.plan_passwords_differ));
                return;
            }
            String problem = Plan.problem(choice, ids());
            if (problem != null && (problem.equals("plan_bad_user") || problem.equals("plan_bad_password"))) {
                say(getString(getResources().getIdentifier(problem, "string", getPackageName())));
                return;
            }
            open(Plan.SHELLS.contains(choice.desktop) ? this::planShells : this::planNetwork);
        });
    }

    /** What was typed stays when the page is built again. */
    private void keep(EditText user, EditText pass, EditText pass2) {
        choice.username = user.getText().toString().trim();
        password = pass.getText().toString();
        again = pass2.getText().toString();
        choice.password = password;
    }

    private HashSet<String> ids() {
        HashSet<String> all = new HashSet<>();
        for (Plan.Image item : images) all.add(item.id);
        return all;
    }

    /** "Shells and sign-in" of the recovery. */
    private void planShells() {
        heading(getString(R.string.plan_shells_title), getString(R.string.plan_shells_sub));
        LinearLayout also = panel(getString(R.string.caps_also));
        TextView main = label(getString(R.string.plan_main_shell, desktopName(choice.desktop)), 13.5f, p.muted, false);
        main.setPadding(0, 0, 0, dp(8));
        also.addView(main);
        List<TextView> list = new ArrayList<>();
        for (final String id : Plan.SHELLS) {
            boolean allowed = !id.equals(choice.desktop) && (image == null || image.debianLike() || !(id.equals("sxmo") || id.equals("lomiri")));
            list.add(chip(desktopName(id), choice.shells.contains(id) || id.equals(choice.desktop), allowed, () -> {
                if (choice.shells.contains(id)) choice.shells.remove(id);
                else choice.shells.add(id);
                refresh();
            }));
        }
        chips(also, list, 3);
        LinearLayout signIn = panel(getString(R.string.caps_sign_in));
        String[] names = getResources().getStringArray(R.array.plan_greeters);
        List<TextView> greeters = new ArrayList<>();
        for (int i = 0; i < Plan.GREETERS.size(); i++) {
            final String id = Plan.GREETERS.get(i);
            greeters.add(chip(names[i], id.equals(choice.signIn), true, () -> {
                choice.signIn = id;
                refresh();
            }));
        }
        chips(signIn, greeters, 2);
        button(content, getString(R.string.next), true, () -> open(this::planNetwork));
    }

    /** "Network and apps" of the recovery. */
    private void planNetwork() {
        heading(getString(R.string.plan_network_title), getString(R.string.plan_network_sub));
        LinearLayout wifi = panel(getString(R.string.caps_wifi));
        wifi.addView(label(getString(R.string.plan_wifi_note), 13.5f, p.text, false));
        button(wifi, getString(R.string.open_wifi), false, () -> open(this::wifiPage));
        LinearLayout apps = panel(getString(R.string.caps_apps));
        apps.addView(label(appsText(), 14, choice.apps.isEmpty() ? p.muted : p.text, false));
        button(apps, getString(R.string.choose_apps), false, () -> open(this::planApps));
        LinearLayout script = panel(getString(R.string.caps_script));
        script.addView(label(choice.script != null ? choice.script : getString(R.string.plan_none), 15, p.text, true));
        button(script, getString(R.string.choose_script), false, () -> background(() -> {
            final List<String> names = RecoveryStore.scripts();
            runOnUiThread(() -> {
                final String[] items = new String[names.size() + 1];
                items[0] = getString(R.string.plan_none);
                for (int i = 0; i < names.size(); i++) items[i + 1] = names.get(i);
                new AlertDialog.Builder(this).setItems(items, (d, which) -> {
                    choice.script = which == 0 ? null : names.get(which - 1);
                    refresh();
                }).show();
            });
        }));
        button(content, getString(R.string.review_plan), true, () -> open(this::planReview));
    }

    private String appsText() {
        if (choice.apps.isEmpty()) return getString(R.string.apps_none);
        String[] labels = getResources().getStringArray(R.array.plan_apps);
        StringBuilder text = new StringBuilder();
        for (String app : Plan.APPS) if (choice.apps.contains(app)) text.append(text.length() > 0 ? ", " : "").append(labels[Plan.APPS.indexOf(app)]);
        return text.toString();
    }

    private void planApps() {
        heading(getString(R.string.plan_apps_title), getString(R.string.plan_apps_sub));
        LinearLayout box = panel(null);
        String[] labels = getResources().getStringArray(R.array.plan_apps);
        List<TextView> list = new ArrayList<>();
        for (int i = 0; i < Plan.APPS.size(); i++) {
            final String id = Plan.APPS.get(i);
            list.add(chip(labels[i], choice.apps.contains(id), true, () -> {
                if (choice.apps.contains(id)) choice.apps.remove(id);
                else choice.apps.add(id);
                refresh();
            }));
        }
        chips(box, list, 2);
        button(content, getString(R.string.done), true, this::back);
    }

    /** The review of the recovery: what will be installed, then a hold to save the plan. */
    private void planReview() {
        heading(getString(R.string.plan_review_title), getString(R.string.plan_review_sub));
        LinearLayout system = panel(getString(R.string.caps_new_system));
        system.addView(label(image != null ? image.name + " " + image.version : "—", 18, p.text, true));
        String[] greeters = getResources().getStringArray(R.array.plan_greeters);
        StringBuilder shells = new StringBuilder(desktopName(choice.desktop));
        for (String shell : Plan.SHELLS) if (choice.shells.contains(shell) && !shell.equals(choice.desktop)) shells.append(" + ").append(desktopName(shell));
        String[] languages = getResources().getStringArray(R.array.plan_locales);
        LinearLayout rows = panel(null);
        pairLine(rows, getString(R.string.row_desktop), shells.toString());
        pairLine(rows, getString(R.string.row_sign_in), greeters[Plan.GREETERS.indexOf(choice.signIn)]);
        pairLine(rows, getString(R.string.row_apps), appsText());
        pairLine(rows, getString(R.string.row_account), choice.username);
        pairLine(rows, getString(R.string.row_language), (choice.locale != null ? languages[Plan.LOCALES.indexOf(choice.locale)] : "—") + " / "
                + (choice.timezone != null ? choice.timezone : "—"));
        pairLine(rows, getString(R.string.row_script), choice.script != null ? choice.script : "—");
        content.addView(label(getString(R.string.plan_erase_warning), 14, p.red, true));
        if (!compat.compatible) {  // Why the buttons below do nothing on another phone.
            TextView other = label(getString(R.string.not_this_phone), 13.5f, p.red, false);
            other.setPadding(0, dp(8), 0, 0);
            content.addView(other);
        }
        hold(content, getString(R.string.hold_save_restart), 2000, compat.compatible, () -> save(true));
        TextView only = button(content, getString(R.string.plan_save), false, () -> save(false));
        enable(only, compat.compatible);
        button(content, getString(R.string.plan_remove), false, () -> background(() -> {
            Root.Result r = RecoveryStore.removePlan();
            say(r.marker("OK") != null ? getString(R.string.plan_removed) : getString(R.string.failed, r.output.trim()));
        }));
    }

    private void pairLine(LinearLayout box, String name, String value) {
        LinearLayout line = new LinearLayout(this);
        line.setPadding(0, dp(6), 0, dp(6));
        line.addView(label(name, 13.5f, p.muted, false), new LinearLayout.LayoutParams(dp(120), ViewGroup.LayoutParams.WRAP_CONTENT));
        line.addView(label(value, 14, p.text, false), new LinearLayout.LayoutParams(0, ViewGroup.LayoutParams.WRAP_CONTENT, 1));
        box.addView(line);
    }

    private void save(final boolean restart) {
        choice.erase = true;
        choice.password = password;
        String problem = Plan.problem(choice, ids());
        if (problem != null) {
            say(getString(getResources().getIdentifier(problem, "string", getPackageName())));
            return;
        }
        final String json = Plan.json(choice, Sha512Crypt.hash(choice.password), "A22x Recovery installer " + versionName(),
                System.currentTimeMillis() / 1000);
        background(() -> {
            try {
                Root.Result r = RecoveryStore.savePlan(this, json);
                if (r.marker("OK") == null) {
                    say(getString(R.string.failed, r.output.trim()));
                    return;
                }
                say(getString(R.string.plan_saved));
                if (prefs().getBoolean(KEY_SYNC, true)) RecoveryStore.sendHandoff(this, null, null);
                if (restart) {
                    Root.Result reboot = RecoveryStore.restartIntoRecovery();
                    if (!reboot.ok()) say(getString(R.string.failed, reboot.output.trim()));
                }
            } catch (Exception e) {
                say(getString(R.string.failed, e.getMessage()));
            }
        });
    }

    private String versionName() {
        try {
            return getPackageManager().getPackageInfo(getPackageName(), 0).versionName;
        } catch (Exception e) {
            return "?";
        }
    }

    // ---------------------------------------------------------------- what this phone is

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
}
