/* SPDX-License-Identifier: GPL-2.0-only */
#ifndef HX_REPORT_H
#define HX_REPORT_H
#include <stddef.h>
#include <stdint.h>

#define HX_CONTACT_SLOTS 10u
#define HX_REPORT_BYTES 56u
struct hx_contact {
    uint16_t x, y;
    uint8_t width;
};
/* Independent wire-format encoder. No sensor acquisition, tracking or MMIO.
 * Unused slots must be supplied explicitly (normally x=y=0xffff).
 * metadata and tag depend on firmware mode: bitmap/0xf or state/diag selector.
 */
int hx_report_encode(uint8_t *out, size_t capacity,
                     const struct hx_contact contacts[HX_CONTACT_SLOTS],
                     unsigned active_count, uint16_t metadata,
                     unsigned tag, int swap_xy);
int hx_report_release(uint8_t *out, size_t capacity);
#endif
