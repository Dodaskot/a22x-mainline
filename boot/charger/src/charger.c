// SPDX-License-Identifier: GPL-2.0-only
#define _GNU_SOURCE
#include "charger_policy.h"
#include "charger_ui.h"
#include <ctype.h>
#include <dirent.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <linux/input.h>
#include <linux/reboot.h>
#include <poll.h>
#include <signal.h>
#include <stdarg.h>
#include <stdbool.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/stat.h>
#include <sys/syscall.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <time.h>
#include <unistd.h>

#define CHARGER_VERSION "a22x-minimal-charger-v1"
#define POWER_ROOT "/sys/class/power_supply"
#define INPUT_ROOT "/sys/class/input"
#define KEY_BYTES ((KEY_MAX + 8) / 8)
#define EVENT_BUDGET (256U * 1024U)

static volatile sig_atomic_t stop_requested;
static int log_fd = -1;

static double monotonic_now(void)
{
    struct timespec t;
    if (clock_gettime(CLOCK_MONOTONIC, &t) != 0)
        return -1.0;
    return (double)t.tv_sec + (double)t.tv_nsec / 1000000000.0;
}

static void log_message(const char *format, ...)
{
    char body[448], line[512];
    va_list args;
    va_start(args, format);
    vsnprintf(body, sizeof(body), format, args);
    va_end(args);
    int n = snprintf(line, sizeof(line), "<6>a22x-charger: %s\n", body);
    if (n > 0) {
        size_t size = (size_t)n < sizeof(line) ? (size_t)n : sizeof(line) - 1;
        int fd = log_fd >= 0 ? log_fd : STDERR_FILENO;
        ssize_t ignored = write(fd, line, size);
        (void)ignored;
    }
}

/* Reads are bounded; a full buffer is never silently accepted as complete. */
static ssize_t read_small(const char *path, void *out, size_t capacity)
{
    if (capacity < 2) { errno = EINVAL; return -1; }
    int fd = open(path, O_RDONLY | O_CLOEXEC);
    if (fd < 0) return -1;
    size_t count = 0;
    while (count < capacity - 1) {
        ssize_t n = read(fd, (char *)out + count, capacity - 1 - count);
        if (n < 0 && errno == EINTR) continue;
        if (n < 0) { int e = errno; close(fd); errno = e; return -1; }
        if (n == 0) break;
        count += (size_t)n;
    }
    char extra;
    ssize_t tail;
    do { tail = read(fd, &extra, 1); } while (tail < 0 && errno == EINTR);
    int saved = errno;
    close(fd);
    if (tail != 0) { errno = tail > 0 ? EOVERFLOW : saved; return -1; }
    ((char *)out)[count] = '\0';
    return (ssize_t)count;
}

static int read_number(const char *path, int low, int high)
{
    char text[64], *end = NULL;
    ssize_t length = read_small(path, text, sizeof(text));
    if (length <= 0 || memchr(text, 0, (size_t)length)) return -1;
    errno = 0;
    long value = strtol(text, &end, 10);
    if (errno || end == text) return -1;
    while (*end && isspace((unsigned char)*end)) end++;
    if (*end || value < low || value > high) return -1;
    return (int)value;
}

static bool numbered_name(const char *name, const char *prefix,
                          const char *suffix)
{
    size_t n = strlen(prefix);
    if (strncmp(name, prefix, n)) return false;
    const char *p = name + n;
    if (!isdigit((unsigned char)*p)) return false;
    while (isdigit((unsigned char)*p)) p++;
    return strcmp(p, suffix) == 0;
}

struct power_sample {
    int charger, tcpm, online, capacity;
    bool battery_status_valid;
    char status[32];
};

static struct power_sample read_power_at(const char *root)
{
    struct power_sample result = {
        .charger = -1, .tcpm = -1, .online = -1, .capacity = -1,
        .battery_status_valid = false, .status = "Unknown"
    };
    unsigned int matches[2] = {0, 0};
    char paths[2][PATH_MAX] = {{0}};
    DIR *directory = opendir(root);
    if (directory) {
        struct dirent *entry;
        while ((entry = readdir(directory))) {
            int role = -1;
            if (numbered_name(entry->d_name, "mt6360-chg.", ".auto")) role = 0;
            if (numbered_name(entry->d_name,
                              "tcpm-source-psy-mt6360-tcpc.", ".auto")) role = 1;
            if (role < 0) continue;
            matches[role]++;
            int n = snprintf(paths[role], sizeof(paths[role]), "%s/%s/online",
                             root, entry->d_name);
            if (n < 0 || (size_t)n >= sizeof(paths[role]))
                matches[role] += 2;
        }
        closedir(directory);
    }
    if (matches[0] == 1) result.charger = read_number(paths[0], 0, 1);
    if (matches[1] == 1) result.tcpm = read_number(paths[1], 0, 1);
    result.online = cp_aggregate_online(result.charger, result.tcpm);
    char path[PATH_MAX];
    int n = snprintf(path, sizeof(path), "%s/battery/capacity", root);
    if (n > 0 && (size_t)n < sizeof(path))
        result.capacity = read_number(path, 0, 100);
    n = snprintf(path, sizeof(path), "%s/battery/status", root);
    ssize_t status_length = -1;
    if (n > 0 && (size_t)n < sizeof(path))
        status_length = read_small(path, result.status, sizeof(result.status));
    if (status_length > 0 &&
        !memchr(result.status, 0, (size_t)status_length)) {
        while (status_length > 0 &&
               isspace((unsigned char)result.status[status_length - 1]))
            result.status[--status_length] = 0;
        const char *allowed[] = {"Unknown", "Charging", "Discharging",
                                 "Not charging", "Full"};
        for (size_t i = 0; i < sizeof(allowed) / sizeof(allowed[0]); i++)
            if (!strcmp(result.status, allowed[i]))
                result.battery_status_valid = true;
    }
    return result;
}

static int current_charger_boot(void)
{
    char cmdline[8192], compatible[2048];
    ssize_t command_size = read_small("/proc/cmdline", cmdline, sizeof(cmdline));
    ssize_t device_size = read_small("/sys/firmware/devicetree/base/compatible",
                                    compatible, sizeof(compatible));
    if (command_size <= 0 || device_size <= 0) return 0;
    if (memchr(cmdline, 0, (size_t)command_size)) return 0;
    return cp_detect_boot(cmdline, compatible, (size_t)device_size);
}

struct power_input {
    int fd, previous_error;
    double retry_at, synced_at;
    bool dropped;
};

static void input_close(struct power_input *input, struct charge_policy *policy)
{
    if (input->fd >= 0) close(input->fd);
    input->fd = -1;
    input->dropped = false;
    cp_lose_key(policy);
}

static int input_snapshot(struct power_input *input,
                          struct charge_policy *policy)
{
    struct input_event events[64];
    size_t bytes = 0;
    for (;;) {
        ssize_t n = read(input->fd, events, sizeof(events));
        if (n < 0 && errno == EINTR) continue;
        if (n < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) break;
        if (n <= 0) { if (!n) errno = ENODEV; return -1; }
        bytes += (size_t)n;
        if (bytes > EVENT_BUDGET) { errno = EOVERFLOW; return -1; }
    }
    unsigned char keys[KEY_BYTES] = {0};
    if (ioctl(input->fd, EVIOCGKEY(sizeof(keys)), keys) < 0) return -1;
    input->synced_at = monotonic_now();
    if (input->synced_at < 0) { errno = EIO; return -1; }
    cp_resync_key(policy, (keys[KEY_POWER / 8] & (1U << (KEY_POWER % 8))) != 0,
                  input->synced_at);
    input->dropped = false;
    return 0;
}

static void input_failed(struct power_input *input,
                         struct charge_policy *policy, int error, double now)
{
    input_close(input, policy);
    input->retry_at = now + 1.0;
    if (input->previous_error != error) {
        log_message("input unavailable errno=%d; automatic poweroff blocked", error);
        input->previous_error = error;
    }
}

static void input_open(struct power_input *input,
                       struct charge_policy *policy, double now)
{
    if (input->fd >= 0 || now < input->retry_at) return;
    input->retry_at = now + 1.0;
    DIR *directory = opendir(INPUT_ROOT);
    char node[PATH_MAX] = {0};
    unsigned int matches = 0;
    if (directory) {
        struct dirent *entry;
        while ((entry = readdir(directory))) {
            if (!numbered_name(entry->d_name, "event", "")) continue;
            char path[PATH_MAX], name[96];
            int n = snprintf(path, sizeof(path), "%s/%s/device/name",
                             INPUT_ROOT, entry->d_name);
            if (n < 0 || (size_t)n >= sizeof(path) ||
                read_small(path, name, sizeof(name)) <= 0) continue;
            name[strcspn(name, "\r\n")] = 0;
            if (strcmp(name, "mtk-pmic-keys")) continue;
            matches++;
            n = snprintf(node, sizeof(node), "/dev/input/%s", entry->d_name);
            if (n < 0 || (size_t)n >= sizeof(node)) matches += 2;
        }
        closedir(directory);
    }
    if (matches != 1) {
        input_failed(input, policy, matches ? EEXIST : ENODEV, now);
        return;
    }
    input->fd = open(node, O_RDONLY | O_NONBLOCK | O_CLOEXEC);
    if (input->fd < 0) { input_failed(input, policy, errno, now); return; }
    char name[96] = {0};
    int event_clock = CLOCK_MONOTONIC;
    errno = 0;
    if (ioctl(input->fd, EVIOCGNAME(sizeof(name)), name) < 0 ||
        !memchr(name, 0, sizeof(name)) || strcmp(name, "mtk-pmic-keys") ||
        ioctl(input->fd, EVIOCSCLOCKID, &event_clock) < 0 ||
        input_snapshot(input, policy) < 0) {
        input_failed(input, policy, errno ? errno : ENODEV, now);
        return;
    }
    input->previous_error = 0;
    log_message("input ready %s initial_held=%d", node, policy->key_down);
}

static void input_read(struct power_input *input,
                       struct charge_policy *policy)
{
    if (input->fd < 0) return;
    size_t total = 0;
    struct input_event events[64];
    for (;;) {
        ssize_t bytes = read(input->fd, events, sizeof(events));
        if (bytes < 0 && errno == EINTR) continue;
        if (bytes < 0 && (errno == EAGAIN || errno == EWOULDBLOCK)) return;
        if (bytes <= 0 || (size_t)bytes % sizeof(events[0])) {
            input_failed(input, policy, bytes < 0 ? errno : EIO, monotonic_now());
            return;
        }
        total += (size_t)bytes;
        if (total > EVENT_BUDGET) {
            input_failed(input, policy, EOVERFLOW, monotonic_now());
            return;
        }
        for (size_t i = 0; i < (size_t)bytes / sizeof(events[0]); i++) {
            struct input_event *e = &events[i];
            double now = monotonic_now();
            double event_when = (double)e->time.tv_sec +
                                (double)e->time.tv_usec / 1000000.0;
            if (now < 0 || e->time.tv_sec < 0 || e->time.tv_usec < 0 ||
                e->time.tv_usec >= 1000000 || event_when > now + 0.01) {
                input_failed(input, policy, EIO, now);
                return;
            }
            if (event_when <= input->synced_at) {
                /* An edge queued around EVIOCGKEY cannot prove a fresh press.
                 * Resnapshot after such an edge; do not replay its batch. */
                if ((e->type == EV_KEY && e->code == KEY_POWER) ||
                    (e->type == EV_SYN && e->code == SYN_DROPPED)) {
                    if (input_snapshot(input, policy) < 0)
                        input_failed(input, policy, errno, now);
                    return;
                }
                continue;
            }
            if (input->dropped) {
                if (e->type == EV_SYN && e->code == SYN_REPORT) {
                    if (input_snapshot(input, policy) < 0)
                        input_failed(input, policy, errno, now);
                    return; /* Discard the rest of this pre-snapshot batch. */
                }
                continue;
            }
            if (e->type == EV_SYN && e->code == SYN_DROPPED) {
                cp_lose_key(policy);
                input->dropped = true;
                log_message("input SYN_DROPPED; waiting for a fresh released state");
            } else if (e->type == EV_KEY && e->code == KEY_POWER &&
                       (e->value == 0 || e->value == 1)) {
                cp_key(policy, e->value == 1, event_when);
            }
        }
    }
}

static void stop_handler(int signal_number)
{
    (void)signal_number;
    stop_requested = 1;
}

#ifndef CHARGER_CONTROLLER_TEST
static void pid1_hold(void)
{
    for (;;) {
        while (waitpid(-1, NULL, WNOHANG) > 0) {}
        struct timespec wait = {1, 0};
        nanosleep(&wait, NULL);
    }
}

static int run_charger(void)
{
    if (geteuid() != 0 || getpid() != 1 || !current_charger_boot()) {
        fprintf(stderr, "charger requires genuine a22x charger boot as PID 1\n");
        if (getpid() == 1) pid1_hold();
        return 77;
    }
    log_fd = open("/dev/kmsg", O_WRONLY | O_CLOEXEC);
    struct sigaction action = {0};
    action.sa_handler = stop_handler;
    sigemptyset(&action.sa_mask);
    sigaction(SIGTERM, &action, NULL);
    sigaction(SIGINT, &action, NULL);
    double now = monotonic_now();
    if (now < 0) { log_message("monotonic clock unavailable; no power action"); pid1_hold(); }
    struct charge_policy policy;
    cp_init(&policy, now);
    struct power_input input = {.fd = -1, .previous_error = 0,
                                .retry_at = 0, .dropped = false};
    struct power_sample sample = {.capacity = -1, .online = -1,
                                  .charger = -1, .tcpm = -1};
    double next_sample = 0, next_ui = 0, next_report = 0;
    bool ui_ready = false;
    int ui_error = 0, shown_capacity = -2, shown_online = -2, shown_on = -1;
    int last_capacity = -2, last_online = -2, last_charger = -2, last_tcpm = -2;
    log_message("%s active pid=1 rootfs=unmounted network=off normal_userspace=off",
                CHARGER_VERSION);
    while (!stop_requested) {
        now = monotonic_now();
        if (now < 0) { stop_requested = 1; break; }
        input_open(&input, &policy, now);
        input_read(&input, &policy);
        if (now >= next_sample) {
            sample = read_power_at(POWER_ROOT);
            now = monotonic_now();
            int effective = sample.online;
            if (effective == 0 &&
                (sample.capacity < 0 || !sample.battery_status_valid))
                effective = -1;
            cp_observe_online(&policy, effective, now);
            next_sample = now + 1.0;
        }
        /* PMIC/sysfs reads may block. Drain key releases with their original
         * monotonic event timestamps before deciding whether a hold is long. */
        input_read(&input, &policy);
        now = monotonic_now();
        if (stop_requested) break;
        enum charge_action requested = cp_decide(&policy, now);
        if (requested != CHARGE_NONE) {
            log_message("one action=%s; consumed before syscall",
                        requested == CHARGE_NORMAL_REBOOT ? "normal-reboot" : "poweroff");
            if (stop_requested) break;
            if (ui_ready) { charger_ui_close(); ui_ready = false; }
            sync();
            if (stop_requested) break;
            if (requested == CHARGE_NORMAL_REBOOT)
                syscall(SYS_reboot, LINUX_REBOOT_MAGIC1, LINUX_REBOOT_MAGIC2,
                        LINUX_REBOOT_CMD_RESTART2, "normal");
            else
                syscall(SYS_reboot, LINUX_REBOOT_MAGIC1, LINUX_REBOOT_MAGIC2,
                        LINUX_REBOOT_CMD_POWER_OFF, NULL);
            log_message("power syscall returned errno=%d; no automatic retry", errno);
            next_ui = now + 2.0;
        }
        if (!ui_ready && now >= next_ui) {
            if (charger_ui_open() == 0) {
                ui_ready = true;
                ui_error = 0;
                shown_capacity = shown_online = -2;
                shown_on = -1;
                policy.light_until = now + 12.0;
                log_message("display ready; framebuffer charging indicator only");
            } else {
                int error = errno;
                if (error != ui_error) log_message("display waiting errno=%d", error);
                ui_error = error;
                next_ui = now + 2.0;
            }
        }
        int screen_on = cp_visible(&policy, now);
        if (ui_ready && (shown_capacity != sample.capacity ||
                         shown_online != sample.online || shown_on != screen_on)) {
            if (charger_ui_render(sample.capacity, sample.online, screen_on) < 0) {
                int error = errno;
                if (error != ui_error) log_message("display render errno=%d", error);
                ui_error = error;
                charger_ui_close();
                ui_ready = false;
                next_ui = now + 2.0;
            } else {
                shown_capacity = sample.capacity;
                shown_online = sample.online;
                shown_on = screen_on;
            }
        }
        if (now >= next_report || sample.capacity != last_capacity ||
            sample.online != last_online || sample.charger != last_charger ||
            sample.tcpm != last_tcpm) {
            log_message("state capacity=%d online=%d charger=%d tcpm=%d key=%d display=%d visible=%d",
                        sample.capacity, sample.online, sample.charger, sample.tcpm,
                        policy.key_known, ui_ready, ui_ready && screen_on);
            last_capacity = sample.capacity;
            last_online = sample.online;
            last_charger = sample.charger;
            last_tcpm = sample.tcpm;
            next_report = now + 30.0;
        }
        while (waitpid(-1, NULL, WNOHANG) > 0) {}
        struct pollfd descriptor = {.fd = input.fd, .events = POLLIN};
        int result = poll(input.fd >= 0 ? &descriptor : NULL,
                          input.fd >= 0 ? 1 : 0, 250);
        if (result < 0 && errno != EINTR)
            input_failed(&input, &policy, errno, monotonic_now());
        if (result > 0 && (descriptor.revents & (POLLERR | POLLHUP | POLLNVAL)))
            input_failed(&input, &policy, ENODEV, monotonic_now());
    }
    input_close(&input, &policy);
    if (ui_ready) charger_ui_close();
    log_message("controller stopped; PID 1 held without reboot or full-system fallback");
    pid1_hold();
    return 0;
}

int main(int argc, char **argv)
{
    if (argc == 2 && !strcmp(argv[1], "--check-boot"))
        return current_charger_boot() ? 0 : 77;
    if (argc == 2 && !strcmp(argv[1], "--version")) {
        puts(CHARGER_VERSION);
        return 0;
    }
    if (argc == 2 && !strcmp(argv[1], "--probe")) {
        struct power_sample sample = read_power_at(POWER_ROOT);
        printf("{\"version\":\"%s\",\"probe_read_only\":true,\"charger_boot\":%s,"
               "\"capacity\":%d,\"online\":%d,\"charger\":%d,\"tcpm\":%d,"
               "\"battery_status_valid\":%s}\n",
               CHARGER_VERSION, current_charger_boot() ? "true" : "false",
               sample.capacity, sample.online, sample.charger, sample.tcpm,
               sample.battery_status_valid ? "true" : "false");
        return 0;
    }
    if (argc != 1) {
        fprintf(stderr, "usage: a22x-charger [--check-boot|--probe|--version]\n");
        return 2;
    }
    return run_charger();
}
#endif
