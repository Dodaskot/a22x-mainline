/*
 * memtool: minimal static /dev/mem peek/poke for bring-up (no libc).
 *   memtool ADDR            print the 32-bit word at physical ADDR
 *   memtool ADDR VALUE      write VALUE, then print the read-back
 * Build: aarch64-linux-gnu-gcc -Os -static -nostdlib -ffreestanding -o memtool memtool.c
 */
typedef unsigned long u64;
typedef unsigned int u32;

static long sys(long n, long a, long b, long c, long d, long e, long f)
{
	register long x8 asm("x8") = n;
	register long x0 asm("x0") = a;
	register long x1 asm("x1") = b;
	register long x2 asm("x2") = c;
	register long x3 asm("x3") = d;
	register long x4 asm("x4") = e;
	register long x5 asm("x5") = f;
	asm volatile("svc #0" : "+r"(x0) : "r"(x8), "r"(x1), "r"(x2), "r"(x3), "r"(x4), "r"(x5) : "memory");
	return x0;
}

#define SYS_openat 56
#define SYS_write 64
#define SYS_exit 93
#define SYS_mmap 222
#define AT_FDCWD -100
#define O_RDWR_SYNC 0x101002

static u64 parse(const char *s)
{
	u64 v = 0;
	int base = 10;

	if (s[0] == '0' && (s[1] == 'x' || s[1] == 'X')) {
		base = 16;
		s += 2;
	}
	for (; *s; s++) {
		int d = *s >= 'a' ? *s - 'a' + 10 : *s >= 'A' ? *s - 'A' + 10 : *s - '0';
		v = v * base + d;
	}
	return v;
}

static void out(u64 addr, u32 val)
{
	char buf[32];
	const char *hex = "0123456789abcdef";
	int i, n = 0;

	buf[n++] = '0';
	buf[n++] = 'x';
	for (i = 36; i >= 0; i -= 4)
		buf[n++] = hex[(addr >> i) & 0xf];
	buf[n++] = ' ';
	buf[n++] = '0';
	buf[n++] = 'x';
	for (i = 28; i >= 0; i -= 4)
		buf[n++] = hex[(val >> i) & 0xf];
	buf[n++] = '\n';
	sys(SYS_write, 1, (long)buf, n, 0, 0, 0);
}

void __attribute__((noreturn)) cmain(long argc, char **argv)
{
	u64 addr, page;
	volatile u32 *p;
	long fd, m;

	if (argc < 2)
		sys(SYS_exit, 2, 0, 0, 0, 0, 0);
	addr = parse(argv[1]);
	page = addr & ~0xfffUL;
	fd = sys(SYS_openat, AT_FDCWD, (long)"/dev/mem", O_RDWR_SYNC, 0, 0, 0);
	if (fd < 0)
		sys(SYS_exit, 3, 0, 0, 0, 0, 0);
	m = sys(SYS_mmap, 0, 0x1000, 3, 1, fd, page);
	if (m < 0 && m > -4096)
		sys(SYS_exit, 4, 0, 0, 0, 0, 0);
	p = (volatile u32 *)(m + (addr & 0xfff));
	if (argc > 2)
		*p = (u32)parse(argv[2]);
	out(addr, *p);
	sys(SYS_exit, 0, 0, 0, 0, 0, 0);
	__builtin_unreachable();
}

asm(".global _start\n"
    "_start:\n"
    "	ldr x0, [sp]\n"
    "	add x1, sp, #8\n"
    "	b cmain\n");
