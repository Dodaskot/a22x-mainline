/* SPDX-License-Identifier: GPL-2.0-only
 * Tests the actual controller readers against private temporary files only.
 * CHARGER_CONTROLLER_TEST removes the live PID 1 loop and real CLI entrypoint.
 */
#define CHARGER_CONTROLLER_TEST
#include "../src/charger.c"

static unsigned int io_checks, io_failures, io_scenarios, next_fixture;
static char temp_root[PATH_MAX];

#define IO_CHECK(expr) do { \
    ++io_checks; \
    if (!(expr)) { \
        ++io_failures; \
        fprintf(stderr, "%s:%d: %s\n", __func__, __LINE__, #expr); \
    } \
} while (0)

static void fatal(const char *what)
{
    perror(what);
    exit(2);
}

static void remove_tree(const char *path)
{
    DIR *d = opendir(path);
    if (!d) {
        (void)unlink(path);
        return;
    }
    struct dirent *e;
    while ((e = readdir(d))) {
        if (!strcmp(e->d_name, ".") || !strcmp(e->d_name, ".."))
            continue;
        char child[PATH_MAX];
        int n = snprintf(child, sizeof(child), "%s/%s", path, e->d_name);
        if (n < 0 || (size_t)n >= sizeof(child))
            fatal("cleanup path");
        struct stat st;
        if (lstat(child, &st) != 0)
            fatal("cleanup lstat");
        if (S_ISDIR(st.st_mode))
            remove_tree(child);
        else if (unlink(child) != 0)
            fatal("cleanup unlink");
    }
    if (closedir(d) != 0 || rmdir(path) != 0)
        fatal("cleanup directory");
}

static void cleanup(void)
{
    if (temp_root[0])
        remove_tree(temp_root);
}

static void path_for(char out[PATH_MAX], const char *root, const char *relative)
{
    int n = snprintf(out, PATH_MAX, "%s/%s", root, relative);
    if (n < 0 || n >= PATH_MAX)
        fatal("fixture path");
}

static void make_directory(const char *root, const char *relative)
{
    char path[PATH_MAX];
    path_for(path, root, relative);
    if (mkdir(path, 0700) != 0)
        fatal("fixture mkdir");
}

static void put_bytes(const char *root, const char *relative,
                      const void *data, size_t length)
{
    char path[PATH_MAX];
    path_for(path, root, relative);
    int fd = open(path, O_WRONLY | O_CREAT | O_TRUNC | O_CLOEXEC, 0600);
    if (fd < 0)
        fatal("fixture open");
    size_t done = 0;
    while (done < length) {
        ssize_t n = write(fd, (const char *)data + done, length - done);
        if (n < 0 && errno == EINTR)
            continue;
        if (n <= 0)
            fatal("fixture write");
        done += (size_t)n;
    }
    if (close(fd) != 0)
        fatal("fixture close");
}

static void put_text(const char *root, const char *relative, const char *text)
{
    put_bytes(root, relative, text, strlen(text));
}

static void drop_file(const char *root, const char *relative)
{
    char path[PATH_MAX];
    path_for(path, root, relative);
    if (unlink(path) != 0)
        fatal("fixture unlink");
}

static void fixture(char out[PATH_MAX])
{
    char leaf[48];
    snprintf(leaf, sizeof(leaf), "case-%u", ++next_fixture);
    make_directory(temp_root, leaf);
    path_for(out, temp_root, leaf);
    make_directory(out, "battery");
    make_directory(out, "mt6360-chg.5.auto");
    make_directory(out, "tcpm-source-psy-mt6360-tcpc.8.auto");
    put_text(out, "battery/capacity", "73\n");
    put_text(out, "battery/status", "Charging\n");
    put_text(out, "mt6360-chg.5.auto/online", "0\n");
    put_text(out, "tcpm-source-psy-mt6360-tcpc.8.auto/online", "0\n");
}

/* Apply the caller's actual documented sample-admission contract before the
 * real pure policy. read_power_at.online describes provider evidence; an
 * invalid battery sample must independently block automatic shutdown. */
static enum charge_action sample_policy_result(struct power_sample sample)
{
    struct charge_policy policy;
    cp_init(&policy, 0.0);
    cp_resync_key(&policy, false, 0.0);
    int effective = sample.online;
    if (effective == 0 &&
        (sample.capacity < 0 || !sample.battery_status_valid))
        effective = -1;
    for (int t = 10; t <= 30; ++t) {
        cp_observe_online(&policy, effective, (double)t);
        enum charge_action result = cp_decide(&policy, (double)t);
        if (result != CHARGE_NONE)
            return result;
    }
    return CHARGE_NONE;
}

static void test_read_small_bounds(void)
{
    char root[PATH_MAX], path[PATH_MAX];
    struct {
        unsigned char before[8];
        char output[8];
        unsigned char after[8];
    } guarded;
    ++io_scenarios;
    fixture(root);
    path_for(path, root, "small");
    memset(&guarded, 0xA5, sizeof(guarded));

    put_text(root, "small", "");
    IO_CHECK(read_small(path, guarded.output, sizeof(guarded.output)) == 0);
    IO_CHECK(guarded.output[0] == '\0');
    put_text(root, "small", "1234567");
    IO_CHECK(read_small(path, guarded.output, sizeof(guarded.output)) == 7);
    IO_CHECK(!memcmp(guarded.output, "1234567", 8));
    put_text(root, "small", "12345678");
    errno = 0;
    IO_CHECK(read_small(path, guarded.output, sizeof(guarded.output)) == -1);
    IO_CHECK(errno == EOVERFLOW);
    for (size_t i = 0; i < sizeof(guarded.before); ++i)
        IO_CHECK(guarded.before[i] == 0xA5 && guarded.after[i] == 0xA5);

    static const char binary[] = {'A', '\0', 'B'};
    put_bytes(root, "small", binary, sizeof(binary));
    IO_CHECK(read_small(path, guarded.output, sizeof(guarded.output)) == 3);
    IO_CHECK(!memcmp(guarded.output, binary, sizeof(binary)));
    IO_CHECK(guarded.output[3] == '\0'); /* read_small supports binary DT data */
    errno = 0;
    IO_CHECK(read_small(path, guarded.output, 1) == -1 && errno == EINVAL);
    errno = 0;
    IO_CHECK(read_small(path, guarded.output, 0) == -1 && errno == EINVAL);

    drop_file(root, "small");
    IO_CHECK(read_small(path, guarded.output, sizeof(guarded.output)) == -1);
    IO_CHECK(read_small(root, guarded.output, sizeof(guarded.output)) == -1);
}

static void test_read_number_validation(void)
{
    char root[PATH_MAX], path[PATH_MAX];
    static const struct {
        const char *value;
        int low, high, expected;
    } cases[] = {
        {"0", 0, 1, 0}, {"1\n", 0, 1, 1}, {" \t1 \r\n", 0, 1, 1},
        {"0\n", 0, 100, 0}, {"100\n", 0, 100, 100}, {"73", 0, 100, 73},
        {"", 0, 1, -1}, {" \n", 0, 1, -1}, {"2\n", 0, 1, -1},
        {"-1", 0, 1, -1}, {"0junk", 0, 1, -1}, {"1 0", 0, 1, -1},
        {"0x0", 0, 1, -1}, {"NaN", 0, 1, -1},
        {"9223372036854775808", 0, 100, -1},
        {"-9223372036854775809", 0, 100, -1},
        {"-1", 0, 100, -1}, {"101", 0, 100, -1},
        {"73\njunk", 0, 100, -1}
    };
    ++io_scenarios;
    fixture(root);
    path_for(path, root, "number");
    for (size_t i = 0; i < sizeof(cases) / sizeof(cases[0]); ++i) {
        put_text(root, "number", cases[i].value);
        IO_CHECK(read_number(path, cases[i].low, cases[i].high) == cases[i].expected);
    }

    static const char nul_online[] = {'0', '\0', 'x'};
    static const char nul_capacity[] = {'7', '3', '\0', '\n'};
    put_bytes(root, "number", nul_online, sizeof(nul_online));
    IO_CHECK(read_number(path, 0, 1) == -1);
    put_bytes(root, "number", nul_capacity, sizeof(nul_capacity));
    IO_CHECK(read_number(path, 0, 100) == -1);
    char long_zeros[96];
    memset(long_zeros, '0', sizeof(long_zeros));
    put_bytes(root, "number", long_zeros, sizeof(long_zeros));
    IO_CHECK(read_number(path, 0, 1) == -1);
    drop_file(root, "number");
    IO_CHECK(read_number(path, 0, 1) == -1);
}

static void test_aggregate_provider_evidence(void)
{
    char root[PATH_MAX];
    ++io_scenarios;
    fixture(root);
    static const char *const values[] = {"?", "0\n", "1\n"};
    for (int a = -1; a <= 1; ++a) {
        for (int b = -1; b <= 1; ++b) {
            put_text(root, "mt6360-chg.5.auto/online", values[a + 1]);
            put_text(root, "tcpm-source-psy-mt6360-tcpc.8.auto/online",
                     values[b + 1]);
            struct power_sample s = read_power_at(root);
            int expected = (a == 1 || b == 1) ? 1 :
                           (a == 0 && b == 0) ? 0 : -1;
            IO_CHECK(s.charger == a && s.tcpm == b && s.online == expected);
            IO_CHECK(s.capacity == 73 && s.battery_status_valid);
            IO_CHECK(sample_policy_result(s) ==
                     (expected == 0 ? CHARGE_POWEROFF : CHARGE_NONE));
        }
    }
}

static void test_missing_and_ambiguous_providers(void)
{
    char root[PATH_MAX], absent[PATH_MAX];
    ++io_scenarios;
    fixture(root);
    drop_file(root, "mt6360-chg.5.auto/online");
    struct power_sample s = read_power_at(root);
    IO_CHECK(s.charger == -1 && s.tcpm == 0 && s.online == -1);
    IO_CHECK(sample_policy_result(s) == CHARGE_NONE);
    put_text(root, "tcpm-source-psy-mt6360-tcpc.8.auto/online", "1\n");
    s = read_power_at(root);
    IO_CHECK(s.charger == -1 && s.online == 1);

    put_text(root, "mt6360-chg.5.auto/online", "0\n");
    put_text(root, "tcpm-source-psy-mt6360-tcpc.8.auto/online", "0\n");
    make_directory(root, "mt6360-chg.6.auto");
    put_text(root, "mt6360-chg.6.auto/online", "0\n");
    s = read_power_at(root);
    IO_CHECK(s.charger == -1 && s.tcpm == 0 && s.online == -1);
    IO_CHECK(sample_policy_result(s) == CHARGE_NONE);
    put_text(root, "tcpm-source-psy-mt6360-tcpc.8.auto/online", "1\n");
    s = read_power_at(root);
    IO_CHECK(s.charger == -1 && s.online == 1);

    fixture(root);
    make_directory(root, "tcpm-source-psy-mt6360-tcpc.009.auto");
    put_text(root, "tcpm-source-psy-mt6360-tcpc.009.auto/online", "0\n");
    s = read_power_at(root);
    IO_CHECK(s.charger == 0 && s.tcpm == -1 && s.online == -1);
    IO_CHECK(sample_policy_result(s) == CHARGE_NONE);

    path_for(absent, temp_root, "nonexistent");
    s = read_power_at(absent);
    IO_CHECK(s.charger == -1 && s.tcpm == -1 && s.online == -1);
    IO_CHECK(s.capacity == -1 && !s.battery_status_valid);
    IO_CHECK(sample_policy_result(s) == CHARGE_NONE);
}

static void test_provider_name_anchoring(void)
{
    char root[PATH_MAX], relative[256];
    static const char *const extra[] = {
        "usb", "ac", "foo-mt6360-chg.5.auto", "mt6360-chg.bad.auto",
        "mt6360-chg..auto", "mt6360-chg.5.auto.extra",
        "tcpm-source-psy-mt6360-tcpc.bad.auto",
        "tcpm-source-psy-mt6360-tcpc.8.autox"
    };
    ++io_scenarios;
    fixture(root);
    for (size_t i = 0; i < sizeof(extra) / sizeof(extra[0]); ++i) {
        make_directory(root, extra[i]);
        int n = snprintf(relative, sizeof(relative), "%s/online", extra[i]);
        if (n < 0 || (size_t)n >= sizeof(relative))
            fatal("extra provider path");
        put_text(root, relative, "1\n");
    }
    struct power_sample s = read_power_at(root);
    IO_CHECK(s.charger == 0 && s.tcpm == 0 && s.online == 0);
    IO_CHECK(sample_policy_result(s) == CHARGE_POWEROFF);
}

static void test_invalid_online_never_proves_disconnect(void)
{
    char root[PATH_MAX];
    static const char *const values[] = {
        "", " ", "2", "-1", "0junk", "0\njunk",
        "999999999999999999999999999999"
    };
    static const char *const sources[] = {
        "mt6360-chg.5.auto/online",
        "tcpm-source-psy-mt6360-tcpc.8.auto/online"
    };
    ++io_scenarios;
    fixture(root);
    for (size_t role = 0; role < 2; ++role) {
        for (size_t i = 0; i < sizeof(values) / sizeof(values[0]); ++i) {
            put_text(root, sources[role], values[i]);
            struct power_sample s = read_power_at(root);
            IO_CHECK(s.online == -1);
            IO_CHECK((role == 0 ? s.charger : s.tcpm) == -1);
            IO_CHECK(sample_policy_result(s) == CHARGE_NONE);
        }
        static const char nul_value[] = {'0', '\0', 'b', 'a', 'd'};
        put_bytes(root, sources[role], nul_value, sizeof(nul_value));
        struct power_sample s = read_power_at(root);
        IO_CHECK(s.online == -1);
        IO_CHECK(sample_policy_result(s) == CHARGE_NONE);
        char too_long[96];
        memset(too_long, '0', sizeof(too_long));
        put_bytes(root, sources[role], too_long, sizeof(too_long));
        s = read_power_at(root);
        IO_CHECK(s.online == -1);
        IO_CHECK(sample_policy_result(s) == CHARGE_NONE);
        put_text(root, sources[role], "0\n");
    }
}

static void test_invalid_capacity_blocks_policy(void)
{
    char root[PATH_MAX];
    static const char *const values[] = {
        "", " ", "-1", "101", "73\njunk", "73x",
        "9223372036854775808"
    };
    ++io_scenarios;
    fixture(root);
    for (size_t i = 0; i < sizeof(values) / sizeof(values[0]); ++i) {
        put_text(root, "battery/capacity", values[i]);
        struct power_sample s = read_power_at(root);
        IO_CHECK(s.online == 0 && s.capacity == -1 && s.battery_status_valid);
        IO_CHECK(sample_policy_result(s) == CHARGE_NONE);
    }
    static const char nul_value[] = {'7', '3', '\0', 'x'};
    put_bytes(root, "battery/capacity", nul_value, sizeof(nul_value));
    struct power_sample s = read_power_at(root);
    IO_CHECK(s.capacity == -1);
    IO_CHECK(sample_policy_result(s) == CHARGE_NONE);

    char too_long[96];
    memset(too_long, '0', sizeof(too_long));
    put_bytes(root, "battery/capacity", too_long, sizeof(too_long));
    s = read_power_at(root);
    IO_CHECK(s.capacity == -1);
    IO_CHECK(sample_policy_result(s) == CHARGE_NONE);
    drop_file(root, "battery/capacity");
    s = read_power_at(root);
    IO_CHECK(s.capacity == -1);
    IO_CHECK(sample_policy_result(s) == CHARGE_NONE);

    put_text(root, "battery/capacity", "0\n");
    s = read_power_at(root);
    IO_CHECK(s.capacity == 0);
    IO_CHECK(sample_policy_result(s) == CHARGE_POWEROFF);
    put_text(root, "battery/capacity", "100\n");
    s = read_power_at(root);
    IO_CHECK(s.capacity == 100);
    IO_CHECK(sample_policy_result(s) == CHARGE_POWEROFF);
}

static void test_invalid_status_blocks_policy(void)
{
    char root[PATH_MAX];
    static const char *const allowed[] = {
        "Unknown\n", "Charging\n", "Discharging\n", "Not charging\n", "Full\n"
    };
    static const char *const invalid[] = {
        "", " ", "Almost full", "Full\njunk", "Full\rjunk",
        "Charging\nFull", "Unknown\001"
    };
    ++io_scenarios;
    fixture(root);
    for (size_t i = 0; i < sizeof(allowed) / sizeof(allowed[0]); ++i) {
        put_text(root, "battery/status", allowed[i]);
        struct power_sample s = read_power_at(root);
        IO_CHECK(s.battery_status_valid && s.online == 0 && s.capacity == 73);
        IO_CHECK(sample_policy_result(s) == CHARGE_POWEROFF);
    }
    for (size_t i = 0; i < sizeof(invalid) / sizeof(invalid[0]); ++i) {
        put_text(root, "battery/status", invalid[i]);
        struct power_sample s = read_power_at(root);
        IO_CHECK(!s.battery_status_valid && s.online == 0 && s.capacity == 73);
        IO_CHECK(sample_policy_result(s) == CHARGE_NONE);
    }
    static const char nul_value[] = {'F', 'u', 'l', 'l', '\0', 'x'};
    put_bytes(root, "battery/status", nul_value, sizeof(nul_value));
    struct power_sample s = read_power_at(root);
    IO_CHECK(!s.battery_status_valid);
    IO_CHECK(sample_policy_result(s) == CHARGE_NONE);

    char too_long[96];
    memset(too_long, 'x', sizeof(too_long));
    put_bytes(root, "battery/status", too_long, sizeof(too_long));
    s = read_power_at(root);
    IO_CHECK(!s.battery_status_valid);
    IO_CHECK(sample_policy_result(s) == CHARGE_NONE);
    drop_file(root, "battery/status");
    s = read_power_at(root);
    IO_CHECK(!s.battery_status_valid);
    IO_CHECK(sample_policy_result(s) == CHARGE_NONE);
}

int main(void)
{
    strcpy(temp_root, "/tmp/a22x-controller-io-XXXXXX");
    if (!mkdtemp(temp_root))
        fatal("mkdtemp");
    if (atexit(cleanup) != 0)
        fatal("atexit");
    test_read_small_bounds();
    test_read_number_validation();
    test_aggregate_provider_evidence();
    test_missing_and_ambiguous_providers();
    test_provider_name_anchoring();
    test_invalid_online_never_proves_disconnect();
    test_invalid_capacity_blocks_policy();
    test_invalid_status_blocks_policy();
    printf("%s: %u controller I/O scenarios, %u checks, %u failures; "
           "only temporary regular files used\n",
           io_failures ? "FAIL" : "PASS", io_scenarios, io_checks, io_failures);
    return io_failures ? 1 : 0;
}
