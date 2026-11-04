/* SPDX-License-Identifier: MIT */
#include "charger_policy.h"

#include <math.h>
#include <stdint.h>
#include <string.h>

static bool valid_time(double now)
{
    return isfinite(now) && now >= 0.0;
}

void cp_init(struct charge_policy *p, double now)
{
    memset(p, 0, sizeof(*p));
    p->started = valid_time(now) ? now : 0.0;
    p->light_until = p->started + CP_LIGHT_SECONDS;
    p->ignore_held = true;
    p->online = -1;
}

void cp_resync_key(struct charge_policy *p, bool held, double now)
{
    if (!valid_time(now)) {
        cp_lose_key(p);
        return;
    }
    /* A snapshot proves no press start time. An initially held key must first
     * be released; a resync also breaks any pending unplug stability window. */
    p->key_known = true;
    p->key_down = held;
    p->ignore_held = held;
    p->have_press = false;
    p->pending_long = false;
    p->have_offline = false;
}

void cp_lose_key(struct charge_policy *p)
{
    p->key_known = false;
    p->key_down = false;
    p->ignore_held = true;
    p->have_press = false;
    p->pending_long = false;
    p->have_offline = false;
}

void cp_key(struct charge_policy *p, bool down, double now)
{
    bool was_down, had_press;
    double started;

    if (!valid_time(now)) {
        cp_lose_key(p);
        return;
    }
    if (!p->key_known)
        return;
    if (down) {
        if (p->key_down)
            return;
        p->key_down = true;
        p->have_offline = false;
        if (!p->ignore_held) {
            p->have_press = true;
            p->press_started = now;
            p->light_until = now + CP_LIGHT_SECONDS;
        }
        return;
    }

    was_down = p->key_down;
    had_press = p->have_press;
    started = p->press_started;
    p->key_down = false;
    p->have_press = false;
    if (p->ignore_held) {
        p->ignore_held = false;
        return;
    }
    if (was_down) {
        p->light_until = now + CP_LIGHT_SECONDS;
        if (had_press && now - started >= CP_LONG_PRESS_SECONDS)
            p->pending_long = true;
    }
}

void cp_observe_online(struct charge_policy *p, int online, double now)
{
    if (!valid_time(now)) {
        p->online = -1;
        p->have_sample = false;
        p->have_offline = false;
        return;
    }
    if (online != 0 && online != 1)
        online = -1;
    if (!p->have_sample || now < p->sample_at ||
        now - p->sample_at > CP_SAMPLE_TTL_SECONDS)
        p->have_offline = false;
    p->have_sample = true;
    p->sample_at = now;
    p->online = online;
    if (online != 0 || now < p->started + CP_STARTUP_GRACE_SECONDS ||
        !p->key_known || p->key_down) {
        p->have_offline = false;
    } else if (!p->have_offline) {
        p->have_offline = true;
        p->offline_since = now;
    }
}

enum charge_action cp_decide(struct charge_policy *p, double now)
{
    if (p->action != CHARGE_NONE)
        return CHARGE_NONE;
    if (!valid_time(now) || now < p->started) {
        p->have_offline = false;
        return CHARGE_NONE;
    }

    if (p->key_known &&
        (p->pending_long ||
         (p->key_down && !p->ignore_held && p->have_press &&
          now - p->press_started >= CP_LONG_PRESS_SECONDS))) {
        p->action = CHARGE_NORMAL_REBOOT;
    } else {
        if (!p->key_known || p->key_down || p->online != 0 ||
            !p->have_sample || now < p->sample_at ||
            now - p->sample_at > CP_SAMPLE_TTL_SECONDS ||
            now < p->started + CP_STARTUP_GRACE_SECONDS) {
            p->have_offline = false;
            return CHARGE_NONE;
        }
        if (!p->have_offline || now - p->offline_since < CP_UNPLUG_SECONDS)
            return CHARGE_NONE;
        p->action = CHARGE_POWEROFF;
    }
    p->action_at = now;
    return p->action;
}

bool cp_visible(const struct charge_policy *p, double now)
{
    return valid_time(now) &&
        (now < p->light_until ||
         (p->key_known && p->key_down && !p->ignore_held));
}

int cp_aggregate_online(int charger, int tcpm)
{
    if (charger == 1 || tcpm == 1)
        return 1;
    if (charger == 0 && tcpm == 0)
        return 0;
    return -1;
}

static bool cmd_space(unsigned char ch)
{
    return ch == ' ' || ch == '\t' || ch == '\r' || ch == '\n';
}

static bool token_eq(const char *prefix, size_t length, const char *s)
{
    size_t n = strlen(s);
    return length == n && memcmp(prefix, s, n) == 0;
}

/* Relevant keys/words all fit in this prefix. We still scan and validate the
 * complete command line, even for longer irrelevant tokens. */
#define TOKEN_PREFIX_SIZE 64u
struct boot_token {
    char prefix[TOKEN_PREFIX_SIZE];
    size_t length;
    size_t equals;
};

static void token_add(struct boot_token *t, char ch)
{
    if (ch == '=' && t->equals == SIZE_MAX)
        t->equals = t->length;
    if (t->length < sizeof(t->prefix))
        t->prefix[t->length] = ch;
    ++t->length;
}

static bool token_key_eq(const struct boot_token *t, const char *key)
{
    size_t n = (t->equals == SIZE_MAX) ? t->length : t->equals;
    return token_eq(t->prefix, n, key);
}

/* Decode shlex-style quoted words, matching the existing pure Python detector.
 * No interpolation or execution occurs. Quoting errors reject the whole input.
 * Inside double quotes only quote/backslash lose their preceding backslash. */
static int next_token(const char *cmd, size_t size, size_t *at,
                      struct boot_token *t)
{
    char quote = '\0';
    size_t i = *at;

    while (i < size && cmd_space((unsigned char)cmd[i]))
        ++i;
    if (i == size) {
        *at = i;
        return 0;
    }

    memset(t, 0, sizeof(*t));
    t->equals = SIZE_MAX;
    while (i < size) {
        char ch = cmd[i++];
        if (quote == '\'') {
            if (ch == '\'')
                quote = '\0';
            else
                token_add(t, ch);
        } else if (quote == '"') {
            if (ch == '"') {
                quote = '\0';
            } else if (ch == '\\') {
                if (i == size)
                    return -1;
                if (cmd[i] == '"' || cmd[i] == '\\')
                    token_add(t, cmd[i++]);
                else
                    token_add(t, ch);
            } else {
                token_add(t, ch);
            }
        } else {
            if (cmd_space((unsigned char)ch))
                break;
            if (ch == '\'' || ch == '"') {
                quote = ch;
            } else if (ch == '\\') {
                if (i == size)
                    return -1;
                token_add(t, cmd[i++]);
            } else {
                token_add(t, ch);
            }
        }
    }
    if (quote)
        return -1;
    *at = i;
    return 1;
}

static bool compatible_a22x(const unsigned char *data, size_t size)
{
    static const char wanted[] = "samsung,a22x";
    size_t at = 0;
    bool found = false;

    if (!data || size == 0 || size > CP_COMPATIBLE_LIMIT ||
        data[size - 1] != '\0')
        return false;
    while (at < size) {
        size_t start = at;
        while (at < size && data[at] != '\0') {
            if (data[at] < 33 || data[at] > 126)
                return false;
            ++at;
        }
        if (at == size || at == start)
            return false;
        if (at - start == sizeof(wanted) - 1 &&
            memcmp(data + start, wanted, sizeof(wanted) - 1) == 0)
            found = true;
        ++at;
    }
    return found;
}

int cp_detect_boot(const char *cmdline, const void *compatible,
                   size_t compatible_size)
{
    static const char *const override_keys[] = {
        "systemd.unit", "rd.systemd.unit", "a22x.noswitch",
        "systemd.break", "rd.systemd.break",
        "systemd.debug_shell", "rd.systemd.debug_shell",
        "systemd.run", "rd.systemd.run"
    };
    static const char *const override_words[] = {
        "single", "s", "S", "-b", "emergency", "rd.emergency",
        "rescue", "rd.rescue", "0", "1", "2", "3", "4", "5", "6"
    };
    struct boot_token token;
    size_t size, at = 0, i;
    unsigned int modes = 0;
    int result;

    if (!cmdline || !compatible_a22x(compatible, compatible_size))
        return 0;
    for (size = 0; size <= CP_CMDLINE_LIMIT; ++size) {
        unsigned char ch = (unsigned char)cmdline[size];
        if (!ch)
            break;
        if (size == CP_CMDLINE_LIMIT ||
            ch > 126 || (ch < 32 && !cmd_space(ch)))
            return 0;
    }
    if (size == 0)
        return 0;

    while ((result = next_token(cmdline, size, &at, &token)) > 0) {
        for (i = 0; i < sizeof(override_keys) / sizeof(override_keys[0]); ++i)
            if (token_key_eq(&token, override_keys[i]))
                return 0;
        for (i = 0; i < sizeof(override_words) / sizeof(override_words[0]); ++i)
            if (token_eq(token.prefix, token.length, override_words[i]))
                return 0;
        if (token_key_eq(&token, "androidboot.mode")) {
            ++modes;
            if (modes != 1 ||
                !token_eq(token.prefix, token.length,
                          "androidboot.mode=charger"))
                return 0;
        }
    }
    return result == 0 && modes == 1;
}
