/* SPDX-License-Identifier: MIT */
#ifndef A22X_CHARGER_POLICY_H
#define A22X_CHARGER_POLICY_H

#include <stdbool.h>
#include <stddef.h>

#define CP_CMDLINE_LIMIT 65536u
#define CP_COMPATIBLE_LIMIT 4096u
#define CP_LONG_PRESS_SECONDS 2.0
#define CP_LIGHT_SECONDS 12.0
#define CP_STARTUP_GRACE_SECONDS 10.0
#define CP_UNPLUG_SECONDS 3.0
#define CP_SAMPLE_TTL_SECONDS 2.5

enum charge_action {
    CHARGE_NONE = 0,
    CHARGE_NORMAL_REBOOT,
    CHARGE_POWEROFF
};

/* Pure policy state. The caller supplies CLOCK_MONOTONIC seconds and fresh I/O.
 * cp_decide consumes an action before returning it; it never returns another
 * action on this state, even when the caller's physical attempt fails. */
struct charge_policy {
    double started;
    double light_until;
    double press_started;
    double sample_at;
    double offline_since;
    double action_at;
    bool key_known;
    bool key_down;
    bool ignore_held;
    bool pending_long;
    bool have_press;
    bool have_sample;
    bool have_offline;
    int online; /* -1 unknown, 0 disconnected, 1 connected */
    enum charge_action action;
};

void cp_init(struct charge_policy *p, double now);
void cp_resync_key(struct charge_policy *p, bool held, double now);
void cp_lose_key(struct charge_policy *p);
void cp_key(struct charge_policy *p, bool down, double now);
void cp_observe_online(struct charge_policy *p, int online, double now);
enum charge_action cp_decide(struct charge_policy *p, double now);
bool cp_visible(const struct charge_policy *p, double now);
int cp_aggregate_online(int charger, int tcpm);

/* Only a single exact charger marker plus the exact device compatibility
 * selects the charging branch. Explicit operator boot selections win.
 * cmdline is a NUL-terminated C string (bounded to CP_CMDLINE_LIMIT chars).
 * compatible is the exact DT property bytes INCLUDING its terminating NUL.
 * No I/O, allocation, environment state, numeric boot tags or side effects. */
int cp_detect_boot(const char *cmdline, const void *compatible,
                   size_t compatible_size);

#endif
