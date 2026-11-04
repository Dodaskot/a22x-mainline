/* SPDX-License-Identifier: MIT
 * Pure native tests: no phone, evdev, sysfs, framebuffer or power system calls.
 */
#include "charger_policy.h"

#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

static unsigned int checks;
static unsigned int scenarios;
static const char a22x[] = "samsung,a22x\0mediatek,mt6833\0";

#define CHECK(expr) do { \
    ++checks; \
    if (!(expr)) { \
        fprintf(stderr, "%s:%d: %s\n", __func__, __LINE__, #expr); \
        exit(1); \
    } \
} while (0)

static int detect(const char *cmd)
{
    return cp_detect_boot(cmd, a22x, sizeof(a22x) - 1);
}

static struct charge_policy ready(void)
{
    struct charge_policy p;
    cp_init(&p, 0.0);
    cp_resync_key(&p, false, 0.0);
    return p;
}

static enum charge_action offline(struct charge_policy *p, int start, int end)
{
    enum charge_action found = CHARGE_NONE;
    for (int n = start; n <= end; ++n) {
        enum charge_action action;
        cp_observe_online(p, 0, (double)n);
        action = cp_decide(p, (double)n);
        if (action != CHARGE_NONE) {
            CHECK(found == CHARGE_NONE);
            found = action;
        }
    }
    return found;
}

static void test_boot_metadata(void)
{
    static const char *const accepted[] = {
        "androidboot.mode=charger",
        "console=ttyS0,921600n8 androidboot.mode=charger loglevel=8",
        "androidboot.mode=\"charger\"",
        "'androidboot.mode=charger'",
        "androidboot.mode=char\\ger",
        " \tandroidboot.mode=charger\r\n",
        "\"\" androidboot.mode=charger ",
        "androidboot.mode=charger systemd.log_level=debug "
        "systemd.mask=unrelated.service other.systemd.unit=rescue.target rescue=0"
    };
    static const char *const rejected[] = {
        "", " ", "console=ttyS0", "androidboot.mode=normal",
        "androidboot.mode=recovery", "androidboot.mode=bootloader",
        "xandroidboot.mode=charger", "androidboot.mode=charger2",
        "androidboot.mode=CHARGER", "androidboot.mode=charger=normal",
        "value=androidboot.mode=charger", "androidboot.mode",
        "androidboot.mode=", "androidboot.mode =charger",
        "androidboot.mode=charger androidboot.mode=normal",
        "androidboot.mode=normal androidboot.mode=charger",
        "androidboot.mode=charger androidboot.mode=charger",
        "androidboot.mode=charger androidboot.mode",
        "androidboot.mode androidboot.mode=charger",
        "androidboot.mode=\"charger", "androidboot.mode=charger\\",
        "androidboot.mode=charger foo='bad", "androidboot.mode=charger \001",
        "androidboot.mode=charger \177", "androidboot.mode=charger \200",
        "androidboot.mode=\"char\\ger\"", "androidboot.mode='char\\ger'"
    };
    static const char *const overrides[] = {
        "systemd.unit=rescue.target", "systemd.unit=graphical.target",
        "systemd.unit=a22x-charger.target", "rd.systemd.unit=emergency.target",
        "systemd.unit=", "systemd.unit", "rd.systemd.unit",
        "a22x.noswitch", "a22x.noswitch=1", "a22x.noswitch=0",
        "single", "s", "S", "-b", "emergency", "rd.emergency",
        "rescue", "rd.rescue", "0", "1", "2", "3", "4", "5", "6",
        "systemd.break=pre-udev", "rd.systemd.break",
        "systemd.debug_shell", "rd.systemd.debug_shell=1",
        "systemd.run=/bin/sh", "rd.systemd.run=/bin/sh"
    };
    char line[512];

    ++scenarios;
    CHECK(!cp_detect_boot(NULL, a22x, sizeof(a22x) - 1));
    CHECK(!cp_detect_boot("androidboot.mode=charger", NULL, 12));
    for (size_t i = 0; i < sizeof(accepted) / sizeof(accepted[0]); ++i)
        CHECK(detect(accepted[i]) == 1);
    for (size_t i = 0; i < sizeof(rejected) / sizeof(rejected[0]); ++i)
        CHECK(detect(rejected[i]) == 0);
    for (size_t i = 0; i < sizeof(overrides) / sizeof(overrides[0]); ++i) {
        int n = snprintf(line, sizeof(line), "%s androidboot.mode=charger",
                         overrides[i]);
        CHECK(n > 0 && (size_t)n < sizeof(line));
        CHECK(detect(line) == 0);
        n = snprintf(line, sizeof(line), "androidboot.mode=charger %s",
                     overrides[i]);
        CHECK(n > 0 && (size_t)n < sizeof(line));
        CHECK(detect(line) == 0);
    }
    CHECK(!detect("androidboot.mode=charger \"rd.systemd.unit=rescue.target\""));
}

static void test_boot_bounds_and_compatible(void)
{
    char large[CP_CMDLINE_LIMIT + 2];
    unsigned char compat[CP_COMPATIBLE_LIMIT + 1];
    static const char marker[] = "androidboot.mode=charger";
    static const char reverse[] = "mediatek,mt6833\0samsung,a22x\0";
    static const char *const invalid[] = {
        "", "mediatek,mt6833", "samsung,a22", "samsung,a22x-other",
        "other,samsung,a22x", "samsung,a22x other", "samsung,a22x\177",
        "samsung,a22x\200"
    };
    static const char empty_entry[] = "samsung,a22x\0\0";
    static const char bad_tail[] = "samsung,a22x\0bad\200\0";
    static const char bad_initial[] = "\0samsung,a22x\0";

    ++scenarios;
    CHECK(cp_detect_boot(marker, reverse, sizeof(reverse) - 1));
    CHECK(!cp_detect_boot(marker, a22x, 0));
    CHECK(!cp_detect_boot(marker, a22x, sizeof(a22x) - 2));
    for (size_t i = 0; i < sizeof(invalid) / sizeof(invalid[0]); ++i)
        CHECK(!cp_detect_boot(marker, invalid[i], strlen(invalid[i]) + 1));
    CHECK(!cp_detect_boot(marker, empty_entry, sizeof(empty_entry) - 1));
    CHECK(!cp_detect_boot(marker, bad_tail, sizeof(bad_tail) - 1));
    CHECK(!cp_detect_boot(marker, bad_initial, sizeof(bad_initial) - 1));

    memset(large, ' ', CP_CMDLINE_LIMIT);
    memcpy(large + CP_CMDLINE_LIMIT - sizeof(marker) + 1,
           marker, sizeof(marker) - 1);
    large[CP_CMDLINE_LIMIT] = '\0';
    CHECK(detect(large) == 1);
    large[CP_CMDLINE_LIMIT] = ' ';
    large[CP_CMDLINE_LIMIT + 1] = '\0';
    CHECK(detect(large) == 0);

    /* Huge irrelevant word must not overflow the fixed token prefix or cause
     * a prefix-only match. Full syntax after that word must still be checked. */
    memset(large, 'x', 60000);
    memcpy(large + 60000, " androidboot.mode=charger", 25);
    large[60025] = '\0';
    CHECK(detect(large) == 1);
    large[30000] = '"';
    CHECK(detect(large) == 0);

    memset(compat, 'x', sizeof(compat));
    memcpy(compat, "samsung,a22x", 12);
    compat[12] = '\0';
    compat[CP_COMPATIBLE_LIMIT - 1] = '\0';
    CHECK(cp_detect_boot(marker, compat, CP_COMPATIBLE_LIMIT));
    compat[CP_COMPATIBLE_LIMIT] = '\0';
    CHECK(!cp_detect_boot(marker, compat, CP_COMPATIBLE_LIMIT + 1));
}

static void test_online_evidence(void)
{
    ++scenarios;
    for (int a = -2; a <= 2; ++a) {
        for (int b = -2; b <= 2; ++b) {
            int expected = (a == 1 || b == 1) ? 1 :
                           (a == 0 && b == 0) ? 0 : -1;
            CHECK(cp_aggregate_online(a, b) == expected);
        }
    }
}

static void test_startup_and_unplug(void)
{
    struct charge_policy p = ready();
    ++scenarios;
    CHECK(offline(&p, 0, 12) == CHARGE_NONE);
    CHECK(offline(&p, 13, 13) == CHARGE_POWEROFF);
    CHECK(p.action == CHARGE_POWEROFF);
    CHECK(p.action_at == 13.0);
    CHECK(offline(&p, 14, 60) == CHARGE_NONE);
    cp_key(&p, true, 61.0);
    CHECK(cp_decide(&p, 63.0) == CHARGE_NONE);
}

static void test_unknown_and_input_absence(void)
{
    struct charge_policy p = ready();
    ++scenarios;
    for (int n = 0; n < 120; ++n) {
        cp_observe_online(&p, -1, (double)n);
        CHECK(cp_decide(&p, (double)n) == CHARGE_NONE);
    }
    CHECK(offline(&p, 120, 122) == CHARGE_NONE);
    CHECK(offline(&p, 123, 123) == CHARGE_POWEROFF);

    cp_init(&p, 0.0);
    CHECK(offline(&p, 0, 60) == CHARGE_NONE);
    cp_resync_key(&p, false, 60.1);
    CHECK(offline(&p, 61, 63) == CHARGE_NONE);
    CHECK(offline(&p, 64, 64) == CHARGE_POWEROFF);
}

static void test_unknown_reconnect_and_gap_reset(void)
{
    struct charge_policy p = ready();
    ++scenarios;
    CHECK(offline(&p, 10, 12) == CHARGE_NONE);
    cp_observe_online(&p, -1, 12.5);
    CHECK(cp_decide(&p, 12.5) == CHARGE_NONE);
    CHECK(offline(&p, 13, 15) == CHARGE_NONE);
    CHECK(offline(&p, 16, 16) == CHARGE_POWEROFF);

    p = ready();
    CHECK(offline(&p, 10, 12) == CHARGE_NONE);
    cp_observe_online(&p, 1, 12.9);
    CHECK(cp_decide(&p, 20.0) == CHARGE_NONE);
    CHECK(offline(&p, 21, 23) == CHARGE_NONE);
    CHECK(offline(&p, 24, 24) == CHARGE_POWEROFF);

    p = ready();
    cp_observe_online(&p, 0, 10.0);
    cp_observe_online(&p, 0, 20.0);
    CHECK(cp_decide(&p, 20.0) == CHARGE_NONE);
    CHECK(offline(&p, 21, 22) == CHARGE_NONE);
    CHECK(offline(&p, 23, 23) == CHARGE_POWEROFF);
}

static void test_stale_samples_and_thresholds(void)
{
    struct charge_policy p = ready();
    ++scenarios;
    cp_observe_online(&p, 0, 10.0);
    CHECK(cp_decide(&p, 30.0) == CHARGE_NONE);
    CHECK(offline(&p, 31, 33) == CHARGE_NONE);
    CHECK(offline(&p, 34, 34) == CHARGE_POWEROFF);

    p = ready();
    cp_observe_online(&p, 0, 10.0);
    cp_observe_online(&p, 0, 12.5); /* TTL inclusive, no gap reset. */
    CHECK(cp_decide(&p, 12.999) == CHARGE_NONE);
    CHECK(cp_decide(&p, 13.0) == CHARGE_POWEROFF);

    p = ready();
    cp_observe_online(&p, 0, 10.0);
    cp_observe_online(&p, 0, 12.501); /* Just over TTL resets stability. */
    CHECK(cp_decide(&p, 13.0) == CHARGE_NONE);
    cp_observe_online(&p, 0, 14.0);
    CHECK(cp_decide(&p, 15.5) == CHARGE_NONE);
    CHECK(cp_decide(&p, 15.501) == CHARGE_POWEROFF);
}

static void test_short_press_visibility_and_defer(void)
{
    struct charge_policy p = ready();
    ++scenarios;
    CHECK(cp_visible(&p, 0.0));
    CHECK(cp_visible(&p, 11.999));
    CHECK(!cp_visible(&p, 12.0));
    cp_key(&p, true, 20.0);
    cp_key(&p, false, 20.2);
    CHECK(cp_decide(&p, 20.2) == CHARGE_NONE);
    CHECK(cp_visible(&p, 32.19));
    CHECK(!cp_visible(&p, 32.21));

    p = ready();
    CHECK(offline(&p, 10, 12) == CHARGE_NONE);
    cp_key(&p, true, 12.9);
    cp_key(&p, false, 13.1);
    CHECK(cp_decide(&p, 13.1) == CHARGE_NONE);
    CHECK(offline(&p, 14, 16) == CHARGE_NONE);
    CHECK(offline(&p, 17, 17) == CHARGE_POWEROFF);
}

static void test_long_press_and_single_action(void)
{
    struct charge_policy p = ready();
    ++scenarios;
    cp_key(&p, true, 30.0);
    CHECK(cp_decide(&p, 31.999) == CHARGE_NONE);
    cp_key(&p, true, 31.5); /* duplicate press does not restart the timer */
    CHECK(cp_decide(&p, 32.0) == CHARGE_NORMAL_REBOOT);
    CHECK(p.action == CHARGE_NORMAL_REBOOT);
    CHECK(p.action_at == 32.0);
    cp_key(&p, false, 33.0);
    cp_key(&p, true, 34.0);
    CHECK(cp_decide(&p, 100.0) == CHARGE_NONE);
    cp_key(&p, false, 101.0);
    CHECK(offline(&p, 102, 110) == CHARGE_NONE);

    p = ready();
    cp_key(&p, true, 20.0);
    cp_key(&p, false, 22.1); /* long release between poll ticks */
    CHECK(cp_decide(&p, 22.1) == CHARGE_NORMAL_REBOOT);
    CHECK(cp_decide(&p, 23.0) == CHARGE_NONE);
}

static void test_initial_held_key(void)
{
    struct charge_policy p;
    ++scenarios;
    cp_init(&p, 0.0);
    cp_resync_key(&p, true, 0.0);
    CHECK(cp_decide(&p, 60.0) == CHARGE_NONE);
    CHECK(!cp_visible(&p, 20.0));
    cp_key(&p, true, 61.0);
    CHECK(cp_decide(&p, 70.0) == CHARGE_NONE);
    cp_key(&p, false, 71.0);
    CHECK(cp_decide(&p, 71.0) == CHARGE_NONE);
    cp_key(&p, true, 72.0);
    CHECK(cp_decide(&p, 74.0) == CHARGE_NORMAL_REBOOT);
}

static void test_power_key_beats_unplug(void)
{
    struct charge_policy p = ready();
    ++scenarios;
    CHECK(offline(&p, 10, 12) == CHARGE_NONE);
    cp_key(&p, true, 12.9);
    cp_observe_online(&p, 0, 13.0);
    CHECK(cp_decide(&p, 13.0) == CHARGE_NONE);
    CHECK(cp_decide(&p, 14.9) == CHARGE_NORMAL_REBOOT);
}

static void test_sync_loss_and_recovery(void)
{
    struct charge_policy p = ready();
    ++scenarios;
    cp_key(&p, true, 20.0);
    cp_lose_key(&p); /* SYN_DROPPED or evdev error */
    cp_key(&p, false, 22.0);
    cp_key(&p, true, 22.1);
    CHECK(cp_decide(&p, 30.0) == CHARGE_NONE);
    cp_resync_key(&p, true, 30.2);
    CHECK(cp_decide(&p, 40.0) == CHARGE_NONE);
    cp_key(&p, false, 40.1);
    cp_key(&p, true, 41.0);
    CHECK(cp_decide(&p, 43.0) == CHARGE_NORMAL_REBOOT);

    p = ready();
    CHECK(offline(&p, 10, 12) == CHARGE_NONE);
    cp_lose_key(&p);
    CHECK(offline(&p, 13, 20) == CHARGE_NONE);
    cp_resync_key(&p, false, 20.1);
    CHECK(offline(&p, 21, 23) == CHARGE_NONE);
    CHECK(offline(&p, 24, 24) == CHARGE_POWEROFF);

    /* A resync snapshot cannot finish a long press whose edge was lost. */
    p = ready();
    cp_key(&p, true, 20.0);
    cp_key(&p, false, 22.0);
    cp_resync_key(&p, false, 22.1);
    CHECK(cp_decide(&p, 22.1) == CHARGE_NONE);
}

static void test_invalid_time_or_samples_fail_open(void)
{
    struct charge_policy p = ready();
    ++scenarios;
    CHECK(offline(&p, 10, 12) == CHARGE_NONE);
    CHECK(cp_decide(&p, NAN) == CHARGE_NONE);
    CHECK(cp_decide(&p, INFINITY) == CHARGE_NONE);
    CHECK(cp_decide(&p, -1.0) == CHARGE_NONE);
    CHECK(cp_decide(&p, 11.0) == CHARGE_NONE); /* older than sample */
    CHECK(!cp_visible(&p, NAN));

    p = ready();
    CHECK(offline(&p, 10, 12) == CHARGE_NONE);
    cp_observe_online(&p, 0, NAN);
    CHECK(cp_decide(&p, 13.0) == CHARGE_NONE);
    CHECK(offline(&p, 14, 16) == CHARGE_NONE);
    CHECK(offline(&p, 17, 17) == CHARGE_POWEROFF);

    p = ready();
    CHECK(offline(&p, 10, 12) == CHARGE_NONE);
    cp_observe_online(&p, 2, 12.5);
    CHECK(cp_decide(&p, 13.0) == CHARGE_NONE);
    CHECK(offline(&p, 14, 16) == CHARGE_NONE);
    CHECK(offline(&p, 17, 17) == CHARGE_POWEROFF);

    p = ready();
    cp_key(&p, true, 20.0);
    cp_key(&p, false, NAN);
    CHECK(!p.key_known);
    CHECK(cp_decide(&p, 23.0) == CHARGE_NONE);
    cp_resync_key(&p, false, NAN);
    CHECK(!p.key_known);
}

int main(void)
{
    test_boot_metadata();
    test_boot_bounds_and_compatible();
    test_online_evidence();
    test_startup_and_unplug();
    test_unknown_and_input_absence();
    test_unknown_reconnect_and_gap_reset();
    test_stale_samples_and_thresholds();
    test_short_press_visibility_and_defer();
    test_long_press_and_single_action();
    test_initial_held_key();
    test_power_key_beats_unplug();
    test_sync_loss_and_recovery();
    test_invalid_time_or_samples_fail_open();
    printf("PASS: %u policy/metadata scenarios, %u checks; no hardware I/O\n",
           scenarios, checks);
    return 0;
}
