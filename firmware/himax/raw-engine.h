/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef HX_RAW_ENGINE_H
#define HX_RAW_ENGINE_H
#include <stdint.h>

enum hx_raw_engine_result {
    HX_RAW_ENGINE_OK = 0,
    HX_RAW_ENGINE_INVALID = -1,
    HX_RAW_ENGINE_IO = -2,
    HX_RAW_ENGINE_TIMEOUT = -3
};

/* Injected transport only: this library does not access a physical controller.
 * Callbacks return zero on success; width is 1, 2 or 4 bytes. The caller owns
 * address mapping, barriers and hardware permission/recovery policy.
 */
struct hx_raw_engine_io {
    void *context;
    int (*write)(void *context, uint32_t address, unsigned width, uint32_t value);
    int (*read8)(void *context, uint32_t address, uint8_t *value);
};

/* Reproduce the CPU-side 0x40009048 transaction. The stock routine polls
 * indefinitely; this API adds a required finite read budget and I/O errors.
 * Destination is truncated to low16, as in the original write instruction.
 * This does not configure or validate the analog acquisition hardware.
 */
int hx_raw_engine_submit(const struct hx_raw_engine_io *io,
                          uint32_t parameter, uint32_t destination,
                          unsigned max_polls);
#endif
