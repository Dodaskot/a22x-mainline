/* SPDX-License-Identifier: GPL-2.0-only
 * Input-boundary regression tests. All evdev reads, ioctls, clock reads and
 * closes below are mocks; the controller's PID 1/runtime section is excluded.
 * Build with charger_policy.c. No device access, reboot or poweroff is possible.
 */
#define _GNU_SOURCE
#include <assert.h>
#include <errno.h>
#include <linux/input.h>
#include <stdbool.h>
#include <stdarg.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include <sys/ioctl.h>
#include <sys/types.h>
#include <time.h>
#include <unistd.h>

static ssize_t fixture_read(int fd, void *buffer, size_t count);
static int fixture_ioctl(int fd, unsigned long request, ...);
static int fixture_clock_gettime(clockid_t id, struct timespec *out);
static int fixture_close(int fd);

#define CHARGER_CONTROLLER_TEST
#define read fixture_read
#define ioctl fixture_ioctl
#define clock_gettime fixture_clock_gettime
#define close fixture_close
#include "../src/charger.c"
#undef read
#undef ioctl
#undef clock_gettime
#undef close

enum { FIXTURE_FD = 101, QUEUE_CAPACITY = 256 };
static struct {
    double now;
    struct input_event queue[QUEUE_CAPACITY];
    size_t queued;
    bool held;
    bool inject_snapshot_edges;
    bool inject_snapshot_loss;
    bool fail_clock;
    int read_error;
    int ioctl_error;
    bool short_read;
    unsigned int key_snapshots;
    unsigned int closes;
} fixture;

static void queue_event(unsigned short type, unsigned short code, int value,
                        long seconds, long micros)
{
    assert(fixture.queued < QUEUE_CAPACITY);
    struct input_event *event = &fixture.queue[fixture.queued++];
    memset(event, 0, sizeof(*event));
    event->time.tv_sec = seconds;
    event->time.tv_usec = micros;
    event->type = type;
    event->code = code;
    event->value = value;
}

static void queue_key(int value, long seconds, long micros)
{
    queue_event(EV_KEY, KEY_POWER, value, seconds, micros);
    queue_event(EV_SYN, SYN_REPORT, 0, seconds, micros);
    if (value == 0 || value == 1)
        fixture.held = value == 1;
}

static ssize_t fixture_read(int fd, void *buffer, size_t count)
{
    assert(fd == FIXTURE_FD);
    if (fixture.read_error) {
        errno = fixture.read_error;
        fixture.read_error = 0;
        return -1;
    }
    if (fixture.short_read) {
        fixture.short_read = false;
        assert(count > 0);
        ((unsigned char *)buffer)[0] = 0;
        return 1;
    }
    if (!fixture.queued) {
        errno = EAGAIN;
        return -1;
    }
    size_t events = count / sizeof(fixture.queue[0]);
    assert(events > 0);
    if (events > fixture.queued)
        events = fixture.queued;
    memcpy(buffer, fixture.queue, events * sizeof(fixture.queue[0]));
    fixture.queued -= events;
    memmove(fixture.queue, fixture.queue + events,
            fixture.queued * sizeof(fixture.queue[0]));
    return (ssize_t)(events * sizeof(fixture.queue[0]));
}

static int fixture_ioctl(int fd, unsigned long request, ...)
{
    assert(fd == FIXTURE_FD);
    va_list args;
    va_start(args, request);
    void *out = va_arg(args, void *);
    va_end(args);
    if (fixture.ioctl_error) {
        errno = fixture.ioctl_error;
        fixture.ioctl_error = 0;
        return -1;
    }
    if (request == EVIOCGKEY(KEY_BYTES)) {
        fixture.key_snapshots++;
        if (fixture.inject_snapshot_edges) {
            /* These events arrived after drain reached EAGAIN but before the
             * snapshot. Current key state is held again; they must not later
             * replay as a fresh released-then-pressed sequence. */
            fixture.inject_snapshot_edges = false;
            queue_key(0, 4, 800000);
            queue_key(1, 4, 900000);
        }
        memset(out, 0, KEY_BYTES);
        if (fixture.held)
            ((unsigned char *)out)[KEY_POWER / 8] |=
                (unsigned char)(1U << (KEY_POWER % 8));
        if (fixture.inject_snapshot_loss) {
            /* State was copied as released. The event queue overflow and a
             * press happen before the caller records the snapshot boundary. */
            fixture.inject_snapshot_loss = false;
            fixture.held = true;
            queue_event(EV_SYN, SYN_DROPPED, 0, 4, 999000);
            queue_event(EV_SYN, SYN_REPORT, 0, 4, 999000);
        }
        return KEY_BYTES;
    }
    assert(!"unexpected ioctl in input snapshot test");
    errno = EINVAL;
    return -1;
}

static int fixture_clock_gettime(clockid_t id, struct timespec *out)
{
    assert(id == CLOCK_MONOTONIC);
    if (fixture.fail_clock) {
        errno = EIO;
        return -1;
    }
    out->tv_sec = (time_t)fixture.now;
    out->tv_nsec = (long)((fixture.now - (double)out->tv_sec) * 1000000000.0 + 0.5);
    if (out->tv_nsec >= 1000000000L) {
        out->tv_sec++;
        out->tv_nsec -= 1000000000L;
    }
    return 0;
}

static int fixture_close(int fd)
{
    assert(fd == FIXTURE_FD);
    fixture.closes++;
    return 0;
}

static void setup_released(struct power_input *input, struct charge_policy *policy)
{
    memset(&fixture, 0, sizeof(fixture));
    memset(input, 0, sizeof(*input));
    input->fd = FIXTURE_FD;
    cp_init(policy, 0.0);
    assert(input_snapshot(input, policy) == 0);
    assert(policy->key_known && !policy->key_down && !policy->ignore_held);
}

static void short_press_during_blocked_power_sample(void)
{
    struct power_input input;
    struct charge_policy policy;
    setup_released(&input, &policy);

    fixture.now = 1.0;
    queue_key(1, 1, 0);
    input_read(&input, &policy);
    assert(policy.have_press && policy.key_down);
    assert(cp_decide(&policy, fixture.now) == CHARGE_NONE);

    /* Simulate a slow PMIC/sysfs operation between the loop's first and second
     * input drains. The release happened at 1.1, not at processing time 4.0. */
    fixture.now = 4.0;
    queue_key(0, 1, 100000);
    input_read(&input, &policy);
    assert(!policy.key_down && !policy.pending_long && !policy.have_press);
    assert(cp_decide(&policy, fixture.now) == CHARGE_NONE);
    assert(cp_decide(&policy, 20.0) == CHARGE_NONE);
}

static void genuine_long_hold_is_consumed_once(void)
{
    struct power_input input;
    struct charge_policy policy;
    setup_released(&input, &policy);
    fixture.now = 1.0;
    queue_key(1, 1, 0);
    input_read(&input, &policy);
    assert(cp_decide(&policy, 2.999) == CHARGE_NONE);
    assert(cp_decide(&policy, 3.001) == CHARGE_NORMAL_REBOOT);
    assert(cp_decide(&policy, 10.0) == CHARGE_NONE);

    /* A failed physical action must not authorize another action later. */
    fixture.now = 11.0;
    queue_key(0, 11, 0);
    input_read(&input, &policy);
    fixture.now = 12.0;
    queue_key(1, 12, 0);
    input_read(&input, &policy);
    assert(cp_decide(&policy, 15.0) == CHARGE_NONE);
}

static void genuine_queued_long_release_keeps_original_duration(void)
{
    struct power_input input;
    struct charge_policy policy;
    setup_released(&input, &policy);
    fixture.now = 1.0;
    queue_key(1, 1, 0);
    input_read(&input, &policy);
    fixture.now = 7.0;
    queue_key(0, 3, 100000);
    input_read(&input, &policy);
    assert(!policy.key_down && policy.pending_long);
    assert(cp_decide(&policy, 7.0) == CHARGE_NORMAL_REBOOT);
    assert(cp_decide(&policy, 8.0) == CHARGE_NONE);
}

static void snapshot_gap_edges_cannot_arm_initially_held_key(void)
{
    struct power_input input = {.fd = FIXTURE_FD};
    struct charge_policy policy;
    memset(&fixture, 0, sizeof(fixture));
    fixture.now = 5.0;
    fixture.held = true;
    fixture.inject_snapshot_edges = true;
    cp_init(&policy, 5.0);
    assert(input_snapshot(&input, &policy) == 0);
    assert(fixture.queued == 4);
    assert(policy.key_known && policy.key_down && policy.ignore_held);

    fixture.now = 6.0;
    input_read(&input, &policy);
    assert(fixture.key_snapshots == 2);
    assert(policy.key_known && policy.key_down && policy.ignore_held);
    assert(!policy.have_press && !policy.pending_long);
    assert(cp_decide(&policy, 20.0) == CHARGE_NONE);

    /* A release occurring after the new snapshot restores ordinary operation. */
    fixture.now = 20.1;
    queue_key(0, 20, 100000);
    input_read(&input, &policy);
    fixture.now = 20.2;
    queue_key(1, 20, 200000);
    input_read(&input, &policy);
    assert(cp_decide(&policy, 22.3) == CHARGE_NORMAL_REBOOT);
    assert(cp_decide(&policy, 30.0) == CHARGE_NONE);
}

static void syn_dropped_resync_ignores_same_batch_edges(void)
{
    struct power_input input;
    struct charge_policy policy;
    setup_released(&input, &policy);
    fixture.now = 1.0;
    queue_key(1, 1, 0);
    input_read(&input, &policy);
    fixture.now = 2.0;
    queue_event(EV_SYN, SYN_DROPPED, 0, 1, 400000);
    queue_event(EV_KEY, KEY_POWER, 0, 1, 500000);
    queue_event(EV_SYN, SYN_REPORT, 0, 1, 500000);
    queue_key(0, 1, 700000);
    queue_key(1, 1, 800000);
    assert(fixture.held);
    input_read(&input, &policy);
    assert(!input.dropped && fixture.key_snapshots == 2);
    assert(policy.key_known && policy.key_down && policy.ignore_held);
    assert(!policy.have_press && !policy.pending_long);
    assert(cp_decide(&policy, 50.0) == CHARGE_NONE);
}

static void syn_dropped_without_report_blocks_all_actions(void)
{
    struct power_input input;
    struct charge_policy policy;
    setup_released(&input, &policy);
    fixture.now = 1.0;
    queue_key(1, 1, 0);
    input_read(&input, &policy);
    fixture.now = 2.0;
    queue_event(EV_SYN, SYN_DROPPED, 0, 2, 0);
    input_read(&input, &policy);
    assert(input.dropped && !policy.key_known);
    cp_observe_online(&policy, 0, 20.0);
    cp_observe_online(&policy, 0, 21.0);
    cp_observe_online(&policy, 0, 22.0);
    cp_observe_online(&policy, 0, 23.0);
    assert(cp_decide(&policy, 23.0) == CHARGE_NONE);
}

static void snapshot_gap_syn_dropped_cannot_keep_false_released_state(void)
{
    struct power_input input = {.fd = FIXTURE_FD};
    struct charge_policy policy;
    memset(&fixture, 0, sizeof(fixture));
    fixture.now = 5.0;
    fixture.held = false;
    fixture.inject_snapshot_loss = true;
    cp_init(&policy, 0.0);
    assert(input_snapshot(&input, &policy) == 0);
    assert(!policy.key_down && fixture.held && fixture.queued == 2);

    fixture.now = 6.0;
    input_read(&input, &policy);
    /* The lost edge cannot authorize automatic poweroff while actually held. */
    for (int second = 10; second <= 13; second++)
        cp_observe_online(&policy, 0, (double)second);
    assert(cp_decide(&policy, 13.0) == CHARGE_NONE);
    assert(policy.key_known && policy.key_down && policy.ignore_held);
}

static void input_error_cancels_pending_hold(void)
{
    struct power_input input;
    struct charge_policy policy;
    setup_released(&input, &policy);
    fixture.now = 1.0;
    queue_key(1, 1, 0);
    input_read(&input, &policy);
    fixture.now = 4.0;
    fixture.read_error = EIO;
    input_read(&input, &policy);
    assert(input.fd == -1 && fixture.closes == 1 && !policy.key_known);
    assert(cp_decide(&policy, 4.0) == CHARGE_NONE);
}

static void bad_future_timestamp_blocks_actions(void)
{
    struct power_input input;
    struct charge_policy policy;
    setup_released(&input, &policy);
    fixture.now = 2.0;
    queue_key(1, 9, 0);
    input_read(&input, &policy);
    assert(input.fd == -1 && !policy.key_known);
    assert(cp_decide(&policy, 100.0) == CHARGE_NONE);
}

static void invalid_microseconds_block_actions(void)
{
    struct power_input input;
    struct charge_policy policy;
    setup_released(&input, &policy);
    fixture.now = 5.0;
    queue_key(1, 1, 1000000);
    input_read(&input, &policy);
    assert(input.fd == -1 && !policy.key_known);
    assert(cp_decide(&policy, 100.0) == CHARGE_NONE);
}

static void resync_ioctl_failure_blocks_actions(void)
{
    struct power_input input;
    struct charge_policy policy;
    setup_released(&input, &policy);
    fixture.now = 1.0;
    queue_key(1, 1, 0);
    input_read(&input, &policy);
    fixture.now = 2.0;
    queue_event(EV_SYN, SYN_DROPPED, 0, 2, 0);
    queue_event(EV_SYN, SYN_REPORT, 0, 2, 0);
    fixture.ioctl_error = EIO;
    input_read(&input, &policy);
    assert(input.fd == -1 && !policy.key_known);
    assert(cp_decide(&policy, 20.0) == CHARGE_NONE);
}

static void partial_event_read_blocks_actions(void)
{
    struct power_input input;
    struct charge_policy policy;
    setup_released(&input, &policy);
    fixture.now = 1.0;
    fixture.short_read = true;
    input_read(&input, &policy);
    assert(input.fd == -1 && !policy.key_known);
    assert(cp_decide(&policy, 20.0) == CHARGE_NONE);
}

int main(void)
{
    short_press_during_blocked_power_sample();
    genuine_long_hold_is_consumed_once();
    genuine_queued_long_release_keeps_original_duration();
    snapshot_gap_edges_cannot_arm_initially_held_key();
    syn_dropped_resync_ignores_same_batch_edges();
    syn_dropped_without_report_blocks_all_actions();
    snapshot_gap_syn_dropped_cannot_keep_false_released_state();
    input_error_cancels_pending_hold();
    bad_future_timestamp_blocks_actions();
    invalid_microseconds_block_actions();
    resync_ioctl_failure_blocks_actions();
    partial_event_read_blocks_actions();
    puts("controller input: 12 regression cases PASS (mocked evdev, no hardware)");
    return 0;
}
