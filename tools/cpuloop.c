/*
 * cpuloop: time a fixed dependent loop to estimate the current CPU clock.
 *   cpuloop [MITER]   (default 500 M iterations; prints iterations/us)
 * Build: clang --target=aarch64-linux-gnu -Os -static -nostdlib -ffreestanding
 *        -fno-stack-protector -fuse-ld=lld -o cpuloop cpuloop.c
 */
typedef unsigned long u64;

static long sys(long n, long a, long b, long c)
{
	register long x8 asm("x8") = n;
	register long x0 asm("x0") = a;
	register long x1 asm("x1") = b;
	register long x2 asm("x2") = c;
	asm volatile("svc #0" : "+r"(x0) : "r"(x8), "r"(x1), "r"(x2) : "memory");
	return x0;
}

struct ts { long s, ns; };

static u64 now_ns(void)
{
	struct ts t;
	sys(113, 1 /* CLOCK_MONOTONIC */, (long)&t, 0);
	return (u64)t.s * 1000000000UL + t.ns;
}

static void put(const char *s, int n) { sys(64, 1, (long)s, n); }

static void putnum(u64 v)
{
	char b[24];
	int i = 23;
	b[i] = '\n';
	do { b[--i] = '0' + v % 10; v /= 10; } while (v);
	put(b + i, 24 - i);
}

void __attribute__((noreturn)) cmain(long argc, char **argv)
{
	u64 n = 500, total, t0, t1;
	if (argc > 1) {
		const char *p = argv[1];
		n = 0;
		while (*p) n = n * 10 + (*p++ - '0');
	}
	n *= 1000000UL;
	total = n;
	t0 = now_ns();
	asm volatile("1: subs %0, %0, #1\n b.ne 1b" : "+r"(n) :: "cc");
	t1 = now_ns();
	/* loop iterations per microsecond (~MHz for a 1 iteration/cycle core) */
	putnum(total * 1000 / (t1 - t0));
	sys(93, 0, 0, 0);
	__builtin_unreachable();
}

asm(".global _start\n_start:\n ldr x0, [sp]\n add x1, sp, #8\n b cmain\n");
