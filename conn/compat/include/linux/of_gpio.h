/* SPDX-License-Identifier: GPL-2.0 */
/* <linux/of_gpio.h> is gone; the connectivity code only probes optional GPIOs */
#ifndef __COMPAT_OF_GPIO_H
#define __COMPAT_OF_GPIO_H
#include <linux/gpio.h>
#include <linux/of.h>
static inline int of_get_named_gpio(const struct device_node *np, const char *name, int idx)
{
	return -ENOENT;
}
#endif
