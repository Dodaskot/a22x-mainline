/* SPDX-License-Identifier: GPL-2.0 */
/* Glue between the 4.14 vendor connectivity code and the mainline kernel. */
#ifndef __MTK_COMPAT_H
#define __MTK_COMPAT_H

#include <linux/io.h>
#include <linux/types.h>
#include <linux/string.h>
#include <linux/time64.h>
#include <linux/timekeeping.h>
#include <linux/ktime.h>
#include <linux/dma-mapping.h>
#include <linux/vmalloc.h>
#include <linux/slab.h>

/* ---- time: struct timeval / timespec and friends are gone from the kernel ---- */
#ifndef __COMPAT_TIMEVAL
#define __COMPAT_TIMEVAL
struct timeval {
	long tv_sec;
	long tv_usec;
};
#endif
#define timespec timespec64
static inline void do_gettimeofday(struct timeval *tv)
{
	struct timespec64 ts;

	ktime_get_real_ts64(&ts);
	tv->tv_sec = ts.tv_sec;
	tv->tv_usec = ts.tv_nsec / NSEC_PER_USEC;
}
#define ktime_to_timespec(kt)		ktime_to_timespec64(kt)
#define getnstimeofday(ts)		ktime_get_real_ts64(ts)
#define get_monotonic_boottime(ts)	ktime_get_boottime_ts64(ts)
#define getrawmonotonic(ts)		ktime_get_raw_ts64(ts)
#define timespec_to_ns(ts)		timespec64_to_ns(ts)
#define current_kernel_time()		({ struct timespec64 __t; ktime_get_coarse_real_ts64(&__t); __t; })

/* ---- string ---- */
static inline char *compat_strncpy(char *d, const char *s, size_t n)
{
	size_t l = strnlen(s, n);

	memcpy(d, s, l);
	memset(d + l, 0, n - l);
	return d;
}
#define strncpy(d, s, n)		compat_strncpy(d, s, n)

/* ---- DMA: buffers handed to the hardware come from dma_alloc_coherent() ---- */
#define dma_zalloc_coherent(d, s, h, f)	dma_alloc_coherent(d, s, h, f)
#define __dma_flush_area(p, l)		do { (void)(p); (void)(l); } while (0)
#define __dma_unmap_area(p, l, d)	do { (void)(p); (void)(l); } while (0)
#define __dma_map_area(p, l, d)		do { (void)(p); (void)(l); } while (0)

/* <mt-plat/sync_write.h> */
#define mt_reg_sync_writeb(v, a)	do { writeb((v), (void __iomem *)(a)); mb(); } while (0)
#define mt_reg_sync_writew(v, a)	do { writew((v), (void __iomem *)(a)); mb(); } while (0)
#define mt_reg_sync_writel(v, a)	do { writel((v), (void __iomem *)(a)); mb(); } while (0)

/* <mtk_lpae.h>: the DRAM of this phone starts at 0x40000000 and goes past 4 GiB */
#define enable_4G()			0

/* <linux/irqchip/mtk-gic-extend.h> */
static inline void mt_irq_dump_status(int irq) { }

/* ---- scheduler: sched_setscheduler() is no longer exported to modules ---- */
#include <linux/sched.h>
static inline int compat_sched_setscheduler(struct task_struct *p, int policy,
					    const void *param)
{
	if (policy == SCHED_FIFO || policy == SCHED_RR)
		sched_set_fifo(p);
	else
		sched_set_normal(p, 0);
	return 0;
}
#define sched_setscheduler(p, pol, prm)	compat_sched_setscheduler(p, pol, prm)

/* ---- timers / alarms ---- */
#include <linux/timer.h>
#include <linux/alarmtimer.h>
#define from_timer(var, t, f)		timer_container_of(var, t, f)
#define del_timer(t)			timer_delete(t)
#define del_timer_sync(t)		timer_delete_sync(t)
#define alarm_start_relative(a, t)	alarm_start_timer(a, t, true)
#define ALARMTIMER_NORESTART		0

/* ---- driver model ---- */
#include <linux/device.h>
#define class_create(owner, name)	class_create(name)

/* ---- display notifier (vendor mtk_disp_notify.h): screen state is not reported ---- */
#define MTK_DISP_EVENT_BLANK		0x1
#define MTK_DISP_BLANK_UNBLANK		0x0
#define MTK_DISP_BLANK_POWERDOWN	0x1
struct notifier_block;
static inline int mtk_disp_notifier_register(const char *src, struct notifier_block *nb) { return 0; }
static inline int mtk_disp_notifier_unregister(struct notifier_block *nb) { return 0; }

#define ioremap_nocache(a, s)		ioremap(a, s)

/* ---- misc ---- */
#include <linux/sched/clock.h>


/* ---- net ---- */
#include <linux/module.h>
#define MODULE_SUPPORTED_DEVICE(x)
#define netif_rx_ni(skb)		netif_rx(skb)
#define strlcpy(d, s, n)		strscpy(d, s, n)

#endif
